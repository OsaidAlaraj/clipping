"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, Clip, ConnectionsData, money, PLATFORM_LABEL, reviewAction, uploadBase } from "@/lib/api";
import { plainAlert } from "@/lib/alertText";
import { clearCache, useData } from "@/lib/data";
import { useLang } from "@/lib/i18n";
import { clock, dialectLabel, percent, providerLabel } from "@/lib/transcript";
import { RejectSheet } from "./Feed";
import { DetailSkeleton, RowsSkeleton } from "./Skeleton";

/** A screen's data: the last copy at once if there is one, refreshed in the background. */
function useLoad<T>(path: string) {
  const result = useData<T>(path);
  const [error, setError] = useState("");
  useEffect(() => { if (result.error) setError(result.error instanceof ApiError ? result.error.message : "No connection."); }, [result.error]);
  useEffect(() => { if (result.data !== undefined) setError(""); }, [result.data]);
  const load = useCallback(() => result.reload(), [result.reload]);
  return { data: (result.data ?? null) as T | null, error, setError, load };
}

function Head({ title, back = "/profile" }: { title: string; back?: string }) {
  return <div className="page-head"><h1>{title}</h1><Link className="btn ghost" href={back}>Back</Link></div>;
}

export function MoreMenu() {
  const { t } = useLang();
  return (
    <div className="page">
      <div className="page-head"><h1>{t("more")}</h1></div>
      <nav className="card" style={{ padding: 0 }} aria-label="More">
        <Link className="list-link" href="/more/review-later">{t("reviewLater")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/ledger">{t("ledger")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/alerts">{t("alerts")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/settings">{t("settings")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/add">Add campaign<span aria-hidden="true">›</span></Link>
      </nav>
    </div>
  );
}

// ------------------------------------------------------------------ Review Later

type Folder = { campaign_id: string; campaign_name: string; deadline: string | null; closed: boolean; count: number; clips: Clip[] };

export function ReviewLater() {
  const { data, error, setError, load } = useLoad<{ folders: Folder[] }>("/api/review-later");
  const [rejecting, setRejecting] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } };
  return (
    <div className="page">
      <Head title="Review Later" />
      {error && <p className="error">{error}</p>}
      {data === null && !error && <RowsSkeleton />}
      {data?.folders.length === 0 && <p className="muted">Nothing here yet. Clips you save for later while reviewing collect here, grouped by campaign.</p>}
      {data?.folders.map((folder) => (
        <section key={folder.campaign_id} className="card stack" aria-label={folder.campaign_name} style={{ opacity: folder.closed ? 0.75 : 1 }}>
          <div className="row between">
            <strong>{folder.campaign_name}</strong>
            <span className="badge">{folder.count} clip{folder.count === 1 ? "" : "s"}{folder.closed ? " · closed" : ""}</span>
          </div>
          <span className="small muted">{folder.deadline ? `Deadline ${new Date(folder.deadline).toLocaleDateString()}` : "No deadline"}{folder.closed ? ". This campaign is closed, so these clips can no longer be accepted." : ""}</span>
          {folder.clips.map((clip) => (
            <div key={clip.id} className="later-row">
              {clip.video_url && <video src={clip.video_url} muted playsInline preload="metadata" />}
              <div className="stack" style={{ gap: 6 }}>
                <span dir="auto" style={{ fontWeight: 600 }}>{clip.hook_text}</span>
                <span className="small muted">Parked: {clip.park_reason ?? "no reason given"}</span>
                {!folder.closed && (
                  <div className="row">
                    <button className="btn primary grow" onClick={() => act(() => reviewAction("accept", clip.id))}>Accept</button>
                    <button className="btn danger grow" onClick={() => setRejecting(clip.id)}>Reject</button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </section>
      ))}
      {rejecting && <RejectSheet onClose={() => setRejecting(null)}
        onSubmit={(reason, notes) => { const id = rejecting; setRejecting(null); act(() => reviewAction("reject", id, { reason_code: reason, notes: notes || null })); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ Ledger

const NEXT_STATUS: Record<string, string[]> = {
  submitted: ["tracking", "rejected"], tracking: ["approved", "rejected", "capped", "budget_exhausted"],
  approved: ["held", "rejected", "capped", "budget_exhausted"], held: ["paid", "rejected"],
};

export function Ledger() {
  const { data, error, setError, load } = useLoad<any>("/api/ledger");
  const [views, setViews] = useState<Record<string, string>>({});
  const month = new Date().toISOString().slice(0, 7);
  const act = async (fn: () => Promise<unknown>) => { try { setError(""); await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } };
  const overall = data?.net_per_hour.overall;
  return (
    <div className="page">
      <Head title="Ledger" />
      {error && <p className="error" role="alert">{error}</p>}
      {data === null && !error && <RowsSkeleton />}
      {data && (
        <>
          <section className="card stack">
            <div className="row between"><h2>Net pay per hour</h2><span className="badge info">Confidence: {data.net_per_hour.confidence}</span></div>
            <div className="row between"><span className="muted">Paid</span><strong>{overall.paid_per_hour === null ? "—" : money(overall.paid_per_hour)} / h</strong></div>
            <div className="row between"><span className="muted">Paid plus pending</span><strong>{overall.with_pending_per_hour === null ? "—" : money(overall.with_pending_per_hour)} / h</strong></div>
            <div className="row between"><span className="muted">Weekly median</span><strong>{data.weekly.median_net_per_hour === null ? "—" : money(data.weekly.median_net_per_hour)} / h</strong></div>
            <div className="row between"><span className="muted">Go / iterate / stop</span><span className="badge">{data.weekly.decision}</span></div>
            <p className="small muted">{overall.hours} h logged · paid {money(overall.paid_usd)} · pending {money(overall.pending_usd)} · {data.net_per_hour.clips_clearing_threshold} of {data.net_per_hour.clips_posted} posted clips clear their view minimum. The go/stop thresholds are a framework from your own target, not market data.</p>
          </section>
          <a className="btn" href={`/api/export.csv?month=${month}`}>Export {month} as CSV</a>
          {data.rows.length === 0 && <p className="muted">No posts recorded yet.</p>}
          {data.rows.map((row: any) => (
            <section key={row.payout_id} className="card stack">
              <div className="row between"><strong>{row.campaign_name}</strong><span className="badge">{row.status}{row.disputed ? " · disputed" : ""}</span></div>
              <span className="small muted">@{row.account_handle} · {PLATFORM_LABEL[row.platform] ?? row.platform} · posted {new Date(row.posted_at).toLocaleDateString()} · <a href={row.post_url} target="_blank" rel="noreferrer">post</a></span>
              <table className="ledger"><tbody>
                <tr><th>Views (48 h / 7 d)</th><td>{row.views_h48 ?? "—"} / {row.views_d7 ?? "—"}{row.below_min_views ? " · below the minimum" : ""}</td></tr>
                <tr><th>Gross</th><td>{money(row.gross_usd)}</td></tr>
                <tr><th>Creator fee</th><td>{row.fee_usd === null ? "fee unknown" : money(row.fee_usd)}</td></tr>
                <tr><th>Withdrawal fee</th><td>{row.withdrawal_fee_usd === null ? "unknown" : money(row.withdrawal_fee_usd)}</td></tr>
                <tr><th>Net</th><td>{row.net_usd === null ? "—" : money(row.net_usd)}</td></tr>
              </tbody></table>
              <div className="row wrap">
                <input className="input" style={{ width: 130 }} inputMode="numeric" placeholder="Views" aria-label="Views" value={views[row.post_id] ?? ""} onChange={(event) => setViews({ ...views, [row.post_id]: event.target.value })} />
                {(["h48", "d7"] as const).map((window) => (
                  <button key={window} className="btn" disabled={!/^\d+$/.test(views[row.post_id] ?? "")}
                    onClick={() => act(() => api(`/api/posts/${row.post_id}/views`, { body: { window, views: Number(views[row.post_id]) } }))}>Log {window === "h48" ? "48 h" : "7 d"}</button>
                ))}
              </div>
              <div className="row wrap">
                {(NEXT_STATUS[row.status] ?? []).map((status) => (
                  <button key={status} className="btn" onClick={() => act(() => api(`/api/payouts/${row.payout_id}`, { body: { status } }))}>Mark {status.replace("_", " ")}</button>
                ))}
                {!row.disputed && <button className="btn ghost" onClick={() => act(() => api(`/api/payouts/${row.payout_id}`, { body: { disputed: true } }))}>Mark disputed</button>}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Alerts

export function Alerts() {
  const { data, error, load } = useLoad<{ alerts: any[] }>("/api/alerts");
  const act = async (path: string, body: object = {}) => { await api(path, { body }).catch(() => undefined); load(); };
  return (
    <div className="page">
      <Head title="Alerts" />
      {error && <p className="error">{error}</p>}
      {data === null && !error && <RowsSkeleton rows={2} />}
      {data?.alerts.length === 0 && <p className="muted">No open alerts.</p>}
      {data?.alerts.map((alert) => (
        <section key={alert.id} className="card stack">
          <div className="row between"><span className={`badge ${alert.severity}`}>{alert.severity}</span><span className="small muted">{new Date(alert.created_at).toLocaleString()}</span></div>
          <p>{plainAlert(alert.message).text}</p>
          {plainAlert(alert.message).text !== alert.message && <details className="small muted"><summary>Technical details</summary>{alert.message}</details>}
          <div className="row">
            <button className="btn grow" onClick={() => act(`/api/alerts/${alert.id}/ack`)}>Acknowledge</button>
            {alert.severity !== "block" && <button className="btn ghost grow" onClick={() => act(`/api/alerts/${alert.id}/snooze`, { hours: 4 })}>Snooze 4 h</button>}
          </div>
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Settings

export function bookmarkletCode(origin: string): string {
  return `javascript:(()=>{const t=(getSelection().toString()||document.body.innerText).slice(0,20000);`
    + `window.open('${origin}/new?url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title)+'&text='+encodeURIComponent(t),'_blank')})()`;
}

type AccuracyRow = { provider: string; dialect: string; wer: number; words: number };

/** How often each transcription provider got the words wrong, per dialect, measured from the owner's own corrections. */
export function AccuracySection() {
  const { data, error } = useLoad<AccuracyRow[]>("/api/transcripts/accuracy");
  return (
    <section className="card stack" aria-label="Transcript accuracy" aria-busy={data === null && !error}>
      <h2>Transcript accuracy</h2>
      <p className="small muted">Measured from the transcripts you fix: the share of words you had to change. Lower is better.</p>
      {data === null && !error && [0, 1].map((n) => <div key={n} className="row between"><i className="sk" style={{ width: "55%", height: 16 }} /><i className="sk" style={{ width: 64, height: 16 }} /></div>)}
      {error && <p className="small muted">Accuracy could not be loaded. {error}</p>}
      {data?.length === 0 && <p className="small muted">Fix a transcript and its accuracy appears here.</p>}
      {data?.map((row) => (
        <div key={`${row.provider}:${row.dialect}`} className="stack" style={{ gap: 6 }} data-testid="accuracy-row">
          <div className="row between">
            <span><strong>{providerLabel(row.provider)}</strong> <span className="muted" dir="auto">{dialectLabel(row.dialect)}</span></span>
            <strong>{percent(row.wer)}</strong>
          </div>
          <div className="bar" aria-hidden="true"><i className={row.wer > 0.15 ? "low" : undefined} style={{ width: `${Math.min(100, Math.round(row.wer * 100))}%` }} /></div>
          <span className="small muted">{row.words.toLocaleString()} words checked</span>
        </div>
      ))}
    </section>
  );
}

function SaveButtonSection() {
  const ref = useRef<HTMLAnchorElement>(null);
  useEffect(() => { ref.current?.setAttribute("href", bookmarkletCode(window.location.origin)); }, []);
  return (
    <section className="card stack" aria-label="Save-campaign button">
      <span className="label">Save-campaign button</span>
      <p className="small muted">Drag this button to your browser's bookmarks bar. On any campaign page, click it: the page's text opens here as a new campaign for you to check. It reads only the page you are looking at.</p>
      <a ref={ref} className="btn" draggable onClick={(e) => e.preventDefault()}>＋ Save to Clipping</a>
      <p className="small muted">On your phone, use the Share button and choose Clipping instead.</p>
    </section>
  );
}

function DeviceSection() {
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [dark, setDark] = useState(false);
  useEffect(() => { setDark(document.documentElement.dataset.theme === "dark"); }, []);
  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    if (next) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem("clipping_theme", next ? "dark" : "light"); } catch { /* storage unavailable */ }
  };
  const newCode = async () => {
    setError("");
    try { setCode((await api("/api/auth/device-code", { body: {} })).code); }
    catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); }
  };
  const signOut = async () => {
    await api("/api/auth/logout", { body: {} }).catch(() => undefined);
    clearCache();
    window.location.assign("/signin");
  };
  return (
    <section className="card stack" aria-label="Devices and appearance">
      <span className="label">Devices</span>
      <p className="small muted">To use the app on your phone: open its Tailscale address there, choose “Set up this device” and enter a code from here.</p>
      {code ? <p className="display" style={{ fontSize: "1.6rem", letterSpacing: "0.12em" }} aria-live="polite">{code}</p> : null}
      {code && <p className="small muted">Valid for 30 minutes, once.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row wrap">
        <button className="btn" onClick={newCode}>Add another device</button>
        <button className="btn ghost" onClick={signOut}>Sign out</button>
      </div>
      <span className="label">Appearance</span>
      <div className="row">
        <button className="btn grow" aria-pressed={!dark} onClick={() => dark && toggleTheme()}>Light</button>
        <button className="btn grow" aria-pressed={dark} onClick={() => !dark && toggleTheme()}>Dark</button>
      </div>
    </section>
  );
}

const SETTING_LABEL: Record<string, string> = {
  hours_cap_per_day: "Hours cap per day", session_length_min: "Session length (minutes)", undo_window_s: "Undo window after reject (seconds)",
  pay_per_clip_display: "Pay per clip display", posts_per_operator_per_day: "Posts per operator per day", posts_per_account_per_day: "Posts per account per day",
  clips_per_campaign_per_week: "Clips per campaign per week", budget_alert_threshold_pct: "Budget alert threshold (%)",
  health_alert_drop_pct: "Health alert: drop vs baseline (%)", source_retention_days: "Source footage retention (days)",
  clip_retention_days_after_close: "Transcript and clip retention after close (days)", priority_weight_net_pay: "Priority weight: net pay per hour",
  priority_weight_deadline: "Priority weight: deadline", priority_weight_hook_score: "Priority weight: hook score", priority_weight_budget: "Priority weight: remaining budget",
  priority_weight_account_cap: "Priority weight: account cap", priority_weight_compliance: "Priority weight: compliance",
  adapter_cap_usd_llm: "Monthly cap: LLM ($)", adapter_cap_usd_transcription: "Monthly cap: transcription ($)", adapter_cap_usd_moments: "Monthly cap: moment finding ($)",
  adapter_cap_usd_database: "Monthly cap: database ($)", adapter_alert_pct: "Adapter alert at (% of cap)", max_accounts_per_campaign_platform: "Accounts per campaign per platform",
  clip_min_duration_s: "Clip minimum length (seconds)", clip_max_duration_s: "Clip maximum length (seconds)",
};

export function Settings() {
  const { data, error, setError, load } = useLoad<{ settings: any[]; kill_switch: { active: boolean; reason: string | null } }>("/api/settings");
  const reference = useLoad<any>("/api/reference");
  const { lang, setLang } = useLang();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState("");
  const save = async (key: string, value: unknown) => {
    setError(""); setSaved("");
    try { await api(`/api/settings/${key}`, { method: "PUT", body: { value } }); setSaved(`${SETTING_LABEL[key] ?? key} saved.`); setDraft((d) => { const next = { ...d }; delete next[key]; return next; }); load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); }
  };
  const kill = async (active: boolean) => {
    try { await api("/api/kill-switch", { body: { active, reason: active ? "Switched on in Settings" : null } }); load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); }
  };
  return (
    <div className="page">
      <Head title="Settings" />
      {data && (
        <section className={`card stack`} style={{ borderColor: data.kill_switch.active ? "var(--danger)" : undefined }}>
          <div className="row between"><strong>Kill switch</strong><span className={`badge ${data.kill_switch.active ? "block" : "pass"}`}>{data.kill_switch.active ? "on" : "off"}</span></div>
          <p className="small muted">Stops new jobs and hides every ready-to-post package.</p>
          <button className={`btn ${data.kill_switch.active ? "primary" : "danger"} block`} onClick={() => kill(!data.kill_switch.active)}>{data.kill_switch.active ? "Switch off" : "Switch on now"}</button>
        </section>
      )}
      <DeviceSection />
      <SaveButtonSection />
      <AccuracySection />
      <section className="card stack">
        <span className="label">Interface language</span>
        <div className="row">
          <button className="btn grow" aria-pressed={lang === "en"} onClick={() => setLang("en")}>English</button>
          <button className="btn grow" aria-pressed={lang === "ar"} onClick={() => setLang("ar")}>العربية</button>
        </div>
        <p className="small muted">Arabic mirrors the layout right-to-left. Swipes keep their meaning: right accepts, left rejects.</p>
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      {saved && <p className="small" role="status">{saved}</p>}
      <p className="small muted">Settings never override the hard rules. Caps warn; compliance rules always block.</p>
      {data?.settings.filter((s) => SETTING_LABEL[s.key]).map((setting) => {
        const value = draft[setting.key] ?? String(setting.value);
        return (
          <div key={setting.key} className="field">
            <label htmlFor={setting.key}>{SETTING_LABEL[setting.key]}{setting.min !== null ? ` (${Number(setting.min)} to ${Number(setting.max)})` : ""}</label>
            {setting.allowed ? (
              <select id={setting.key} className="input" value={String(setting.value)} onChange={(event) => save(setting.key, event.target.value)}>
                {setting.allowed.map((option: string) => <option key={option} value={option}>{option === "card" ? "On each card" : option === "summary" ? "Summary only" : "Off"}</option>)}
              </select>
            ) : (
              <div className="row">
                <input id={setting.key} className="input" inputMode="decimal" value={value} onChange={(event) => setDraft({ ...draft, [setting.key]: event.target.value })} />
                <button className="btn" disabled={draft[setting.key] === undefined || draft[setting.key] === "" || Number.isNaN(Number(draft[setting.key]))} onClick={() => save(setting.key, Number(draft[setting.key]))}>Save</button>
              </div>
            )}
          </div>
        );
      })}
      {reference.data && (
        <section className="card stack">
          <h2>Adapters</h2>
          {reference.data.adapters.filter((a: any) => a.active).map((a: any) => (
            <div key={a.stage} className="row between"><span>{a.stage}</span><span className="small muted">{a.provider}{a.is_fake ? " (fake)" : ""}{a.paused_reason ? ` · paused: ${a.paused_reason}` : ""}</span></div>
          ))}
          <h2>Your accounts</h2>
          {reference.data.accounts.length === 0 && <p className="small muted">No accounts yet.</p>}
          {reference.data.accounts.map((a: any) => (
            <div key={a.id} className="row between"><span>@{a.handle}</span><span className="small muted">{PLATFORM_LABEL[a.platform] ?? a.platform} · {a.status}</span></div>
          ))}
        </section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Add campaign (manual paste) and campaign detail

function guessMarketplace(url: string): string | null {
  const host = (() => { try { return new URL(url).hostname; } catch { return ""; } })();
  const known: [string, string][] = [["contentrewards.com", "content_rewards"], ["whop.com", "content_rewards"], ["vyro.com", "vyro"], ["clipping.net", "clipping_net"],
    ["elev8or.io", "elev8or"], ["clipflip.io", "clipflip"], ["tryshoot.com", "shoot"], ["clipaffiliates.com", "clipaffiliates"], ["clipping.io", "clipping_io"],
    ["promote.fun", "promote_fun"], ["clipster.com", "clipster"], ["clipstake.com", "clipstake"], ["evangelist.so", "evangelist"]];
  return known.find(([h]) => host === h || host.endsWith(`.${h}`))?.[1] ?? null;
}

export function AddCampaign({ embedded = false, initial }: { embedded?: boolean; initial?: { name?: string; text?: string; url?: string } }) {
  const reference = useLoad<any>("/api/reference");
  const [form, setForm] = useState({ marketplace: (initial?.url && guessMarketplace(initial.url)) || "content_rewards", name: initial?.name ?? "",
    brief_text: initial?.text ?? "", url: initial?.url ?? "", category: "podcast", licence_ref: "" });
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const readScreenshot = async (file: File | undefined) => {
    if (!file) return;
    setReading(true); setError("");
    try {
      const data = new FormData();
      data.append("image", file);
      const response = await fetch(`${uploadBase()}/api/capture/screenshot`, { method: "POST", body: data, credentials: "include" });
      const body = await response.json();
      if (!response.ok) throw new ApiError(body.error ?? "Could not read the screenshot.", response.status);
      setForm((f) => ({ ...f, brief_text: [f.brief_text, body.text].filter(Boolean).join("\n\n") }));
    } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } finally { setReading(false); }
  };
  const submit = async () => {
    setError("");
    try {
      const result = await api("/api/campaigns", { body: { ...form, url: form.url || null, licence_ref: form.licence_ref || null } });
      window.location.href = `/campaign?id=${result.campaign.id}`;
    } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); }
  };
  const set = (key: string) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: event.target.value });
  return (
    <div className={embedded ? "stack" : "page"}>
      {!embedded && <Head title="Add campaign" back="/" />}
      <p className="small muted">Paste the brief. Rules are extracted as a draft with the exact sentence each one came from. You confirm before anything is produced.</p>
      <div className="field"><label htmlFor="c-name">Campaign name</label><input id="c-name" className="input" value={form.name} onChange={set("name")} /></div>
      <div className="row">
        <div className="field grow"><label htmlFor="c-market">Marketplace</label>
          <select id="c-market" className="input" value={form.marketplace} onChange={set("marketplace")}>{(reference.data?.marketplaces ?? []).map((m: any) => <option key={m.slug} value={m.slug}>{m.name}</option>)}</select></div>
        <div className="field grow"><label htmlFor="c-cat">Category</label>
          <select id="c-cat" className="input" value={form.category} onChange={set("category")}>{(reference.data?.categories ?? []).map((c: any) => <option key={c.name} value={c.name}>{c.name.replace("_", " ")}</option>)}</select></div>
      </div>
      <div className="field"><label htmlFor="c-url">Brief link (optional)</label><input id="c-url" className="input" inputMode="url" value={form.url} onChange={set("url")} /></div>
      <div className="field"><label htmlFor="c-brief">Brief text</label><textarea id="c-brief" className="input" style={{ minHeight: 220 }} dir="auto" value={form.brief_text} onChange={set("brief_text")} /></div>
      <div className="row wrap">
        <label htmlFor="c-shot" className="btn">{reading ? "Reading…" : "Read from a screenshot"}</label>
        <input id="c-shot" type="file" accept="image/*" className="sr-only" onChange={(e) => readScreenshot(e.target.files?.[0])} />
        <span className="small muted">The text is read into the box above for you to check.</span>
      </div>
      <div className="field"><label htmlFor="c-lic">Licensed-client reference (finance or crypto campaigns only)</label><input id="c-lic" className="input" value={form.licence_ref} onChange={set("licence_ref")} /></div>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn primary block" disabled={!form.name.trim() || !form.brief_text.trim()} onClick={submit}>Read the brief</button>
    </div>
  );
}

export function CampaignDetail({ id }: { id: string }) {
  const { data, error, setError, load } = useLoad<any>(`/api/campaigns/${id}`);
  const [reason, setReason] = useState("");
  const [source, setSource] = useState({ file_path: "", language: "en", permission_ref: "" });
  const [note, setNote] = useState("");
  const act = async (fn: () => Promise<unknown>, done?: string) => { setError(""); try { await fn(); setNote(done ?? ""); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } };
  if (!data) return error ? <div className="page"><Head title="Campaign" back="/" /><p className="muted">{error}</p></div> : <DetailSkeleton />;
  const { campaign, version } = data;
  const rules = version.rules_json;
  const payoutQuotes = rules.payout?.quotes ?? {};
  return (
    <div className="page">
      <Head title={campaign.name} back="/" />
      <div className="row wrap">
        <span className="badge">{campaign.status.replace("_", " ")}</span>
        <span className={`badge ${version.compliance_status}`}>rules: {version.compliance_status}</span>
        <span className="badge">version {version.version_no}</span>
      </div>
      {campaign.status_reason && <div className={`banner ${campaign.status === "rejected" ? "block" : "warn"}`}>{campaign.status_reason}</div>}
      {version.hard_blocks.map((block: any) => (
        <div key={block.code} className="banner block">{block.reason}<blockquote dir="auto">{block.quote}</blockquote></div>
      ))}
      {version.unclear_fields.length > 0 && <div className="banner warn">Unclear in the brief: {version.unclear_fields.join(", ")}. An unclear campaign is never marked as passing.</div>}
      <section className="card stack">
        <h2>Payout</h2>
        {[["Model", rules.payout.model, "model"], ["Rate per 1,000 views", rules.payout.rate_per_1k !== null ? money(rules.payout.rate_per_1k) : null, "rate_per_1k"],
          ["Minimum views", rules.payout.min_views_to_qualify, "min_views_to_qualify"], ["Maximum per clip", rules.payout.max_payout_per_clip !== null ? money(rules.payout.max_payout_per_clip) : null, "max_payout_per_clip"],
          ["Budget", rules.payout.budget_total !== null ? money(rules.payout.budget_total) : null, "budget_total"]].map(([label, value, key]) => (
          <div key={key as string}><div className="row between"><span className="muted">{label}</span><strong>{value ?? "unclear"}</strong></div>{payoutQuotes[key as string] && <blockquote dir="auto">{payoutQuotes[key as string]}</blockquote>}</div>
        ))}
        <div className="row between"><span className="muted">Creator fee</span><strong>{version.fee_pct === null ? "fee unknown" : `${Number(version.fee_pct)}%`}</strong></div>
        <div className="row between"><span className="muted">Platforms</span><strong>{rules.platforms_allowed.map((p: string) => PLATFORM_LABEL[p] ?? p).join(", ") || "unclear"}</strong></div>
        <div className="row between"><span className="muted">Music</span><strong>{rules.music_policy.replaceAll("_", " ")}</strong></div>
      </section>
      <section className="card stack">
        <h2>Rules, each with its source sentence</h2>
        {rules.content_rules.length === 0 && <p className="small muted">No content rules were found in the brief.</p>}
        {rules.content_rules.map((rule: any, index: number) => (
          <div key={index}><div className="row between"><span dir="auto">{rule.text}</span><span className={`badge ${rule.requirement === "unclear" ? "review" : ""}`}>{rule.requirement}</span></div>
            {rule.quote ? <blockquote dir="auto">{rule.quote}</blockquote> : <p className="small muted">No source sentence: stored as unclear.</p>}</div>
        ))}
      </section>
      {data.sources.length > 0 && (
        <section className="card stack" aria-label="Footage">
          <h2>Footage</h2>
          {data.sources.map((source: any, n: number) => (
            <div key={source.id} className="row between wrap">
              <span className="stack" style={{ gap: 0, minWidth: 0 }}>
                <strong>Footage {n + 1}</strong>
                <span className="small muted">{source.language === "ar" ? "Arabic" : "English"}{source.duration_s ? ` · ${clock(Number(source.duration_s))}` : ""}</span>
              </span>
              <Link className="btn" href={`/transcript?id=${source.id}`}>Fix transcript</Link>
            </div>
          ))}
        </section>
      )}
      <p className="small muted">This is a draft extracted by Agent A. The system flags and records; it does not certify legal compliance. You decide.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="small" role="status">{note}</p>}
      {(campaign.status === "pending_approval" || campaign.status === "paused") && (
        <div className="stack">
          <button className="btn primary block" onClick={() => act(() => api(`/api/campaigns/${id}/approve`, { method: "POST" }), "Approved.")}>Confirm rules and approve</button>
          {campaign.status === "pending_approval" && <button className="btn block" onClick={() => act(() => api(`/api/campaigns/${id}/park`, { method: "POST" }), "Parked.")}>Park</button>}
          <div className="row"><input className="input" aria-label="Reason for rejecting" placeholder="Reason for rejecting" value={reason} onChange={(event) => setReason(event.target.value)} />
            <button className="btn danger" disabled={!reason.trim()} onClick={() => act(() => api(`/api/campaigns/${id}/reject`, { body: { reason } }), "Rejected.")}>Reject</button></div>
        </div>
      )}
      {campaign.status === "approved" && (
        <section className="card stack">
          <h2>Add permitted footage</h2>
          <p className="small muted">Only footage you have a licence or written permission for. The file is read from this laptop.</p>
          <div className="field"><label htmlFor="s-path">File path on this laptop</label><input id="s-path" className="input" value={source.file_path} onChange={(e) => setSource({ ...source, file_path: e.target.value })} /></div>
          <div className="row">
            <div className="field"><label htmlFor="s-lang">Language</label><select id="s-lang" className="input" value={source.language} onChange={(e) => setSource({ ...source, language: e.target.value })}><option value="en">English</option><option value="ar">Arabic</option></select></div>
            <div className="field grow"><label htmlFor="s-perm">Permission or licence reference</label><input id="s-perm" className="input" value={source.permission_ref} onChange={(e) => setSource({ ...source, permission_ref: e.target.value })} /></div>
          </div>
          <button className="btn block" disabled={!source.file_path.trim() || !source.permission_ref.trim()} onClick={() => act(() => api(`/api/campaigns/${id}/sources`, { body: source }), "Footage added. Clips appear in the Feed when the pipeline finishes.")}>Add footage and start</button>
          <span className="small muted">{data.sources.length} source file{data.sources.length === 1 ? "" : "s"} so far.</span>
          <Link className="btn primary block" href={`/review?campaign=${id}`}>Open this campaign's feed</Link>
        </section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Connected accounts (shown in Profile)

const CONNECT_TEXT = {
  en: {
    title: "Connected accounts", intro: "Connect an account so a post can go out through its official API after your confirm tap. Your passwords are never asked for or stored.",
    connect: "Connect", reconnect: "Reconnect", disconnect: "Disconnect", confirmDisconnect: "Yes, disconnect", cancel: "Cancel", none: "Not connected",
    connected: "Connected", expiring: "Expiring soon", needs_reconnect: "Needs reconnect", noVault: "Connecting is off until TOKEN_VAULT_KEY is set in .env (see .env.example). Then restart.",
    justConnected: "Connected.", offline: "No connection.", loadFailed: "Could not load your connections.", busy: "Working…",
    platform: { youtube_shorts: "YouTube", instagram_reels: "Instagram", tiktok: "TikTok" },
  },
  ar: {
    title: "الحسابات المربوطة", intro: "اربط حساباً ليُنشر المقطع عبر الواجهة الرسمية للمنصّة بعد أن تضغط على تأكيد. لا نطلب كلمات مرورك ولا نخزّنها.",
    connect: "اربط", reconnect: "أعد الربط", disconnect: "افصل", confirmDisconnect: "نعم، افصل", cancel: "إلغاء", none: "غير مربوط",
    connected: "متصل", expiring: "ينتهي قريباً", needs_reconnect: "يحتاج إعادة ربط", noVault: "الربط متوقف حتى تضبط TOKEN_VAULT_KEY في ملف .env (انظر .env.example) ثم تعيد التشغيل.",
    justConnected: "تم الربط.", offline: "لا يوجد اتصال.", loadFailed: "تعذّر تحميل الحسابات المربوطة.", busy: "جارٍ العمل…",
    platform: { youtube_shorts: "يوتيوب", instagram_reels: "إنستغرام", tiktok: "تيك توك" },
  },
} as const;

const STATE_BADGE: Record<string, string> = { connected: "pass", expiring: "review", needs_reconnect: "fail" };

/** Each platform's connections with their state, a Connect button and a two-step Disconnect. Tokens never reach this screen. */
export function Connections() {
  const { lang, rtl } = useLang();
  const tx = CONNECT_TEXT[lang];
  const { data, error, setError, load } = useLoad<ConnectionsData>("/api/connections");
  const [busy, setBusy] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
    if (params.get("connect_error")) setError(params.get("connect_error") as string);
    else if (params.get("connected")) setNote(tx.justConnected);
  }, [setError, tx.justConnected]);

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : tx.offline);
  const connect = async (platform: string, accountId?: string) => {
    setBusy(platform); setError(""); setNote("");
    try { window.location.assign((await api<{ url: string }>(`/api/connections/${platform}/start`, { body: { account_id: accountId ?? null } })).url); }
    catch (e) { fail(e); setBusy(null); }
  };
  const disconnect = async (id: string) => {
    setBusy(id); setError(""); setAsking(null);
    try { await api(`/api/connections/${id}/disconnect`, { body: {} }); load(); } catch (e) { fail(e); } finally { setBusy(null); }
  };

  return (
    <section className="stack" aria-label={tx.title} dir={rtl ? "rtl" : "ltr"}>
      <h2>{tx.title}</h2>
      <p className="small muted">{tx.intro}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="small" role="status">{note}</p>}
      {data && !data.vault_ready && <p className="small muted" role="status">{tx.noVault}</p>}
      {data?.platforms.map(({ platform }) => {
        const mine = data.connections.filter((c) => c.platform === platform);
        return (
          <div key={platform} className="card stack" aria-label={tx.platform[platform as keyof typeof tx.platform]}>
            <div className="row between"><strong>{tx.platform[platform as keyof typeof tx.platform]}</strong>
              {mine.length === 0 && <span className="small muted">{tx.none}</span>}</div>
            {mine.map((c) => (
              <div key={c.id} className="row between wrap" data-testid={`connection-${c.id}`}>
                <span dir="ltr">@{c.handle}</span>
                <span className="row">
                  <span className={`badge ${STATE_BADGE[c.state] ?? ""}`}>{tx[c.state as "connected" | "expiring" | "needs_reconnect"]}</span>
                  {c.state === "needs_reconnect" && <button className="btn" disabled={!data.vault_ready || busy === platform} onClick={() => connect(platform, c.account_id)}>{tx.reconnect}</button>}
                  {asking === c.id
                    ? <><button className="btn danger" disabled={busy === c.id} onClick={() => disconnect(c.id)}>{tx.confirmDisconnect}</button><button className="btn ghost" onClick={() => setAsking(null)}>{tx.cancel}</button></>
                    : <button className="btn ghost" disabled={busy === c.id} onClick={() => setAsking(c.id)}>{tx.disconnect}</button>}
                </span>
              </div>
            ))}
            <button className="btn primary" disabled={!data.vault_ready || busy === platform} onClick={() => connect(platform)}>
              {busy === platform ? tx.busy : `${tx.connect} ${tx.platform[platform as keyof typeof tx.platform]}`}</button>
          </div>
        );
      })}
    </section>
  );
}
