# ADR 0005: FastAPI for the API; React + Vite + TypeScript for the UI

- **Status:** Accepted, 2026-11-24

## Context
We need an HTTP API in our existing Python codebase, and a web UI that can only use what the
contract offers.

## Decision
- **API: FastAPI**, as a new *entry point* next to the CLI. It reuses `bootstrap.py` and the same use
  cases, so no business logic lives in route handlers. FastAPI's auto-generated schema is **not** our
  contract: `openapi.yaml` is (ADR 0001), and contract tests keep them aligned.
- **Contract tests:** response validation against `openapi.yaml` in our tests, plus **Schemathesis**
  (property-based tests generated from the spec).
- **UI: React + Vite + TypeScript** in `web/`. API types are generated from `openapi.yaml` with
  `openapi-typescript`, so calling a field the contract doesn't define fails to compile.
- Node version pinned in `.nvmrc` (22 LTS).

## Consequences
- ✅ One language for backend logic, the industry-standard stack for the UI.
- ✅ The type generator enforces "the UI only knows the contract".
- ➖ Two toolchains (uv + npm) in one repo and in CI.
