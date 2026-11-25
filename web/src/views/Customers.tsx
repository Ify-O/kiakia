/** 👥 Customers: the light customer list with recorded consent (docs/adr/0007). */
import { useState } from "react";
import { api, unwrap, type Channel, type ConsentStatus, type Customer } from "../api/client";
import { Empty, InlineError, Skeletons } from "../components/common";
import { useDebounced, useNow, usePolling } from "../hooks/usePolling";
import { ago, plural, when } from "../lib/format";
import { CHANNELS, PLATFORMS, PlatformIcon } from "../lib/platforms";

const CONSENT_LABEL: Record<ConsentStatus, string> = {
  unknown: "🆕 not asked yet",
  asked: "⏳ asked",
  granted: "✅ yes",
  declined: "🚫 said no",
  withdrawn: "🚫 withdrew",
};

export function ConsentBadge({ label, status }: { label: string; status: ConsentStatus }) {
  return <span className={`cbadge ${status}`}>{label}: {CONSENT_LABEL[status]}</span>;
}

export function Customers({ onOpenChat }: { onOpenChat: (conversationId: number) => void }) {
  const now = useNow(30_000);
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [details, setDetails] = useState<ConsentStatus | "">("");
  const [offers, setOffers] = useState<ConsentStatus | "">("");
  const [channel, setChannel] = useState<Channel | "">("");
  const [selected, setSelected] = useState<number | null>(null);

  const key = JSON.stringify({ q, details, offers, channel });
  const list = usePolling(async () => {
    const query = {
      limit: 50,
      ...(q ? { q } : {}),
      ...(details ? { detailsConsent: details } : {}),
      ...(offers ? { marketingConsent: offers } : {}),
      ...(channel ? { channel } : {}),
    };
    return unwrap(await api.GET("/v1/customers", { params: { query } }));
  }, key);
  const rows: Customer[] = list.data?.data ?? [];

  return (
    <div className={`panes ${selected ? "show-chat" : ""}`}>
      <section className="card list" aria-label="Customers">
        <div className="filters">
          <div className="frow">
            <input type="search" placeholder="Search name, @username, phone or email" aria-label="Search customers"
              maxLength={100} value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="frow" aria-label="Filter by platform">
            <span className="hint">Platform:</span>
            <button className="chipbtn all" aria-pressed={channel === ""} onClick={() => setChannel("")}>All</button>
            {CHANNELS.map((ch) => (
              <button key={ch} className="chipbtn" style={{ ["--c" as string]: `var(--${ch})` }}
                aria-pressed={channel === "" || channel === ch} onClick={() => setChannel(channel === ch ? "" : ch)}>
                <PlatformIcon channel={ch} />
              </button>
            ))}
          </div>
          <div className="frow">
            <select aria-label="Details consent" value={details} onChange={(e) => setDetails(e.target.value as ConsentStatus | "")}>
              <option value="">💾 Saved details: any</option>
              <option value="granted">✅ Saved (said yes)</option>
              <option value="unknown">🆕 Not asked yet</option>
              <option value="asked">⏳ Asked, no answer</option>
              <option value="declined">🚫 Said no</option>
              <option value="withdrawn">🚫 Withdrew</option>
            </select>
            <select aria-label="Offers consent" value={offers} onChange={(e) => setOffers(e.target.value as ConsentStatus | "")}>
              <option value="">📣 Offers: any</option>
              <option value="granted">✅ Wants offers</option>
              <option value="unknown">Not asked</option>
              <option value="declined">🚫 No offers</option>
              <option value="withdrawn">🚫 Replied STOP</option>
            </select>
          </div>
        </div>
        <div className="rows">
          {list.error !== undefined && !list.data ? (
            <InlineError onRetry={list.refresh} />
          ) : !list.data ? (
            <Skeletons n={5} />
          ) : rows.length === 0 ? (
            <Empty icon="👥">No customers match.</Empty>
          ) : (
            rows.map((c) => (
              <button key={c.id} className="crow" aria-current={c.id === selected ? "true" : "false"} onClick={() => setSelected(c.id)}>
                <span style={{ minWidth: 0 }}>
                  <span className="name">
                    {c.displayName}{" "}
                    <span className="icons">{c.channels.map((x) => <PlatformIcon key={x.channel} channel={x.channel} size="sm" />)}</span>
                  </span>
                  <span className="preview" style={{ display: "block" }}>
                    {[c.phone, c.email].filter(Boolean).join(" · ") || `First came from ${PLATFORMS[c.firstChannel].label}`}
                  </span>
                  <span style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 3 }}>
                    <ConsentBadge label="Details" status={c.detailsConsent} />
                    <ConsentBadge label="Offers" status={c.marketingConsent} />
                  </span>
                </span>
                <span className="side">
                  {ago(c.lastContactAt, now)}
                  <br />
                  {c.conversationCount} {plural(c.conversationCount, "chat")} · {c.orderCount} {plural(c.orderCount, "order")}
                </span>
              </button>
            ))
          )}
        </div>
      </section>
      <section className="card chat" aria-label="Customer">
        {selected ? (
          <CustomerPanel key={selected} id={selected} onBack={() => setSelected(null)} onOpenChat={onOpenChat} />
        ) : (
          <Empty icon="👥"><b>Pick a customer</b><br />See their platforms, details and consent history.</Empty>
        )}
      </section>
    </div>
  );
}

const EVENT_TEXT: Record<string, string> = {
  "details:asked": "💬 Asked to save their details",
  "details:granted": "✅ Said YES to saving their details",
  "details:declined": "🚫 Said NO to saving their details",
  "details:withdrawn": "🚫 Withdrew details consent (details erased)",
  "marketing:asked": "📣 Asked about offers & sales",
  "marketing:granted": "✅ Said YES to offers",
  "marketing:declined": "🚫 Said NO to offers",
  "marketing:withdrawn": "🚫 Opted out of offers (e.g. replied STOP)",
};

function CustomerPanel({ id, onBack, onOpenChat }: { id: number; onBack: () => void; onOpenChat: (id: number) => void }) {
  const d = usePolling(
    async () => unwrap(await api.GET("/v1/customers/{customerId}", { params: { path: { customerId: id } } })),
    `c${id}`,
  );
  if (d.error !== undefined && !d.data) return <InlineError onRetry={d.refresh} />;
  const c = d.data;
  if (!c) return <Skeletons n={4} />;
  return (
    <>
      <div className="chat-head">
        <button className="back" onClick={onBack} aria-label="Back to customers">←</button>
        <PlatformIcon channel={c.firstChannel} size="lg" />
        <div>
          <div className="who">{c.displayName}</div>
          <div className="sub">First came from {PLATFORMS[c.firstChannel].label} · {when(c.firstContactAt)}</div>
        </div>
        <div className="actions">
          <ConsentBadge label="Details" status={c.detailsConsent} />
          <ConsentBadge label="Offers" status={c.marketingConsent} />
        </div>
      </div>
      <div className="bubbles" style={{ background: "var(--surface)" }}>
        <dl className="kv">
          <dt>Phone</dt><dd>{c.phone ?? <span className="hint">not saved</span>}</dd>
          <dt>Email</dt><dd>{c.email ?? <span className="hint">not saved</span>}</dd>
          <dt>Platforms</dt>
          <dd>{c.channels.map((x) => (
            <span key={x.channel} style={{ marginRight: 8 }}><PlatformIcon channel={x.channel} size="sm" /> {x.username ? `@${x.username}` : PLATFORMS[x.channel].label}</span>
          ))}</dd>
          <dt>Chats</dt>
          <dd>{c.conversations.map((cv) => (
            <button key={cv.id} className="btn" style={{ marginRight: 6, marginBottom: 4 }} onClick={() => onOpenChat(cv.id)}>
              <PlatformIcon channel={cv.channel} size="sm" /> {cv.status} · {when(cv.lastMessageAt)} →
            </button>
          ))}</dd>
          <dt>Orders</dt><dd>{c.orderCount}</dd>
        </dl>
        <h3 style={{ margin: "4px 14px 8px", fontSize: 15 }}>🧾 Consent history (proof)</h3>
        {c.consentHistory.length === 0 ? (
          <p className="hint" style={{ padding: "0 14px" }}>Nothing recorded yet: this customer has never been asked.</p>
        ) : (
          <ul className="timeline">
            {c.consentHistory.map((e) => (
              <li key={e.id}>
                <b>{EVENT_TEXT[`${e.kind}:${e.status}`]}</b>
                <br />
                <span className="hint">
                  {when(e.recordedAt)}{e.channel ? ` · ${PLATFORMS[e.channel].label}` : ""}
                  {e.evidenceMessageId ? " · their message kept as proof" : ""}
                </span>
                {e.wording && <div className="hint" style={{ fontStyle: "italic" }}>“{e.wording}”</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
