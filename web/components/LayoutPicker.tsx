"use client";

import type { Clip } from "@/lib/api";
import { useLang } from "@/lib/i18n";

/** The five choices of the clip layout: auto follows the suggestion, the other four force one look for this clip. */
export const LAYOUTS = ["auto", "single", "gaming", "split", "reaction"] as const;
export type Layout = (typeof LAYOUTS)[number];

const asLayout = (value: unknown): Layout | null => (LAYOUTS as readonly string[]).includes(String(value)) ? (value as Layout) : null;

/** The layout the owner picked for this clip (auto when none), the one the last render used and what the render suggested. */
export function layoutOf(clip: Clip) {
  const meta = clip.render_meta ?? {};
  const suggested = (meta.layout_suggested as { layout?: string; why?: string } | null | undefined) ?? null;
  return { picked: asLayout(meta.layout) ?? "auto", used: asLayout(meta.layout_used), suggested: asLayout(suggested?.layout), why: suggested?.why ?? "", fallback: String(meta.layout_fallback ?? "") };
}

/** A 9:16 sketch of the layout: the same geometry the render uses (panels, seam, card), drawn in currentColor. */
function Sketch({ kind }: { kind: Layout }) {
  return (
    <svg viewBox="0 0 36 64" width="28" height="50" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="2" y="2" width="32" height="60" rx="5" />
      {kind === "single" && <circle cx="18" cy="26" r="7" />}
      {kind === "gaming" && <><path d="M2 24h32" /><circle cx="18" cy="13" r="5" /><path d="M10 48h16" /></>}
      {kind === "split" && <><path d="M2 32h32" strokeWidth="3" /><circle cx="18" cy="17" r="5" /><circle cx="18" cy="47" r="5" /></>}
      {kind === "reaction" && <><circle cx="18" cy="26" r="9" /><rect x="14" y="40" width="18" height="10" rx="2" fill="currentColor" /></>}
      {kind === "auto" && <path d="M12 38l6-18 6 18M14.5 32h7" />}
    </svg>
  );
}

/** One tap on a layout makes one new version of the clip with it (the same way a tapped hook does). */
export function LayoutPicker({ clip, onPick, busy }: { clip: Clip; onPick: (layout: Layout) => void; busy: boolean }) {
  const { t } = useLang();
  const now = layoutOf(clip);
  return (
    <section className="layout-pick" data-testid="layout-pick" aria-label={t("layoutTitle")}>
      <h3 className="small">{t("layoutTitle")}</h3>
      <div className="layout-row" role="radiogroup" aria-label={t("layoutTitle")}>
        {LAYOUTS.map((kind) => (
          <button key={kind} role="radio" aria-checked={now.picked === kind} disabled={busy} onClick={() => now.picked !== kind && onPick(kind)}>
            <Sketch kind={kind} />
            <span>{t(`layout_${kind}` as "layout_auto")}</span>
          </button>
        ))}
      </div>
      {now.suggested && <p className="small muted" data-testid="layout-suggested">{t("layoutSuggested")}: <b>{t(`layout_${now.suggested}` as "layout_auto")}</b>{now.why ? `. ${now.why}` : ""}</p>}
      {now.fallback && <p className="small muted" data-testid="layout-fallback">{t("layoutFallback")}: {now.fallback}</p>}
    </section>
  );
}
