# ADR 0003: Cursor-based pagination for lists

- **Status:** Accepted, 2026-11-24

## Context
Conversation and message lists change constantly (new messages arrive every minute). With
`?page=3` / `OFFSET`, items shift between requests, so users see duplicates or miss items, and
`OFFSET` gets slower as tables grow.

## Decision
- Lists take `limit` (1–100, default 20) and an opaque `cursor`.
- Responses are `{ "data": [...], "page": { "nextCursor": "…" | null, "limit": 20 } }`.
- The cursor is an **opaque** base64url token (clients must not build or parse it). Internally it
  encodes the sort key of the last item (keyset pagination: `WHERE (sort_key, id) > (…)`).
- An invalid or tampered cursor gives **400** (Problem Details).

## Consequences
- ✅ Stable pages while data changes; constant-time page fetches with an index.
- ✅ The encoding can change later without breaking clients (it's opaque).
- ➖ No "jump to page 7" or total page count (not needed for an inbox).
