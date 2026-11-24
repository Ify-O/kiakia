# Kiakia

*Every customer, answered fast.* ("Kiakia" means "quickly" in Yoruba.)

One simple inbox for small businesses that sell through chat. Kiakia brings in customer
messages from every channel, shows you **which customers are still waiting for a reply**,
turns voice notes into text, and sends payment links from inside the chat.
Messaging only: no CRM, no marketing broadcasts.

- Product plan: [PLAN.md](PLAN.md)
- Engineering rules (architecture, errors, migrations, tests, security): [docs/ENGINEERING.md](docs/ENGINEERING.md)
- Architecture diagrams and review (weak parts + improvement plan): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

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
cli.py (composition root: wires real adapters into use cases)
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

### Quality checks

```bash
uv run ruff check .        # lint (incl. security rules)
uv run ruff format .       # format
uv run mypy                # strict type checking
uv run pytest              # 33 unit + integration tests
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
- The token is part of Telegram's URL, so URLs are never logged or shown in errors.
- All SQL is parameterised. Test fixtures contain fake data only.
