/**
 * "🆕 New customer" banner in the chat (docs/adr/0007). It never blocks replying.
 * Consent comes FROM THE CUSTOMER: we send the question in the chat, then record their answer.
 * The latest customer message is stored by the API as evidence.
 */
import { useRef, useState, type FormEvent } from "react";
import { api, ApiProblem, unwrap, type ConsentRecord, type ConsentStatus, type ConversationDetail } from "../api/client";
import { useToast } from "./common";

export const DETAILS_QUESTION =
  "Hi! 👋 Can we save your contact details (name, phone, email) so we can serve you better? Reply YES or NO.";
export const OFFERS_QUESTION =
  "Would you like to hear about our offers and sales? Reply YES or NO. You can reply STOP anytime to opt out.";

type Kind = "details" | "marketing";

export function ConsentBanner({ c, onChanged }: { c: ConversationDetail; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(false); // "Not now": hide for this visit only, nothing is recorded
  const [form, setForm] = useState(false);
  const [name, setName] = useState(c.contact.displayName);
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  // One Idempotency-Key per question, kept while this banner is shown (ADR 0004). If sending worked but
  // recording failed, pressing "Ask" again reuses the key, so the customer doesn't get the question twice.
  const askKeys = useRef<Partial<Record<Kind, string>>>({});
  const customerId = c.contact.id;
  const first = c.contact.displayName.split(" ")[0] ?? c.contact.displayName;
  const details: ConsentStatus = c.contact.detailsConsent;
  const offers: ConsentStatus = c.contact.marketingConsent;

  // Typed by the contract: details can only be sent with kind "details" + status "granted".
  async function record(body: ConsentRecord) {
    unwrap(await api.POST("/v1/customers/{customerId}/consent", { params: { path: { customerId } }, body }));
  }

  async function ask(kind: Kind) {
    const wording = kind === "details" ? DETAILS_QUESTION : OFFERS_QUESTION;
    const key = (askKeys.current[kind] ??= crypto.randomUUID());
    setBusy(true);
    try {
      // 1) send the question to the customer (idempotent reply), 2) record that we asked, with the wording
      unwrap(await api.POST("/v1/conversations/{conversationId}/replies", {
        params: { path: { conversationId: c.id }, header: { "Idempotency-Key": key } },
        body: { text: wording },
      }));
      await record({ kind, status: "asked", conversationId: c.id, wording });
      delete askKeys.current[kind]; // the whole ask worked: a later ask is a new question
      toast(`Question sent to ${first}. Record their answer when they reply.`);
      onChanged();
    } catch (err) {
      toast(err instanceof ApiProblem ? `Couldn't ask: ${err.message}` : "Couldn't reach Kiakia. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function answer(kind: Kind, status: "granted" | "declined") {
    setBusy(true);
    try {
      await record({ kind, status, conversationId: c.id });
      toast(status === "granted" ? "✅ Recorded: they said YES" : "🚫 Recorded: they said NO. We won't ask again.");
      onChanged();
    } catch (err) {
      toast(err instanceof ApiProblem ? `Couldn't save: ${err.message}` : "Couldn't reach Kiakia. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function saveDetails(e: FormEvent) {
    e.preventDefault();
    const shared: { name?: string; phone?: string; email?: string } = {};
    if (name.trim() && name.trim() !== c.contact.displayName) shared.name = name.trim();
    if (phone.trim()) shared.phone = phone.trim();
    if (email.trim()) shared.email = email.trim();
    setBusy(true);
    setErrors({});
    try {
      await record(
        Object.keys(shared).length
          ? { kind: "details", status: "granted", conversationId: c.id, details: shared }
          : { kind: "details", status: "granted", conversationId: c.id },
      );
      toast(`✅ ${name.trim() || first}'s details saved, with their YES as proof.`);
      setForm(false);
      onChanged();
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 422) {
        const byField: Record<string, string> = {};
        for (const fe of err.problem.errors ?? []) byField[fe.field] = fe.message;
        setErrors(byField);
      } else {
        toast(err instanceof ApiProblem ? `Couldn't save: ${err.message}` : "Couldn't reach Kiakia. Try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (hidden) return null;

  // ---- "Save my details" ----
  if (details === "unknown") {
    return (
      <div className="consent new" role="region" aria-label="New customer">
        <span className="grow">🆕 <b>New customer</b>: {first} isn't saved yet. Ask before saving any details.</span>
        <button className="btn primary" disabled={busy} onClick={() => void ask("details")}>💬 Ask to save details</button>
        <button className="btn" onClick={() => setHidden(true)}>Not now</button>
      </div>
    );
  }
  if (details === "asked" && !form) {
    return (
      <div className="consent new" role="region" aria-label="Waiting for the customer's answer">
        <span className="grow">⏳ You asked {first} to save their details. When they reply:</span>
        <button className="btn ok" disabled={busy} onClick={() => setForm(true)}>✅ They said YES</button>
        <button className="btn bad" disabled={busy} onClick={() => void answer("details", "declined")}>🚫 They said NO</button>
      </div>
    );
  }
  if (form) {
    return (
      <div className="consent new" role="region" aria-label="Save customer details">
        <form onSubmit={(e) => void saveDetails(e)} noValidate>
          <span className="grow" style={{ width: "100%" }}>✅ {first} said YES. Save only what they shared:</span>
          <label className="field">Name<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            {errors.name && <small>{errors.name}</small>}</label>
          <label className="field">Phone<input value={phone} inputMode="tel" placeholder="+234 803 123 4567" maxLength={20}
            onChange={(e) => setPhone(e.target.value)} />{errors.phone && <small>{errors.phone}</small>}</label>
          <label className="field">Email<input value={email} inputMode="email" placeholder="ada@example.com" maxLength={254}
            onChange={(e) => setEmail(e.target.value)} />{errors.email && <small>{errors.email}</small>}</label>
          <button className="btn primary" type="submit" disabled={busy}>Save</button>
          <button className="btn" type="button" onClick={() => setForm(false)}>Cancel</button>
        </form>
      </div>
    );
  }
  if (details === "declined" || details === "withdrawn") {
    return (
      <div className="consent no" role="status">
        🚫 {first} {details === "declined" ? "said NO to saving their details" : "withdrew consent: details erased"}. We won't ask again.
      </div>
    );
  }

  // ---- details granted: now the separate "offers & sales" question ----
  return (
    <div className="consent ok" role="region" aria-label="Customer saved">
      <span className="grow">✅ Saved customer.</span>
      {offers === "unknown" && (
        <button className="btn" disabled={busy} onClick={() => void ask("marketing")}>📣 Ask about offers & sales</button>
      )}
      {offers === "asked" && (
        <>
          <span>📣 Offers question sent. Their answer:</span>
          <button className="btn ok" disabled={busy} onClick={() => void answer("marketing", "granted")}>✅ YES</button>
          <button className="btn bad" disabled={busy} onClick={() => void answer("marketing", "declined")}>🚫 NO</button>
        </>
      )}
      {offers === "granted" && <span>📣 Gets offers (they can reply STOP anytime)</span>}
      {(offers === "declined" || offers === "withdrawn") && (
        <span className="no">🚫 No offers{offers === "withdrawn" ? " (replied STOP)" : ""}</span>
      )}
    </div>
  );
}
