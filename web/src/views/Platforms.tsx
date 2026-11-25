import { useEffect, useState } from "react";
import { api, unwrap, type Channel, type FeedItem } from "../api/client";
import { Empty, InlineError, Skeletons } from "../components/common";
import { usePolling } from "../hooks/usePolling";
import { percent, plural, presetRange, when } from "../lib/format";
import { CHANNELS, KIND_LABEL, PLATFORMS, PlatformIcon } from "../lib/platforms";
import { PeriodSwitch } from "./Dashboard";

type Period = "today" | "7d" | "30d" | "all";

export function Platforms({
  channel,
  onChannel,
  onOpen,
}: {
  channel: Channel | null;
  onChannel: (c: Channel | null) => void;
  onOpen: (id: number) => void;
}) {
  const [period, setPeriod] = useState<Period>("7d");
  const [extra, setExtra] = useState<FeedItem[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const range = presetRange(period);
  const key = `${period}:${channel ?? "all"}`;

  const stats = usePolling(async () => unwrap(await api.GET("/v1/stats", { params: { query: range } })), period);
  const feed = usePolling(
    async () =>
      unwrap(await api.GET("/v1/messages", {
        params: { query: { limit: 30, ...range, ...(channel ? { channel } : {}) } },
      })),
    key,
  );
  // "Load more" pages belong to one filter: reset them only when the filter changes, not on every refresh.
  useEffect(() => {
    setExtra([]);
    setCursor(undefined);
  }, [key]);

  const next = cursor === undefined ? feed.data?.page.nextCursor : cursor;
  async function loadMore() {
    if (!next) return;
    const page = unwrap(await api.GET("/v1/messages", {
      params: { query: { limit: 30, cursor: next, ...range, ...(channel ? { channel } : {}) } },
    }));
    setExtra((x) => [...x, ...page.data]);
    setCursor(page.page.nextCursor);
  }
  const seen = new Set<number>();
  const items = [...(feed.data?.data ?? []), ...extra].filter((f) => !seen.has(f.message.id) && seen.add(f.message.id));

  return (
    <section>
      <div className="period">
        Period: <PeriodSwitch value={period} onChange={setPeriod} />
        <span>· Click a platform to filter the messages below.</span>
      </div>
      <div className="pcards">
        {CHANNELS.map((ch) => {
          const s = stats.data?.byChannel.find((c) => c.channel === ch);
          return (
            <button key={ch} className="card pcard" style={{ ["--c" as string]: `var(--${ch})` }}
              aria-pressed={channel === ch} onClick={() => onChannel(channel === ch ? null : ch)}>
              <PlatformIcon channel={ch} size="lg" /> <b>{s?.messagesReceived ?? 0}</b>
              <small>{plural(s?.messagesReceived ?? 0, "message")} received on {PLATFORMS[ch].label}</small>
              <small>
                {s?.conversations ?? 0} {plural(s?.conversations ?? 0, "chat")} · {s?.waiting ?? 0} waiting · {percent(s?.responseRate)} answered
              </small>
            </button>
          );
        })}
      </div>
      <div className="card">
        <div className="box">
          <h3>
            {channel ? (
              <>
                <PlatformIcon channel={channel} /> {PLATFORMS[channel].label} messages{" "}
                <button className="link" onClick={() => onChannel(null)}>Show all platforms</button>
              </>
            ) : (
              "All messages, newest first"
            )}
          </h3>
        </div>
        {feed.error !== undefined && !feed.data ? (
          <InlineError onRetry={feed.refresh} />
        ) : !feed.data ? (
          <Skeletons n={5} />
        ) : items.length === 0 ? (
          <Empty icon="📭">No messages in this period.</Empty>
        ) : (
          <>
            {items.map(({ message: m, contact }) => (
              <button key={m.id} className="mrow" onClick={() => onOpen(m.conversationId)}>
                <PlatformIcon channel={m.channel} size="lg" />
                <span style={{ minWidth: 0 }}>
                  <b>{contact.displayName}</b> <span className="dir">{m.direction === "in" ? "→ to you" : "← your reply"}</span>
                  <span className="preview" style={{ display: "block" }}>{m.body ?? KIND_LABEL[m.kind] ?? "📎 File"}</span>
                </span>
                <span className="side">{when(m.sentAt)}</span>
              </button>
            ))}
            {next && <button className="more" onClick={() => void loadMore()}>Load more</button>}
          </>
        )}
      </div>
    </section>
  );
}
