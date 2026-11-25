import { useEffect, useRef, useState } from "react";
import { api, isOutage, unwrap, type Channel } from "./api/client";
import { ThemeToggle, useToast } from "./components/common";
import { usePolling } from "./hooks/usePolling";
import { PLATFORMS } from "./lib/platforms";
import { Customers } from "./views/Customers";
import { Dashboard } from "./views/Dashboard";
import { Inbox } from "./views/Inbox";
import { Platforms } from "./views/Platforms";

type View = "dash" | "inbox" | "platforms" | "customers";

export function App() {
  const [view, setView] = useState<View>("dash");
  const [selected, setSelected] = useState<number | null>(null);
  const [platform, setPlatform] = useState<Channel | null>(null);
  // Health drives the "● Live" dot and the "can't reach Kiakia" banner (an error state from the design).
  const health = usePolling(async () => unwrap(await api.GET("/health")), "health");
  const toast = useToast();

  // 🆕 New-customer alert (ADR 0007): a waiting chat we haven't seen before, from someone never asked.
  const seen = useRef<Set<number> | null>(null);
  const waiting = usePolling(
    async () => unwrap(await api.GET("/v1/conversations", { params: { query: { status: "waiting", limit: 50 } } })),
    "new-customers",
  );
  useEffect(() => {
    const rows = waiting.data?.data;
    if (!rows) return;
    if (seen.current === null) {
      seen.current = new Set(rows.map((c) => c.id)); // first load: don't alert about old chats
      return;
    }
    for (const c of rows) {
      if (seen.current.has(c.id)) continue;
      seen.current.add(c.id);
      if (c.contact.detailsConsent === "unknown") {
        toast(`🆕 New customer: ${c.contact.displayName} (${PLATFORMS[c.channel].label}). Open the chat to welcome them.`);
      }
    }
  }, [waiting.data, toast]);
  const down = health.error !== undefined && isOutage(health.error);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  const secs = health.updatedAt ? Math.round((Date.now() - health.updatedAt) / 1000) : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement).tagName);
      if (e.key === "Escape") setSelected(null);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        setView("inbox");
        window.setTimeout(() => document.querySelector<HTMLInputElement>('input[type="search"]')?.focus(), 0);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const open = (id: number) => {
    setSelected(id);
    setView("inbox");
    window.scrollTo(0, 0);
  };

  return (
    <div className="app">
      <header>
        <div className="logo">Kiakia<span>⚡</span></div>
        <div className="tag">Every customer, answered fast</div>
        <div className="spacer" />
        <div className="live" aria-live="polite">
          <span className={`dot ${down ? "off" : ""}`} />
          <span>{down ? "Offline" : secs === null ? "Connecting…" : `Live · updated ${secs < 2 ? "just now" : `${secs}s ago`}`}</span>
        </div>
        <ThemeToggle />
      </header>
      {health.data?.demoMode && (
        <div className="demo-ribbon" role="note">
          🎬 <b>Demo data</b>: these customers are fake, and replies are not sent to anyone (ADR 0008).
        </div>
      )}
      <nav className="views" aria-label="Views">
        {([["dash", "📊 Dashboard"], ["inbox", "📥 Inbox"], ["customers", "👥 Customers"], ["platforms", "📡 By platform"]] as const).map(([v, label]) => (
          <button key={v} aria-current={view === v ? "page" : "false"} onClick={() => setView(v)}>{label}</button>
        ))}
      </nav>
      {down && (
        <div className="banner show" role="alert">
          ⚠ Can't reach Kiakia. Retrying every 10 s… The last data you saw stays on screen.
          <button onClick={() => void health.refresh()}>Retry now</button>
        </div>
      )}
      {view === "dash" && (
        <Dashboard onOpen={open} onInbox={() => setView("inbox")}
          onPlatforms={(ch) => { setPlatform(ch ?? null); setView("platforms"); }} />
      )}
      {view === "inbox" && <Inbox selected={selected} onSelect={setSelected} />}
      {view === "platforms" && <Platforms channel={platform} onChannel={setPlatform} onOpen={open} />}
      {view === "customers" && <Customers onOpenChat={open} />}
    </div>
  );
}
