/**
 * Platform icons + unique colours (UI_DESIGN.md §8.1). The icons are simple shapes drawn for
 * Kiakia, not the official logos; the platform name is always the accessible label.
 */
import type { JSX } from "react";
import type { Channel } from "../api/client";

export const PLATFORMS: Record<Channel, { label: string; icon: JSX.Element }> = {
  telegram: {
    label: "Telegram",
    icon: (
      <>
        <path d="M21 4 3 11l6 2 2 6 3-4 5 4z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <path d="m9 13 12-9" stroke="currentColor" strokeWidth="2" />
      </>
    ),
  },
  whatsapp: {
    label: "WhatsApp",
    icon: (
      <>
        <path d="M4 20l1.3-4A8 8 0 1 1 8.6 19z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <path d="M9 8.5c0 3 3 6.5 6.5 6.5l1-1.6-2-1-1 1c-1.2-.5-2.4-1.7-2.9-2.9l1-1-1-2z" fill="currentColor" />
      </>
    ),
  },
  instagram: {
    label: "Instagram",
    icon: (
      <>
        <rect x="3.5" y="3.5" width="17" height="17" rx="5" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="17.3" cy="6.7" r="1.3" fill="currentColor" />
      </>
    ),
  },
  messenger: {
    label: "Messenger",
    icon: (
      <>
        <path
          d="M12 3C7 3 3 6.7 3 11.3c0 2.6 1.3 4.9 3.3 6.4V21l3-1.7c.9.3 1.8.4 2.7.4 5 0 9-3.7 9-8.4S17 3 12 3z"
          fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"
        />
        <path d="m7 13.5 3.5-3.7 2.2 2.2 4.3-2.5-3.6 3.8-2.2-2.2z" fill="currentColor" />
      </>
    ),
  },
  tiktok: {
    label: "TikTok",
    icon: (
      <>
        <path d="M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <path d="M14 3c.5 2.6 2.3 4.3 5 4.6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      </>
    ),
  },
  email: {
    label: "Email",
    icon: (
      <>
        <rect x="3" y="5.5" width="18" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="m4 7 8 6 8-6" fill="none" stroke="currentColor" strokeWidth="2" />
      </>
    ),
  },
};

export const CHANNELS = Object.keys(PLATFORMS) as Channel[];

export function PlatformIcon({ channel, size = "" }: { channel: Channel; size?: "" | "sm" | "lg" }) {
  const p = PLATFORMS[channel];
  return (
    <span className={`pf ${size} ${channel}`} style={{ background: `var(--${channel})` }} role="img" aria-label={p.label} title={p.label}>
      <svg viewBox="0 0 24 24" aria-hidden="true">{p.icon}</svg>
    </span>
  );
}

export function PlatformPill({ channel }: { channel: Channel }) {
  return (
    <span className="pill" style={{ ["--c" as string]: `var(--${channel})` }}>
      <PlatformIcon channel={channel} size="sm" />
      {PLATFORMS[channel].label}
    </span>
  );
}

export const CATEGORY_LABEL: Record<string, string> = {
  enquiry: "❓ Enquiry",
  order: "🛍️ Order",
  complaint: "⚠️ Complaint",
  other: "💬 Other",
  none: "Uncategorised",
};

export const KIND_LABEL: Record<string, string> = {
  voice: "🎤 Voice note",
  audio: "🎵 Audio",
  image: "📷 Photo",
  video: "🎬 Video",
  document: "📎 File",
  sticker: "🙂 Sticker",
  other: "📎 Attachment",
};

/** "[voice]" previews from the API become "🎤 Voice note". */
export function previewText(preview: string | null): string {
  if (!preview) return "";
  const m = /^\[(\w+)\]$/.exec(preview);
  return m ? (KIND_LABEL[m[1] ?? ""] ?? preview) : preview;
}
