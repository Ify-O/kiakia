# ChatDesk: Project Plan

> *ChatDesk is a working name; change it any time.*
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

| # | Problem (in my words) | What ChatDesk does about it |
|---|---|---|
| **1** | *"When I'm busy, I miss and forget to reply to customers."* | **Waiting list + alerts.** One list of every customer whose last message has no reply, sorted by how long they've waited. An alert when someone has waited too long. A daily "missed customers" summary. |
| **2** | *"I have a sales rep and I don't know how she's replying or how fast."* | **Staff view.** Each reply records *who* sent it. The owner sees each person's response times, chats handled, and chats left waiting. The owner can read every conversation. |
| **3** | *"Voice notes take too long. I have to find time to listen."* | **Voice notes become text.** Every incoming voice note is transcribed automatically and shown as text under the audio, so you can read it in seconds and search it later. |
| **4** | *"Payment would create a sense of urgency."* | **Payment link in the chat.** Send a Paystack payment link from the conversation in one tap. The chat shows "Paid ✅" automatically when the money arrives. |
| **5** | *"Switching between apps is tiring."* | **One inbox for all channels**: the base that makes 1–4 possible. |

## 4. What makes us different (USP)

Competitors (Simpu, NativeTalk, Chatwoot, respond.io) are built like **customer-support
software for teams**. ChatDesk is built for **the owner-seller**:

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
  replied". Staff tracking (P2) works only for replies sent *through ChatDesk*.
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

**Key design idea:** each platform gets its own **connector** that translates the
platform's format into our single message format. Adding TikTok later means one new
connector, and nothing else changes.

### Tech stack

| Part | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Our choice for the course |
| HTTP client | `requests` (or `httpx`) | Timeouts, retries, rate limits |
| Web API + webhooks | FastAPI | Simple, fast, automatic API docs |
| Database | PostgreSQL 16 in Docker | Required by the course |
| Background jobs | A simple scheduler/worker to start; Redis queue later if needed | Alerts, transcription, daily summary |
| Voice transcription | Whisper (open source, runs locally) to start | Free, local. **Test it on real Pidgin and Yoruba voice notes early.** |
| Payments | Paystack (test mode) | Common in Nigeria, has payment links and webhooks |
| Front end | Mobile-friendly web app (PWA) | Works on any phone; a native app later |
| Tunnel for webhooks | ngrok | Meta can reach your laptop while developing |

## 8. Data model (first version)

| Table | Holds | Key columns |
|---|---|---|
| `businesses` | The business using ChatDesk | name, timezone (e.g. Africa/Lagos) |
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

| Week | Build | Proves |
|---|---|---|
| **1** | **Importer:** call Meta's Graph API (Conversations API) for my own Facebook Page and Instagram, parse the messages, store them in Postgres (Docker) through a repository layer, idempotently. Plus a **"who's waiting" query** on the real data. | Client (timeouts, retries, rate limit), parser, schema, repository, no duplicates. **P1 is already visible.** |
| 2 | **Webhooks:** a FastAPI endpoint receives new messages live (ngrok). Add the WhatsApp Cloud API test number. | Real-time messages, duplicate-safe |
| 3 | **Inbox + replies:** a REST API and a simple web inbox. Reply from ChatDesk, with a reply-window countdown. Staff logins. | P5: one inbox. Replies record who sent them |
| 4 | **Alerts + staff stats:** waiting alerts, daily summary, per-staff response times. | P1 + P2 |
| 5 | **Voice notes:** download the audio, transcribe it, show the text. | P3 |
| 6 | **Payments:** Paystack payment link from a chat, plus a webhook that marks it "Paid ✅". | P4 |
| Later | TikTok, Telegram, email connectors; AI reply suggestions; mobile app | P5 expansion |

### Week 1 in detail

**Before writing any code (my homework):**
1. Make sure the business's Instagram is a **professional account** linked to the **Facebook Page**.
2. Create a **Meta developer account** at developers.facebook.com and create an app (type: Business).
3. In **Graph API Explorer**, get a **Page access token** with message permissions
   (`pages_show_list`, `pages_messaging`, `pages_read_engagement`, `instagram_basic`,
   `instagram_manage_messages`). Check the current permission names in Meta's docs.
4. **Explore by hand first:** call the Page's `conversations` endpoint for Messenger and
   Instagram in Graph API Explorer and look at the real response. Note the fields, the
   date format and the pagination.
5. Put the token in `.env` (never in git).

**Then we build:**
- `docker-compose.yml` + `db/schema.sql`
- `chatdesk/client.py`: Graph API client with timeouts, retries and backoff, respecting Meta's rate-limit headers, and following pagination
- `chatdesk/parser.py`: Meta JSON → our `Message` and `Conversation` models; handles missing fields, dates (UTC) and bad records
- `chatdesk/repository.py`: idempotent upserts keyed on the platform's message ID
- `chatdesk/import_messages.py`: run the import, then print **"customers waiting for a reply"**
- `README.md`: which API and why, how to run it, surprises in the data

**Demo on Saturday:** my real conversations in Postgres, plus the list of customers still waiting for a reply.

## 10. Rules we follow

- Commit before each task, so the agent's work can always be undone.
- Secrets (tokens, passwords) go in `.env`, and `.env` is in `.gitignore` **before** the first commit.
- Explore every API by hand before building against it; don't trust invented endpoints.
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
| Old history doesn't show which staff member replied | Staff stats start from the day replies go through ChatDesk |

**Open questions:**
- Final product name?
- Alert limit: is 15 minutes right? Does it apply only during business hours?
- Should the sales rep see *all* chats, or only those assigned to her?
