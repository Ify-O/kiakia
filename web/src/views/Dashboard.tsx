import { useState } from "react";
import { api, unwrap, type Channel } from "../api/client";
import { ConversationRow, Empty, InlineError, Skeletons } from "../components/common";
import { usePolling, useNow } from "../hooks/usePolling";
import { duration, percent, plural, presetRange, since, when, type Preset } from "../lib/format";
import { CATEGORY_LABEL, CHANNELS, PlatformPill } from "../lib/platforms";

type Period = Extract<Preset, "today" | "7d" | "30d" | "all">;
const PERIODS: [Period, string][] = [["today", "Today"], ["7d", "Last 7 days"], ["30d", "Last 30 days"], ["all", "All time"]];

export function PeriodSwitch({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div className="seg" role="group" aria-label="Period">
      {PERIODS.map(([p, label]) => (
        <button key={p} aria-pressed={value === p} onClick={() => onChange(p)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function Dashboard({
  onOpen,
  onPlatforms,
  onInbox,
}: {
  onOpen: (id: number) => void;
  onPlatforms: (channel?: Channel) => void;
  onInbox: () => void;
}) {
  const [period, setPeriod] = useState<Period>("7d");
  const now = useNow();
  const stats = usePolling(
    async () => unwrap(await api.GET("/v1/stats", { params: { query: presetRange(period) } })),
    period,
  );
  const waiting = usePolling(
    async () =>
      unwrap(await api.GET("/v1/conversations", { params: { query: { status: "waiting", sort: "longestWaiting", limit: 6 } } })),
    "waiting",
  );

  const s = stats.data;
  const longest = waiting.data?.data[0];
  const waitingNow = s?.waiting ?? 0;
  const maxReceived = Math.max(1, ...(s?.byChannel.map((c) => c.messagesReceived) ?? [0]));
  const rate = s?.responseRate ?? null;

  return (
    <section>
      <div className="period">
        Period: <PeriodSwitch value={period} onChange={setPeriod} />
      </div>

      {stats.error !== undefined && !s ? (
        <InlineError error={stats.error} onRetry={stats.refresh} />
      ) : !s ? (
        <div className="kpis">
          <Skeletons n={5} />
        </div>
      ) : (
        <div className="kpis">
          {/* Traffic light (UI_DESIGN.md §8.3b): solid red while anyone waits, green when nobody does */}
          <button
            className={`card kpi ${waitingNow ? "alert" : "clear"}`}
            onClick={onInbox}
            aria-label={waitingNow ? `${waitingNow} customers waiting for a reply. Open the inbox.` : "Nobody is waiting. All caught up."}
          >
            <span className="sig" aria-hidden="true">{waitingNow ? "🚨" : "✅"}</span>
            <b>{waitingNow}</b>
            <small>
              {waitingNow
                ? `${plural(waitingNow, "customer")} waiting for a reply right now · tap to answer`
                : "nobody waiting · all caught up 🎉"}
            </small>
          </button>
          <div className="card kpi">
            <b>{s.oldestWaitingSince ? duration(since(s.oldestWaitingSince, now)) : "–"}</b>
            <small>
              {s.oldestWaitingSince
                ? `longest wait${longest ? `: ${longest.contact.displayName}` : ""}, since ${when(s.oldestWaitingSince)}`
                : "nobody is waiting 🎉"}
            </small>
          </div>
          <div className="card kpi">
            <span className="ring" style={{ ["--p" as string]: rate === null ? 0 : Math.round(rate * 100) }} aria-hidden="true">
              <i>{rate === null ? "" : rate >= 0.8 ? "👍" : rate >= 0.5 ? "🙂" : "⚠️"}</i>
            </span>
            <b>{percent(rate)}</b>
            <small>response rate: chats answered or resolved</small>
          </div>
          <div className="card kpi">
            <b>{s.medianFirstResponseSeconds === null ? "–" : duration(s.medianFirstResponseSeconds * 1000)}</b>
            <small>typical time to your first reply (median)</small>
          </div>
          <button className="card kpi" onClick={() => onPlatforms()} title="Open the messages-by-platform dashboard">
            <b>{s.messagesReceived}</b>
            <small>messages received · by platform →</small>
          </button>
        </div>
      )}

      <div className="grid2">
        <div className="card box">
          <h3>
            ⏳ Waiting for a reply{" "}
            <button className="link" onClick={onInbox}>
              Open inbox →
            </button>
          </h3>
          {waiting.error !== undefined && !waiting.data ? (
            <InlineError error={waiting.error} onRetry={waiting.refresh} />
          ) : !waiting.data ? (
            <Skeletons n={3} />
          ) : waiting.data.data.length === 0 ? (
            <Empty icon="🎉">
              <b>Nobody is waiting.</b>
              <br />
              Every customer has a reply.
            </Empty>
          ) : (
            waiting.data.data.map((c) => <ConversationRow key={c.id} c={c} now={now} onOpen={onOpen} />)
          )}
        </div>
        <div>
          <div className="card box">
            <h3>
              📡 Messages by platform{" "}
              <button className="link" onClick={() => onPlatforms()}>
                Details →
              </button>
            </h3>
            {s &&
              CHANNELS.map((ch) => {
                const n = s.byChannel.find((c) => c.channel === ch)?.messagesReceived ?? 0;
                return (
                  <button key={ch} className="hbar" onClick={() => onPlatforms(ch)}>
                    <PlatformPill channel={ch} />
                    <span className="track">
                      <span className="fill" style={{ display: "block", width: `${(100 * n) / maxReceived}%`, background: `var(--${ch})` }} />
                    </span>
                    <span>{n}</span>
                  </button>
                );
              })}
          </div>
          <div className="card box" style={{ marginTop: 14 }}>
            <h3>🏷️ What customers write about</h3>
            <div className="cats">
              {s &&
                Object.entries(s.byCategory).map(([k, n]) => (
                  <span key={k} className="cat">
                    {CATEGORY_LABEL[k]} <b>{n}</b>
                  </span>
                ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
