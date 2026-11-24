import { useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiProblem, unwrap, type Category, type Message } from "../api/client";
import { celebrate, Empty, InlineError, Skeletons, timeLine, useToast } from "../components/common";
import { ConsentBanner } from "../components/ConsentBanner";
import { useNow, usePolling } from "../hooks/usePolling";
import { when } from "../lib/format";
import { CATEGORY_LABEL, KIND_LABEL, PLATFORMS, PlatformIcon, PlatformPill } from "../lib/platforms";

/** One draft per conversation. The Idempotency-Key is created once and reused on retries (ADR 0004). */
export type Draft = { text: string; key: string; locked: boolean };
const MAX = 4096;

export function ConversationPane({
  id,
  drafts,
  onBack,
  onChanged,
}: {
  id: number;
  drafts: Map<number, Draft>;
  onBack: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const now = useNow();
  const detail = usePolling(
    async () => unwrap(await api.GET("/v1/conversations/{conversationId}", { params: { path: { conversationId: id } } })),
    `d${id}`,
  );
  const msgs = usePolling(
    async () =>
      unwrap(await api.GET("/v1/conversations/{conversationId}/messages", {
        params: { path: { conversationId: id }, query: { limit: 50 } },
      })),
    `m${id}`,
  );
  const [older, setOlder] = useState<Message[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null | undefined>(undefined);
  const draft = drafts.get(id) ?? { text: "", key: crypto.randomUUID(), locked: false };
  if (!drafts.has(id)) drafts.set(id, draft);
  const [text, setText] = useState(draft.text);
  const [fieldError, setFieldError] = useState("");
  const [notice, setNotice] = useState<{ kind: "warn" | "bad"; msg: string; offerNewKey?: boolean } | null>(null);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);

  if (detail.error instanceof ApiProblem && detail.error.status === 404) {
    return (
      <Empty icon="🔎">
        This conversation doesn't exist (anymore).
        <br />
        <button className="btn" onClick={onBack}>Back to the inbox</button>
      </Empty>
    );
  }
  if (detail.error !== undefined && !detail.data) return <InlineError onRetry={detail.refresh} />;
  const c = detail.data;
  if (!c) return <Skeletons n={4} />;

  const t = timeLine(c, now);
  const cursor = olderCursor === undefined ? msgs.data?.page.nextCursor : olderCursor;
  // API: newest first; chat: oldest at top. New messages can shift pages, so drop duplicates by id.
  const seen = new Set<number>();
  const shown = [...(msgs.data?.data ?? []), ...older].filter((m) => !seen.has(m.id) && seen.add(m.id)).reverse();

  async function loadOlder() {
    if (!cursor) return;
    const page = unwrap(await api.GET("/v1/conversations/{conversationId}/messages", {
      params: { path: { conversationId: id }, query: { limit: 50, cursor } },
    }));
    setOlder((o) => [...o, ...page.data]);
    setOlderCursor(page.page.nextCursor);
  }

  async function patch(body: { resolved?: boolean; category?: Category | null }, success: string) {
    setSaving(true);
    try {
      const updated = unwrap(await api.PATCH("/v1/conversations/{conversationId}", {
        params: { path: { conversationId: id } }, body,
      }));
      detail.setData(updated);
      toast(success);
      onChanged();
      if (updated.status !== "waiting") await celebrateIfAllDone();
    } catch (err) {
      toast(err instanceof ApiProblem ? `Couldn't save: ${err.message}` : "Couldn't reach Kiakia. Try again.");
    } finally {
      setSaving(false);
    }
  }

  async function celebrateIfAllDone() {
    const waiting = unwrap(await api.GET("/v1/conversations", { params: { query: { status: "waiting", limit: 1 } } }));
    if (waiting.data.length === 0) {
      celebrate();
      toast("🎉 Nobody is waiting. Every customer has a reply!");
    }
  }

  function updateText(v: string) {
    setText(v);
    draft.text = v;
    setFieldError("");
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const trimmed = text.trim();
    // Same rules as the contract (ReplyCreate), so most mistakes are caught before the network.
    if (!trimmed) return setFieldError("must not be empty or only spaces");
    if (trimmed.length > MAX) return setFieldError(`must be at most ${MAX} characters`);
    if (draft.locked) {
      return setNotice({ kind: "warn", offerNewKey: true,
        msg: "This reply may already have been delivered, so Kiakia won't send it again with the same key. Check the chat on your phone first." });
    }
    setSending(true);
    setNotice(null);
    try {
      const result = await api.POST("/v1/conversations/{conversationId}/replies", {
        params: { path: { conversationId: id }, header: { "Idempotency-Key": draft.key } },
        body: { text: trimmed },
      });
      unwrap(result);
      const replayed = result.response.headers.get("Idempotent-Replayed") === "true";
      toast(replayed ? "✓ Already sent before: not sent twice" : `✓ Sent to ${c!.contact.displayName}`);
      drafts.delete(id);
      updateText("");
      void detail.refresh();
      void msgs.refresh();
      onChanged();
      await celebrateIfAllDone();
    } catch (err) {
      if (err instanceof ApiProblem) {
        const fe = err.fieldError("text");
        if (err.status === 422 && fe) setFieldError(fe);
        else if (err.kind === "delivery-unknown") {
          draft.locked = true;
          setNotice({ kind: "warn", offerNewKey: true,
            msg: "⚠ We couldn't confirm it was delivered. Check the chat on your phone before sending again." });
        } else if (err.status === 409) {
          draft.locked = true;
          setNotice({ kind: "warn", offerNewKey: true, msg: `⚠ ${err.message}` });
        } else if (err.status === 502) {
          setNotice({ kind: "bad", msg: `${err.message} Nothing was sent; your draft is kept.` });
        } else {
          setNotice({ kind: "bad", msg: `${err.message} Your draft is kept.` });
        }
      } else {
        // Network problem: we don't know if the request arrived. Retrying with the SAME key is safe.
        setNotice({ kind: "bad", msg: "Couldn't reach Kiakia. Your draft is kept: press Send again (it's safe, it won't be sent twice)." });
      }
    } finally {
      setSending(false);
    }
  }

  function newKey() {
    draft.key = crypto.randomUUID();
    draft.locked = false;
    setNotice(null);
    toast("New reply key created. You can send now.");
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <>
      <div className="chat-head">
        <button className="back" onClick={onBack} aria-label="Back to the list">←</button>
        <PlatformIcon channel={c.channel} size="lg" />
        <div>
          <div className="who">{c.contact.displayName}</div>
          <div className="sub">
            {c.contact.username && <>@{c.contact.username} ·</>} <PlatformPill channel={c.channel} />
          </div>
        </div>
        <div className="actions">
          <select aria-label="Category" disabled={saving} value={c.category ?? ""}
            onChange={(e) => {
              const v = (e.target.value || null) as Category | null;
              void patch({ category: v }, v ? `Labelled as ${CATEGORY_LABEL[v]}` : "Label removed");
            }}>
            <option value="">🏷️ Category…</option>
            {(["enquiry", "order", "complaint", "other"] as const).map((k) => (
              <option key={k} value={k}>{CATEGORY_LABEL[k]}</option>
            ))}
          </select>
          {c.status === "resolved" ? (
            <button className="btn" disabled={saving} onClick={() => void patch({ resolved: false }, "Reopened")}>↺ Reopen</button>
          ) : (
            <button className="btn ok" disabled={saving}
              onClick={() => void patch({ resolved: true }, `✓ ${c.contact.displayName} marked resolved. If they write again, they'll come back to Waiting.`)}>
              ✓ Mark resolved
            </button>
          )}
        </div>
      </div>
      <div className={`statusline ${t.cls}`}>{t.long}</div>
      <ConsentBanner
        key={`${c.contact.id}:${c.contact.detailsConsent}:${c.contact.marketingConsent}`}
        c={c}
        onChanged={() => {
          void detail.refresh();
          void msgs.refresh();
          onChanged();
        }}
      />
      <div className="bubbles">
        {cursor && <button className="more" onClick={() => void loadOlder()}>Load older messages</button>}
        {msgs.error !== undefined && !msgs.data && <InlineError onRetry={msgs.refresh} />}
        {shown.map((m) => (
          <div key={m.id} className={`bubble ${m.direction}`}>
            {m.body}
            {m.hasMedia && (
              <span className="media">
                {m.body && <br />}
                {KIND_LABEL[m.kind] ?? "📎 File"}
              </span>
            )}
            <span className="t">
              <PlatformIcon channel={m.channel} size="sm" /> {when(m.sentAt)}
              {m.direction === "out" && " · ✓ sent"}
            </span>
          </div>
        ))}
      </div>
      {notice && (
        <div className={`notice show ${notice.kind}`} role="alert">
          {notice.msg}
          {notice.offerNewKey && (
            <>
              <br />
              <button type="button" onClick={newKey}>I checked: it didn't arrive. Send as a new message</button>
            </>
          )}
        </div>
      )}
      <form className="composer" onSubmit={(e) => void send(e)} noValidate>
        <label htmlFor="reply" className="hint">
          Reply via {PLATFORMS[c.channel].label} (Enter sends · Shift+Enter = new line)
        </label>
        <textarea id="reply" placeholder="Type a reply…" value={text} maxLength={MAX + 100} disabled={sending}
          aria-invalid={fieldError ? "true" : "false"} aria-describedby="replyErr"
          onChange={(e) => updateText(e.target.value)} onKeyDown={onKey} />
        <div className="line">
          <span className="err" id="replyErr" aria-live="polite">{fieldError && `⚠ text: ${fieldError}`}</span>
          <span className="counter" style={text.length > MAX ? { color: "var(--red)" } : undefined}>{text.length}/{MAX}</span>
        </div>
        <div className="line">
          <span className="hint" title="One Idempotency-Key per draft, reused on retries (ADR 0004)">🔑 {draft.key.slice(0, 8)}…</span>
          <button className="send" type="submit" disabled={sending}>{sending ? "Sending…" : "Send ➤"}</button>
        </div>
      </form>
    </>
  );
}
