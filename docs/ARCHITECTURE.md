# Kiakia: Architecture Review

> A review of the code (Week 1, Sprints 1–2, Assignment 2: API + web inbox, customer consent,
> live watcher and demo mode). Last updated 2026-11-25.
> Part A explains how it works, in diagrams. Part B judges it against best practice
> and lists the weak parts, with evidence and fixes.
> GitHub draws the diagrams automatically.

---

# Part A: How Kiakia works

## A1. The big picture

What Kiakia does today, in one picture.

```mermaid
flowchart LR
    C["👤 Customer<br/>sends a message"] --> T["✈️ Telegram<br/>(holds messages ~24h)"]
    T <-->|"watch: long polling<br/>(getUpdates, 25 s)"| W["👀 kiakia watch<br/>(background worker)"]
    W -->|"save, clean, no duplicates"| DB[("🐘 PostgreSQL<br/>in Docker")]
    DB <--> API["⚙️ Kiakia API<br/>(FastAPI, contract = openapi.yaml)"]
    API <-->|"/v1/… JSON"| WEB["📱 Web inbox<br/>(React, polls every few seconds)"]
    O["🧑‍💼 Business owner"] --> WEB
    API -->|"reply (sendMessage)"| T
    S["🚀 scripts/start.sh"] -.->|"starts all of it<br/>(--demo: fake data, offline)"| W & API & WEB & DB
```

## A2. What kind of architecture is this?

**Clean Architecture, in the "Ports and Adapters" (Hexagonal) style.**

- The **domain** (Kiakia's own idea of a message, a customer, a waiting list) sits in the middle and
  depends on nothing.
- The **use cases** (import messages, list waiting customers) only talk to **ports**, which are interfaces.
- **Adapters** plug the real world in: Telegram on one side, PostgreSQL on the other.
- One place, `bootstrap.py`, builds and wires everything: the **composition root**. The CLI only parses commands and prints.

Patterns used: Repository, Adapter, Dependency Injection (constructor injection), Composition Root,
Use Case / Interactor, Anti-Corruption Layer (the parser), Strategy (the pluggable "retry-after" reader),
and Value Objects (frozen dataclasses).

## A3. Layers: who may talk to whom

Arrows mean "depends on / imports". **Every arrow points inwards**, which is the main rule of
Clean Architecture. There are now **two entry points** (CLI and API) and both go through the same
`bootstrap.py`, so they share every use case.

```mermaid
flowchart TB
    subgraph ENTRY["🚪 Entry points"]
        CLI["cli.py<br/>import · watch · waiting · stats · seed-demo"]
        API["api/<br/>FastAPI routes + Problem Details errors"]
        BOOT["bootstrap.py<br/>wiring + connection lifecycle<br/>(composition root)"]
        CFG["config.py<br/>validated settings (pydantic)<br/>KIAKIA_DEMO guard"]
    end

    subgraph APP["🧠 Application: use cases"]
        IMP["ImportMessages"]
        WATCH["WatchMessages"]
        CONV["ListConversations · GetConversation<br/>ListMessages · ListFeed · UpdateConversation"]
        REP["SendReply"]
        CUST["ListCustomers · GetCustomer · RecordConsent"]
        STATS["GetInboxStats"]
    end

    subgraph DOMAIN["❤️ Domain: depends on nothing"]
        MODELS["models.py<br/>Message, Conversation, Customer, Consent…"]
        PORTS["ports.py<br/>13 small interfaces"]
        ERR["errors.py<br/>KiakiaError family"]
    end

    subgraph ADAPTERS["🔌 Adapters: the outside world"]
        TG["telegram/<br/>client · parser · source · sender"]
        PG["postgres/<br/>PostgresStore (only SQL)"]
        DEMO["demo/<br/>DemoSender (offline)"]
    end

    subgraph INFRA["🛠️ Infrastructure"]
        HTTP["http.py<br/>timeouts, retries, rate limit"]
    end

    CLI & API --> BOOT
    BOOT --> CFG
    BOOT --> IMP & WATCH & CONV & REP & CUST & STATS
    BOOT -.->|builds| TG & PG & DEMO
    WATCH --> IMP
    IMP & CONV & REP & CUST & STATS --> PORTS
    PORTS --> MODELS
    TG & PG & DEMO -->|implement| PORTS
    TG --> HTTP
    TG & PG & HTTP --> ERR

    classDef core fill:#ffe9e9,stroke:#c0392b;
    classDef app fill:#fff4d6,stroke:#d4a017;
    classDef adapter fill:#e6f2ff,stroke:#2e86de;
    class MODELS,PORTS,ERR core;
    class IMP,WATCH,CONV,REP,CUST,STATS app;
    class TG,PG,DEMO,HTTP adapter;
```

## A4. Ports and adapters (the "plugs")

The use cases only know the **interfaces** on the left. The real classes on the right fit into them.
Tests plug in fakes instead. (This diagram shows the import ports; the API added more of the same kind:
`ConversationQuery`, `ConversationUpdater`, `MessageSender`, `ReplyStore`, `IdempotencyStore`,
`CustomerStore`, `OptOutRecorder`, `HealthCheck`. `PostgresStore` implements all the storage ones;
`TelegramSender` and `DemoSender` both implement `MessageSender`.)

```mermaid
classDiagram
    direction LR
    class MessageSource {
        <<port>>
        name
        fetch(cursor, limit) FetchResult
    }
    class MessageRepository {
        <<port>>
        save_messages(messages) int
        waiting_customers() list
    }
    class CursorStore {
        <<port>>
        get_cursor(source)
        set_cursor(source, cursor)
    }
    class RejectStore {
        <<port>>
        save_rejects(source, rejects) int
    }
    class StatsQuery {
        <<port>>
        inbox_stats() InboxStats
    }
    class ImportRunLog {
        <<port>>
        start_run(source) int
        finish_run(run_id, status, counts)
    }
    class TelegramSource {
        <<adapter>>
    }
    class PostgresStore {
        <<adapter>>
    }
    class FakeSource {
        <<fake>>
    }
    class FakeStore {
        <<fake>>
    }
    class ImportMessages {
        <<use case>>
        run(max_records) ImportSummary
    }

    TelegramSource ..|> MessageSource
    FakeSource ..|> MessageSource
    PostgresStore ..|> MessageRepository
    PostgresStore ..|> CursorStore
    PostgresStore ..|> ImportRunLog
    PostgresStore ..|> RejectStore
    PostgresStore ..|> StatsQuery
    ImportMessages --> RejectStore
    FakeStore ..|> MessageRepository
    ImportMessages --> MessageSource
    ImportMessages --> MessageRepository
    ImportMessages --> CursorStore
    ImportMessages --> ImportRunLog
```

## A5. What happens when you run `import`

The most important flow. Note the order: **save first, move the cursor second.** Telegram deletes
messages once we ask for a later cursor, so this order means a crash can never lose a message.

```mermaid
sequenceDiagram
    autonumber
    actor Owner
    participant CLI as cli.py + bootstrap
    participant UC as ImportMessages
    participant DB as PostgresStore
    participant SRC as TelegramSource
    participant HTTP as ResilientHttpClient
    participant TG as Telegram API

    Owner->>CLI: import
    CLI->>UC: run()
    UC->>DB: start_run("telegram")
    loop until no new updates
        UC->>DB: get_cursor("telegram")
        UC->>SRC: fetch(cursor, 100)
        SRC->>HTTP: get_json(getUpdates?offset=cursor)
        HTTP->>TG: GET (timeout 5s/30s, retries)
        TG-->>HTTP: JSON updates
        HTTP-->>SRC: dict
        SRC->>SRC: parser: JSON → Message (bad → Reject)
        SRC-->>UC: FetchResult(messages, rejects, next_cursor)
        UC->>DB: save_messages() [ONE transaction, upsert]
        UC->>DB: save_rejects() [dead letter: keep bad records]
        UC->>DB: set_cursor(next_cursor)
    end
    UC->>DB: finish_run(succeeded, counts)
    UC-->>CLI: ImportSummary
    CLI-->>Owner: "2 updates → 2 saved, 0 bad"
```

## A6. Error handling: what happens when things go wrong

```mermaid
flowchart TD
    REQ["HTTP request to Telegram"] --> R{"What came back?"}
    R -->|"200 + valid JSON"| OK["✅ return data"]
    R -->|"timeout / connection error /<br/>429 / 500, 502, 503, 504 /<br/>invalid JSON"| T{"Attempts left?<br/>(max 5)"}
    T -->|"yes"| W["wait: server hint or<br/>1s, 2s, 4s, 8s ± jitter"] --> REQ
    T -->|"no, and it was 429"| RL["RateLimited"]
    T -->|"no"| SU["SourceUnavailable"]
    R -->|"400 / 401 / 403 / 404 / 409"| SR["SourceRejectedRequest<br/>(fail fast: bad token?)"]

    OK --> P["Parser, record by record"]
    P -->|"good"| MSG["Message"]
    P -->|"group chat / button press"| IGN["ignored (counted)"]
    P -->|"missing field / bad date"| REJ["Reject (logged + counted)<br/>never crashes the run"]

    RL & SU & SR & DBE["StorageUnavailable<br/>(DB down)"] --> UCE["ImportMessages: run marked 'failed'<br/>with the error, then re-raised"]
    UCE --> CLIE["cli.py: prints 'Error: …'<br/>exit code 1, no stack trace, no secrets"]

    classDef bad fill:#ffe9e9,stroke:#c0392b;
    classDef good fill:#e9f7ef,stroke:#27ae60;
    class RL,SU,SR,DBE,REJ bad;
    class OK,MSG good;
```

## A7. Database schema

Managed by **Alembic migrations `0001` → `0006`**. Unique keys (🔑) are what make imports idempotent.

```mermaid
erDiagram
    contacts ||--o{ contact_identities : "has one per channel"
    contacts ||--o{ conversations : "talks in"
    contacts ||--o{ consent_events : "history of YES/NO"
    conversations ||--o{ messages : "contains"
    messages |o--o{ idempotency_keys : "reply it produced"

    contacts {
        bigint id PK
        text display_name
        text phone "only if details_consent = granted"
        text email "only if details_consent = granted"
        text details_consent "unknown, asked, granted, declined, withdrawn"
        text marketing_consent "same states"
    }
    contact_identities {
        bigint id PK
        bigint contact_id FK
        text channel "UNIQUE with external_id"
        text external_id
        text username
    }
    conversations {
        bigint id PK
        text channel "UNIQUE with external_chat_id"
        text external_chat_id
        bigint contact_id FK
        timestamptz last_inbound_at
        timestamptz last_outbound_at
        text category "enquiry, order, complaint, other (0005)"
        timestamptz resolved_at "(0005)"
    }
    messages {
        bigint id PK
        bigint conversation_id FK
        text channel "UNIQUE with chat + message id"
        text external_message_id
        text direction "in or out"
        text kind "text, voice, image..."
        text body
        timestamptz sent_at "always UTC"
    }
    consent_events {
        bigint id PK
        bigint contact_id FK
        text kind "details or marketing"
        text status "asked, granted, declined, withdrawn"
        text wording "the exact question asked"
        timestamptz recorded_at
    }
    idempotency_keys {
        text scope PK "e.g. replies"
        text key PK "client's Idempotency-Key"
        text request_hash "same key + different body = 409"
        text status "in_progress or completed"
        bigint message_id FK
    }
```

Bookkeeping tables not shown: `sync_cursors`, `import_runs`, `rejected_updates` (dead letter).
The database itself enforces the privacy rule: CHECK `ck_contacts_details_need_consent` makes it
**impossible** to store a phone or email unless details consent is `granted` (migration `0006`).

## A8. API design

The contract is written **first**, by hand, in `openapi.yaml` (OpenAPI 3.1, v1.3.0), linted with
Redocly in CI. The server serves that file at `/openapi.yaml`, contract tests (openapi-core +
Schemathesis) prove the code matches it, and the web app's TypeScript types are **generated** from it.
Every contract change has an ADR (`docs/adr/`).

```mermaid
flowchart LR
    SPEC["📜 openapi.yaml<br/>(source of truth)"]
    SPEC -->|"Redocly lint"| CI["✅ CI"]
    SPEC -->|"contract tests"| API
    SPEC -->|"openapi-typescript"| TYPES["web/src/api/schema.d.ts"]
    TYPES --> WEB["📱 Web app (openapi-fetch)"]
    WEB -->|"Vite proxy /v1, /health"| API

    subgraph API["⚙️ FastAPI (kiakia/api)"]
        E1["GET /v1/conversations<br/>status · channel · q · category · from/to · cursor"]
        E2["GET · PATCH /v1/conversations/{id}"]
        E3["GET /v1/conversations/{id}/messages"]
        E4["POST /v1/conversations/{id}/replies<br/>Idempotency-Key required"]
        E5["GET /v1/messages (feed)"]
        E6["GET /v1/customers · /v1/customers/{id}<br/>POST /v1/customers/{id}/consent"]
        E7["GET /v1/stats · /health (demoMode)"]
    end
    API --> BOOT["bootstrap.py → same use cases → same ports"]
```

Rules every endpoint follows: errors are **RFC 9457 Problem Details** (`application/problem+json`),
lists use an **opaque keyset cursor** (no page numbers that skip rows when data changes), dates are
strict **RFC 3339**, and unknown or `null` fields are rejected with a friendly field message.

## A9. Coupling: how tightly parts are tied together

| From → To | Type of coupling | Verdict |
|---|---|---|
| Use cases → ports | **Interface coupling** (depends on a Protocol) | ✅ Best kind |
| Use cases → domain models | **Data coupling** (passes plain dataclasses) | ✅ Good |
| Parser → Telegram JSON | **Format coupling**, but *contained* in one file (anti-corruption layer) | ✅ Correct place for it |
| TelegramClient → ResilientHttpClient | **Concrete coupling** (a class, not an interface) | 🟡 Acceptable; could be a port later |
| Adapters → domain errors | **Inward dependency** | ✅ Correct direction |
| `bootstrap.py` → everything | **Concrete coupling**, on purpose (composition root) | ✅ Fixed W8: the CLI no longer imports adapters |
| `cli stats` → `GetInboxStats` → `StatsQuery` | Interface coupling | ✅ Fixed W9 (was a leak) |
| Alembic `env.py` → `kiakia.config` | Config coupling | ✅ Fine |
| Integration tests → migrations | Tests build the DB through Alembic | ✅ Excellent: tests prove migrations work |


## A10. Live messages: `kiakia watch`

The watcher is a loop around the same `ImportMessages` use case. Telegram's **long polling** holds
the request open for up to 25 s and answers the moment a message arrives, so new messages show up
in a second or two without webhooks (which would need a public HTTPS address).

```mermaid
flowchart TD
    START(["kiakia watch"]) --> IMP["ImportMessages.run()<br/>getUpdates(timeout=25)"]
    IMP -->|"saved N (maybe 0)"| IMP
    IMP -->|"SourceUnavailable / RateLimited<br/>(network down)"| BACK["wait 5 s → 10 → 20 → … max 60 s"] --> IMP
    IMP -->|"409 Conflict<br/>(another poller running)"| BACK
    IMP -->|"other 4xx<br/>(bad token)"| STOP(["stop with a clear error"])
    IMP -->|"Ctrl+C"| BYE(["stop cleanly"])
```

## A11. Sending a reply safely (idempotency)

A shaky phone connection can make the browser send the same reply twice. The `Idempotency-Key`
(one per draft) makes the second request return the **first** result instead of messaging the
customer twice.

```mermaid
sequenceDiagram
    autonumber
    participant WEB as Web app
    participant API as POST /replies
    participant UC as SendReply
    participant DB as PostgresStore
    participant OUT as MessageSender<br/>(Telegram or Demo)

    WEB->>API: body + Idempotency-Key K
    API->>UC: execute(conversation, text, K)
    UC->>DB: claim K (hash of body)
    alt K already completed, same body
        DB-->>UC: earlier message
        UC-->>WEB: 201 same reply (nothing re-sent)
    else K used with a different body
        UC-->>WEB: 409 Problem: key reused
    else K new
        UC->>OUT: send
        alt delivered
            UC->>DB: save outbound message + mark K completed
            UC-->>WEB: 201 reply
        else failed for sure (4xx)
            UC->>DB: release K (safe to retry)
            UC-->>WEB: 502 Problem
        else timeout: maybe delivered
            Note over UC,DB: K stays locked (DeliveryUnknown):<br/>never risk a double message
            UC-->>WEB: 502 Problem "check the chat"
        end
    end
```

## A12. Customer details and consent (NDPA 2023)

Details are saved **only after a recorded YES**, and every change is kept in `consent_events`
(with the exact wording asked). There are two separate consents, each with the same states:

```mermaid
stateDiagram-v2
    [*] --> unknown: new customer
    unknown --> asked: owner taps "Ask" (question sent in the chat)
    asked --> granted: they said YES
    asked --> declined: they said NO (the app won't ask again)
    granted --> withdrawn: they change their mind
```

- **Details** (name, phone, email): saved only on `granted`; on `withdrawn` the phone and email are
  erased (the database CHECK enforces it).
- **Marketing** ("offers & sales"): the app only offers to ask once details are granted. A customer
  who replies **STOP** (or "unsubscribe", "opt out"…) is moved to `withdrawn` automatically by the importer.

## A13. Demo mode (ADR 0008)

```mermaid
flowchart LR
    ENV["KIAKIA_DEMO=1"] --> CFG{"config.py:<br/>does the DB name<br/>end in 'demo'?"}
    CFG -->|"no"| ERR["❌ ConfigError:<br/>refuse to start"]
    CFG -->|"yes (kiakia_demo)"| BOOT["bootstrap.py"]
    BOOT -->|"every channel"| DS["DemoSender:<br/>'delivers' offline,<br/>nothing reaches a person"]
    BOOT -->|"import / watch"| OFF["disabled"]
    SEED["seed-demo"] --> RESET["reset_demo_data()<br/>(refuses unless DB ends in 'demo')"] --> FILL["12 fake customers,<br/>6 platforms, every consent state"]
    BOOT --> H["/health → demoMode: true"] --> RIB["🎬 ribbon in the web app"]
```

Two independent guards (config **and** the reset query) mean the real database can't be wiped by mistake.

---

# Part B: Review against best practice

## B1. What's already strong ✅

| Area | Why it's good |
|---|---|
| **Dependency rule** | The domain imports nothing from other layers; use cases only see ports. Checked file by file |
| **Idempotency** | DB unique keys + `ON CONFLICT DO UPDATE` + "save first, move cursor second" = no loss, no duplicates |
| **Resilience** | Timeouts, capped retries, exponential backoff with jitter, server hints, fail-fast on permanent errors |
| **Error translation** | `requests`/`psycopg` errors become Kiakia errors at the edge; the CLI shows clean messages |
| **Bad data** | The parser never crashes on one bad record; rejects are counted per run |
| **Schema versioning** | Alembic migrations; CI runs them up → down → up |
| **Testing** | 54 tests: unit (no network/DB, using fakes) + integration (real Postgres built by migrations) |
| **Tooling** | uv lockfile, ruff (incl. security rules), strict mypy, CI on every PR |
| **Secrets** | `.env` git-ignored; token never in logs or errors; `Settings.__repr__` hides it |
| **Time** | Aware datetimes enforced by the domain; stored in UTC, shown in Africa/Lagos time |

## B2. Weak parts 🔴

Each item says how I **proved** it, where it is, and how to fix it. Ordered by priority.

### 🔴 High priority

**W1. A database error can hide the real error.**
> ✅ **Fixed in Sprint 1:** the failure log is wrapped in its own try/except, and the original error is always re-raised. Test: `test_original_error_survives_when_logging_the_failure_also_fails`.
[kiakia/application/import_messages.py:70-79](../kiakia/application/import_messages.py)
If the database goes down while saving, the `except` block tries to write `finish_run(... "failed")`
to that same database, which fails too. The user then sees the *second* error and the real cause is lost.
*Proof:* with a fake DB that fails on both calls, the user saw `SECONDARY: could not log the run`.
*Fix:* wrap `finish_run` in its own try/except that logs and keeps the original error, so the
original is always what's re-raised.

**W2. A server can make Kiakia sleep for an hour.**
> ✅ **Fixed in Sprint 1:** a hint above `max_delay` raises `RateLimited(retry_after=…)` immediately instead of sleeping. Test: `test_huge_retry_after_is_not_obeyed_blindly`.
[kiakia/infrastructure/http.py:110](../kiakia/infrastructure/http.py)
The server's "retry after" hint is used as-is. `RetryPolicy.max_delay = 30` doesn't apply to it.
*Proof:* a `Retry-After: 3600` response made the client sleep 3600 seconds.
*Fix:* `delay = min(wait_hint, self.retry.max_delay)`. If the hint is bigger than allowed, raise
`RateLimited(retry_after=hint)` instead of blocking.

**W3. The database is reachable from your whole network.**
> ✅ **Fixed in Sprint 1:** Compose publishes `127.0.0.1:55432` only; the local container was recreated the same way, with the same data.
[docker-compose.yml:10](../docker-compose.yml)
`"55432:5432"` binds to `0.0.0.0`, so anyone on the same Wi-Fi (café, office) can try to log in.
*Proof:* `docker port kiakia-db` shows `0.0.0.0:55432`.
*Fix:* `"127.0.0.1:${POSTGRES_PORT:-55432}:5432"`, so only your own laptop can connect.

**W4. Messages are lost if the import isn't run within ~24 hours.**
> 🟡 **Mostly fixed (2026-11-25):** `kiakia watch` (long polling, see A10) runs all the time, started by
> `scripts/start.sh`. Still open: it only runs while your laptop is on; webhooks on a server remain the long-term fix.
This is how Telegram's `getUpdates` works, not a bug, but today nothing runs the import automatically.
*Fix (short term):* a scheduled job (cron/launchd) every few minutes.
*Fix (proper, week 3):* **webhooks**, where Telegram pushes each message to us instantly. That's also
needed for the real-time "customer waiting" alerts.

**W5. Rejected records are gone for good.**
> ✅ **Fixed in Sprint 2:** each `Reject` carries its original JSON and is stored in `rejected_updates` (migration `0003`) before the cursor moves. New `RejectStore` port. Tests prove call order and idempotency.
[kiakia/application/import_messages.py:65-66](../kiakia/application/import_messages.py)
A bad update is only *logged*, then the cursor moves past it and Telegram deletes it, so it can never be
inspected or replayed.
*Fix:* a **dead-letter table** (`rejected_updates`: source, ref, reason, raw JSON, received_at), written in
the same transaction as the good messages.

### 🟡 Medium priority

**W6. A password with special characters breaks migrations.**
> ✅ **Fixed in Sprint 1:** the URL is built with `sqlalchemy.URL.create`. Test: `test_password_with_special_characters_survives_the_url`.
[kiakia/config.py:28-33](../kiakia/config.py)
The DB URL is built by gluing strings together, so `@` or `/` in the password corrupts it.
*Proof:* with password `p@ss/word`, the URL parser read the host as `ss` and the password as `p`.
*Fix:* build it with `sqlalchemy.URL.create(...)`, which escapes every part safely.

**W7. A wrong setting shows a raw Python crash.**
> ✅ **Fixed in Sprint 1:** settings are validated by **pydantic-settings**; all problems are reported at once, without echoing values, and secrets are `SecretStr`. 9 tests in `tests/unit/test_config.py`.
[kiakia/config.py:60](../kiakia/config.py)
`int(os.getenv("POSTGRES_PORT"))` throws a plain `ValueError` that skips the friendly `ConfigError` path.
*Proof:* `POSTGRES_PORT=abc` printed `ValueError: invalid literal for int()` with a stack trace.
*Fix:* validate all settings in one place. Best practice is **pydantic-settings**, which validates types and
reports every bad setting at once with a clear message.

**W8. `cli.py` does two jobs.**
> ✅ **Fixed in Sprint 2:** `kiakia/bootstrap.py` is the composition root; the CLI only parses and prints, and imports no adapters.
[kiakia/cli.py](../kiakia/cli.py)
It's both the **user interface** (printing) and the **composition root** (building objects). When the
FastAPI entry point arrives, the wiring would get copied, which is duplication.
*Fix:* move wiring to `kiakia/bootstrap.py` (e.g. `build_import_use_case(settings)`), used by both
the CLI and the future API.

**W9. `stats` bypasses the ports.**
> ✅ **Fixed in Sprint 2:** `GetInboxStats` use case + `StatsQuery` port, returning a typed `InboxStats`.
[kiakia/cli.py:73-77](../kiakia/cli.py), [kiakia/adapters/postgres/repository.py:198](../kiakia/adapters/postgres/repository.py)
`counts()` isn't on any port, so the CLI depends directly on `PostgresStore`, a small crack in the
architecture.
*Fix:* add a `GetStats` use case and a small `StatsQuery` port.

**W10. Database connections are never closed.**
> ✅ **Fixed in Sprint 2:** `with app(settings) as services:` always closes the connection, even after an error (integration-tested).
[kiakia/cli.py:27-30](../kiakia/cli.py)
Fine for a short command, but a long-running web API would leak connections.
*Fix:* use `with connect(...) as conn:` in the composition root now, and a **connection pool**
(`psycopg_pool`) for the API later.

**W11. Two imports at the same time can crash each other.**
[kiakia/adapters/postgres/repository.py:85](../kiakia/adapters/postgres/repository.py)
`_contact_id` does "look up, then insert if missing". If two runs see a new customer simultaneously, both
insert and one fails on the unique key. Nothing stops two runs at once (e.g. a scheduled job + you by hand).
*Found by reasoning, not yet proven with a test.*
*Fix:* take a Postgres **advisory lock** per source at the start of `ImportMessages.run`, and make
`_contact_id` a single `INSERT … ON CONFLICT … RETURNING`.

**W12. Messages and cursor are saved in two separate transactions.**
[kiakia/application/import_messages.py:68-69](../kiakia/application/import_messages.py)
This is safe today thanks to idempotency (at-least-once), but it relies on every future repository being
idempotent too.
*Fix (cleaner):* a **Unit of Work** port, so the use case can say "save these messages *and* this cursor
together". That gives exactly-once delivery inside the DB.

### 🟢 Low priority / polish

| # | Weakness | Where | Fix |
|---|---|---|---|
| W13 ✅ | ~~Run status is a free string~~ **Fixed:** `RunStatus` StrEnum | `ports.py`, `import_messages.py` | `RunStatus` StrEnum (avoid "stringly typed" code) |
| W14 ✅ | ~~`update_id: true` (a boolean) is accepted as `1`~~ **Fixed** with a `TypeGuard` helper `_is_int` | [parser.py:122](../kiakia/adapters/telegram/parser.py) | `type(update_id) is int` |
| W15 ✅ | ~~`channel` column has no CHECK~~ **Fixed** in migration `0002`, with a test that every `Channel` is accepted | migration 0001 | Add it in migration `0002` |
| W16 ✅ | ~~Foreign keys without indexes~~ **Fixed** in migration `0002` | migration 0001 | Indexes in `0002` |
| W17 | 4–6 SQL statements per message (N+1 pattern) | `repository.py:76` | Batch with `executemany` when volumes grow |
| W18 | Plain-text logs, no metrics | everywhere | Structured JSON logs + counters (later: OpenTelemetry) |
| W19 | No tests for `TelegramSource` or `cli.py` (config tests ✅ added in Sprint 1) | `tests/` | Add them; measure coverage with `pytest-cov` |
| W20 | Sync `requests`, while FastAPI is async | `http.py` | Consider `httpx` when the API arrives |

## B3. Security review 🔐

| Check | Status | Notes |
|---|---|---|
| Secrets out of git | ✅ | `.env` ignored; CI uses a throwaway password |
| Token never logged | ✅ | URL never in logs/errors; urllib3 debug muted; `__repr__` masked |
| SQL injection | ✅ | Parameterised SQL only; ruff's bandit rules (`S`) enforce it |
| Input validation | ✅ | Parser treats all API data as untrusted |
| DB exposed to the network | 🔴 | W3: bind to 127.0.0.1 |
| Least privilege | 🟡 | The app uses the Postgres **superuser**. Create a `kiakia_app` role with only SELECT/INSERT/UPDATE, and keep the owner role for migrations |
| Personal data (NDPR/NDPA, GDPR) | 🟡 | Message text is stored unencrypted, with no retention policy. Add a retention job ("delete messages older than N months") and a "delete this customer" use case |
| Dependency vulnerabilities | 🟡 | Add **Dependabot** (or `pip-audit` in CI) to get alerts for vulnerable packages |
| CI supply chain | 🟡 | GitHub Actions use version tags (`@v4`); best practice is pinning to a commit SHA |
| Dead-lettered raw JSON (W5) | ⚠️ | When added, it contains customer data, so give it the same retention rules |

## B4. Recommended order of improvements

```mermaid
flowchart LR
    subgraph S1["✅ Sprint 1: quick safety fixes (DONE)"]
        A1["W3 bind DB to localhost"]
        A2["W2 cap retry-after"]
        A3["W1 keep the original error"]
        A4["W6/W7 safe config<br/>(pydantic-settings)"]
        A5["W14 strict update_id"]
    end
    subgraph S2["✅ Sprint 2: architecture polish (DONE)"]
        B1["W8 bootstrap.py"]
        B2["W9 GetStats use case"]
        B3["W10 close connections"]
        B4["W13 RunStatus enum"]
        B5["Migration 0002:<br/>W15 + W16 + dead-letter table (W5)"]
    end
    subgraph S3["Sprint 3: production readiness"]
        C1["W11 advisory lock"]
        C2["W12 Unit of Work"]
        C3["W4 webhooks (week 3)"]
        C4["Least-privilege DB role"]
        C5["Dependabot + coverage"]
    end
    S1 --> S2 --> S3
```

**Bottom line:** the architecture is **sound and follows Clean Architecture correctly**. The
dependency rule is respected, the use cases are fully decoupled, and the reliability design (idempotency,
save-then-confirm) is right. The weak parts are mostly **edges**: error masking, uncapped waits, config
validation, a network-exposed database, and lost rejects. All are fixable without changing the architecture.
