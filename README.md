# Kiakia

*Every customer, answered fast.* ("Kiakia" means "quickly" in Yoruba.)

One simple inbox for small businesses that sell through chat. Kiakia brings in customer
messages from every channel, shows you **which customers are still waiting for a reply**,
turns voice notes into text, and sends payment links from inside the chat.
Messaging only: no CRM, no marketing broadcasts.

- Product plan: [PLAN.md](PLAN.md)
- Engineering rules (architecture, errors, migrations, tests, security): [docs/ENGINEERING.md](docs/ENGINEERING.md)
- Architecture diagrams and review (weak parts + improvement plan): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- UI/UX design (screens, states, contract mapping) and a clickable mockup: [docs/ui/UI_DESIGN.md](docs/ui/UI_DESIGN.md)

## Quick start: one command

Needs uv, Docker (Colima is fine), Node 22 and a filled-in `.env` (see "How to run" below).

```bash
./scripts/start.sh          # real mode: database + API + live Telegram watcher + web app
./scripts/start.sh --demo   # demo mode: fake customers on all 6 platforms, nothing is really sent
```

Then open http://localhost:5173. Ctrl+C stops everything.

- **Live messages:** `uv run python -m kiakia.cli watch` keeps listening to Telegram (long polling),
  so new messages appear in the inbox within seconds. The start script runs it for you.
- **Demo mode** ([ADR 0008](docs/adr/0008-demo-mode.md)): `KIAKIA_DEMO=1` with a database whose name
  ends in `demo` (the script creates `kiakia_demo`). `seed-demo` resets and fills it.
  `import` and `watch` are disabled, and the web app shows a 🎬 ribbon.
- **Customer rights** ([ADR 0009](docs/adr/0009-customer-rights-and-export.md)): on a customer's page,
  *Stop offers*, *Withdraw details* (erases what they gave) or *Forget this customer* (deletes everything,
  logged anonymously).
- **Export the offers list**: *Customers → ⬇️ Export offers list* gives Excel, CSV (Google Sheets:
  File → Import) or a Google Ads Customer Match file. Only customers who said YES to offers are included.
  API: `GET /v1/exports/customers?format=csv|xlsx&layout=standard|google-ads`.
- **Presenting?** Follow [docs/DEMO.md](docs/DEMO.md).

## Week 1: Telegram importer

### Which API and why

**[Telegram Bot API](https://core.telegram.org/bots/api)**, method `getUpdates`.

- **Real customer messaging:** it's one of the channels Kiakia will support, so this is real
  product work, not a throwaway exercise.
- **Free, with instant access:** create a bot with @BotFather in 2 minutes. No business verification
  or app review (unlike Meta, which comes next as a second adapter).
- **Runs fully locally:** we *poll* for messages, so no public URL or webhook is needed.
- **Real-world behaviour to handle:** rate limits (HTTP 429 with `retry_after`), several message
  kinds (text, voice notes, photos…), edits, and group chats we must ignore.

### Architecture (Clean Architecture / Ports and Adapters)

```
cli.py (commands + printing) → bootstrap.py (composition root: wires adapters into use cases)
  └─ application/   ImportMessages, ListWaitingCustomers      ← depend only on ports
       └─ domain/   Message, Sender, WaitingCustomer + PORTS  ← no dependencies at all
            ▲ implemented by
  adapters/telegram/  client (HTTP) + parser (JSON → domain) + source
  adapters/postgres/  repository: the ONLY code with SQL
  infrastructure/     resilient HTTP: timeouts, retries + backoff + jitter, rate limit
migrations/           Alembic: versioned database schema
```

| Assignment part | Where |
|---|---|
| API client (timeouts, retries, rate limit) | [kiakia/infrastructure/http.py](kiakia/infrastructure/http.py), [kiakia/adapters/telegram/client.py](kiakia/adapters/telegram/client.py) |
| Parser (clean model, bad records never crash) | [kiakia/adapters/telegram/parser.py](kiakia/adapters/telegram/parser.py), [kiakia/domain/models.py](kiakia/domain/models.py) |
| Repository layer (one place for DB access) | [kiakia/adapters/postgres/repository.py](kiakia/adapters/postgres/repository.py) |
| Schema | [migrations/versions/0001_initial_schema.py](migrations/versions/0001_initial_schema.py) |
| PostgreSQL in Docker | [docker-compose.yml](docker-compose.yml) |
| Idempotency | Unique key `(channel, chat id, message id)` + upserts; "save first, move cursor second" in [kiakia/application/import_messages.py](kiakia/application/import_messages.py) |
| Tests | [tests/unit](tests/unit) (no network or DB), [tests/integration](tests/integration) (real Postgres) |

### How to run

Requirements: [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and all packages), Docker (with Compose), a Telegram bot token from @BotFather.

```bash
# 1. Settings: copy the example and fill in a DB password + your bot token
cp .env.example .env

# 2. Start PostgreSQL (port 55432, to avoid clashing with a local Postgres)
docker compose up -d
#    no "docker compose" (e.g. Colima)? -> brew install docker-compose, then: docker-compose up -d

# 3. Install Python 3.12 + all packages, exactly as pinned in uv.lock
uv sync

# 4. Create the tables (database migrations)
uv run alembic upgrade head

# 5. Import: start small, then everything
uv run python -m kiakia.cli import --limit 10
uv run python -m kiakia.cli import

# 6. See the results
uv run python -m kiakia.cli stats      # what's in the database
uv run python -m kiakia.cli waiting    # customers still waiting for a reply

# 7. Tests (integration tests need step 2 running; otherwise they're skipped)
uv run pytest
```

Run the import as often as you like: a second run never creates duplicates.

### Run the web API (Assignment 2)

The API contract is [openapi.yaml](openapi.yaml), written **before** the code
([decision records](docs/adr/README.md)).

```bash
uv run uvicorn kiakia.api.app:create_app_from_env --factory --reload
```

Then open <http://localhost:8000/docs> to browse and try the API (it shows `openapi.yaml` itself).

| Endpoint | What it does |
|---|---|
| `GET /v1/conversations` | List with filters (`status`, `channel`, `q`, `sort`) and cursor pagination |
| `GET /v1/conversations/{id}` | One conversation with details |
| `GET /v1/conversations/{id}/messages` | Its messages, newest first, paginated |
| `POST /v1/conversations/{id}/replies` | Reply to the customer through Telegram. **Idempotent**: send an `Idempotency-Key` header; retries never message the customer twice |
| `PATCH /v1/conversations/{id}` | Mark resolved / reopen, set a category (enquiry, order, complaint, other) |
| `GET /v1/messages` | All messages across conversations, newest first (filter by platform, direction, dates) |
| `GET /v1/customers`, `GET /v1/customers/{id}` | The customer list (search, consent filters) and one customer with their consent history |
| `POST /v1/customers/{id}/consent` | Record that we asked, or the customer's YES / NO / withdrawal (details only with a YES) |
| `GET /v1/stats` | Dashboard numbers for a period: response rate, typical reply time, per platform, per category |
| `GET /health` | API + database health |

Lint the contract: `npx @redocly/cli@2.83.0 lint openapi.yaml` (Node 22, see `.nvmrc`).

### Run the web inbox (Assignment 2)

The UI is built **only** from `openapi.yaml`: its TypeScript types are generated from the contract
([ADR 0005](docs/adr/0005-fastapi-and-react.md)). Design: [docs/ui/UI_DESIGN.md](docs/ui/UI_DESIGN.md).

```bash
# Terminal 1: the API
uv run uvicorn kiakia.api.app:create_app_from_env --factory --reload

# Terminal 2: the web app (Node 22, see .nvmrc)
cd web
npm install
npm run dev          # open http://localhost:5173
```

| Screen | What you can do |
|---|---|
| 📊 Dashboard | Red/green "customers waiting" light, longest wait, response rate, typical reply time, messages per platform, categories |
| 📥 Inbox | Filter by status, platform, date, category, search; open a chat; reply; mark resolved; label it |
| 📡 Messages by platform | Per-platform numbers and every message, newest first |
| 👥 Customers | Everyone who wrote, their platforms, "first came from", saved details **only with their YES**, consent badges and the full consent history ([ADR 0007](docs/adr/0007-customer-list-with-recorded-consent.md)) |

Web checks: `npm run check:api` (types match the contract), `npm run typecheck`, `npm test`, `npm run build`.

### Quality checks

```bash
uv run ruff check .        # lint (incl. security rules)
uv run ruff format .       # format
uv run mypy                # strict type checking
uv run pytest              # unit + integration + contract tests (incl. Schemathesis)
```

The same checks run automatically on GitHub for every push and pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

### What's in the database

| Table | Holds |
|---|---|
| `contacts` | A customer |
| `contact_identities` | That customer's id on a channel (e.g. Telegram user id); unique per channel |
| `conversations` | One chat thread; `last_inbound_at` / `last_outbound_at` drive the waiting list |
| `messages` | Every message; unique on `(channel, external_chat_id, external_message_id)`; times in UTC |
| `sync_cursors` | Where each source continues from (Telegram's update offset) |
| `import_runs` | One row per import: status, seen, saved, bad, error |
| `rejected_updates` | Dead letter: updates we couldn't parse, with the original JSON, so nothing is lost |
| `alembic_version` | Which migration the database is at |

### Surprising things in the data

- **Telegram forgets messages once you confirm them.** `getUpdates` keeps updates for only
  about 24 hours, and asking with a higher `offset` **deletes** the older ones. So the order matters:
  **save to the database first, then move the cursor**. If we crash in between, the next run fetches
  the same messages again, and the unique key stops duplicates.
- **A message id is NOT unique on its own:** every chat counts from 1. The real key is
  *chat id + message id*.
- **Every new customer's first message is `/start`**, which Telegram sends automatically when they
  press Start. It's stored like any text message for now; later it can be shown as "started a chat".
- **Edits arrive as a separate `edited_message` update with the same `message_id`.** Our upsert
  simply replaces the text, which is exactly the right behaviour.
- **A photo arrives as a list of sizes.** We keep the largest one's `file_id`.
- **Voice notes have no text**, only a `file_id`. We store it so week 5 can download and transcribe it.
  In the waiting list they appear as `[voice]`.
- **Dates are unix timestamps (UTC).** We store UTC and show times in Africa/Lagos time.
- **Group chats also arrive at the bot**, but they aren't customer conversations, so they're ignored
  (and counted).
- **Telegram sends its rate-limit hint in the JSON body** (`parameters.retry_after`), not only the
  standard `Retry-After` header, so the client reads both.

### Security

- The bot token and DB password live only in `.env` (git-ignored). `.env.example` has placeholders.
- Settings are validated at startup (pydantic-settings); secrets are `SecretStr`, masked in logs and errors.
- The database only accepts connections from this machine (`127.0.0.1`).
- The token is part of Telegram's URL, so URLs are never logged or shown in errors.
- All SQL is parameterised. Test fixtures contain fake data only.
