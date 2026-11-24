# ADR 0002: Errors use RFC 9457 Problem Details

- **Status:** Accepted, 2026-11-24

## Context
Clients need one predictable error format to show useful messages (e.g. under a form field) and to
decide whether to retry. Ad-hoc `{"error": "..."}` bodies differ per endpoint and leak internals.

## Decision
Every error response uses **RFC 9457 "Problem Details for HTTP APIs"**, media type
`application/problem+json`:

```json
{
  "type": "https://kiakia.app/problems/validation-error",
  "title": "Your request has invalid fields",
  "status": 422,
  "detail": "1 field is invalid.",
  "instance": "/v1/conversations/42/replies",
  "errors": [{ "field": "text", "message": "must not be empty" }]
}
```

Status codes we use:

| Code | When |
|---|---|
| 400 | Malformed request (bad JSON, bad query value type, invalid cursor) |
| 404 | The resource doesn't exist |
| 409 | `Idempotency-Key` reused with a *different* request body |
| 422 | Well-formed but invalid fields (`errors` lists each field) |
| 502 | The upstream channel (e.g. Telegram) refused or failed |
| 503 | A dependency (database, channel) is temporarily unavailable; may include `Retry-After` |
| 500 | Unexpected bug. Generic message only; details go to logs, never to the client |

## Consequences
- ✅ One error component in the contract; the UI has one error-rendering path.
- ✅ No stack traces, SQL or tokens ever reach clients.
- ➖ Framework defaults (e.g. FastAPI's own 422 format) must be overridden.
