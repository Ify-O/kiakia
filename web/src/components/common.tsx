import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Conversation } from "../api/client";
import { ago, duration, heat, since, when, type Heat } from "../lib/format";
import { CATEGORY_LABEL, PlatformIcon, previewText } from "../lib/platforms";

/* ---------- Toasts ---------- */

const ToastContext = createContext<(msg: string) => void>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const [show, setShow] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const toast = useCallback((m: string) => {
    setMsg(m);
    setShow(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(false), 4200);
  }, []);
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className={`toast ${show ? "show" : ""}`} role="status" aria-live="polite">
        {msg}
      </div>
    </ToastContext.Provider>
  );
}

/* ---------- Theme: Auto -> Light -> Dark, remembered on this device ---------- */

type Theme = "auto" | "light" | "dark";
const THEME_LABEL: Record<Theme, string> = { auto: "🌗 Auto", light: "☀️ Light", dark: "🌙 Dark" };

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("kiakia-theme");
    return t === "light" || t === "dark" ? t : "auto";
  } catch {
    return "auto";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("kiakia-theme", theme);
    } catch {
      /* private mode: the choice just isn't remembered */
    }
  }, [theme]);
  const next: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };
  return (
    <button className="theme" onClick={() => setTheme(next[theme])} title="Switch light / dark mode">
      {THEME_LABEL[theme]}
    </button>
  );
}

/* ---------- Clear time wording for a conversation (UI_DESIGN.md §8.2) ---------- */

export function timeLine(c: Conversation, now: number): { cls: Heat | "done"; text: string; long: string } {
  if (c.status === "waiting" && c.waitingSince) {
    const ms = since(c.waitingSince, now);
    return {
      cls: heat(ms),
      text: `📩 Received ${ago(c.waitingSince, now)} · no reply yet`,
      long: `Waiting ${duration(ms)} for a reply: the first unanswered message arrived ${when(c.waitingSince)}.`,
    };
  }
  if (c.status === "resolved" && c.resolvedAt) {
    return {
      cls: "done",
      text: `✓ Resolved ${ago(c.resolvedAt, now)}`,
      long: `Marked resolved ${when(c.resolvedAt)}. If the customer writes again, it comes back to Waiting.`,
    };
  }
  const replied = c.lastReplyAt ?? c.lastMessageAt;
  return { cls: "done", text: `↩ You replied ${ago(replied, now)}`, long: `Answered: your last reply was sent ${when(replied)}.` };
}

export function ConversationRow({
  c,
  now,
  selected,
  onOpen,
}: {
  c: Conversation;
  now: number;
  selected?: boolean;
  onOpen: (id: number) => void;
}) {
  const t = timeLine(c, now);
  return (
    <button className="row" aria-current={selected ? "true" : "false"} title={t.long} onClick={() => onOpen(c.id)}>
      <PlatformIcon channel={c.channel} size="lg" />
      <span style={{ minWidth: 0 }}>
        <span className="name">
          {c.contact.displayName} {c.contact.username && <small>@{c.contact.username}</small>}
        </span>
        <span className="preview" style={{ display: "block" }}>
          {previewText(c.lastMessagePreview)}
        </span>
        <span className={`when ${t.cls}`} style={{ display: "block" }}>
          {t.text}
        </span>
      </span>
      <span className="side">
        {c.unansweredCount > 0 && (
          <>
            <span className="badge">{c.unansweredCount} new</span>
            <br />
          </>
        )}
        {c.category && <span className="catTag">{CATEGORY_LABEL[c.category]}</span>}
      </span>
    </button>
  );
}

export function Skeletons({ n = 4 }: { n?: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skel" aria-hidden="true" />
      ))}
      <span className="sr-only">Loading…</span>
    </>
  );
}

export function InlineError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="inline-error" role="alert">
      ⚠ Couldn't load this. <button onClick={onRetry}>Retry</button>
    </div>
  );
}

export function Empty({ icon, children }: { icon: string; children: ReactNode }) {
  return (
    <div className="empty">
      <div className="big" aria-hidden="true">
        {icon}
      </div>
      {children}
    </div>
  );
}

/** A small celebration when the last waiting customer gets a reply. */
export function celebrate() {
  const box = document.createElement("div");
  box.className = "confetti";
  box.innerHTML = Array.from(
    { length: 24 },
    () =>
      `<span style="left:${Math.random() * 100}%;animation-delay:${Math.random() * 0.6}s">${
        ["🎉", "⚡", "✅", "💚"][Math.floor(Math.random() * 4)]
      }</span>`,
  ).join("");
  document.body.appendChild(box);
  window.setTimeout(() => box.remove(), 2400);
}
