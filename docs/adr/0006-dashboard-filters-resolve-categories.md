# ADR 0006: Dashboard insights, date filters, and marking conversations resolved / by category

- **Status:** Proposed, 2026-11-25 (becomes *Accepted* when `openapi.yaml` is updated, before any UI code uses it)
- **Requested by:** Ify-O (product owner), after reviewing the first UI mockup

## Context
Reviewing the mockup, the product owner asked for things the API contract doesn't offer yet. Our rule
([ADR 0001](0001-contract-first-openapi.md)): **contract first, with a decision record.**

| UI request | Needs from the API |
|---|---|
| Filter by date/time and by platform | `from` / `to` filters (platform filter already exists) |
| Platform icon on every message, incl. a list of *all* messages | `channel` on each `Message`; a cross-conversation message list |
| "Messages received" opens a dashboard **per messaging platform** | Stats broken down per channel |
| Show the **response rate** | A defined response-rate number (and how fast we reply) |
| Mark a conversation **resolved**; label it **enquiry / order / complaint / other** | A write endpoint + new fields |

The UI-only requests (platform icons and unique colours, clearer "received … ago, not answered" wording,
an easy light/dark switch) need **no** contract change.

## Decision

### 1. Date filters
`GET /v1/conversations` and `GET /v1/stats` accept optional `from` and `to` (RFC 3339 date-times,
`from` inclusive, `to` exclusive). Conversations are matched on `lastMessageAt`; stats count messages
by `sentAt`. `from` after `to` gives **400**. The UI offers presets: Today, Yesterday, Last 7 days,
Last 30 days, and Custom.

### 2. Every message knows its platform; list all messages
- `Message` gains `channel`.
- New `GET /v1/messages?channel=&direction=&from=&to=&limit=&cursor=`: all messages, newest first,
  each with the customer's name and its conversation id, for the "messages per platform" dashboard.

### 3. Per-platform stats and response rate
`InboxStats` gains:
- `responseRate` (0–1, `null` if there are no customer conversations in the period): the **share of
  conversations whose latest customer message has been answered or resolved**.
- `medianFirstResponseSeconds` (`null` if there were no replies): the median time from a customer's
  first unanswered message to the business's next reply.
- `byChannel`: per channel, `{ channel, conversations, messages, waiting, responseRate }`.
- `byCategory`: count of conversations per category (including `uncategorized`).

### 4. Resolve and categorise (the second write endpoint)
`PATCH /v1/conversations/{conversationId}` with `{ "resolved": true|false, "category": "enquiry" |
"order" | "complaint" | "other" | null }` (at least one field), which returns the updated conversation.
- `status` gains a third value, **`resolved`**: someone marked it done (e.g. the customer just said
  "thanks"). A resolved conversation **leaves the waiting list**.
- If the customer writes again after it was resolved, it **automatically becomes `waiting` again**, so
  a resolved chat can never hide a new question.
- Conversations gain `category` (nullable) and `resolvedAt` (nullable).
- `GET /v1/conversations` gains `category` filter and `status=resolved`.
- PATCH sets values (it doesn't add or toggle), so repeating it gives the same result: it's
  **naturally idempotent** and needs no Idempotency-Key.
- Storage: migration `0005` adds `conversations.category` and `conversations.resolved_at`.

### Scope check: still "messaging only, no CRM"?
Yes. A single label and a "resolved" tick help the owner see what kind of chats come in and close them
off. There are no pipelines, deals, stages or customer records beyond the conversation. We will
**not** add more fields to conversations without a new ADR.

## Consequences
- ✅ The dashboard can answer "how fast do we reply, and on which platform do we lose customers?"
- ✅ Resolving keeps the waiting list honest, without ever hiding a new customer message.
- ✅ The new query parameters give `GET /v1/stats` real 4xx cases (the Redocly exception for it can go).
- ➖ More contract surface to keep tested (contract tests + Schemathesis cover it automatically).
- ➖ Response-rate definitions must stay stable: changing them later needs a new ADR.
