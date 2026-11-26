#!/usr/bin/env bash
# Start everything Kiakia needs with ONE command.
#
#   ./scripts/start.sh          real mode: database + API + live Telegram watcher + web app
#   ./scripts/start.sh --demo   demo mode: separate demo database with fake customers, offline (ADR 0008)
#
# Press Ctrl+C once to stop everything.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO=0
[[ "${1:-}" == "--demo" ]] && DEMO=1

say()  { printf "\033[1m%s\033[0m\n" "$*"; }
fail() { printf "\033[31m✖ %s\033[0m\n" "$*" >&2; exit 1; }

# --- 0. Tools -------------------------------------------------------------
command -v docker >/dev/null || fail "Docker is not installed."
command -v uv >/dev/null || fail "uv is not installed (see README)."
command -v npm >/dev/null || fail "npm is not on your PATH (open a new terminal so nvm loads Node 22)."
[[ -f .env ]] || fail "No .env file. Copy .env.example to .env and fill it in."
set -a; source .env; set +a

for port in 8000 5173; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "Port $port is already in use (is Kiakia already running in another terminal?). Stop it with Ctrl+C there first."
  fi
done

# --- 1. Database ----------------------------------------------------------
if command -v colima >/dev/null && ! colima status >/dev/null 2>&1; then
  say "▶ Starting Docker (Colima)…"; colima start
fi
say "▶ Starting PostgreSQL…"
if docker compose version >/dev/null 2>&1; then docker compose up -d db
elif command -v docker-compose >/dev/null; then docker-compose up -d db
elif docker inspect kiakia-db >/dev/null 2>&1; then docker start kiakia-db >/dev/null
else
  docker run -d --name kiakia-db -e POSTGRES_USER="$POSTGRES_USER" -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_DB="$POSTGRES_DB" -p "127.0.0.1:${POSTGRES_PORT:-55432}:5432" -v kiakia_pgdata:/var/lib/postgresql/data \
    postgres:16 >/dev/null
fi
for _ in $(seq 1 30); do
  docker exec kiakia-db pg_isready -q -U "$POSTGRES_USER" && break; sleep 1
done
docker exec kiakia-db pg_isready -q -U "$POSTGRES_USER" || fail "PostgreSQL did not start. Try: colima restart"

if [[ $DEMO == 1 ]]; then
  export POSTGRES_DB=kiakia_demo KIAKIA_DEMO=1
  if ! docker exec kiakia-db psql -U "$POSTGRES_USER" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='kiakia_demo'" | grep -q 1; then
    docker exec kiakia-db psql -q -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE kiakia_demo"
  fi
fi

say "▶ Database migrations…"
uv run --quiet alembic upgrade head
if [[ $DEMO == 1 ]]; then
  say "▶ Filling the demo database with fake customers…"
  uv run --quiet python -m kiakia.cli seed-demo
fi

# --- 2. App processes -----------------------------------------------------
pids=()
cleanup() { say $'\n■ Stopping Kiakia…'; kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

say "▶ API on http://localhost:8000 …"
uv run --quiet uvicorn kiakia.api.app:create_app_from_env --factory --host 127.0.0.1 --port 8000 --log-level warning &
pids+=($!)

if [[ $DEMO == 0 ]]; then
  say "▶ Live Telegram watcher…"
  uv run --quiet python -m kiakia.cli watch &
  pids+=($!)
  if [[ -n "${EMAIL_ADDRESS:-}" ]]; then
    say "▶ Live email watcher ($EMAIL_ADDRESS)…"
    uv run --quiet python -m kiakia.cli watch --channel email &
    pids+=($!)
  fi
fi

say "▶ Web app…"
[[ -d web/node_modules ]] || npm --prefix web install --silent
npm --prefix web run dev --silent -- --port 5173 --strictPort >/dev/null &
pids+=($!)

# Only say "running" once both answer (the API checks the database in /health)
for _ in $(seq 1 30); do curl -sf -o /dev/null http://localhost:8000/health && break; sleep 1; done
curl -sf -o /dev/null http://localhost:8000/health || { say "✖ The API didn't start: see the error above."; exit 1; }
for _ in $(seq 1 30); do curl -s -o /dev/null http://localhost:5173/ && break; sleep 1; done
echo
if [[ $DEMO == 1 ]]; then
  say "🎬 Kiakia DEMO is running: open http://localhost:5173  (fake customers, nothing is sent)"
else
  say "⚡ Kiakia is running: open http://localhost:5173  (new Telegram${EMAIL_ADDRESS:+ and email} messages appear live)"
fi
say "   API docs: http://localhost:8000/docs   ·   Press Ctrl+C to stop everything."
wait
