"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError, Clip, PLATFORM_LABEL } from "@/lib/api";

type ChecklistState = {
  clip: Clip;
  server: Record<string, { ok: boolean; label: string }>;
  owner_ticks: string[];
  warnings: string[];
  scheduler_has_disclosure_toggle: boolean;
  campaign_needs_reapproval: boolean;
};

const TICK_LABEL: Record<string, string> = {
  account_and_platform_correct: "I am posting from the right account, on the right platform",
  disclosure_toggle_on: "The paid-partnership toggle is on (set it by hand in the platform app)",
  caption_pasted: "The caption is pasted, starting with the disclosure",
  hashtags_and_tags_checked: "Hashtags, tags and mentions are checked",
};

export function Checklist({ clipId, onClose, onChanged }: { clipId: string; onClose: () => void; onChanged: () => void }) {
  const [state, setState] = useState<ChecklistState | null>(null);
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [url, setUrl] = useState("");
  const [toggle, setToggle] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [briefText, setBriefText] = useState("");

  const load = useCallback(() => api<ChecklistState>(`/api/clips/${clipId}/checklist`).then(setState).catch((e) => setError(e.message)), [clipId]);
  useEffect(() => { load(); }, [load]);

  const run = async (fn: () => Promise<unknown>, after?: () => void) => {
    setError("");
    try {
      await fn();
      after ? after() : await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No connection. Posting needs a connection.");
    }
  };

  if (!state) return <div className="scrim"><div className="sheet"><p className="muted">{error || "Loading…"}</p><button className="btn" onClick={onClose}>Close</button></div></div>;
  const clip = state.clip;
  const ready = clip.status === "ready_to_post" || clip.status === "queued";
  const serverOk = Object.values(state.server).every((item) => item.ok);
  const ticksOk = state.owner_ticks.every((name) => ticks[name]);
  const copy = (text: string) => navigator.clipboard?.writeText(text).catch(() => undefined);

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Publish checklist" onClick={(event) => event.stopPropagation()}>
        <div className="row between"><h1>Publish checklist</h1><button className="btn ghost" onClick={onClose}>Close</button></div>
        <p className="small muted">{clip.campaign_name} · {clip.account_handle ? `@${clip.account_handle}` : "no account"} · {clip.platform ? PLATFORM_LABEL[clip.platform] : "no platform"}</p>

        <div className="card stack">
          <div className="row between"><span className="label">Caption</span><button className="btn" onClick={() => copy(clip.caption_text ?? "")}>Copy caption</button></div>
          <p dir="auto">{clip.caption_text}</p>
          {clip.video_url && <a className="btn" href={clip.video_url} download>Download the approved video</a>}
        </div>

        {!ready && (
          <>
            <div>
              {Object.entries(state.server).map(([name, item]) => (
                <div className="check" key={name}>
                  <span className={`state ${item.ok ? "ok" : "no"}`} aria-hidden="true">{item.ok ? "✓" : "!"}</span>
                  <span>{item.label}<span className="sr-only">: {item.ok ? "done" : "not done"}</span></span>
                </div>
              ))}
              {state.owner_ticks.map((name) => (
                <label className="check" key={name}>
                  <input type="checkbox" checked={!!ticks[name]} onChange={(event) => setTicks({ ...ticks, [name]: event.target.checked })} />
                  <span>{TICK_LABEL[name] ?? name}</span>
                </label>
              ))}
            </div>
            {state.campaign_needs_reapproval && <div className="banner block">The brief changed. Re-approve the campaign on Home before posting.</div>}
            {!state.server.brief_rechecked.ok && !state.campaign_needs_reapproval && (
              <div className="card stack">
                <span className="label">Re-check the brief before this batch</span>
                <p className="small muted">Open the brief. If anything changed, paste its current text here. Any change blocks the batch until you re-approve.</p>
                <textarea className="input" placeholder="Paste the current brief text (optional)" value={briefText} onChange={(event) => setBriefText(event.target.value)} />
                <div className="row wrap">
                  <button className="btn" disabled={!briefText.trim()} onClick={() => run(() => api(`/api/campaigns/${clip.campaign_id}/recheck`, { body: { current_text: briefText } }))}>Compare pasted text</button>
                  <button className="btn" onClick={() => run(() => api(`/api/campaigns/${clip.campaign_id}/recheck`, { body: { unchanged_confirmed: true } }))}>I re-read it: unchanged</button>
                </div>
              </div>
            )}
            {state.warnings.map((warning) => <div className="banner warn" key={warning}>{warning}</div>)}
            <button className="btn primary block" disabled={!serverOk || !ticksOk}
              onClick={() => run(() => api(`/api/clips/${clipId}/publish-approve`, { body: { ticks } }), () => { load(); onChanged(); })}>
              Approve for publishing
            </button>
            <button className="btn ghost block" onClick={() => run(() => api(`/api/clips/${clipId}/back-to-later`, { body: {} }), () => { onChanged(); onClose(); })}>Move back to Review Later</button>
          </>
        )}

        {ready && (
          <div className="stack">
            <div className="banner">Approved for publishing. Post it yourself in the platform app, then record the link here. Nothing posts automatically.</div>
            <div className="field">
              <label htmlFor="post-url">Link to the post</label>
              <input id="post-url" className="input" inputMode="url" placeholder="https://" value={url} onChange={(event) => { setUrl(event.target.value); setConfirming(false); }} />
            </div>
            <label className="check">
              <input type="checkbox" checked={toggle} onChange={(event) => setToggle(event.target.checked)} />
              <span>The paid-partnership toggle is on for this post</span>
            </label>
            {!confirming ? (
              <button className="btn primary block" disabled={!/^https?:\/\/\S+/.test(url.trim()) || !toggle} onClick={() => setConfirming(true)}>Mark posted</button>
            ) : (
              <div className="card stack">
                <strong>Confirm: this clip is live at the link above?</strong>
                <div className="row">
                  <button className="btn primary grow" onClick={() => run(() => api(`/api/clips/${clipId}/posted`, { body: { post_url: url.trim(), confirm: true, disclosure_toggle_on: toggle } }), () => { onChanged(); onClose(); })}>Yes, mark posted</button>
                  <button className="btn grow" onClick={() => setConfirming(false)}>Not yet</button>
                </div>
              </div>
            )}
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </div>
  );
}

export default function Queue() {
  const [data, setData] = useState<{ kill_switch: boolean; items: Clip[] } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => api("/api/queue").then(setData).catch(() => setError("Could not load the Queue.")), []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="page">
      <div className="page-head"><h1>Queue</h1><span className="muted small">{data ? `${data.items.length} to post` : ""}</span></div>
      {data?.kill_switch && <div className="banner block" role="alert">The kill switch is on. Ready-to-post clips are hidden.</div>}
      {error && <p className="error">{error}</p>}
      {data && data.items.length === 0 && <div className="empty"><h1>Nothing to post</h1><p className="muted">Clips you accept in the Feed appear here, ordered by due time.</p></div>}
      {data?.items.map((clip) => (
        <button key={clip.id} className="card stack" style={{ textAlign: "start" }} onClick={() => setOpen(clip.id)}>
          <div className="row between">
            <strong>{clip.campaign_name}</strong>
            <span className={`badge ${clip.status === "accepted" ? "review" : "pass"}`}>{clip.status === "accepted" ? "checklist open" : clip.status === "queued" ? "scheduled" : "ready to post"}</span>
          </div>
          <span className="small muted">{clip.account_handle ? `@${clip.account_handle}` : "No account"} · {clip.platform ? PLATFORM_LABEL[clip.platform] : "No platform"} · due {clip.due_at ? new Date(clip.due_at).toLocaleDateString() : "any time"}</span>
          <span className="small" dir="auto">{clip.hook_text}</span>
        </button>
      ))}
      {open && <Checklist clipId={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
