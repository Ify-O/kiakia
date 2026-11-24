# Kiakia: Project Plan

> *Kiakia* means "quickly" in Yoruba: every customer, answered fast.
> This is the plan we build from. Update it whenever a decision changes.

## 1. The idea in one sentence

**A simple, phone-friendly inbox where a small business owner sees and answers every
customer message from WhatsApp, Instagram, Facebook (and later TikTok, Telegram and email)
in one place, and is told straight away when a customer is still waiting.**

Messaging only. **No CRM, no marketing broadcasts, no posting.**

## 2. Who it's for

**The owner of a small business that sells through chat** (1–10 people), often in Nigeria
or elsewhere in Africa, who:

- sells mostly through WhatsApp and Instagram DMs,
- has one or two sales reps answering customers,
- runs the business from a phone and is often busy or away.

**First real user: me.** I run a business with a sales rep, and I have every problem below.

## 3. Problems, in priority order

| # | Problem (in my words) | What Kiakia does about it |
|---|---|---|
| **1** | *"When I'm busy, I miss and forget to reply to customers."* | **Waiting list + alerts.** One list of every customer whose last message has no reply, sorted by how long they've waited. An alert when someone has waited too long. A daily "missed customers" summary. |
| **2** | *"I have a sales rep and I don't know how she's replying or how fast."* | **Staff view.** Each reply records *who* sent it. The owner sees each person's response times, chats handled, and chats left waiting. The owner can read every conversation. |
| **3** | *"Voice notes take too long. I have to find time to listen."* | **Voice notes become text.** Every incoming voice note is transcribed automatically and shown as text under the audio, so you can read it in seconds and search it later. |
| **4** | *"Payment would create a sense of urgency."* | **Payment link in the chat.** Send a Paystack payment link from the conversation in one tap. The chat shows "Paid ✅" automatically when the money arrives. |
| **5** | *"Switching between apps is tiring."* | **One inbox for all channels**: the base that makes 1–4 possible. |

## 4. What makes us different (USP)

Competitors (Simpu, NativeTalk, Chatwoot, respond.io) are built like **customer-support
software for teams**. Kiakia is built for **the owner-seller**:

1. **"No customer left waiting"** is the main screen, not a report hidden in a menu.
2. **The owner sees how staff are doing**: who replied, how fast, and who was missed.
3. **Voice notes as text**, because that's how customers here actually message.
4. **Chat → payment** in one tap, to turn conversations into sales while they're hot.
5. **Simple on purpose.** If a feature is something a CRM does, we don't build it.

## 5. Scope

### In scope (MVP)
- Channels: **Facebook Messenger, Instagram DMs, WhatsApp** (all through Meta's official APIs).
- Unified conversation list, read and reply.
- Waiting list, alerts, daily summary (P1).
- Staff accounts (owner and rep roles), per-staff stats (P2).
- Voice note transcription (P3).
- Paystack payment links in test mode (P4).

### Later
- TikTok DMs, Telegram, email (P5 expansion).
- AI suggested replies, including in Pidgin.
- A mobile app (start with a mobile-friendly web app / PWA).

### Never (to stay simple)
- Sales pipelines, deals, lead scoring (CRM).
- Bulk marketing messages and broadcasts.
- Posting content to social media.

## 6. Platform rules that affect the design

| Platform | How messages reach us | Can we fetch old history? | Reply window |
|---|---|---|---|
| Facebook Messenger | Webhooks (live) + Graph API | ✅ Yes, Conversations API | 24h after the customer's last message |
| Instagram DMs | Webhooks (live) + Graph API | ✅ Yes, Conversations API | 24h |
| WhatsApp (Cloud API) | Webhooks only | ❌ No history API | 24h, then approved templates only (paid) |
| TikTok (later) | Business Messaging API through approved partners | Check the docs | 48h; the business can't start the chat |

Consequences:
- **Webhooks need a public URL.** While everything runs locally, use a tunnel (e.g. ngrok).
- **Webhooks can send the same message twice**, so every message is stored with a unique
  key of channel plus the platform's message ID (idempotency).
- **Show a reply-window countdown** so a sale isn't lost because the 24h window closed.
- **Imported history can't tell us WHICH staff member replied.** Meta only says "the Page
  replied". Staff tracking (P2) works only for replies sent *through Kiakia*.
- **My own accounts need no app review.** As the admin of my own Meta app (development
  mode), I can use my own Page, Instagram and WhatsApp test number. Serving other
  businesses later needs Meta app review and business verification.

## 7. Architecture

```
 Customers on WhatsApp / Instagram / Messenger (later: TikTok, Telegram, email)
            │ webhooks (live)            ▲ send reply
            ▼                            │
 ┌───────────────────────────────────────────────────────────────┐
 │  Channel connectors: one per platform                         │
 │    • receive (webhook / API fetch)   • send replies           │
 ├───────────────────────────────────────────────────────────────┤
 │  Parser / normaliser: platform JSON → ONE Message model        │
 ├───────────────────────────────────────────────────────────────┤
 │  Core services                                                │
 │    • Inbox: conversations, contacts, replies                  │
 │    • Waiting tracker + alerts (P1)                            │
 │    • Staff stats (P2)                                         │
 │    • Voice transcription worker (P3)                          │
 │    • Payments: Paystack links + webhook (P4)                  │
 ├───────────────────────────────────────────────────────────────┤
 │  Repository layer: the ONLY code that talks to the database    │
 ├───────────────────────────────────────────────────────────────┤
 │  PostgreSQL (Docker)                                          │
 └───────────────────────────────────────────────────────────────┘
            ▲
            │ REST API
   Web inbox (mobile-friendly) used by the owner and staff
```

**Architecture style: Clean Architecture (Ports and Adapters).** Full rules, diagram and
coupling guide: [docs/ENGINEERING.md](docs/ENGINEERING.md).

**Key design idea:** each platform gets its own **connector** that translates the
platform's format into our single message format. Adding TikTok later means one new
connector, and nothing else changes.

### Tech stack

| Part | Choice | Why |
|---|---|---|
| Language | Python 3.9+ | Our choice for the course |
| HTTP client | `requests` (or `httpx`) | Timeouts, retries, rate limits |
| Web API + webhooks | FastAPI | Simple, fast, automatic API docs |
| Database | PostgreSQL 16 in Docker | Required by the course |
| Schema versioning | **Alembic** migrations | Industry standard for evolving SQL schemas (see docs/ENGINEERING.md §6) |
| Tests | **pytest** (unit + integration against Postgres in Docker) | Automate "it works" (§7) |
| Background jobs | A simple scheduler/worker to start; Redis queue later if needed | Alerts, transcription, daily summary |
| Voice transcription | Whisper (open source, runs locally) to start | Free, local. **Test it on real Pidgin and Yoruba voice notes early.** |
| Payments | Paystack (test mode) | Common in Nigeria, has payment links and webhooks |
| Front end | Mobile-friendly web app (PWA) | Works on any phone; a native app later |
| Tunnel for webhooks | ngrok | Meta can reach your laptop while developing |

## 8. Data model (first version)

| Table | Holds | Key columns |
|---|---|---|
| `businesses` | The business using Kiakia | name, timezone (e.g. Africa/Lagos) |
| `users` | Owner and staff | email (unique), name, role `owner`/`agent` |
| `channel_accounts` | A connected Page, Instagram account or WhatsApp number | platform, external_id, access token (**encrypted, never in git**) |
| `contacts` | A customer | display name |
| `contact_identities` | That customer's ID on each platform | platform + external_id (unique) |
| `conversations` | A thread with one customer on one channel | status, assigned_user, `last_inbound_at`, `last_outbound_at`, **`waiting_since`** |
| `messages` | Every message in and out | platform + external_id (**unique**), direction `in`/`out`, `sent_by_user_id` (who replied), type `text`/`audio`/`image`/..., body, `transcript`, `sent_at` (UTC) |
| `attachments` | Media files | message, type, url/path |
| `payment_links` | Paystack links sent in a chat | conversation, amount (kobo), reference (unique), status `pending`/`paid` |
| `alerts` | Waiting alerts that were sent | conversation, sent_at |
| `import_runs` | One row per import or sync | counts of seen, saved and bad records |

**Definitions (so the numbers mean the same thing everywhere):**
- **Waiting:** the conversation's last message is from the customer and has no reply after it.
- **Response time:** time from a customer message to the first reply after it.
- **Missed:** waited longer than the alert limit (default **15 minutes** in business
  hours; configurable).
- All times are **stored in UTC** and **shown in the business's time zone**.

## 9. Roadmap: one step per course week

Each week builds on the last. Adjust when each week's assignment brief comes out.
**Change (Nov 2026):** Week 1 uses **Telegram** instead of Meta. Meta's setup (developer
account, Page, Instagram link) takes time, while a Telegram bot takes 2 minutes. Because
every channel is a separate adapter, adding Meta later changes nothing else.

| Week | Build | Proves |
|---|---|---|
| **1** | **Telegram importer:** call the Telegram Bot API (`getUpdates`), parse it into our domain model, store it in Postgres (Docker) through a repository, idempotently. Schema managed by **migrations**. Unit and integration **tests**. Plus a **"who's waiting" list**. | Client (timeouts, retries with backoff, rate limit), parser, schema, repository, no duplicates, clean architecture. **P1 is visible.** |
| 2 | **Meta connector:** Facebook Messenger + Instagram history through the Graph API (as a new adapter). | Adding a channel without touching the core |
| 3 | **Live messages + replies:** webhooks (FastAPI + ngrok), send replies (records *who* replied), WhatsApp Cloud API test number. | P5 + the start of P2 |
| 4 | **Inbox UI + alerts + staff stats:** web inbox, waiting alerts, daily summary, per-staff response times. | P1 + P2 |
| 5 | **Voice notes:** download the audio, transcribe it, show the text. | P3 |
| 6 | **Payments:** Paystack payment link from a chat, plus a webhook that marks it "Paid ✅". | P4 |
| Later | TikTok, email; AI reply suggestions; mobile app | P5 expansion |

### Week 1 in detail

**My homework (2 minutes):** in Telegram, message **@BotFather**, send `/newbot`, choose a
name and a username, and copy the **token** into `.env` (never into git or a chat). Then have
a few people message the bot so there's real data.

**Explore by hand first:** open
`https://api.telegram.org/bot<TOKEN>/getUpdates` in the browser and look at the real JSON.

**Build steps (one commit each):**
1. Engineering guidelines + plan update
2. Project skeleton: folders per layer, config, Docker Compose, Alembic migration #1
3. Domain model + ports + Telegram parser, with unit tests (fake fixtures)
4. Resilient HTTP helper + Telegram client, with unit tests (fake HTTP)
5. Postgres repository, with integration tests (no duplicates on re-import)
6. Use cases (ImportMessages, ListWaitingCustomers) + CLI: start with 10 messages, then all
7. README: which API and why, how to run it, surprises in the data

**Telegram facts that shape the design** (check them against the official docs at core.telegram.org/bots/api):
- `getUpdates` only keeps updates for about **24 hours**, and calling it with an `offset`
  **confirms and deletes** older updates. So we save to the DB **first**, then move the offset.
- Each update has a unique `update_id`, and each message a `message_id` that is unique
  **within its chat**. So our unique key is `channel + chat_id + message_id`.
- A 429 response includes `parameters.retry_after` (seconds to wait).
- Dates are **unix timestamps (UTC)**.

**Demo on Saturday:** real Telegram messages in Postgres, re-importing creates no duplicates,
tests pass, and the list of customers still waiting for a reply.

## 10. Rules we follow

- Commit before each task, so the agent's work can always be undone.
- Secrets (tokens, passwords) go in `.env`, and `.env` is in `.gitignore` **before** the first commit.
- Explore every API by hand before building against it; don't trust invented endpoints.
- Follow [docs/ENGINEERING.md](docs/ENGINEERING.md): clean architecture, migrations, tests, security.
- Start small: 10 messages end to end first, then complete and robust.
- Don't keep code I can't explain.
- **Customer messages are private data.** Never commit real exports or screenshots of customer chats.

## 11. Risks and open questions

| Risk | Plan |
|---|---|
| Meta changes its rules or prices (especially WhatsApp) | Keep connectors separate; watch Meta's changelog and pricing pages |
| Whisper may be weak on Pidgin, Yoruba, Igbo or Hausa | Test real voice notes in week 5 before promising it; try other models if needed |
| App review and business verification for other businesses | Not needed while I'm the only user; plan for it before onboarding others |
| Competitors (Simpu, NativeTalk) copy features | Stay focused on the owner-seller and talk to real sellers |
| Old history doesn't show which staff member replied | Staff stats start from the day replies go through Kiakia |

**Open questions:**
- ~~Final product name?~~ Decided: **Kiakia**.
- Alert limit: is 15 minutes right? Does it apply only during business hours?
- Should the sales rep see *all* chats, or only those assigned to her?
