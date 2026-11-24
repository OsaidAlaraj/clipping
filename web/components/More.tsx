"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, Clip, ConnectionsData, money, PLATFORM_LABEL, reviewAction, uploadBase } from "@/lib/api";
import { plainAlert } from "@/lib/alertText";
import { clearCache, useData } from "@/lib/data";
import { useLang } from "@/lib/i18n";
import { clock, dialectLabel, percent, providerLabel } from "@/lib/transcript";
import { setSoundOn, useSound } from "@/lib/sound";
import { RejectSheet } from "./Feed";
import { DetailSkeleton, RowsSkeleton } from "./Skeleton";
import { useAdd } from "./Add";
import { Empty, PageHead, UiIcon } from "./ui";

/** A screen's data: the last copy at once if there is one, refreshed in the background. */
function useLoad<T>(path: string) {
  const result = useData<T>(path);
  const [error, setError] = useState("");
  useEffect(() => { if (result.error) setError(result.error instanceof ApiError ? result.error.message : "No connection."); }, [result.error]);
  useEffect(() => { if (result.data !== undefined) setError(""); }, [result.data]);
  const load = useCallback(() => result.reload(), [result.reload]);
  return { data: (result.data ?? null) as T | null, error, setError, load };
}

export function Head({ title, back = "/profile" }: { title: string; back?: string }) {
  return <PageHead title={title} back={back} />;
}

type Folder = { campaign_id: string; campaign_name: string; deadline: string | null; closed: boolean; count: number; clips: Clip[] };

export function ReviewLater() {
  const { tr } = useLang();
  const { data, error, setError, load } = useLoad<{ folders: Folder[] }>("/api/review-later");
  const soundOn = useSound(); // owner note 11: the same sound choice as the feed
  const [rejecting, setRejecting] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } };
  return (
    <div className="page">
      <Head title={tr("Review later", "للمراجعة لاحقاً")} />
      {error && <p className="error">{error}</p>}
      {data === null && !error && <RowsSkeleton />}
      {data?.folders.length === 0 && <Empty icon={UiIcon.video} title={tr("Nothing saved for later", "لا شيء محفوظ لاحقاً")} body={tr("Clips you set aside while reviewing collect here, grouped by campaign.", "المقاطع التي تؤجّلها أثناء المراجعة تُجمع هنا حسب الحملة.")} />}
      {data?.folders.map((folder) => (
        <section key={folder.campaign_id} className="card stack" aria-label={folder.campaign_name} style={{ opacity: folder.closed ? 0.75 : 1 }}>
          <div className="row between">
            <strong>{folder.campaign_name}</strong>
            <span className="badge">{folder.count} clip{folder.count === 1 ? "" : "s"}{folder.closed ? " · closed" : ""}</span>
          </div>
          <span className="small muted">{folder.deadline ? `Deadline ${new Date(folder.deadline).toLocaleDateString()}` : "No deadline"}{folder.closed ? ". This campaign is closed, so these clips can no longer be accepted." : ""}</span>
          {folder.clips.map((clip) => (
            <div key={clip.id} className="later-row">
              {clip.video_url && <video src={clip.video_url} muted={!soundOn} controls playsInline preload="metadata" />}
              <div className="stack" style={{ gap: 6 }}>
                <span dir="auto" style={{ fontWeight: 600 }}>{clip.hook_text}</span>
                {clip.video_url && <button className="chip" style={{ alignSelf: "start" }} aria-pressed={soundOn} onClick={() => setSoundOn(!soundOn)}>{soundOn ? "Mute" : "Sound on"}</button>}
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
  const { tr } = useLang();
  const { data, error, setError, load } = useLoad<any>("/api/ledger");
  const [views, setViews] = useState<Record<string, string>>({});
  const month = new Date().toISOString().slice(0, 7);
  const act = async (fn: () => Promise<unknown>) => { try { setError(""); await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } };
  const overall = data?.net_per_hour.overall;
  return (
    <div className="page">
      <Head title={tr("Ledger", "السجل المالي")} />
      {error && <p className="error" role="alert">{error}</p>}
      {data === null && !error && <RowsSkeleton />}
      {data && (
        <>
          <section className="group" aria-label={tr("Net pay per hour", "صافي الأجر في الساعة")}>
            <div className="item" style={{ display: "grid", gap: 4 }}>
              <span className="eyebrow">{tr("Net pay per hour, paid and pending", "صافي الأجر في الساعة، المدفوع والمعلّق")}</span>
              <span className="money xl">{overall.with_pending_per_hour === null ? "—" : money(overall.with_pending_per_hour)}</span>
              <span className="small muted">{tr("Confidence", "درجة الثقة")}: {data.net_per_hour.confidence} · {overall.hours} h · {tr("paid", "مدفوع")} {money(overall.paid_usd)} · {tr("pending", "معلّق")} {money(overall.pending_usd)}</span>
            </div>
            <div className="stats">
              <div><span className="num">{overall.paid_per_hour === null ? "—" : money(overall.paid_per_hour)}</span><span className="lab">{tr("Paid / h", "المدفوع / س")}</span></div>
              <div><span className="num">{data.weekly.median_net_per_hour === null ? "—" : money(data.weekly.median_net_per_hour)}</span><span className="lab">{tr("Weekly median", "وسيط الأسبوع")}</span></div>
              <div><span className="num">{data.net_per_hour.clips_clearing_threshold}/{data.net_per_hour.clips_posted}</span><span className="lab">{tr("Clips over minimum", "مقاطع تجاوزت الحدّ")}</span></div>
            </div>
          </section>
          <a className="btn" href={`/api/export.csv?month=${month}`}>Export {month} as CSV</a>
          {data.rows.length === 0 && <Empty icon={UiIcon.flag} title={tr("No posts recorded yet", "لا منشورات مسجّلة بعد")} body={tr("When you post an accepted clip, its views and payout appear here.", "عندما تنشر مقطعاً مقبولاً تظهر مشاهداته ودفعته هنا.")} />}
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
  const { tr } = useLang();
  const { data, error, load } = useLoad<{ alerts: any[] }>("/api/alerts");
  const act = async (path: string, body: object = {}) => { await api(path, { body }).catch(() => undefined); load(); };
  const level = (severity: string) => (severity === "block" ? tr("Needs you", "يحتاجك") : severity === "warn" ? tr("Heads up", "تنبيه") : tr("Info", "معلومة"));
  return (
    <div className="page">
      <PageHead title={tr("Alerts", "التنبيهات")} back="/" />
      {error && <p className="error">{error}</p>}
      {data === null && !error && <RowsSkeleton rows={2} />}
      {data?.alerts.length === 0 && <Empty icon={UiIcon.flag} title={tr("All clear", "كل شيء تمام")} body={tr("Nothing needs you right now. New problems show up here and as a dot on the bell.", "لا شيء يحتاجك الآن. المشاكل الجديدة تظهر هنا وكنقطة على الجرس.")} />}
      {data && data.alerts.length > 0 && (
        <div className="group">
          {data.alerts.map((alert) => {
            const plain = plainAlert(alert.message);
            return (
              <div key={alert.id} className="item" style={{ display: "grid", gap: 8 }}>
                <div className="row between"><span className={`badge ${alert.severity}`}>{level(alert.severity)}</span><span className="small muted">{new Date(alert.created_at).toLocaleString()}</span></div>
                <p dir="auto">{plain.text}</p>
                {plain.text !== alert.message && <details className="small muted"><summary>{tr("Technical details", "تفاصيل تقنية")}</summary>{alert.message}</details>}
                <div className="row">
                  <button className="btn grow" onClick={() => act(`/api/alerts/${alert.id}/ack`)}>{tr("Got it", "تمام")}</button>
                  {alert.severity !== "block" && <button className="btn ghost grow" onClick={() => act(`/api/alerts/${alert.id}/snooze`, { hours: 4 })}>{tr("Snooze 4 h", "أجّل 4 ساعات")}</button>}
                </div>
              </div>
            );
          })}
        </div>
      )}
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

const SETTING_HELP: Record<string, string> = {
  hours_cap_per_day: "The most you plan to work on clips in one day.", session_length_min: "How long one review session lasts before a break.",
  undo_window_s: "How long you can undo a reject.", pay_per_clip_display: "Where the pay per clip is shown while you review.",
  posts_per_operator_per_day: "The most posts you make in a day, across all accounts.", posts_per_account_per_day: "The most posts one account makes in a day.",
  clips_per_campaign_per_week: "The most clips made for one campaign each week.", max_accounts_per_campaign_platform: "How many of your accounts may post the same campaign on one platform.",
  clip_min_duration_s: "Shorter moments are not made into clips.", clip_max_duration_s: "Longer moments are trimmed or skipped.",
  adapter_cap_usd_llm: "Monthly limit for the AI that writes hooks and rates moments.", adapter_cap_usd_transcription: "Monthly limit for turning speech into text.",
  adapter_cap_usd_moments: "Monthly limit for finding moments.", adapter_cap_usd_database: "Monthly limit for the online database.",
  adapter_alert_pct: "You get a warning when a service has used this share of its limit.", budget_alert_threshold_pct: "You get a warning when a campaign has this share of its budget left.",
  health_alert_drop_pct: "You get a warning when an account's views fall this far below its usual.",
  source_retention_days: "How long original videos are kept before they are deleted.", clip_retention_days_after_close: "How long clips and transcripts are kept after a campaign closes.",
  priority_weight_net_pay: "How much pay per hour pushes a clip up the review order.", priority_weight_deadline: "How much a near deadline pushes a clip up.",
  priority_weight_hook_score: "How much a strong hook pushes a clip up.", priority_weight_budget: "How much remaining budget pushes a clip up.",
  priority_weight_account_cap: "How much free room on the account pushes a clip up.", priority_weight_compliance: "How much a clean rules check pushes a clip up.",
};
const SETTING_GROUPS: { title: string; hint: string; keys: string[]; advanced?: boolean }[] = [
  { title: "Your day", hint: "How long you work and how review feels.", keys: ["hours_cap_per_day", "session_length_min", "undo_window_s", "pay_per_clip_display"] },
  { title: "Posting and clips", hint: "How much is made and posted.", keys: ["posts_per_operator_per_day", "posts_per_account_per_day", "clips_per_campaign_per_week", "max_accounts_per_campaign_platform", "clip_min_duration_s", "clip_max_duration_s"] },
  { title: "Money limits and warnings", hint: "Monthly limits for paid services and when to be warned.", keys: ["adapter_cap_usd_llm", "adapter_cap_usd_transcription", "adapter_cap_usd_moments", "adapter_cap_usd_database", "adapter_alert_pct", "budget_alert_threshold_pct", "health_alert_drop_pct"] },
  { title: "Keep and delete", hint: "How long files are kept.", keys: ["source_retention_days", "clip_retention_days_after_close"] },
  { title: "Review order", hint: "What puts a clip first. Most people never change these.", advanced: true,
    keys: ["priority_weight_net_pay", "priority_weight_deadline", "priority_weight_hook_score", "priority_weight_budget", "priority_weight_account_cap", "priority_weight_compliance"] },
];
/** What always stops a clip, however the settings are set (read-only). */
const ALWAYS_BLOCKED = ["A caption that does not start with the #ad disclosure", "No #ad label on screen for the whole clip", "Words the campaign bans",
  "A clip outside the allowed length or not vertical (9:16)", "A campaign you have not approved"];

export function Settings() {
  const { data, error, setError, load } = useLoad<{ settings: any[]; kill_switch: { active: boolean; reason: string | null } }>("/api/settings");
  const reference = useLoad<any>("/api/reference");
  const { lang, setLang, t } = useLang();
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
  const control = (setting: any) => {
    const value = draft[setting.key] ?? String(setting.value);
    return (
      <div key={setting.key} className="field">
        <label htmlFor={setting.key}>{SETTING_LABEL[setting.key]}{setting.min !== null ? ` (${Number(setting.min)} to ${Number(setting.max)})` : ""}</label>
        {SETTING_HELP[setting.key] && <span className="small muted" id={`${setting.key}-help`}>{SETTING_HELP[setting.key]}</span>}
        {setting.allowed ? (
          <select id={setting.key} className="input" aria-describedby={`${setting.key}-help`} value={String(setting.value)} onChange={(event) => save(setting.key, event.target.value)}>
            {setting.allowed.map((option: string) => <option key={option} value={option}>{option === "card" ? "On each card" : option === "summary" ? "Summary only" : "Off"}</option>)}
          </select>
        ) : (
          <div className="row">
            <input id={setting.key} className="input" aria-describedby={`${setting.key}-help`} inputMode="decimal" value={value} onChange={(event) => setDraft({ ...draft, [setting.key]: event.target.value })} />
            <button className="btn" disabled={draft[setting.key] === undefined || draft[setting.key] === "" || Number.isNaN(Number(draft[setting.key]))} onClick={() => save(setting.key, Number(draft[setting.key]))}>Save</button>
          </div>
        )}
      </div>
    );
  };
  const byKey = new Map((data?.settings ?? []).map((s) => [s.key, s]));
  return (
    <div className="page">
      <Head title={t("settings")} />
      {error && <p className="error" role="alert">{error}</p>}
      {saved && <p className="small" role="status">{saved}</p>}
      {SETTING_GROUPS.map((group) => {
        const items = group.keys.map((k) => byKey.get(k)).filter((x) => x && SETTING_LABEL[x.key]);
        if (!items.length) return null;
        const body = <div className="stack" style={{ gap: 14 }}>{items.map(control)}</div>;
        return (
          <section key={group.title} className="card stack" aria-label={group.title}>
            {group.advanced ? (
              <details>
                <summary><strong>{group.title}</strong> <span className="small muted">{group.hint}</span></summary>
                <div style={{ marginTop: 12 }}>{body}</div>
              </details>
            ) : (<><div className="stack" style={{ gap: 2 }}><h2>{group.title}</h2><p className="small muted">{group.hint}</p></div>{body}</>)}
          </section>
        );
      })}
      <section className="card stack" aria-label="This device">
        <div className="stack" style={{ gap: 2 }}><h2>This device</h2><p className="small muted">Language, theme and how you save clips.</p></div>
        <span className="label">Interface language</span>
        <div className="row">
          <button className="btn grow" aria-pressed={lang === "en"} onClick={() => setLang("en")}>English</button>
          <button className="btn grow" aria-pressed={lang === "ar"} onClick={() => setLang("ar")}>العربية</button>
        </div>
        <p className="small muted">Arabic mirrors the layout right to left. Swipe right accepts, swipe left rejects.</p>
      </section>
      <DeviceSection />
      <SaveButtonSection />
      <AccuracySection />
      <section className="card stack" aria-label="Always blocked">
        <div className="stack" style={{ gap: 2 }}><h2>Always blocked</h2><p className="small muted">These stop a clip whatever you set above.</p></div>
        <ul className="stack small" style={{ gap: 4, paddingInlineStart: 18, margin: 0 }}>{ALWAYS_BLOCKED.map((rule) => <li key={rule}>{rule}</li>)}</ul>
      </section>
      {data && (
        <section className="card stack" aria-label="Emergency stop" style={{ borderColor: data.kill_switch.active ? "var(--danger)" : undefined }}>
          <div className="row between"><h2>Emergency stop</h2><span className={`badge ${data.kill_switch.active ? "block" : "pass"}`}>{data.kill_switch.active ? "on" : "off"}</span></div>
          <p className="small muted">Stops new jobs and hides every ready-to-post package.</p>
          <button className={`btn ${data.kill_switch.active ? "primary" : "danger"} block`} onClick={() => kill(!data.kill_switch.active)}>{data.kill_switch.active ? "Switch off" : "Switch on now"}</button>
        </section>
      )}
      {reference.data && (
        <section className="card stack" aria-label="Services and accounts">
          <h2>Services</h2>
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

export function CampaignDetail({ id }: { id: string }) {
  const { tr } = useLang();
  const add = useAdd();
  const { data, error, setError, load } = useLoad<any>(`/api/campaigns/${id}`);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const act = async (fn: () => Promise<unknown>, done?: string) => { setError(""); try { await fn(); setNote(done ?? ""); load(); } catch (e) { setError(e instanceof ApiError ? e.message : tr("No connection.", "لا يوجد اتصال.")); } };
  if (!data) return error ? <div className="page"><PageHead title={tr("Campaign", "الحملة")} back="/" /><p className="muted">{error}</p></div> : <DetailSkeleton />;
  const { campaign, version } = data;
  const rules = version.rules_json;
  const payoutQuotes = rules.payout?.quotes ?? {};
  const adOn = campaign.ad_label !== false;
  const adRequired = Boolean(rules.disclosure?.required_by_brief);
  const approved = campaign.status === "approved";
  const open = campaign.status === "pending_approval" || campaign.status === "paused";
  const payout: [string, React.ReactNode, string][] = [
    [tr("Model", "نموذج الدفع"), rules.payout.model, "model"],
    [tr("Rate per 1,000 views", "السعر لكل 1000 مشاهدة"), rules.payout.rate_per_1k !== null ? money(rules.payout.rate_per_1k) : null, "rate_per_1k"],
    [tr("Minimum views", "الحدّ الأدنى للمشاهدات"), rules.payout.min_views_to_qualify, "min_views_to_qualify"],
    [tr("Maximum per clip", "الحد الأقصى للمقطع"), rules.payout.max_payout_per_clip !== null ? money(rules.payout.max_payout_per_clip) : null, "max_payout_per_clip"],
    [tr("Budget", "الميزانية"), rules.payout.budget_total !== null ? money(rules.payout.budget_total) : null, "budget_total"],
  ];
  return (
    <div className="page stack-lg enter">
      <PageHead title={campaign.name} back="/" />
      <div className="row wrap">
        <span className="badge">{campaign.status.replace("_", " ")}</span>
        <span className={`badge ${version.compliance_status}`}>{tr("rules", "القواعد")}: {version.compliance_status}</span>
        <span className="badge">{tr("version", "نسخة")} {version.version_no}</span>
      </div>
      {campaign.status_reason && <div className={`banner ${campaign.status === "rejected" ? "block" : "warn"}`}>{campaign.status_reason}</div>}
      {version.hard_blocks.map((block: any) => (
        <div key={block.code} className="banner block">{block.reason}<blockquote dir="auto">{block.quote}</blockquote></div>
      ))}
      {version.unclear_fields.length > 0 && <div className="banner warn">{tr("Unclear in the brief", "غير واضح في البريف")}: {version.unclear_fields.join(", ")}. {tr("An unclear campaign is never marked as passing.", "الحملة غير الواضحة لا تُعتبر ناجحة أبداً.")}</div>}

      {approved && (
        <div className="stack" style={{ gap: 8 }}>
          <Link className="btn primary block" href={`/review?campaign=${id}`}>{tr("Review this campaign's clips", "راجع مقاطع هذه الحملة")}</Link>
          <button className="btn block" onClick={() => add.open({ kind: "video", campaignId: id })}>{tr("Add footage", "أضف فيديو")}</button>
        </div>
      )}
      {open && (
        <div className="stack">
          <button className="btn primary block" onClick={() => act(() => api(`/api/campaigns/${id}/approve`, { method: "POST" }), tr("Approved.", "تمت الموافقة."))}>{tr("Confirm rules and approve", "أكّد القواعد ووافق")}</button>
          {campaign.status === "pending_approval" && <button className="btn block" onClick={() => act(() => api(`/api/campaigns/${id}/park`, { method: "POST" }), tr("Parked.", "تم التأجيل."))}>{tr("Park", "أجّل")}</button>}
          <div className="row"><input className="input" aria-label={tr("Reason for rejecting", "سبب الرفض")} placeholder={tr("Reason for rejecting", "سبب الرفض")} value={reason} onChange={(event) => setReason(event.target.value)} />
            <button className="btn danger" disabled={!reason.trim()} onClick={() => act(() => api(`/api/campaigns/${id}/reject`, { body: { reason } }), tr("Rejected.", "تم الرفض."))}>{tr("Reject", "ارفض")}</button></div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="small" role="status">{note}</p>}

      <section className="section">
        <h2>{tr("Payout", "الدفع")}</h2>
        <div className="group">
          {payout.map(([label, value, key]) => (
            <div key={key} className="item" style={{ display: "grid", gap: 4 }}>
              <div className="row between"><span className="muted">{label}</span><strong>{value ?? tr("unclear", "غير واضح")}</strong></div>
              {payoutQuotes[key] && <blockquote dir="auto">{payoutQuotes[key]}</blockquote>}
            </div>
          ))}
          <div className="item"><span className="muted grow">{tr("Creator fee", "عمولة المنصّة")}</span><strong>{version.fee_pct === null ? tr("fee unknown", "العمولة غير معروفة") : `${Number(version.fee_pct)}%`}</strong></div>
          <div className="item"><span className="muted grow">{tr("Platforms", "المنصّات")}</span><strong>{rules.platforms_allowed.map((p: string) => PLATFORM_LABEL[p] ?? p).join(", ") || tr("unclear", "غير واضح")}</strong></div>
          <div className="item"><span className="muted grow">{tr("Music", "الموسيقى")}</span><strong>{rules.music_policy.replaceAll("_", " ")}</strong></div>
        </div>
      </section>

      <section className="section">
        <h2>{tr("Rules, each with its source sentence", "القواعد، كل واحدة مع جملتها الأصلية")}</h2>
        {rules.content_rules.length === 0 ? <p className="small muted">{tr("No content rules were found in the brief.", "لم تُوجد قواعد للمحتوى في البريف.")}</p> : (
          <div className="group">
            {rules.content_rules.map((rule: any, index: number) => (
              <div key={index} className="item" style={{ display: "grid", gap: 6 }}>
                <div className="row between"><span dir="auto">{rule.text}</span><span className={`badge ${rule.requirement === "unclear" ? "review" : ""}`}>{rule.requirement}</span></div>
                {rule.quote ? <blockquote dir="auto">{rule.quote}</blockquote> : <p className="small muted">{tr("No source sentence: stored as unclear.", "لا توجد جملة أصلية: تُحفظ كغير واضحة.")}</p>}
              </div>
            ))}
          </div>
        )}
      </section>

      {data.sources.length > 0 && (
        <section className="section" aria-label="Footage">
          <h2>{tr("Footage", "الفيديوهات")}</h2>
          <div className="group">
            {data.sources.map((source: any, n: number) => (
              <Link key={source.id} className="item" href={`/transcript?id=${source.id}`}>
                <span className="grow"><span className="title">{tr("Footage", "فيديو")} {n + 1}</span>
                  <span className="note">{source.language === "ar" ? tr("Arabic", "عربي") : tr("English", "إنجليزي")}{source.duration_s ? ` · ${clock(Number(source.duration_s))}` : ""}</span></span>
                <span className="small">{tr("Fix transcript", "صحّح النص")}</span><span className="flip chev">{UiIcon.chev}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="section" aria-label="Ad label">
        <h2>{tr("Ad label", "وسم الإعلان")}</h2>
        <div className="group"><div className="item">
          <span className="grow"><span className="title">{adOn ? tr("On", "مفعّل") : tr("Off", "معطّل")}</span>
            <span className="note" style={{ whiteSpace: "normal" }}>{adRequired ? tr("The brief requires the label, so it stays on.", "البريف يطلب الوسم، لذلك يبقى مفعّلاً.") : tr("Turn it off only if this campaign does not need the label.", "عطّله فقط إذا كانت الحملة لا تحتاج الوسم.")}</span></span>
          <button className="btn" disabled={adOn && adRequired}
            onClick={() => act(() => api(`/api/campaigns/${id}/ad-label`, { body: { on: !adOn } }), adOn ? tr("Ad label off for this campaign.", "تم إيقاف الوسم لهذه الحملة.") : tr("Ad label on.", "تم تفعيل الوسم."))}>
            {adOn ? tr("Turn off", "عطّل") : tr("Turn on", "فعّل")}
          </button>
        </div></div>
      </section>
      <p className="small muted">{tr("This is a draft extracted by Agent A. The system flags and records; it does not certify legal compliance. You decide.", "هذه مسودّة استخرجها الوكيل A. النظام ينبّه ويسجّل ولا يشهد بالامتثال القانوني. القرار لك.")}</p>
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
