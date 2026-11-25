/**
 * 🔐 What a customer can ask us to do with their data (docs/adr/0009):
 * stop offers, withdraw their details, or forget them completely.
 * Each action explains what will happen and asks for a confirmation first.
 */
import { useState, type FormEvent } from "react";
import { api, ApiProblem, ensureOk, unwrap, type CustomerDetail } from "../api/client";
import { useToast } from "./common";

type Action = "offers" | "details" | "forget";

const EXPLAIN: Record<Action, string> = {
  offers: "They will stop getting offers and won't be in any ads export. Their saved details stay.",
  details:
    "Erases the name they gave, their phone and email, and also stops offers (offers need contact details). " +
    "The chat itself stays, so you can still reply to them.",
  forget:
    "Deletes this customer, all their chats and messages, and their consent history from Kiakia. " +
    "This can't be undone. (The chat still exists on their own app, e.g. Telegram.)",
};

export function CustomerRights({
  c,
  onChanged,
  onForgotten,
}: {
  c: CustomerDetail;
  onChanged: () => void;
  onForgotten: () => void;
}) {
  const toast = useToast();
  const [pending, setPending] = useState<Action | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const name = c.fullName ?? c.displayName;
  const customerId = c.id;

  async function run(action: Action) {
    if (action === "forget") {
      ensureOk(await api.DELETE("/v1/customers/{customerId}", { params: { path: { customerId } } }));
      return;
    }
    unwrap(
      await api.POST("/v1/customers/{customerId}/consent", {
        params: { path: { customerId } },
        body: { kind: action === "offers" ? "marketing" : "details", status: "withdrawn" },
      }),
    );
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    if (!pending) return;
    setBusy(true);
    try {
      await run(pending);
      if (pending === "forget") {
        toast(`🗑️ ${name} was forgotten. Only an anonymous note that it happened is kept.`);
        onForgotten();
      } else {
        toast(pending === "offers" ? `📭 ${name} won't get offers any more.` : `🧹 ${name}'s details were erased.`);
        onChanged();
      }
      setPending(null);
      setTyped("");
    } catch (err) {
      toast(err instanceof ApiProblem ? `Couldn't do that: ${err.message}` : "Couldn't reach Kiakia. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const mustType = pending === "forget";
  const canConfirm = !busy && (!mustType || typed.trim().toLowerCase() === name.trim().toLowerCase());

  return (
    <section className="rights" aria-labelledby="rights-title">
      <h3 id="rights-title">🔐 If they change their mind</h3>
      {pending === null ? (
        <div className="rights-actions">
          {c.marketingConsent === "granted" && (
            <button className="btn" onClick={() => setPending("offers")}>📭 Stop offers</button>
          )}
          {c.detailsConsent === "granted" && (
            <button className="btn" onClick={() => setPending("details")}>🧹 Withdraw details</button>
          )}
          <button className="btn bad" onClick={() => setPending("forget")}>🗑️ Forget this customer</button>
        </div>
      ) : (
        <form className="rights-confirm" onSubmit={(e) => void confirm(e)}>
          <p>{EXPLAIN[pending]}</p>
          {mustType && (
            <label className="field">
              Type <b>{name}</b> to confirm
              <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus autoComplete="off" />
            </label>
          )}
          <div className="rights-actions">
            <button type="submit" className={`btn ${pending === "forget" ? "bad" : "ok"}`} disabled={!canConfirm}>
              {busy ? "Working…" : pending === "forget" ? "Forget forever" : "Confirm"}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => { setPending(null); setTyped(""); }}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
