# ADR 0004: The write endpoint is "send a reply", made idempotent with `Idempotency-Key`

- **Status:** Accepted, 2026-11-24

## Context
Assignment 2 requires one write endpoint with validation. Kiakia's #1 problem is "customers left
waiting", so the most valuable write is **replying to a customer from the inbox**. That reply then
removes the customer from the waiting list.

Sending a message is **not naturally idempotent**: if the network drops after Telegram delivered
the message but before we got the answer, a retry (or a double click) would send it **twice** to the
customer. That's embarrassing for a business.

## Decision
- `POST /v1/conversations/{conversationId}/replies` with `{ "text": "…" }`, which returns **201** and
  the created message.
- Validation: `text` is required, trimmed, 1–4096 characters (Telegram's limit). Failures return **422**
  Problem Details with per-field `errors`.
- **Idempotency:** the client sends an `Idempotency-Key` header (a UUID it generates once per reply).
  - First time: we process the request and store the key, a hash of the request, and our response.
  - Same key + same body again: we **return the stored response** and send nothing again
    (`Idempotent-Replayed: true` header).
  - Same key + *different* body: **409 Conflict**.
  - Keys expire after 24 hours.
  - The header is **required** for this endpoint (a missing key gives 400), because sending a duplicate
    to a customer is never acceptable.
- Outgoing HTTP calls that *send* (Telegram `sendMessage`) are **not retried on timeouts**, because the
  first attempt may already have been delivered. They are retried only when the request provably
  never arrived (connection refused) or on 429.

## Consequences
- ✅ Safe retries for clients, mobile networks and double clicks.
- ✅ Teaches and applies a core industry pattern (Stripe-style idempotency keys).
- ➖ An extra table (`idempotency_keys`, migration `0004`) and a cleanup of expired keys.
