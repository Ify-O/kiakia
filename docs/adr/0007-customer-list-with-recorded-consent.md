# ADR 0007: A light customer list, with consent asked in the chat and recorded

- **Status:** Accepted, 2026-11-25
- **Decided by:** Ify-O (product owner)
- **Changes:** PLAN.md "Never: CRM / broadcasts" becomes "light customer list + opt-in offers; still no pipelines"

## Context
Chat sellers want to remember their customers (name, phone, email, which platform they came from)
and, later, tell them about sales. Two hard constraints:

1. **Law: Nigeria Data Protection Act 2023 (+ GAID 2025).** It applies to any use of personal data,
   sold or not. Serving a customer who wrote to us is fine; storing extra details and especially
   **direct marketing need clear, specific consent**, and people can object or withdraw at any time.
2. **Platform rules.** WhatsApp and Messenger only allow marketing messages to people who **opted in**
   (via their own marketing APIs/templates); TikTok doesn't let businesses start chats at all.

## Decision

### What we keep, and why
| Data | Lawful reason | Kept when the customer says "no"? |
|---|---|---|
| The conversation, display name, platform handle/ID | Needed to answer the customer who wrote to us | Yes (needed to reply) |
| Phone, email (+ edited name) | **Consent** ("save my details") | **No: never stored** (and erased if consent is withdrawn) |
| On the offers list | **Separate consent** ("send me offers") | **No** |
| The fact they said "no" | Needed to respect it (don't ask again) | **Yes** |

### The flow (in the chat, never a blocking pop-up)
1. A customer whose details consent is still **unknown** shows a **🆕 New customer** banner in the
   chat, and staff get a toast when a new customer writes.
2. **Ask to save details** sends a fixed question to the customer through the normal (idempotent)
   reply endpoint and records `asked` with the exact wording.
3. Staff record the customer's answer: **They said YES** (then fill in name / phone / email, only what
   was shared) or **They said NO**. The latest customer message is stored as **evidence**.
4. **Offers & sales** is a second, separate question with the same steps. Nothing is pre-ticked.
5. A customer message that is just **STOP** (or "unsubscribe") automatically **withdraws** offers
   consent. Withdrawing details consent **erases** phone and email.
6. Every change is an append-only **consent event** (what, when, channel, wording, evidence), so we
   can always show *how* consent was obtained.

### Contract additions (openapi.yaml v1.2.0)
- `Contact` gains `detailsConsent` and `marketingConsent`.
- `GET /v1/customers` (search, consent filter, cursor pagination), `GET /v1/customers/{id}` (with
  platforms, conversations and consent history), and `POST /v1/customers/{id}/consent` (record
  `asked` / `granted` / `declined` / `withdrawn`; details only allowed with a details `granted`).
  Recording the current status again changes nothing.
- Storage: migration `0006` adds `contacts.phone`, `email`, `details_consent`, `marketing_consent`
  and a `consent_events` table.

### Not now (next steps)
- Sending promotions (WhatsApp templates, Messenger Marketing Messages, email with unsubscribe link):
  only to `marketingConsent = granted`, through each platform's approved route.
- "Show me / delete my data" requests and a retention period for inactive customers.
- Merging the same person across platforms.

## Consequences
- ✅ Consent comes **from the customer, in their own words**, with proof, not from a staff click alone.
- ✅ "No" is respected permanently, and STOP works without staff action.
- ➖ Kiakia is no longer "messaging only": it now holds a small customer list. We hold the line at
  **no pipelines, deals or lead scoring**; anything beyond this needs a new ADR.
- ➖ Not legal advice: check the NDPC's guidance (e.g. registration/audit duties) before marketing.
