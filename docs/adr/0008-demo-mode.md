# ADR 0008: A demo mode with its own database and offline delivery

- **Status:** Accepted, 2026-11-25

## Context
Kiakia will be demonstrated live (Saturday class). The real database only has a few Telegram test
messages, so most screens (platforms, response rate, categories, consent) would look empty. A live
demo also depends on the classroom Wi-Fi and on Telegram being reachable.

## Decision
- **Demo mode** is switched on with `KIAKIA_DEMO=1` and **only runs on a database whose name ends in
  `demo`** (e.g. `kiakia_demo`). Starting demo mode against the real database is a configuration error.
- `python -m kiakia.cli seed-demo` fills the demo database with realistic, **fake** customers on all six
  platforms (waiting, answered, resolved; categories; every consent state). It refuses to run
  outside demo mode.
- In demo mode, replies go to an **offline demo sender** that "delivers" instantly without the internet.
  Nothing is ever sent to a real person. `import` and `watch` are disabled, so real messages can't mix
  in with fake ones.
- **Contract change:** `Health` gains `demoMode` (boolean), so the UI can show a clear
  "🎬 Demo data" label. No one should mistake demo data for real customers.

## Consequences
- ✅ Every screen can be shown with meaningful data, reliably, even offline.
- ✅ The real database and real customers are never touched by the demo.
- ➖ One more setting to know about, documented in the README and `docs/DEMO.md`.
