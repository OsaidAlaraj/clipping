"use client";

import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, Clip, money, PLATFORM_LABEL, Review, reviewAction, undoReview } from "@/lib/api";
import { useLang } from "@/lib/i18n";
import { useSwipe } from "@/lib/useSwipe";
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
type Sheet = null | "reject" | "hooks" | "edit" | "report";
type UndoState = { review: Review; clip: Clip; index: number } | null;

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

export function ComplianceSheet({ clip, onClose }: { clip: Clip; onClose: () => void }) {
  const report = clip.compliance_report;
  return (
    <SheetFrame title="Compliance report" onClose={onClose}>
      {!report && <p className="muted">No report yet.</p>}
      {report && (
        <>
          <div className="stack">
            {report.deterministic.map((check) => (
              <div key={check.check} className="row between">
                <span>{check.check.replaceAll("_", " ")}<br /><span className="small muted">{check.evidence}</span></span>
                <span className={`badge ${check.status}`}>{check.status}</span>
              </div>
            ))}
            {report.rules.map((rule, index) => (
              <div key={index} className="row between">
                <span dir="auto">{rule.rule}<br /><span className="small muted">{rule.evidence || "No evidence found in the clip text."}</span></span>
                <span className={`badge ${rule.status}`}>{rule.status}</span>
              </div>
            ))}
          </div>
          <p className="small muted">{report.note} {report.legal_notice}</p>
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

function ClipCard({ clip, onAccept, onReject, onNext, onPrev, onOpen, showPay }: {
  clip: Clip; onAccept: () => void; onReject: () => void; onNext: () => void; onPrev: () => void; onOpen: (sheet: Sheet | "later") => void; showPay: boolean;
}) {
  const { t } = useLang();
  const reduce = useReducedMotion();
  const video = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true); // iOS blocks sound autoplay: clips start muted, a tap turns sound on
  const [progress, setProgress] = useState(0);
  const { offset, bind } = useSwipe({ onRight: onAccept, onLeft: onReject, onUp: onNext, onDown: onPrev, onTap: () => setMuted((value) => !value) });

  useEffect(() => {
    setMuted(true);
    setProgress(0);
  }, [clip.id]);

  return (
    <motion.div className="feed-card" data-testid="clip-card" {...bind}
      animate={reduce ? { x: 0, y: 0 } : { x: offset.x, y: offset.y * 0.4, rotate: offset.x / 40 }}
      transition={{ type: "tween", duration: offset.x || offset.y ? 0 : 0.18 }}>
      <div className="progress" role="progressbar" aria-label="Clip progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
        <i style={{ width: `${progress}%` }} />
      </div>
      {clip.video_url && (
        <video ref={video} key={clip.id} src={clip.video_url} muted={muted} autoPlay loop playsInline preload="metadata"
          onTimeUpdate={(event) => { const el = event.currentTarget; if (el.duration) setProgress((el.currentTime / el.duration) * 100); }} />
      )}
      {offset.x > 30 && <div className="swipe-flag yes">{t("accept")}</div>}
      {offset.x < -30 && <div className="swipe-flag no">{t("reject")}</div>}
      {muted && <div className="mute-hint"><span className="chip">{t("soundOn")}</span></div>}
      <div className="bottom">
        <div className="line">
          <strong>{clip.account_handle ? `@${clip.account_handle}` : "No account"}</strong>
          <span>{clip.platform ? PLATFORM_LABEL[clip.platform] ?? clip.platform : "No platform"}</span>
          <span>{clip.language === "ar" ? "Arabic" : "English"}</span>
          <span>{clip.duration_s ? `${Math.round(Number(clip.duration_s))} s` : ""}</span>
          <button className={`badge ${clip.compliance_status}`} style={{ minHeight: 28, border: 0 }} onPointerDown={(event) => event.stopPropagation()} onClick={() => onOpen("report")}>
            Compliance: {clip.compliance_status}
          </button>
        </div>
        <div className="caption" dir="auto">{clip.caption_text}</div>
        <div className="reason">{clip.priority_reason}{showPay && clip.rate_per_1k ? ` · ${money(clip.rate_per_1k)} per 1,000 views` : ""}</div>
      </div>
      <div className="rail" onPointerDown={(event) => event.stopPropagation()}>
        <button aria-label={t("later")} onClick={() => onOpen("later")}>{Icon.later}</button>
        <button aria-label={t("edit")} onClick={() => onOpen("edit")}>{Icon.edit}</button>
        <button aria-label={t("hooks")} onClick={() => onOpen("hooks")}>{Icon.hooks}</button>
        <button className="reject" aria-label={t("reject")} onClick={onReject}>{Icon.cross}</button>
        <button className="accept" aria-label={t("accept")} onClick={onAccept}>{Icon.check}</button>
      </div>
    </motion.div>
  );
}

// ------------------------------------------------------------------ the feed

export default function Feed({ campaignId, onExit }: { campaignId?: string; onExit?: () => void }) {
  const { t } = useLang();
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [index, setIndex] = useState(0);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [undo, setUndo] = useState<UndoState>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState("");
  const [config, setConfig] = useState({ undo: 10, length: 25, pay: "summary", killed: false });
  const session = useRef<string | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ended = useRef(false);
  const state = useRef({ clips: [] as Clip[], index: 0 });
  const clip = clips && clips.length ? clips[Math.min(index, clips.length - 1)] : null;
  state.current = { clips: clips ?? [], index };

  const fail = (error: unknown) => setMessage(error instanceof ApiError ? error.message : "Something went wrong. Try again.");

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

  // Load the feed (recalculated by the server on every load) and start the session timer.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api(`/api/feed${campaignId ? `?campaign_id=${campaignId}` : ""}`);
        if (cancelled) return;
        setClips(data.clips);
        setConfig({ undo: Number(data.undo_window_s), length: Number(data.session_length_min), pay: data.pay_per_clip_display, killed: data.kill_switch });
        const started = await api("/api/sessions", { body: { campaign_id: campaignId ?? null } });
        if (cancelled) return;
        session.current = started.id;
        const resume = started.resume_position?.clip_id;
        const at = resume ? (data.clips as Clip[]).findIndex((item) => item.id === resume) : -1;
        if (at > 0) setIndex(at);
        timer = setTimeout(() => endSession(), Number(data.session_length_min) * 60 * 1000); // bounded feed
      } catch (error) {
        if (!cancelled) { setClips([]); fail(error); }
      }
    })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); if (undoTimer.current) clearTimeout(undoTimer.current); };
  }, [campaignId, endSession]);

  const removeCurrent = useCallback(() => {
    const { clips: list, index: at } = state.current;
    const next = list.filter((_, i) => i !== at);
    setClips(next);
    setIndex(Math.min(at, Math.max(next.length - 1, 0)));
  }, []);

  const startUndo = useCallback((review: Review, removed: Clip, at: number) => {
    if (undoTimer.current) clearTimeout(undoTimer.current);
    setUndo({ review, clip: removed, index: at });
    undoTimer.current = setTimeout(() => setUndo(null), config.undo * 1000);
  }, [config.undo]);

  const accept = useCallback(async () => {
    const current = state.current.clips[state.current.index];
    if (!current) return;
    removeCurrent(); // accept updates at once; the server confirms in the background
    try {
      await reviewAction("accept", current.id, { session_id: session.current });
    } catch (error) {
      setClips((list) => [current, ...(list ?? [])]);
      fail(error);
    }
  }, [removeCurrent]);

  const reject = useCallback(async (reason: string, notes: string) => {
    const { clips: list, index: at } = state.current;
    const current = list[at];
    if (!current || !reason) return; // a rejection always needs a reason
    setSheet(null);
    removeCurrent();
    try {
      const review = await reviewAction("reject", current.id, { reason_code: reason, notes: notes || null, session_id: session.current });
      startUndo(review, current, at);
    } catch (error) {
      setClips((items) => [current, ...(items ?? [])]);
      fail(error);
    }
  }, [removeCurrent, startUndo]);

  const doUndo = useCallback(async () => {
    const pending = undo;
    if (!pending) return;
    if (undoTimer.current) clearTimeout(undoTimer.current);
    setUndo(null);
    try {
      await undoReview(pending.review);
      setClips((list) => { const next = [...(list ?? [])]; next.splice(Math.min(pending.index, next.length), 0, pending.clip); return next; });
      setIndex(pending.index);
    } catch (error) {
      fail(error);
    }
  }, [undo]);

  const later = useCallback(async () => {
    const current = state.current.clips[state.current.index];
    if (!current) return;
    removeCurrent();
    try {
      await reviewAction("later", current.id, { session_id: session.current });
    } catch (error) {
      setClips((list) => [current, ...(list ?? [])]);
      fail(error);
    }
  }, [removeCurrent]);

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

  const next = useCallback(() => setIndex((i) => Math.min(i + 1, Math.max(state.current.clips.length - 1, 0))), []);
  const prev = useCallback(() => setIndex((i) => Math.max(i - 1, 0)), []);

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
  if (clips === null) return <div className="empty"><p className="muted">Loading the feed…</p></div>;

  return (
    <>
      {clip ? (
        <div className="feed">
          <div className="top">
            <span className="name">{clip.campaign_name}</span>
            <span className="row">
              <span className="chip" aria-label="Position in feed">{index + 1} of {clips.length}</span>
              <button className="chip" onClick={() => endSession()}>{t("stopHere")}</button>
            </span>
          </div>
          <ClipCard clip={clip} showPay={showPay} onAccept={accept} onReject={() => setSheet("reject")} onNext={next} onPrev={prev}
            onOpen={(which) => (which === "later" ? later() : setSheet(which))} />
        </div>
      ) : (
        <div className="empty">
          <h1>{config.killed ? "The kill switch is on" : "Nothing to review"}</h1>
          <p className="muted">{config.killed ? "The feed is paused until you switch it off in Settings." : "New clips appear here when the pipeline finishes them."}</p>
          <button className="btn" onClick={() => endSession()}>End session</button>
        </div>
      )}
      {message && (
        <div className="toast" role="alert" style={{ bottom: undo ? "calc(var(--tab-h) + 84px)" : undefined }}>
          <span>{message}</span>
          <button className="btn ghost" onClick={() => setMessage("")}>Dismiss</button>
        </div>
      )}
      {undo && <UndoToast seconds={config.undo} onUndo={doUndo} label={undo.review.queued ? `${t("rejected")} (saved offline)` : t("rejected")} />}
      {sheet === "reject" && <RejectSheet onSubmit={reject} onClose={() => setSheet(null)} />}
      {sheet === "hooks" && clip && <HooksSheet clip={clip} onClose={() => setSheet(null)} onPick={(hookId) => saveEdit({ hook_id: hookId })} />}
      {sheet === "edit" && clip && <EditSheet clip={clip} onClose={() => setSheet(null)} onSave={saveEdit} />}
      {sheet === "report" && clip && <ComplianceSheet clip={clip} onClose={() => setSheet(null)} />}
    </>
  );
}
