# Kiakia: Engineering Guidelines

These are the rules we build Kiakia by. They come from the class notes and the instructor's
follow-up questions, plus industry best practice. Every piece of code should follow them.
If code breaks a rule, either fix the code or write down why here.

---

## 1. What makes a bad design (and what we do instead)

| Bad design | What it looks like | What we do instead |
|---|---|---|
| **Complexity** | Long functions, deep nesting, clever tricks, unclear names | Small functions that do one thing; clear names; plain code over clever code |
| **Hard to change** (fragile) | Change one thing and something unrelated breaks | Clear layers and boundaries; tests that catch the break straight away |
| **Tight coupling** | Code depends directly on details of other code (the database, the Telegram JSON, `requests`) | Depend on **interfaces (ports)**, not concrete classes; pass dependencies in (dependency injection) |
| **Duplication** | The same retry logic or SQL copied in three places | One place for each idea (DRY), e.g. one shared resilient HTTP helper for every API |

## 2. Architecture: Clean Architecture (Ports and Adapters)

Kiakia uses **Clean Architecture**, also called **Hexagonal** or **Ports and Adapters**.
The business logic sits in the middle and knows nothing about Telegram, Facebook, PostgreSQL
or HTTP. Those are "details" plugged in from the outside.

```
                 ┌──────────────────────────────────────────────┐
                 │  ENTRY POINTS (CLI now, web API later)        │
                 │  cli.py: the "composition root" that wires   │
                 │  real adapters into use cases                 │
                 └──────────────────────┬───────────────────────┘
                                        │ calls
                 ┌──────────────────────▼───────────────────────┐
                 │  APPLICATION (use cases)                      │
                 │  • ImportMessages   • ListWaitingCustomers    │
                 │  Depends ONLY on domain + ports                │
                 └──────────────────────┬───────────────────────┘
                                        │ uses
                 ┌──────────────────────▼───────────────────────┐
                 │  DOMAIN (the heart, no dependencies at all)   │
                 │  • Message, Contact, Channel, Direction        │
                 │  • PORTS (interfaces):                         │
                 │      MessageSource     MessageRepository       │
                 └──────────▲───────────────────────▲───────────┘
                            │ implements            │ implements
     ┌──────────────────────┴─────────┐   ┌─────────┴────────────────────────┐
     │ ADAPTERS: channels              │   │ ADAPTERS: storage                 │
     │  telegram/ client + parser      │   │  postgres/ repository             │
     │  (later) meta/, whatsapp/, ...  │   │  (only place that writes SQL)     │
     └──────────────┬──────────────────┘   └──────────────┬───────────────────┘
                    │ uses                                │
     ┌──────────────▼──────────────────┐   ┌──────────────▼───────────────────┐
     │ INFRASTRUCTURE: resilient HTTP   │   │ PostgreSQL in Docker              │
     │ (timeouts, retries, rate limits) │   │ schema managed by migrations      │
     └──────────────────────────────────┘   └───────────────────────────────────┘
```

**The dependency rule:** arrows point **inwards only**.
- `domain` imports nothing from our other layers.
- `application` imports only `domain`.
- `adapters` import `domain` (to implement its ports) and `infrastructure`.
- Only `cli.py` (the composition root) knows about everything, and it just wires things together.

**Why this answers "when one thing breaks, the others aren't affected":**
- If Telegram changes its JSON, only `adapters/telegram/parser.py` changes.
- If we add Facebook, we add `adapters/meta/` and nothing else changes.
- If we change databases, only `adapters/postgres/` changes.
- Use cases can be tested with **fake** sources and repositories, with no network and no database.

### Coupling: what we allow

| Type | Example | Allowed? |
|---|---|---|
| Data coupling: pass plain data | `save(messages: list[Message])` | ✅ Best |
| Interface coupling: depend on a port | Use case takes any `MessageSource` | ✅ Yes |
| Concrete coupling: depend on a specific class | Use case creates `TelegramClient()` itself | ❌ Only in the composition root |
| Format coupling: leaking an external format inward | Use case reads `update["message"]["chat"]["id"]` | ❌ Never; the parser converts it first |
| Global state: hidden shared variables | A module-level DB connection | ❌ Never |

### SOLID (short version)
- **S**ingle responsibility: one reason to change per class (the client fetches, the parser converts, the repository stores).
- **O**pen/closed: add a channel by adding an adapter, not by editing the use case.
- **L**iskov: every `MessageSource` must behave the same way to the use case.
- **I**nterface segregation: small ports (`MessageSource` doesn't also send replies; that will be its own port).
- **D**ependency inversion: use cases depend on ports; adapters implement them.

## 3. Project structure

```
kiakia/
  domain/            models.py (dataclasses), ports.py (interfaces), errors.py
  application/       use cases: import_messages.py, waiting.py
  adapters/
    telegram/        client.py (HTTP), parser.py (JSON → domain), source.py (implements MessageSource)
    postgres/        repository.py (implements MessageRepository)
  infrastructure/    http.py (shared resilient HTTP), logging.py
  config.py          settings from environment variables (.env)
  cli.py             composition root + command line
migrations/          Alembic database migrations (versioned schema)
tests/
  unit/              fast; no network, no database (fakes + recorded JSON)
  integration/       real PostgreSQL in Docker
  fixtures/          sample API responses (fake data only, never real customers)
docs/                PLAN.md lives at the root; design docs live here
```

**Rule:** a file's folder tells you its layer. If you can't decide where a file goes, the design is unclear. Stop and decide before coding.

## 4. Error handling

1. **Use our own exception types** (`domain/errors.py`): `SourceUnavailable`, `RateLimited`,
   `BadRecord`. Adapters translate library errors (e.g. `requests.Timeout`) into these, so
   the inner layers never depend on `requests` or `psycopg2`.
2. **Bad record → skip, log and count. Never crash the run.** The parser returns good
   records plus a list of rejects with reasons. `import_runs` stores the counts.
3. **Transient error → retry** (timeouts, connection errors, HTTP 429, 5xx).
   **Permanent error → fail fast** with a clear message (HTTP 400, 401, 404: wrong token, bad request).
4. **Never swallow errors silently.** Every `except` must log, re-raise, or turn the error into a result.
5. **Database writes are transactions:** a batch is either fully saved or not at all.
6. **No sensitive data in logs or errors:** no tokens, and no message text at INFO level.

## 5. Resilience: slow, failing or missing services

| Problem | Our strategy |
|---|---|
| Service is slow | **Timeouts** on every call: connect 5s, read 30s. Never wait forever |
| Temporary failure | **Retry with exponential backoff + jitter**: about 1s, 2s, 4s, 8s with randomness, so retries don't all hit at the same moment. Limited attempts |
| Rate limited (HTTP 429) | Honour the server's `retry_after` / `Retry-After`; keep a minimum gap between calls |
| Service down for a long time | Give up after N attempts with a clear error. Nothing is lost: we only move our "read position" (offset/cursor) **after** the data is safely saved, so the next run picks up where it stopped. *Later: a circuit breaker that stops calling a dead service for a while* |
| Same data delivered twice | **Idempotency:** unique keys (`channel + external_id`) and upserts. Running the import twice never duplicates |
| Crash halfway through | Save first, acknowledge second (**at-least-once** delivery + idempotency = no loss, no duplicates) |

## 6. Database: versioning and migrations

The schema **will** change (businesses, staff and payments come later). The industry
answer is **migrations**:

- Every schema change is a **numbered, versioned migration file** in `migrations/`, committed to git.
- The tool (**Alembic**, the standard for Python) records which version the database is at,
  in a table called `alembic_version`.
- `alembic upgrade head` brings any database (yours, a teammate's, production) to the latest version.
  `alembic downgrade -1` undoes the last one.
- **Never** edit a migration that has already been applied. Write a new one.
- Never change the database by hand in production.
- The application code and the migration for a change go in the **same commit**.

## 7. Testing (automating "it works")

| Level | What | Tools |
|---|---|---|
| **Unit** | Parser (good, missing and odd fields, bad records), use cases with fake ports, retry logic with a fake HTTP server | `pytest`; fixtures in `tests/fixtures/` |
| **Integration** | Repository against **real PostgreSQL in Docker**: save, re-save (no duplicates), queries | `pytest` + Docker |
| **End to end** | Import from the real API into the DB, done by hand for the demo | CLI |

Rules:
- Every bug found gets a test first, then the fix.
- Tests use **fake data only**. Never commit real customer messages.
- `pytest` must pass before every commit.

## 8. Security best practice

- **Secrets in `.env` only** (bot token, DB password). `.env` is in `.gitignore`;
  `.env.example` shows the names with fake values.
- **Parameterised SQL only.** Never build SQL by joining strings with user input (SQL injection).
- **Least privilege:** later, the app's database user gets only the rights it needs.
- **Validate all external input** in the parser. API data is untrusted.
- **Customer data is personal data:** keep only what we need, don't log message text, and plan
  deletion (GDPR / Nigeria's NDPA).
- **Tokens for connected accounts** (Meta, later) are stored **encrypted**.
- Pin dependency versions in `requirements.txt` and update them on purpose.

## 9. Caching: TTL and eviction (for later)

Not needed in week 1, but noted from class:
- **TTL (Time To Live):** a cached value expires after a set time, e.g. a contact's profile
  picture URL cached for 1 hour.
- **Eviction when the cache is full:**
  - **LRU** (Least *Recently* Used): throw out what hasn't been used for the longest time.
  - **LFU** (Least *Frequently* Used): throw out what is used least often. *("FLU" in my notes; I think this is the one meant.)*
- Rule: cache only what is slow to fetch and safe to be slightly out of date. Never cache payment status.

## 10. Working with Claude (AI-assisted development)

- **Opus for planning and design; Haiku for routine coding.** In Claude Code, switch with `/model`.
- Commit before each task, so the agent's work can be undone (`git revert`).
- Explore APIs by hand (browser/curl) before asking the agent; check fields against the real docs.
- Don't keep code you can't explain.
- **Review prompts** to run after each milestone:
  1. *"Review this in detail and make sure you understand my code's behaviour, architecture and patterns."*
  2. *"Extract my application's architecture. Does it follow Clean Architecture? Show the types of coupling and the error handling in a high-level diagram I can understand."*
  3. *"Split the diagram into API design, DB schema and all components."*
  4. *"How can I improve it, and where? Use industry best practice and clean architecture."*
  5. *"What kind of architecture did I use in this project?"*

## 11. Git workflow

- `main` always works. New work goes on a branch, e.g. `week1/telegram-importer`.
- Small commits, each one a working step with a clear message.
- Merge to `main` when the step is tested; push to GitHub.
