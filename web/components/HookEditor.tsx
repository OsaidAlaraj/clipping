"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";

import type { Clip } from "@/lib/api";
import { useLang } from "@/lib/i18n";

/** The owner's hook for one clip while they edit it: what the live overlay draws and what Apply sends. */
export type HookDraft = { text: string; from_s: number; for_s: number; position: "top" | "middle" | "bottom" };

export const HOOK_POSITIONS: HookDraft["position"][] = ["top", "middle", "bottom"];
export const HOOK_FADES_S = 0.6;   // the render adds the two fades to the time on screen (build_ass)
const FOR_MIN = 0.5;
const FOR_MAX = 10;

/** The automatic time the render picks when the owner sets none: words / 2 s, between 3 and 5 s (hook_rules.show_seconds). */
export function autoSeconds(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.round(Math.min(Math.max(3, words / 2), 5) * 100) / 100;
}

/** The starting draft for a clip: its own choices if it has any, else the automatic time at the top. */
export function draftOf(clip: Clip): HookDraft {
  const meta = clip.render_meta ?? {};
  const text = String(clip.hook_text ?? meta.hook_text ?? "");
  const position = HOOK_POSITIONS.includes(meta.hook_position as HookDraft["position"]) ? (meta.hook_position as HookDraft["position"]) : "top";
  return { text, from_s: Number(meta.hook_from_s ?? 0), for_s: Number(meta.hook_for_s ?? autoSeconds(text)), position };
}

export const maxStart = (clip: Clip) => Math.max(Math.round((Number(clip.duration_s ?? clip.end_s - clip.start_s) - FOR_MIN) * 10) / 10, 0);

// A tiny external store: moving a slider redraws only the overlay, never the whole feed.
const drafts = new Map<string, HookDraft>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

export function setHookDraft(clipId: string, draft: HookDraft | null) {
  if (draft) drafts.set(clipId, draft); else drafts.delete(clipId);
  listeners.forEach((listener) => listener());
}
export const useHookDraft = (clipId: string): HookDraft | null => useSyncExternalStore(subscribe, () => drafts.get(clipId) ?? null, () => null);

/**
 * The live preview: the hook drawn over the playing video. It follows the video's clock with requestAnimationFrame and flips one
 * attribute (no React render per frame): full strength while the hook is on screen, a faint ghost the rest of the loop so the
 * position is always visible. Approximate (the real title card is burned in on Apply and checked after the render).
 */
export function HookOverlay({ clipId, video }: { clipId: string; video: React.RefObject<HTMLVideoElement | null> }) {
  const draft = useHookDraft(clipId);
  const box = useRef<HTMLDivElement>(null);
  const range = useRef({ from: 0, to: 0 });
  range.current = draft ? { from: draft.from_s, to: draft.from_s + draft.for_s + HOOK_FADES_S } : range.current;
  const on = Boolean(draft);

  useEffect(() => {
    if (!on) return;
    const el = box.current;
    const media = video.current;
    if (!el || !media) return;
    const size = () => { el.style.fontSize = `${Math.max((media.clientHeight || 640) * 0.036 * 0.78, 12)}px`; };   // the render's title size, 3.6% of the frame height
    size();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(size) : null;
    observer?.observe(media);
    let frame = 0;
    let last = "";
    const tick = () => {
      const t = media.currentTime;
      const state = t >= range.current.from && t < range.current.to ? "live" : "ghost";
      if (state !== last) { el.dataset.state = state; last = state; }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [on, video]);

  if (!draft) return null;
  return (
    <div className={`hook-preview at-${draft.position}`} ref={box} data-testid="hook-preview" data-state="live" aria-hidden="true">
      <span dir="auto">{draft.text || "…"}</span>
    </div>
  );
}

/** Text, when it shows, how long, and where: the one set of controls, used by the hooks sheet wherever a clip is shown. */
export function HookControls({ clip, draft, onChange, onApply, busy }: {
  clip: Clip; draft: HookDraft; onChange: (next: HookDraft) => void; onApply: () => void; busy: boolean;
}) {
  const { t } = useLang();
  const set = (patch: Partial<HookDraft>) => onChange({ ...draft, ...patch });
  const empty = !draft.text.trim();
  return (
    <div className="hook-edit" data-testid="hook-edit">
      <label className="field">
        <span className="small muted">{t("hookOwn")}</span>
        <textarea rows={2} dir="auto" maxLength={140} value={draft.text} placeholder={t("hookOwnPlaceholder")} onChange={(event) => set({ text: event.target.value })} />
      </label>
      <label className="slide">
        <span className="small">{t("hookShowFrom")} <b>{draft.from_s.toFixed(1)} s</b></span>
        <input type="range" min={0} max={maxStart(clip)} step={0.1} value={Math.min(draft.from_s, maxStart(clip))} aria-label={t("hookShowFrom")}
               onChange={(event) => set({ from_s: Number(event.target.value) })} />
      </label>
      <label className="slide">
        <span className="small">{t("hookShowFor")} <b>{draft.for_s.toFixed(1)} s</b></span>
        <input type="range" min={FOR_MIN} max={FOR_MAX} step={0.1} value={draft.for_s} aria-label={t("hookShowFor")}
               onChange={(event) => set({ for_s: Number(event.target.value) })} />
      </label>
      <div className="seg" role="radiogroup" aria-label={t("hookPosition")}>
        {HOOK_POSITIONS.map((position) => (
          <button key={position} role="radio" aria-checked={draft.position === position} onClick={() => set({ position })}>{t(`hookAt_${position}` as "hookAt_top")}</button>
        ))}
      </div>
      <p className="small muted">{t("hookPreviewNote")}</p>
      <button className="btn primary block" disabled={empty || busy} aria-busy={busy} onClick={onApply}>{t("hookApply")}</button>
    </div>
  );
}
