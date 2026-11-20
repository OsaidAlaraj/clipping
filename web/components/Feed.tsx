"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, Clip, FilteredMoment, money, NetworkError, newActionId, PLATFORM_LABEL, Review, reviewAction, SwipeResult, undoReview } from "@/lib/api";
import { fetchData, invalidate, mutate, peek } from "@/lib/data";
import { qualityText, useLang } from "@/lib/i18n";
import { useSwipe } from "@/lib/useSwipe";
import PostBar from "./PostBar";
import { FeedSkeleton } from "./Skeleton";
import { Icon } from "./Shell";

export const REASONS: [string, string][] = [
  ["weak_hook", "Weak hook"],
  ["cut_mid_thought", "Cut mid-thought"],
  ["wrong_context", "Wrong context"],
  ["caption_error", "Caption error (name, term or language)"],
  ["audio_problem", "Audio problem"],
  ["framing_problem", "Framing problem"],
  ["copyright_or_music_risk", "Copyright or music risk"],
  ["disclosure_problem", "Disclosure problem"],
  ["outside_campaign_rules", "Outside campaign rules"],
  ["duplicate", "Duplicate"],
  ["other", "Other (note required)"],
];

type Summary = { clips_reviewed: number; accepted: number; rejected: number; parked: number; posts_due: number; minutes_logged: number; hours_today: number; pay_per_clip?: { name: string; rate_per_1k: number | null; max_payout_per_clip: number | null }[] };
type Sheet = null | "reject" | "hooks" | "edit" | "report" | "filtered";
type Decision = "accept" | "reject" | "later";
const SKIP_GAP = 5;        // a skipped clip returns after this many other clips
const MAX_RETURNS = 2;     // it returns at most twice; the next skip moves it to Review Later
/** The last decision, kept for Undo. `review` settles when the server (or the offline outbox) has recorded it. */
type UndoState = { review: Promise<Review>; clip: Clip; index: number; kind: Decision; queued: boolean } | null;
type FeedData = { clips: Clip[]; kill_switch: boolean; filtered?: FilteredMoment[]; undo_window_s: number; session_length_min: number; pay_per_clip_display: string };

const feedKey = (campaignId?: string) => `/api/feed${campaignId ? `?campaign_id=${campaignId}` : ""}`;
const configOf = (data?: FeedData) => ({ undo: Number(data?.undo_window_s ?? 10), length: Number(data?.session_length_min ?? 25), pay: data?.pay_per_clip_display ?? "summary", killed: data?.kill_switch ?? false });
/** The clip on screen, the one behind it and the next two stay mounted, so the next clip is already loaded when a swipe lands. */
const BEHIND = 1;
const AHEAD = 2;

// ------------------------------------------------------------------ sheets

function SheetFrame({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <div className="row between">
          <h1>{title}</h1>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function RejectSheet({ onSubmit, onClose }: { onSubmit: (reason: string, notes: string) => void; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const needsNote = reason === "other" && !notes.trim();
  return (
    <SheetFrame title="Why reject this clip?" onClose={onClose}>
      <div className="choices" role="radiogroup" aria-label="Reason">
        {REASONS.map(([code, label]) => (
          <button key={code} className="choice" role="radio" aria-checked={reason === code} onClick={() => setReason(code)}>
            {label}
          </button>
        ))}
      </div>
      {reason === "other" && (
        <div className="field">
          <label htmlFor="reject-note">Note</label>
          <input id="reject-note" className="input" value={notes} onChange={(event) => setNotes(event.target.value)} autoFocus />
        </div>
      )}
      <button className="btn danger block" disabled={!reason || needsNote} onClick={() => onSubmit(reason, notes.trim())}>
        Reject clip
      </button>
      {!reason && <p className="small muted">Pick a reason to continue.</p>}
    </SheetFrame>
  );
}

function HooksSheet({ clip, onPick, onClose }: { clip: Clip; onPick: (hookId: string) => void; onClose: () => void }) {
  const current = String(clip.render_meta?.hook_id ?? "");
  return (
    <SheetFrame title="Hooks" onClose={onClose}>
      <p className="small muted">Tap a hook to use it. The clip is re-rendered as a new version and returns to the feed.</p>
      <div className="choices">
        {clip.hooks.map((hook) => (
          <button key={hook.id} className="choice" aria-pressed={hook.id === current} onClick={() => (hook.id === current ? onClose() : onPick(hook.id))}>
            <span dir="auto">{hook.text}</span>
            <span className="badge">{hook.hook_type}</span>
          </button>
        ))}
      </div>
    </SheetFrame>
  );
}

function EditSheet({ clip, onSave, onClose }: { clip: Clip; onSave: (body: Record<string, unknown>) => void; onClose: () => void }) {
  const [caption, setCaption] = useState(clip.caption_text ?? "");
  const [start, setStart] = useState(String(clip.start_s));
  const [end, setEnd] = useState(String(clip.end_s));
  const save = () => {
    const body: Record<string, unknown> = {};
    if (caption !== (clip.caption_text ?? "")) body.caption = caption;
    if (Number(start) !== Number(clip.start_s) || Number(end) !== Number(clip.end_s)) body.trim = { start_s: Number(start), end_s: Number(end) };
    if (Object.keys(body).length) onSave(body);
    else onClose();
  };
  return (
    <SheetFrame title="Edit clip" onClose={onClose}>
      <p className="small muted">Each edit creates a new clip version. The caption must still start with the disclosure.</p>
      <div className="field">
        <label htmlFor="edit-caption">Caption</label>
        <textarea id="edit-caption" className="input" dir="auto" value={caption} onChange={(event) => setCaption(event.target.value)} />
      </div>
      <div className="row">
        <div className="field grow">
          <label htmlFor="edit-start">Start (s)</label>
          <input id="edit-start" className="input" inputMode="decimal" value={start} onChange={(event) => setStart(event.target.value)} />
        </div>
        <div className="field grow">
          <label htmlFor="edit-end">End (s)</label>
          <input id="edit-end" className="input" inputMode="decimal" value={end} onChange={(event) => setEnd(event.target.value)} />
        </div>
      </div>
      <button className="btn primary block" onClick={save}>Save as new version</button>
      {clip.source_id && <Link className="btn block" href={`/transcript?id=${clip.source_id}`}>Fix transcript</Link>}
    </SheetFrame>
  );
}

const LAYER_LABEL: Record<number, string> = { 1: "Blocked by a rule", 2: "Not strong enough", 3: "Not among the best" };
/** The engine's reason in plain words (it writes scores and cut-offs). */
export function plainMoment(reason: string): string {
  let m = reason.match(/Scored (\d+\.\d+), below this episode's cut-off of (\d+\.\d+)/);
  if (m) return `Score ${m[1]} out of 1. It needs ${m[2]} to be picked for this video.`;
  m = reason.match(/Ranked #(\d+); this type keeps the top (\d+)/);
  if (m) return `Number ${m[1]} of its kind. Only the best ${m[2]} are kept.`;
  return reason;
}
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Moments the engine set aside, each with its reason. Rescuing one removes it at once and cuts it like any other moment. */
export function FilteredSheet({ items, onRescue, onClose }: { items: FilteredMoment[]; onRescue: (item: FilteredMoment) => void; onClose: () => void }) {
  const { t } = useLang();
  return (
    <SheetFrame title={t("filtered")} onClose={onClose}>
      <p className="small muted">These moments were not chosen, and here is why. Tap Rescue to make a clip from one anyway.</p>
      {items.length === 0 && <p className="muted">Nothing is set aside.</p>}
      <div className="stack">
        {items.map((item) => (
          <div key={item.id} className="row between" data-testid="filtered-moment">
            <span dir="auto">
              {item.campaign_name} · {clock(Number(item.start_s))}–{clock(Number(item.end_s))}<br />
              <span className="small muted">{LAYER_LABEL[item.layer]}. {plainMoment(item.reason)}</span>
            </span>
            <button className="btn" onClick={() => onRescue(item)}>{t("rescue")}</button>
          </div>
        ))}
      </div>
    </SheetFrame>
  );
}

const CHECK_NAME: Record<string, string> = {
  duration: "Clip length", aspect_9_16: "Vertical video (9:16)", caption_starts_with_disclosure: "Caption starts with #ad",
  overlay_present_full_duration: "#ad label on screen the whole clip", banned_words: "No banned words",
};
const STATUS_WORD: Record<string, string> = { pass: "OK", fail: "Failed", review: "Check", pending: "Waiting" };
/** Turns a check's name and evidence into plain words; unknown ones are shown as written. */
export function plainCheck(check: string): string { return CHECK_NAME[check] ?? check.replaceAll("_", " "); }
export function plainEvidence(text: string): string {
  const m = text.match(/^([\d.]+) s; allowed (\d+) to (\d+) s$/);
  if (m) return `${m[1]} seconds. Allowed: ${m[2]} to ${m[3]}.`;
  return text === "none found" ? "None found." : text;
}

export function ComplianceSheet({ clip, onClose }: { clip: Clip; onClose: () => void }) {
  const { t } = useLang();
  const report = clip.compliance_report;
  return (
    <SheetFrame title="Compliance report" onClose={onClose}>
      {!report && <p className="muted">No report yet.</p>}
      {report && (
        <>
          <div className="stack checks">
            {report.deterministic.map((check, index) => (
              <div key={`${check.check}-${index}`} className="row between" data-testid={check.brand ? "brand-check" : undefined}>
                <span dir="auto">
                  {check.brand ? <>{t("brandNote")}: {check.rule}</> : plainCheck(check.check)}
                  <br /><span className="small muted">{plainEvidence(check.evidence)}</span>
                </span>
                <span className={`badge ${check.status}`}>{STATUS_WORD[check.status] ?? check.status}</span>
              </div>
            ))}
            {report.rules.map((rule, index) => (
              <div key={index} className="row between">
                <span dir="auto">{rule.rule}<br /><span className="small muted">{rule.evidence || "No evidence found in the clip text."}</span></span>
                <span className={`badge ${rule.status}`}>{STATUS_WORD[rule.status] ?? rule.status}</span>
              </div>
            ))}
          </div>
          <p className="small muted">Checked automatically against the campaign rules. It is a helper, not legal advice, and it cannot judge copyright or licences. You make the final call.</p>
        </>
      )}
    </SheetFrame>
  );
}

export function UndoToast({ seconds, onUndo, label }: { seconds: number; onUndo: () => void; label: string }) {
  const { t } = useLang();
  return (
    <div className="toast" role="status">
      <span>{label}</span>
      <button className="btn" onClick={onUndo}>{t("undo")}</button>
      <i className="timer" style={{ animationDuration: `${seconds}s` }} />
    </div>
  );
}

export function SessionSummary({ summary, onDone }: { summary: Summary; onDone: () => void }) {
  return (
    <div className="page" role="region" aria-label="Session summary">
      <div className="page-head"><h1>Session summary</h1></div>
      <div className="tiles">
        <div className="tile"><div className="num">{summary.clips_reviewed}</div><div className="small muted">Clips reviewed</div></div>
        <div className="tile"><div className="num">{summary.accepted}</div><div className="small muted">Accepted</div></div>
        <div className="tile"><div className="num">{summary.rejected}</div><div className="small muted">Rejected</div></div>
        <div className="tile"><div className="num">{summary.posts_due}</div><div className="small muted">Posts due</div></div>
      </div>
      <div className="card stack">
        <div className="row between"><span className="muted">Time this session</span><span>{Math.round(summary.minutes_logged)} min</span></div>
        <div className="row between"><span className="muted">Hours logged today</span><span>{summary.hours_today.toFixed(2)} h</span></div>
        {summary.parked > 0 && <div className="row between"><span className="muted">Moved to Review Later</span><span>{summary.parked}</span></div>}
      </div>
      {summary.pay_per_clip && summary.pay_per_clip.length > 0 && (
        <div className="card stack">
          <h2>Pay for accepted clips</h2>
          {summary.pay_per_clip.map((row) => (
            <div key={row.name} className="row between">
              <span>{row.name}</span>
              <span className="small muted">{money(row.rate_per_1k)} per 1,000 views{row.max_payout_per_clip ? `, max ${money(row.max_payout_per_clip)} per clip` : ""}</span>
            </div>
          ))}
        </div>
      )}
      <button className="btn primary block" onClick={onDone}>Done</button>
    </div>
  );
}

// ------------------------------------------------------------------ one clip, full screen

function ClipCard({ clip, active, preload, onAccept, onSwipeRight, onReject, onNext, onPrev, onOpen, showPay }: {
  clip: Clip; active: boolean; preload: "auto" | "metadata"; onAccept: () => void; onSwipeRight: () => void; onReject: () => void; onNext: () => void; onPrev: () => void;
  onOpen: (sheet: Sheet | "later") => void; showPay: boolean;
}) {
  const { t, lang } = useLang();
  const video = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true); // iOS blocks sound autoplay: clips start muted, a tap turns sound on
  const [progress, setProgress] = useState(0);
  const [textHidden, setTextHidden] = useState(false); // the caption block can be hidden to see the whole picture
  const { ref, intent, bind } = useSwipe(active ? { onRight: onSwipeRight, onLeft: onReject, onUp: onNext, onDown: onPrev, onTap: () => setMuted((value) => !value) } : {}, { tilt: true });

  // Only the clip on screen plays; the buffered ones wait and start from their first frame the moment they are shown.
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    if (active) el.play()?.catch?.(() => undefined);
    else { el.pause(); setMuted(true); setProgress(0); }
  }, [active]);

  /** Full screen with sound. iPhone Safari only supports its own player on a video element (webkitEnterFullscreen). */
  const fullScreen = () => {
    const el = video.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    if (!el) return;
    el.muted = false;
    setMuted(false);
    if (el.requestFullscreen) el.requestFullscreen().catch(() => el.webkitEnterFullscreen?.());
    else el.webkitEnterFullscreen?.();
  };

  const player = clip.video_url && (
    <video ref={video} src={clip.video_url} poster={clip.cover_url ?? undefined} muted={muted} autoPlay={active} loop playsInline preload={preload}
      onTimeUpdate={active ? (event) => { const el = event.currentTarget; if (el.duration) setProgress((el.currentTime / el.duration) * 100); } : undefined} />
  );
  // The buffered card keeps the video in the same position as the visible one, so React reuses the element (and its loaded data) when the card becomes active.
  if (!active) return <div className="feed-card idle" aria-hidden="true">{null}{player}</div>;

  return (
    <div className="feed-card" data-testid="clip-card" ref={ref as React.RefObject<HTMLDivElement>} {...bind}>
      <div className="progress" role="progressbar" aria-label="Clip progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
        <i style={{ width: `${progress}%` }} />
      </div>
      {player}
      {intent === "right" && <div className="swipe-flag yes">{t("acceptPost")}</div>}
      {intent === "left" && <div className="swipe-flag no">{t("reject")}</div>}
      {muted && <div className="mute-hint"><span className="chip">{t("soundOn")}</span></div>}
      <div className={`bottom${textHidden ? " text-hidden" : ""}`} aria-hidden={textHidden || undefined}>
        <div className="line">
          <strong>{clip.account_handle ? `@${clip.account_handle}` : "No account"}</strong>
          <span>{clip.platform ? PLATFORM_LABEL[clip.platform] ?? clip.platform : "No platform"}</span>
          <span>{clip.language === "ar" ? "Arabic" : "English"}</span>
          <span>{clip.duration_s ? `${Math.round(Number(clip.duration_s))} s` : ""}</span>
        </div>
        {(clip.post_targets?.length ?? 0) > 0 && (
          <div className="reason" data-testid="targets" dir="auto">
            {t("swipePostsTo")} {clip.post_targets!.map((x) => `${x.label} @${x.handle}${x.manual ? ` (${t("manualWord")})` : ""}`).join(", ")}
          </div>
        )}
        <div className="caption" dir="auto">{clip.caption_text}</div>
        {clip.moment_reason && <div className="reason" dir="auto">Why this moment: {clip.moment_reason}</div>}
        {clip.quality && <div className={`quality${clip.quality.weak ? " weak" : ""}`} data-testid="quality" dir="auto">{qualityText(clip.quality, lang)}</div>}
        <div className="reason">{clip.priority_reason}{showPay && clip.rate_per_1k ? ` · ${money(clip.rate_per_1k)} per 1,000 views` : ""}</div>
      </div>
      <div className="rail" onPointerDown={(event) => event.stopPropagation()}>
        <button aria-label={t("fullScreen")} onClick={fullScreen}>{Icon.expand}</button>
        <button aria-label={textHidden ? t("showText") : t("hideText")} aria-pressed={textHidden} onClick={() => setTextHidden((value) => !value)}>{Icon.text}</button>
        <button aria-label={t("later")} onClick={() => onOpen("later")}>{Icon.later}</button>
        <button aria-label={t("edit")} onClick={() => onOpen("edit")}>{Icon.edit}</button>
        <button aria-label={t("hooks")} onClick={() => onOpen("hooks")}>{Icon.hooks}</button>
        <button className="reject" aria-label={t("reject")} onClick={onReject}>{Icon.cross}</button>
        <button className="accept" aria-label={t("accept")} onClick={onAccept}>{Icon.check}</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ the feed

export default function Feed({ campaignId, onExit }: { campaignId?: string; onExit?: () => void }) {
  const { t } = useLang();
  const key = feedKey(campaignId);
  const cached = peek<FeedData>(key);
  // A feed seen a moment ago is on screen straight away; the fresh one replaces it while nothing has been decided yet.
  const [clips, setClips] = useState<Clip[] | null>(cached?.clips ?? null);
  const [index, setIndex] = useState(0);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [undo, setUndo] = useState<UndoState>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filtered, setFiltered] = useState<FilteredMoment[]>(cached?.filtered ?? []);
  const [message, setMessage] = useState("");
  const [bar, setBar] = useState<{ clip: Clip; index: number; result: SwipeResult | null } | null>(null);
  const [rolledBack, setRolledBack] = useState(false);
  const [config, setConfig] = useState(configOf(cached));
  const session = useRef<string | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ended = useRef(false);
  const touched = useRef(false); // true once the person has decided or moved, so a refreshed list never reshuffles under them
  const state = useRef({ clips: [] as Clip[], index: 0 });
  const clip = clips && clips.length ? clips[Math.min(index, clips.length - 1)] : null;
  state.current = { clips: clips ?? [], index };

  const fail = (error: unknown, putBack = false) => {
    setRolledBack(putBack);
    setMessage(error instanceof ApiError ? error.message : error instanceof Error && error.message ? error.message : "Something went wrong. Try again.");
  };

  const endSession = useCallback(async (position?: Record<string, unknown>) => {
    if (ended.current) return;
    ended.current = true;
    const current = state.current.clips[state.current.index];
    const fallback: Summary = { clips_reviewed: 0, accepted: 0, rejected: 0, parked: 0, posts_due: 0, minutes_logged: 0, hours_today: 0 };
    try {
      setSummary(session.current
        ? await api<Summary>(`/api/sessions/${session.current}/stop`, { body: { position: position ?? { campaign_id: campaignId ?? null, clip_id: current?.id ?? null } } })
        : fallback);
    } catch {
      setSummary(fallback);
    }
  }, [campaignId]);

  // The feed (recalculated by the server on every load) and the session timer start together, not one after the other.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const starting = api("/api/sessions", { body: { campaign_id: campaignId ?? null } }).catch(() => null);
    (async () => {
      try {
        const data = await fetchData<FeedData>(key);
        if (cancelled) return;
        if (!touched.current) setClips(data.clips);
        setFiltered(data.filtered ?? []);
        setConfig(configOf(data));
        const started = await starting;
        if (cancelled) return;
        session.current = started?.id ?? null;
        const resume = started?.resume_position?.clip_id;
        const at = resume && !touched.current ? data.clips.findIndex((item) => item.id === resume) : -1;
        if (at > 0) setIndex(at);
        timer = setTimeout(() => endSession(), Number(data.session_length_min) * 60 * 1000); // bounded feed
      } catch (error) {
        if (!cancelled) { setClips((list) => list ?? []); fail(error); }
      }
    })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); if (undoTimer.current) clearTimeout(undoTimer.current); };
  }, [campaignId, endSession, key]);

  // Keep the cached copy in step with what is on screen, so coming back later never shows a clip already decided.
  useEffect(() => { if (clips && peek(key)) mutate<FeedData>(key, (d) => ({ ...d!, clips })); }, [clips, key]);

  const removeCurrent = useCallback(() => {
    const { clips: list, index: at } = state.current;
    const next = list.filter((_, i) => i !== at);
    touched.current = true;
    setClips(next);
    setIndex(Math.min(at, Math.max(next.length - 1, 0)));
  }, []);

  /** Put a clip back where it was (a refused decision, or Undo). */
  const reinsert = useCallback((back: Clip, at: number) => {
    setClips((list) => {
      if ((list ?? []).some((item) => item.id === back.id)) return list;
      const next = [...(list ?? [])];
      next.splice(Math.min(at, next.length), 0, back);
      return next;
    });
    setIndex(at);
  }, []);

  /** Accept, reject and save for later all work the same way: the clip leaves at once, the server is told in the background,
   *  Undo is offered, and a refusal puts the clip back with the reason. Nothing here waits on the network. */
  const decide = useCallback((kind: Decision, body: Record<string, unknown> = {}) => {
    const { clips: list, index: at } = state.current;
    const current = list[at];
    if (!current) return;
    removeCurrent();
    mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: Math.max(c.to_review - 1, 0) } : c as any));
    const review = reviewAction(kind, current.id, { ...body, session_id: session.current });
    if (undoTimer.current) clearTimeout(undoTimer.current);
    const entry: UndoState = { review, clip: current, index: at, kind, queued: false };
    setUndo(entry);
    undoTimer.current = setTimeout(() => setUndo((u) => (u?.review === review ? null : u)), config.undo * 1000);
    review.then(
      (done) => {
        setUndo((u) => (u?.review === review ? { ...u, queued: !!done.queued } : u));
        for (const path of ["/api/home", "/api/counts", "/api/queue", "/api/review-later"]) invalidate(path);
      },
      (error) => {
        reinsert(current, at);
        setUndo((u) => (u?.review === review ? null : u));
        mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: c.to_review + 1 } : c as any));
        fail(error, true);
      },
    );
  }, [removeCurrent, reinsert, config.undo]);

  const accept = useCallback(() => decide("accept"), [decide]);

  /** Swipe right (decision 31): accepts the clip AND is the owner's confirm to post. The clip leaves at once and the bar says what was queued;
   *  Undo (inside the Undo window) cancels the posts and puts the clip back. Posting needs the server, so offline nothing happens. */
  const swipeRight = useCallback(async () => {
    const { clips: list, index: at } = state.current;
    const current = list[at];
    if (!current) return;
    if (current.compliance_status === "fail") { setSheet("report"); return; }       // a failed clip never posts: show why
    removeCurrent();
    setUndo(null);
    setBar({ clip: current, index: at, result: null });
    mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: Math.max(c.to_review - 1, 0) } : c as any));
    const back = () => {
      reinsert(current, at);
      setBar(null);
      mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: c.to_review + 1 } : c as any));
    };
    try {
      const result = await api<SwipeResult>(`/api/clips/${current.id}/swipe`, { body: { request_id: newActionId(), session_id: session.current } });
      setBar({ clip: current, index: at, result });
      for (const path of ["/api/home", "/api/counts", "/api/queue", "/api/review-later"]) invalidate(path);
    } catch (error) {
      back();
      if (error instanceof ApiError && error.code === "compliance_blocked") setSheet("report");
      else if (error instanceof NetworkError) setMessage(t("noConnection"));
      else fail(error, true);
    }
  }, [removeCurrent, reinsert, t]);

  const undoSwipe = useCallback(async () => {
    const pending = bar;
    if (!pending?.result?.review) return;
    try {
      await api(`/api/clips/${pending.clip.id}/swipe/undo`, { body: { review_id: pending.result.review.id } });
      setBar(null);
      reinsert(pending.clip, pending.index);
      mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: c.to_review + 1 } : c as any));
      for (const path of ["/api/home", "/api/counts", "/api/queue", "/api/review-later"]) invalidate(path);
      setMessage(t("swipeUndone"));
    } catch (error) {
      fail(error);
    }
  }, [bar, reinsert, t]);
  const later = useCallback(() => decide("later"), [decide]);
  const reject = useCallback((reason: string, notes: string) => {
    if (!state.current.clips[state.current.index] || !reason) return; // a rejection always needs a reason
    setSheet(null);
    decide("reject", { reason_code: reason, notes: notes || null });
  }, [decide]);

  const doUndo = useCallback(async () => {
    const pending = undo;
    if (!pending) return;
    if (undoTimer.current) clearTimeout(undoTimer.current);
    setUndo(null);
    reinsert(pending.clip, pending.index); // back on screen at once
    try {
      await undoReview(await pending.review);
      for (const path of ["/api/home", "/api/counts", "/api/queue", "/api/review-later"]) invalidate(path);
    } catch (error) {
      setClips((list) => (list ?? []).filter((item) => item.id !== pending.clip.id)); // the server refused: the decision stands
      fail(error);
    }
  }, [undo, reinsert]);

  const saveEdit = useCallback(async (body: Record<string, unknown>) => {
    const current = state.current.clips[state.current.index];
    if (!current) return;
    setSheet(null);
    try {
      const next = await api(`/api/clips/${current.id}/edit`, { body });
      if (next.status === "ready_for_review") {
        const fresh = await api<Clip>(`/api/clips/${next.id}`);
        setClips((list) => (list ?? []).map((item) => (item.id === current.id ? { ...fresh, priority_reason: current.priority_reason } : item)));
        setMessage("Saved as a new version.");
      } else {
        removeCurrent();
        setMessage("Re-rendering as a new version. It returns to the feed when ready.");
      }
    } catch (error) {
      fail(error);
    }
  }, [removeCurrent]);

  // Rescue: the moment leaves the list at once; the server cuts it like any other. A refusal puts it back.
  const rescue = useCallback(async (item: FilteredMoment) => {
    setFiltered((list) => list.filter((m) => m.id !== item.id));
    try {
      await api(`/api/candidates/${item.id}/rescue`, { body: {} });
      setMessage("Rescued. It joins the feed when it has been cut.");
    } catch (error) {
      setFiltered((list) => [item, ...list]);
      fail(error);
    }
  }, []);

  /** Swiping past a clip is a skip: it comes back after SKIP_GAP other clips, at most MAX_RETURNS times, and the third skip moves it
   *  to Review Later. The server keeps the order (so a reload gives the same feed); the screen moves at once and never waits. */
  const next = useCallback(() => {
    const { clips: list, index: at } = state.current;
    const current = list[at];
    if (!current || at >= list.length - 1) return;           // nothing ahead to skip to
    touched.current = true;
    const rest = list.filter((_, i) => i !== at);
    const skips = current.skip_count ?? 0;
    let anchor: string | null = null;
    if (skips >= MAX_RETURNS) {
      setClips(rest);
      mutate<{ to_review: number; alerts: number }>("/api/counts", (c) => (c ? { ...c, to_review: Math.max(c.to_review - 1, 0) } : c as any));
      setMessage("Skipped three times: moved to Review Later.");
    } else {
      const to = Math.min(at + SKIP_GAP, rest.length);
      anchor = rest[to - 1]?.id ?? null;
      setClips([...rest.slice(0, to), { ...current, skip_count: skips + 1 }, ...rest.slice(to)]);
    }
    setIndex(at);
    api(`/api/clips/${current.id}/skip`, { body: { anchor_id: anchor, session_id: session.current } }).then(
      () => { if (skips >= MAX_RETURNS) for (const path of ["/api/home", "/api/counts", "/api/review-later"]) invalidate(path); },
      () => undefined,                                          // the order is a comfort: a failed report changes nothing the owner decided
    );
  }, []);
  const prev = useCallback(() => { touched.current = true; setIndex((i) => Math.max(i - 1, 0)); }, []);

  // Desktop keyboard shortcuts (FR-35).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key === "Escape") return setSheet(null);
      if (sheet || summary) return;
      const key = event.key.toLowerCase();
      if (key === "a") accept();
      else if (key === "r") setSheet("reject");
      else if (key === "h") setSheet("hooks");
      else if (key === "e") setSheet("edit");
      else if (key === "u") doUndo();
      else if (event.key === "ArrowDown" || event.key === "ArrowRight") next();
      else if (event.key === "ArrowUp" || event.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [accept, doUndo, next, prev, sheet, summary]);

  const showPay = useMemo(() => config.pay === "card", [config.pay]);

  if (summary) return <SessionSummary summary={summary} onDone={() => (onExit ? onExit() : (window.location.href = "/"))} />;
  if (clips === null) return <FeedSkeleton />;
  const at = Math.min(index, Math.max(clips.length - 1, 0));
  const visible = clips.slice(Math.max(at - BEHIND, 0), at + AHEAD + 1);

  return (
    <>
      {clip ? (
        <div className="feed">
          <div className="top">
            <span className="name">{clip.is_practice && <span className="badge info practice">Practice</span>}<span className="title">{clip.campaign_name}</span></span>
            <span className="row">
              <button className={`chip rules ${clip.compliance_status}`} onClick={() => setSheet("report")} aria-label={`Rules check: ${clip.compliance_status}`}>
                {clip.compliance_status === "pass" ? "Rules OK" : clip.compliance_status === "fail" ? "Rules failed" : "Check rules"}
              </button>
              <span className="chip" aria-label="Position in feed">{index + 1} of {clips.length}</span>
              {filtered.length > 0 && <button className="chip" onClick={() => setSheet("filtered")}>{t("filtered")} {filtered.length}</button>}
              <button className="chip" onClick={() => endSession()}>{t("stopHere")}</button>
            </span>
          </div>
          {visible.map((item) => (
            <ClipCard key={item.id} clip={item} active={item.id === clip.id} preload={item.id === clip.id || item.id === clips[at + 1]?.id ? "auto" : "metadata"}
              showPay={showPay} onAccept={accept} onSwipeRight={swipeRight} onReject={() => setSheet("reject")} onNext={next} onPrev={prev}
              onOpen={(which) => (which === "later" ? later() : setSheet(which))} />
          ))}
        </div>
      ) : (
        <div className="empty">
          <h1>{config.killed ? "The kill switch is on" : "Nothing to review"}</h1>
          <p className="muted">{config.killed ? "The feed is paused until you switch it off in Settings." : "New clips appear here when the pipeline finishes them."}</p>
          {filtered.length > 0 && <button className="btn" onClick={() => setSheet("filtered")}>{t("filtered")} {filtered.length}</button>}
          <button className="btn" onClick={() => endSession()}>End session</button>
        </div>
      )}
      {message && (
        <div className="toast" role="alert" style={{ bottom: undo ? "calc(var(--tab-h) + 84px)" : undefined }}>
          <span className="msg">{message}{rolledBack && <><br /><span className="small">The clip is back in your feed.</span></>}</span>
          <button className="btn ghost" onClick={() => { setMessage(""); setRolledBack(false); }}>Dismiss</button>
        </div>
      )}
      {bar && <PostBar key={bar.clip.id} result={bar.result} clip={bar.clip} seconds={config.undo} onUndo={undoSwipe} onClose={() => setBar(null)} />}
      {undo && <UndoToast seconds={config.undo} onUndo={doUndo} label={`${t(undo.kind === "accept" ? "accepted" : undo.kind === "reject" ? "rejected" : "parked")}${undo.queued ? " · saved" : ""}`} />}
      {sheet === "reject" && <RejectSheet onSubmit={reject} onClose={() => setSheet(null)} />}
      {sheet === "hooks" && clip && <HooksSheet clip={clip} onClose={() => setSheet(null)} onPick={(hookId) => saveEdit({ hook_id: hookId })} />}
      {sheet === "edit" && clip && <EditSheet clip={clip} onClose={() => setSheet(null)} onSave={saveEdit} />}
      {sheet === "filtered" && <FilteredSheet items={filtered} onRescue={rescue} onClose={() => setSheet(null)} />}
      {sheet === "report" && clip && <ComplianceSheet clip={clip} onClose={() => setSheet(null)} />}
    </>
  );
}
