"use client";

// One card per platform the clip is going to: where it stands now, what the owner still has to do, and Cancel while nothing has been sent.

import { useEffect, useState } from "react";

import { Clip, PostTarget } from "@/lib/api";
import { PostManual } from "./PostManual";
import { usePostText } from "./PostText";

/** Time left until `until`, ticking each second. Stops ticking once it passes. */
export function Countdown({ until }: { until: string }) {
  const { t } = usePostText();
  const end = new Date(until).getTime();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (Date.now() >= end) return;
    const timer = setInterval(() => { setNow(Date.now()); if (Date.now() >= end) clearInterval(timer); }, 1000);
    return () => clearInterval(timer);
  }, [end]);
  const left = Math.max(0, Math.ceil((end - now) / 1000));
  if (left === 0) return <p className="banner warn" role="status">{t("countdownOver")}</p>;
  const clock = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
  return (
    <p className="banner ok post-count-line" data-testid="submit-countdown">
      {t("countdown")} <span className="post-count" role="timer" aria-live="off" dir="ltr">{clock}</span>
    </p>
  );
}

const BADGE: Record<PostTarget["state"], string> = {
  scheduled: "review", sending: "info", processing: "info", published: "pass", draft: "review", private: "review",
  needs_check: "block", failed: "fail", cancelled: "",
};

export function TargetCard({ clip, target, onCancel, onLinked }: {
  clip: Clip; target: PostTarget; onCancel: (target: PostTarget) => void; onLinked: (target: PostTarget) => void;
}) {
  const { t, f, platform, time } = usePostText();
  const name = platform(target.platform);
  const state = target.state;
  const waitingForHand = state === "scheduled" && target.manual;
  const label = waitingForHand ? t("waitingYou") : state === "scheduled" ? f("scheduled", { time: time(target.scheduled_at) }) : state === "processing" ? f("processing", { platform: name }) : t(state);

  return (
    <article className="post-target" data-state={state} aria-label={name}>
      <div className="row between wrap">
        <strong>{name}</strong>
        <span className={`badge ${BADGE[state]}`} role="status">{label}</span>
      </div>

      {(state === "failed" || state === "cancelled") && target.reason && <p className="small muted" dir="auto">{target.reason}</p>}

      {state === "published" && (
        <>
          {target.url && <a className="small" href={target.url} target="_blank" rel="noreferrer noopener" dir="ltr">{t("openPost")}</a>}
          {target.submit_by && <Countdown until={target.submit_by} />}
        </>
      )}

      {(state === "draft" || state === "private") && (
        <>
          <div className="banner warn"><strong>{t("finishTitle")}</strong><br />{f(state === "draft" ? "finishDraft" : "finishPrivate", { platform: name })}</div>
          <PostManual clip={clip} target={target} withFile={false} onLinked={onLinked} />
        </>
      )}

      {state === "needs_check" && (
        <>
          <div className="banner block" role="alert">
            {target.reason && <span dir="auto">{target.reason}<br /></span>}
            {f("checkBody", { platform: name })}
          </div>
          <PostManual clip={clip} target={target} withFile={false} onLinked={onLinked} />
          <button type="button" className="btn" onClick={() => onCancel(target)}>{t("didNotPost")}</button>
        </>
      )}

      {waitingForHand && (
        <>
          <p className="small muted">{f("manualTitle", { platform: name })}</p>
          <PostManual clip={clip} target={target} withFile onLinked={onLinked} />
        </>
      )}

      {state === "scheduled" && <button type="button" className="btn ghost" aria-label={`${t("cancel")} ${name}`} onClick={() => onCancel(target)}>{t("cancel")}</button>}
    </article>
  );
}
