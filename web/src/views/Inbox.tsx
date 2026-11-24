import { useRef, useState } from "react";
import { api, unwrap, type Channel, type ConversationStatus } from "../api/client";
import { ConversationRow, Empty, InlineError, Skeletons } from "../components/common";
import { useDebounced, useNow, usePolling } from "../hooks/usePolling";
import { presetRange, type Preset } from "../lib/format";
import { CHANNELS, PlatformIcon } from "../lib/platforms";
import { ConversationPane, type Draft } from "./ConversationPane";

type Tab = ConversationStatus | "all";
type CategoryFilter = "" | "enquiry" | "order" | "complaint" | "other" | "none";
const TABS: [Tab, string][] = [["waiting", "⏳ Waiting"], ["answered", "↩ Answered"], ["resolved", "✓ Resolved"], ["all", "All"]];
const PAGE = 20;

export function Inbox({ selected, onSelect }: { selected: number | null; onSelect: (id: number | null) => void }) {
  const now = useNow();
  const [tab, setTab] = useState<Tab>("waiting");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [channel, setChannel] = useState<Channel | "">("");
  const [preset, setPreset] = useState<Preset>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("");
  const [pages, setPages] = useState(1);
  const drafts = useRef(new Map<number, Draft>()); // one draft + Idempotency-Key per conversation

  const filters = JSON.stringify({ tab, q, channel, preset, customFrom, customTo, category, pages });
  // The first `pages` pages are re-polled together (up to the API's max of 100), so the list stays live.
  const list = usePolling(async () => {
    const query = {
      status: tab,
      sort: tab === "waiting" ? ("longestWaiting" as const) : ("recent" as const),
      limit: Math.min(100, PAGE * pages),
      ...(q ? { q } : {}),
      ...(channel ? { channel } : {}),
      ...(category ? { category } : {}),
      ...presetRange(preset, customFrom, customTo),
    };
    return unwrap(await api.GET("/v1/conversations", { params: { query } }));
  }, filters);
  const counts = usePolling(
    async () => unwrap(await api.GET("/v1/stats", { params: { query: {} } })).waiting,
    "counts",
  );

  const filtersActive = Boolean(q || channel || category || preset !== "all");
  const clearFilters = () => {
    setSearch("");
    setChannel("");
    setCategory("");
    setPreset("all");
    setCustomFrom("");
    setCustomTo("");
  };
  const rows = list.data?.data ?? [];

  return (
    <div className={`panes ${selected ? "show-chat" : ""}`}>
      <section className="card list" aria-label="Conversations">
        <div className="tabs" role="tablist">
          {TABS.map(([t, label]) => (
            <button key={t} aria-pressed={tab === t} onClick={() => { setTab(t); setPages(1); }}>
              {label} {t === "waiting" && counts.data !== undefined && <span>{counts.data}</span>}
            </button>
          ))}
        </div>
        <div className="filters">
          <div className="frow">
            <input type="search" placeholder="Search name or @username" aria-label="Search" maxLength={100}
              value={search} onChange={(e) => setSearch(e.target.value)} />
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
            <select aria-label="Date" value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
              <option value="all">📅 Any time</option>
              <option value="today">Today</option>
              <option value="yesterday">Yesterday</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="custom">Custom…</option>
            </select>
            {preset === "custom" && (
              <span>
                <input type="date" aria-label="From" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
                {" – "}
                <input type="date" aria-label="To" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
              </span>
            )}
            <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value as CategoryFilter)}>
              <option value="">🏷️ Any category</option>
              <option value="enquiry">Enquiry</option>
              <option value="order">Order</option>
              <option value="complaint">Complaint</option>
              <option value="other">Other</option>
              <option value="none">Uncategorised</option>
            </select>
          </div>
        </div>
        <div className="rows">
          {list.error !== undefined && !list.data ? (
            <InlineError onRetry={list.refresh} />
          ) : list.loading && !list.data ? (
            <Skeletons n={5} />
          ) : rows.length === 0 ? (
            filtersActive ? (
              <Empty icon="🔍">
                No chats match these filters.
                <br />
                <button className="btn" style={{ marginTop: 8 }} onClick={clearFilters}>Clear all filters</button>
              </Empty>
            ) : tab === "waiting" ? (
              <Empty icon="🎉"><b>Nobody is waiting.</b><br />Every customer has a reply.</Empty>
            ) : (
              <Empty icon="📭">No conversations here yet.</Empty>
            )
          ) : (
            <>
              {rows.map((c) => (
                <ConversationRow key={c.id} c={c} now={now} selected={c.id === selected} onOpen={onSelect} />
              ))}
              {list.data?.page.nextCursor && PAGE * pages < 100 && (
                <button className="more" onClick={() => setPages(pages + 1)}>Load more</button>
              )}
            </>
          )}
        </div>
      </section>

      <section className="card chat" aria-label="Conversation">
        {selected ? (
          <ConversationPane
            key={selected}
            id={selected}
            drafts={drafts.current}
            onBack={() => onSelect(null)}
            onChanged={() => { void list.refresh(); void counts.refresh(); }}
          />
        ) : (
          <Empty icon="💬"><b>Pick a conversation</b><br />Start with the longest-waiting customer at the top.</Empty>
        )}
      </section>
    </div>
  );
}
