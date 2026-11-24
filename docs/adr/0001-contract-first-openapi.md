# ADR 0001: Contract-first API, with `openapi.yaml` as the source of truth

- **Status:** Accepted, 2026-11-24
- **Deciders:** Ify-O (product owner), with Claude Code

## Context
Kiakia's data will be used by our own web inbox, and later by a mobile app and possibly partners.
If the API is defined by whatever the server code happens to return, every consumer has to read
the code, and any change can silently break them.

## Decision
- We write **`openapi.yaml` (OpenAPI 3.1) by hand, before the server code.** It's the *contract*.
- It's linted with **Redocly** (`npx @redocly/cli lint openapi.yaml`) locally and in CI.
- The server must match it: **contract tests** validate every real response against the spec and
  fail CI on any mismatch.
- The UI is built from the contract only: its TypeScript types are generated from `openapi.yaml`.
  The UI agent is given the contract, never the server code.
- API versioning in the path: `/v1/...`. Breaking changes require `/v2`.

## Consequences
- ✅ Frontend and backend can be built in parallel against the same contract.
- ✅ Drift is caught automatically, not by users.
- ➖ Two things to keep in sync (spec and code), which is why the contract tests are mandatory.
- ➖ Any new UI need goes through a contract change + ADR first (a small, deliberate cost).
