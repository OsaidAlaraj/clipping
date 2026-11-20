"use client";

import { useEffect, useState } from "react";

import type { Clip, SwipeResult } from "@/lib/api";
import { LabelKey, useLang } from "@/lib/i18n";

type T = (key: LabelKey) => string;

/** "TikTok @a now · Instagram @b in 20 min": when each queued post goes out, counted from the swipe. */
export function whenText(scheduledAt: string, now: number, t: T): string {
  const minutes = Math.round((new Date(scheduledAt).getTime() - now) / 60000);
  return minutes <= 0 ? t("postNow") : `${t("postIn")} ${minutes} ${t("minShort")}`;
}

/** What the bar says after a swipe, as separate lines so the screen (and the tests) can read them. */
export function postBarLines(result: SwipeResult, now: number, t: T): { main: string; manual: string | null; warning: string | null } {
  const queued = result.targets.filter((x) => !x.manual);
  const manual = result.targets.filter((x) => x.manual);
  let main: string;
  if (result.practice) main = t("practiceNoPost");
  else if (result.targets.length === 0) main = t("noAccountsToPost");
  else if (queued.length === 0) main = t("accepted");
  else main = `${t("postsTo")} ${queued.map((x) => `${x.label} @${x.handle} ${whenText(x.scheduled_at, now, t)}`).join(" · ")}`;
  return {
    main,
    manual: manual.length ? `${t("manualReady")}: ${manual.map((x) => `${x.label} @${x.handle}`).join(", ")}` : null,
    warning: result.warning,
  };
}

/** The bar at the bottom after a swipe right: what will be posted and when, the manual fallback, and Undo for the Undo window. */
export default function PostBar({ result, clip, seconds, onUndo, onClose }: {
  result: SwipeResult | null; clip: Clip; seconds: number; onUndo: () => void; onClose: () => void;
}) {
  const { t } = useLang();
  const [now] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);
  const [undoable, setUndoable] = useState(true);
  const manual = !!result && result.targets.some((x) => x.manual);

  useEffect(() => {
    if (!result) return;
    const closeUndo = setTimeout(() => setUndoable(false), seconds * 1000);
    const close = manual ? undefined : setTimeout(onClose, seconds * 1000 + 600);
    const closeManual = manual ? setTimeout(onClose, 45_000) : undefined;
    return () => { clearTimeout(closeUndo); if (close) clearTimeout(close); if (closeManual) clearTimeout(closeManual); };
  }, [result, seconds, manual, onClose]);

  const lines = result ? postBarLines(result, now, t) : null;
  const copy = async () => {
    try { await navigator.clipboard.writeText(clip.caption_text ?? ""); setCopied(true); } catch { setCopied(false); }
  };
  return (
    <div className="swipebar" role="status" data-testid="post-bar">
      <div className="swipebar-text" dir="auto">
        <span className="swipebar-main">{lines ? lines.main : t("confirming")}</span>
        {lines?.warning && <span className="swipebar-warn">{lines.warning}</span>}
        {lines?.manual && <span className="swipebar-manual">{lines.manual}</span>}
      </div>
      {result && undoable && <button className="btn" onClick={onUndo}>{t("undo")}</button>}
      {manual && (
        <div className="swipebar-actions">
          <button className="btn ghost" onClick={copy}>{copied ? t("copied") : t("copyCaption")}</button>
          {clip.video_url && <a className="btn ghost" href={clip.video_url} download>{t("downloadVideo")}</a>}
        </div>
      )}
      {result && undoable && <i className="timer" style={{ animationDuration: `${seconds}s` }} />}
    </div>
  );
}
