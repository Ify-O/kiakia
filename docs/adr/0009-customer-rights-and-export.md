# ADR 0009: Customer rights (withdraw, forget) and a consented export for ads

- **Status:** Accepted, 2026-11-25
- **Decided by:** Ify-O (product owner)
- **Builds on:** [ADR 0007](0007-customer-list-with-recorded-consent.md) (consent), which listed
  "delete my data" as a next step

## Context
1. **A customer can change their mind.** Under the NDPA 2023 people may withdraw consent at any time,
   object to marketing, and ask for their data to be erased. Staff need a clear way to do this from the
   app, not only through a STOP message.
2. **Two bugs found while checking this:**
   - A name the customer gave us (*"Sade Bello"*) overwrote the platform display name, so withdrawing
     consent **did not erase it**, although ADR 0007 promised it would.
   - Withdrawing *details* consent left *marketing* consent as `granted`: someone we may no longer
     contact stayed on the offers list.
3. **The owner wants to reach customers with ads** (e.g. Google Ads Customer Match, which takes a CSV
   of emails and phone numbers), or work with the list in Excel or Google Sheets.

## Decision

### Withdraw and forget
- **Withdraw details** (existing endpoint, `status: withdrawn`): erases phone, email **and the name
  they gave us**, and **also withdraws marketing** (recorded as its own consent event), because offers
  need contact details.
- **Forget this customer**: new `DELETE /v1/customers/{customerId}` (204). It deletes the contact, their
  platform identities, conversations, messages and consent history in **one transaction**. The web app
  asks the owner to type the customer's name to confirm. It cannot be undone.
- What we keep after a deletion: one row in a new **`privacy_log`** saying *an erasure happened* (date,
  platforms, number of messages), with **no personal data**, so we can show we honoured the request.
- The chat still exists on the customer's platform (e.g. Telegram). Kiakia can't delete it there.

### Export for ads and spreadsheets
New `GET /v1/exports/customers` returns a downloadable file:

| Parameter | Values | Meaning |
|---|---|---|
| `format` | `csv` (default), `xlsx` | CSV opens in Excel, Google Sheets (File → Import) and Google Ads. `xlsx` is a real Excel file. |
| `layout` | `standard` (default), `google-ads` | `google-ads` uses Google's Customer Match columns: `Email, Phone, First Name, Last Name, Country`, with phones in `+234…` form. |

- **Only customers who said YES to offers** (`marketingConsent = granted`) **and** whose details are
  saved are exported. That's the whole point: ads are marketing.
- Every export writes a row to `privacy_log` (when, format, layout, how many rows; no personal data).
- **Spreadsheet formula injection** is blocked: a text cell that starts with `=`, `+`, `-`, `@` is
  prefixed with `'`, so a customer called `=HYPERLINK(…)` can't run a formula in Excel (OWASP advice).
  Phones are normalised to digits first, so `+234…` stays valid for Google.
- **Not now:** a direct Google Sheets or Google Ads connection. It would need Google sign-in (OAuth)
  and API approval; downloading a file and uploading it is enough today.

### Contract and storage (openapi.yaml v1.4.0, migration 0007)
- `CustomerFields` gains `fullName` (string or null): the name the customer gave us, only present
  with details consent. `displayName` is again only the platform name.
- New `DELETE /v1/customers/{customerId}` and `GET /v1/exports/customers`.
- Migration `0007`: `contacts.full_name` (moved out of `display_name` for consented rows), the consent
  CHECK now covers `full_name` too, and a `privacy_log` table.

## Consequences
- ✅ Withdraw, opt-out and erasure all work from the app, and each leaves proof without keeping the data.
- ✅ The export can only contain people who agreed to marketing.
- ➖ Deleting a customer also deletes their conversation history from Kiakia, and the stats change.
  That's what "forget me" means.
- ➖ One new dependency, `openpyxl`, for real Excel files.
- ➖ Not legal advice. Before running ads, check Google's Customer Match policy (it requires consent
  for this use) and the NDPC's guidance.
