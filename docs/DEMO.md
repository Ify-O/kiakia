# Kiakia: Saturday demo script

About **6 minutes** plus questions. Everything below uses **demo mode** (ADR 0008): fake customers,
and replies are "sent" offline, so it works without Wi-Fi and never messages a real person.

---

## Before class (10 minutes, at home or on arrival)

1. Laptop charged, notifications off, browser zoom ~110% so the back row can read it.
2. Start Docker (Colima): `colima start` (the start script also does this, but it's slow the first time).
3. Start the demo:

   ```bash
   ./scripts/start.sh --demo
   ```

   Wait for **"🎬 Kiakia DEMO is running"**. This **resets** the demo data, so the timers are fresh.
4. Open **http://localhost:5173**. Check you see the striped **🎬 Demo data** ribbon and a red
   **"5 customers waiting"** card.
5. Open a second tab with **http://localhost:8000/docs** (the API, for the "contract" part).
6. Have `docs/ARCHITECTURE.md` open on GitHub (diagrams render there) and `openapi.yaml` in VS Code.

> Restart the demo just before presenting (Ctrl+C, then run it again) so "waiting" times are short
> and realistic. Each restart gives you a clean slate.

---

## The script

### 1. The problem (30 s): no laptop needed

> "In Nigeria, a lot of selling happens in chat: WhatsApp, Instagram, TikTok, Telegram.
> A small business owner has five apps open and **forgets to reply**. A customer who waits an hour
> buys from someone else. Kiakia means *quickly* in Yoruba: one inbox that shows **who is still
> waiting for you**."

### 2. Dashboard (1 min)

- Point to the **red card**: "5 customers are waiting right now. It turns green when nobody is."
- **Longest wait** (Ada Test, about 1 hour): "This is the number that loses sales."
- **Response rate** and **median first reply**: "How fast are we, really?"
- **Messages by platform**: all six platforms in one place. Click **TikTok** to show the drill-down,
  then go back.
- **What customers write about**: enquiries, orders, complaints.

### 3. Inbox: answer the longest-waiting customer (1½ min)

- Click **Open inbox**. The **⏳ Waiting** tab is sorted **longest-waiting first**: "the list tells
  you who to answer next."
- Show a filter: click the **Instagram** icon, then **Clear all filters**.
- Open **Ada Test** and type a reply, e.g. *"Yes, we have it in red! ₦15,000."* Press **Send**.
- Ada **leaves the Waiting list** and the waiting count drops to 4.
- Set the category to **Order** and press **✓ Mark resolved**.

> Talking point (**idempotency**): "If my connection drops and the browser sends this twice, the
> customer still gets **one** message. Every draft carries an `Idempotency-Key`; the server remembers it."

### 4. New customer and consent (1½ min)

- Open **Amaka** (Instagram). A **🆕 New customer** banner appears.
- Click **Ask to save details**. Show the polite question sent in the chat.
- Click **They said YES**. Type a phone number like `call me` → a **friendly error** appears.
  Fix it to `+234 802 555 0123` → **Saved**.
- Then show **Ask about offers & sales**: "a separate question, because saving my number
  isn't the same as agreeing to marketing."
- Go to **👥 Customers**: show the consent badges. Open **Emeka**: details withdrawn, so
  phone and email are gone.

> Talking point (**privacy, NDPA 2023**): "We **can't** store a phone number without a recorded
> YES. That rule is in the database itself, not just the app. Every YES/NO is kept with the exact
> question we asked. Replying STOP opts a customer out of offers automatically."

### 5. How it's built (1½ min)

- **Contract first:** show `openapi.yaml`. "I wrote the API contract **before** the code. Redocly
  lints it, the web app's types are **generated** from it, and tests check the server matches it."
- Show **http://localhost:8000/docs** briefly.
- **Schemathesis:** "a tool that fires thousands of weird requests at the API. It found real bugs:
  a NUL character crashed search, `to=0.5` was accepted as a date. All fixed, all tested."
- **ADRs:** `docs/adr/`. "Every contract change has a short decision record. Demo mode is ADR 0008."
- **Architecture:** show the layers diagram in `docs/ARCHITECTURE.md` (A3): "The core doesn't
  know Telegram or Postgres exist; adding WhatsApp is a new adapter, not a rewrite."
- Numbers: **205 tests**, CI on every PR (lint, types, migrations up/down, API drift check).

### 6. Close (15 s)

> "What's next: WhatsApp and Instagram adapters, voice notes to text, and payment links in the chat."

---

## If something goes wrong (backup plan)

| Problem | Do this |
|---|---|
| Port 8000 or 5173 "already in use" | Close the other terminal running Kiakia, or `lsof -i :8000` to find it, then rerun. |
| Docker/Colima won't start | `colima stop && colima start`, then rerun the script. |
| Page shows "Couldn't load…" | The API stopped: look at the terminal for the error, Ctrl+C, rerun. |
| Wi-Fi is down | Nothing to do: demo mode works fully offline. |
| Everything fails | Show the clickable mockup `docs/ui/mockup.html` (open the file in a browser), the diagrams in `docs/ARCHITECTURE.md`, and the passing CI on GitHub. |
| Data got messy during rehearsal | Ctrl+C and rerun `./scripts/start.sh --demo`: it resets the demo data. |

## Likely questions

- **"Is this real data?"** No: the 🎬 ribbon means demo mode. Real mode (`./scripts/start.sh`) uses the
  live Telegram bot, and the demo can't touch the real database (it only runs on a DB named `…demo`).
- **"Why Telegram first?"** Free, instant bot access, no business verification. Meta (WhatsApp,
  Instagram, Messenger) needs app review; it's the next adapter.
- **"How do messages arrive live?"** `kiakia watch` uses long polling: Telegram holds the request
  open and answers the moment a message arrives. Webhooks come when it runs on a server.
- **"Why not a CRM?"** On purpose: owners want *fast replies*, not another system to fill in.
