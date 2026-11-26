# ADR 0010: Email as the second live channel (IMAP in, SMTP out)

- **Status:** Accepted, 2026-11-25

## Context
On Saturday, people in class should be able to message the business on more than one channel.
Telegram works. Meta's channels can't be opened to everyone in time: Messenger and Instagram only
accept app testers until Meta's app review (weeks), WhatsApp's free test number only replies to a
few pre-registered phones, and all three need a public HTTPS address for webhooks.
Email needs no approval, anyone can write to an address, and it works from a laptop: Kiakia asks the
mailbox for new mail, the same "pull" model as Telegram's `getUpdates`. `email` is already a
channel in the contract (`Channel` enum) and the web app, so **no contract change** is needed.

## Decision
- **A new adapter, nothing else changes:** `kiakia/adapters/email/` implements the existing
  `MessageSource` port (IMAP) and `MessageSender` port (SMTP). Use cases, API and UI are untouched.
- **Use a dedicated mailbox** (e.g. a new Gmail account for the business) with an **app password**,
  never the owner's personal password. Settings: `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD` (a
  `SecretStr`), hosts and ports default to Gmail. Encrypted connections only (IMAPS 993, SMTPS 465,
  certificates verified).
- **Only new mail is imported.** On first connection Kiakia remembers where the mailbox ends and
  brings in only what arrives after that. It never reads old mail. The cursor is
  `UIDVALIDITY:last UID`; if the mailbox is rebuilt (new UIDVALIDITY) it starts from "now" again.
  The mailbox is opened **read-only** and messages are fetched with `BODY.PEEK`: Kiakia never
  marks mail as read, moves or deletes it.
- **One conversation per sender address.** The address is the channel identity (like a Telegram
  user id): it is needed to reply. It is not "details" in the consent sense (ADR 0007) and is not
  added to the offers export unless the customer gives it there.
- **Only people, not machines:** newsletters, mailing lists, auto-replies, bounces and `no-reply`
  senders are ignored (`List-Id`, `List-Unsubscribe`, `Precedence: bulk/list/junk`,
  `Auto-Submitted`, `X-Autoreply`, mailer-daemon/postmaster/no-reply). Mail from our own address
  is ignored. This also prevents reply loops with other robots.
- **Spoofing:** mail that the mailbox provider marked `dmarc=fail` in `Authentication-Results` is
  rejected (kept in `rejected_updates`), because the From address can't be trusted.
- **Untrusted input:** parsed with Python's standard `email` package (modern policy); plain text is
  preferred and HTML is turned into text (never rendered); quoted earlier messages and signatures
  are cut; NUL characters removed; body capped at 4,000 characters; emails over 5 MB are not
  downloaded (headers only, with a note to open them in the mailbox). Attachments are never stored.
- **Message time = the server's arrival time** (`INTERNALDATE`), which the sender can't fake.
- **Replies never go twice:** an error before the message is handed over (can't connect, wrong
  password, address refused) → "not sent" and a retry is safe; an error while handing it over →
  `DeliveryUnknown`, the Idempotency-Key stays locked (ADR 0004). The recipient address is
  checked again before sending, so a header can never be injected.
- **Live:** `kiakia watch --channel email` checks the mailbox every 10 seconds over one kept-open
  connection; `scripts/start.sh` runs it when `EMAIL_ADDRESS` is set.

## Consequences
- ✅ Anyone can reach the business by email on Saturday; replies are sent from the inbox.
- ✅ Shows the architecture working: a whole new channel is one adapter plus wiring.
- ➖ Up to ~10 s delay (polling, not IMAP IDLE: Python 3.12's `imaplib` has no IDLE).
- ➖ Replies start a new email thread for the customer (subject "Message from …"), because the
  sender port only knows the address. Threading (`In-Reply-To`) needs the last message id passed
  to the sender: a small port change for later.
- ➖ Gmail needs 2-Step Verification on the account to create an app password.
