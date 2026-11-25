# Architecture Decision Records (ADRs)

An ADR records **one important decision**: the context, what we chose, and the consequences.
They're never deleted. If a decision changes, a new ADR *supersedes* the old one.

Rule (Assignment 2): **if the UI needs something the API contract doesn't have, the contract is
changed first, and an ADR explains why.**

| # | Decision | Status |
|---|---|---|
| [0001](0001-contract-first-openapi.md) | Contract-first API: `openapi.yaml` is the source of truth | Accepted |
| [0002](0002-problem-details-errors.md) | Errors use RFC 9457 Problem Details | Accepted |
| [0003](0003-cursor-pagination.md) | Cursor-based pagination for lists | Accepted |
| [0004](0004-reply-write-endpoint-idempotency.md) | The write endpoint is "send a reply", made idempotent with `Idempotency-Key` | Accepted |
| [0005](0005-fastapi-and-react.md) | FastAPI for the API; React + Vite + TypeScript for the UI, typed from the contract | Accepted |
| [0006](0006-dashboard-filters-resolve-categories.md) | Date filters, per-platform stats + response rate, all-messages list, resolve + categories | Accepted |
| [0007](0007-customer-list-with-recorded-consent.md) | A light customer list, with consent asked in the chat and recorded (NDPA, platform rules) | Accepted |
| [0008](0008-demo-mode.md) | Demo mode: own database, fake customers, offline delivery, clearly labelled | Accepted |

Template: Status · Context · Decision · Consequences.
