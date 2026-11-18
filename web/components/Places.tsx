"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, money, PLATFORM_LABEL, uploadBase } from "@/lib/api";
import { useLang } from "@/lib/i18n";
import { Card } from "./Home";
import { AccountLook } from "./Look";
import { AddCampaign } from "./More";

// ---------------------------------------------------------------- New: add a campaign or a video

export type Approved = { id: string; name: string; practice?: boolean; language?: string | null };

// The server takes up to 10 GB. Past 1.5 GB a phone upload often drops, so say so before it starts.
export const MAX_UPLOAD_BYTES = 10 * 1024 ** 3;
export const LARGE_UPLOAD_BYTES = 1.5 * 1024 ** 3;
const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GB`;
const PRACTICE_PERMISSION = "Practice footage (the owner's own test video)";

export function uploadProblem(file: File | null): { block: boolean; text: string } | null {
  if (!file) return null;
  if (file.size > MAX_UPLOAD_BYTES) return { block: true, text: `This video is ${gb(file.size)}. The most the system takes is 10 GB. Trim it, or send it as a link instead.` };
  if (file.size > LARGE_UPLOAD_BYTES) return { block: false, text: `This is a large video (${gb(file.size)}). Keep this screen open until it finishes. If it stops, send it as a link instead.` };
  return null;
}

export function UploadVideo({ campaigns, initialId }: { campaigns: Approved[]; initialId?: string }) {
  const [campaignId, setCampaignId] = useState(initialId ?? campaigns[0]?.id ?? "");
  const [mode, setMode] = useState<"file" | "link">("file");
  const [file, setFile] = useState<File | null>(null);
  const [link, setLink] = useState("");
  const [language, setLanguage] = useState("en");
  const [permission, setPermission] = useState("");
  const [youtubeOk, setYoutubeOk] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [over, setOver] = useState(false);
  const [doneFor, setDoneFor] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const isYoutube = /youtu\.?be/i.test(link);
  const chosen = campaigns.find((c) => c.id === campaignId);
  const practice = !!chosen?.practice;
  const problem = mode === "file" ? uploadProblem(file) : null;

  useEffect(() => { if (!campaignId && campaigns[0]) setCampaignId(campaigns[0].id); }, [campaigns, campaignId]);
  useEffect(() => { if (initialId) setCampaignId(initialId); }, [initialId]);
  useEffect(() => { if (chosen?.practice && chosen.language) setLanguage(chosen.language); }, [chosen]);

  const ready = campaignId && (practice || permission.trim()) && !problem?.block
    && (mode === "file" ? !!file : /^https?:\/\//i.test(link.trim()) && (!isYoutube || youtubeOk));

  const sendFile = () => new Promise<void>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file as File);
    form.append("language", language);
    form.append("permission_ref", practice ? "" : permission.trim());
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${uploadBase()}/api/campaigns/${campaignId}/sources/upload`);
    xhr.withCredentials = true;   // the session cookie also applies on the API's own port
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) setProgress(Math.round((event.loaded / event.total) * 100)); };
    xhr.onload = () => {
      if (xhr.status < 300) resolve();
      else {
        let msg = `Upload failed (${xhr.status}).`;
        try { msg = JSON.parse(xhr.responseText).error ?? msg; } catch { /* not JSON */ }
        reject(new ApiError(msg, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError(file && file.size > LARGE_UPLOAD_BYTES
      ? `The upload of this ${gb(file.size)} video stopped. Big videos often drop on a phone: send it as a link instead, or try again on Wi-Fi.`
      : "No connection. Uploading needs a connection.", 0));
    xhr.send(form);
  });

  const submit = async () => {
    setError(""); setMessage("");
    try {
      if (mode === "file") {
        setProgress(0);
        await sendFile();
      } else {
        await api(`/api/campaigns/${campaignId}/sources/link`, { body: { url: link.trim(), language, permission_ref: practice ? PRACTICE_PERMISSION : permission.trim(), youtube_permitted: youtubeOk } });
      }
      setMessage(mode === "file" ? "Uploaded. Clips appear in Review when they are ready." : "Fetching the video. Clips appear in Review when they are ready.");
      setDoneFor(campaignId);
      setFile(null); setLink(""); setProgress(null);
    } catch (e) {
      setProgress(null);
      setError(e instanceof ApiError ? e.message : "That did not work.");
    }
  };

  if (campaigns.length === 0) return <p className="muted">Approve a campaign first. Footage is always added to an approved campaign.</p>;

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="field">
        <label htmlFor="v-campaign">Campaign</label>
        <select id="v-campaign" className="input" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
          {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {practice && <span className="small muted">Practice: for trying the system. Nothing is posted and no money is involved.</span>}
      </div>
      <div className="segmented" role="tablist" aria-label="Source">
        <button role="tab" aria-selected={mode === "file"} onClick={() => setMode("file")}>From this device</button>
        <button role="tab" aria-selected={mode === "link"} onClick={() => setMode("link")}>From a link</button>
      </div>
      {mode === "file" ? (
        <div className={`dropzone${over ? " over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) setFile(f); }}>
          <input ref={input} id="v-file" type="file" accept="video/*" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p style={{ fontWeight: 600 }}>{file ? file.name : "Drop a video here"}</p>
          <p className="small muted">{file ? `${(file.size / 1e6).toFixed(1)} MB` : "or choose one from your phone or computer"}</p>
          <label htmlFor="v-file" className="btn" style={{ marginTop: 12 }}>Choose video</label>
        </div>
      ) : (
        <div className="stack">
          <div className="field">
            <label htmlFor="v-link">Video link</label>
            <input id="v-link" className="input" inputMode="url" placeholder="Google Drive, Dropbox or a direct link" value={link} onChange={(e) => setLink(e.target.value)} />
          </div>
          {isYoutube && (
            <div className="banner warn stack">
              <span>YouTube's terms forbid downloading videos except through YouTube's own features. Only continue if this campaign's brief explicitly lets you clip this content, and you accept that risk.</span>
              <label className="check"><input type="checkbox" checked={youtubeOk} onChange={(e) => setYoutubeOk(e.target.checked)} /><span>The brief explicitly permits clipping this video</span></label>
            </div>
          )}
        </div>
      )}
      <div className="row wrap">
        <div className="field grow">
          <label htmlFor="v-lang">Language</label>
          <select id="v-lang" className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="en">English</option><option value="ar">Arabic</option>
          </select>
        </div>
        {!practice && (
          <div className="field" style={{ flex: "2 1 220px" }}>
            <label htmlFor="v-perm">Permission or licence reference</label>
            <input id="v-perm" className="input" placeholder="e.g. footage link from the brief" value={permission} onChange={(e) => setPermission(e.target.value)} />
          </div>
        )}
      </div>
      {progress !== null && <div className="progress-line" role="progressbar" aria-label="Upload progress" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${progress}%` }} /></div>}
      {problem && <div className={`banner ${problem.block ? "block" : "warn"}`} role={problem.block ? "alert" : "status"}>{problem.text}</div>}
      {error && <p className="error" role="alert">{error}</p>}
      {message && (
        <div className="banner ok stack" role="status" style={{ gap: 8 }}>
          <span>{message}</span>
          {doneFor && <Link className="btn" href={`/review?campaign=${doneFor}`}>Open Review</Link>}
        </div>
      )}
      <button className="btn primary block" disabled={!ready || progress !== null} onClick={submit}>{mode === "file" ? "Upload and start" : "Fetch and start"}</button>
    </div>
  );
}

const PRACTICE_NICHES = ["podcast", "gaming", "lifestyle", "reddit_story"];

/** One tap: a practice campaign for trying the system on your own video. No marketplace, no money, nothing is posted. */
export function PracticeForm({ onCreated }: { onCreated: (campaign: Approved) => void }) {
  const [name, setName] = useState("My first video");
  const [niche, setNiche] = useState("podcast");
  const [language, setLanguage] = useState<"en" | "ar">("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const create = async () => {
    setBusy(true); setError("");
    try {
      const made = await api("/api/campaigns/practice", { body: { name: name.trim(), niche, language } });
      onCreated({ id: made.id, name: made.name, practice: true, language });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No connection. Try again when you are back online.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <p className="small muted">A practice campaign has no brief and no money. Upload any video to see the clips the system makes. Nothing is posted, and posting is switched off for it.</p>
      <div className="field">
        <label htmlFor="p-name">Name</label>
        <input id="p-name" className="input" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="row wrap">
        <div className="field grow">
          <label htmlFor="p-niche">Niche</label>
          <select id="p-niche" className="input" value={niche} onChange={(e) => setNiche(e.target.value)}>
            {PRACTICE_NICHES.map((n) => <option key={n} value={n}>{n.replace("_", " ")}</option>)}
          </select>
        </div>
        <div className="field grow">
          <label htmlFor="p-lang">Language of the video</label>
          <select id="p-lang" className="input" value={language} onChange={(e) => setLanguage(e.target.value as "en" | "ar")}>
            <option value="en">English</option><option value="ar">Arabic</option>
          </select>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn primary block" disabled={busy || !name.trim()} onClick={create}>{busy ? "Creating…" : "Create practice campaign"}</button>
    </div>
  );
}

export function NewPlace() {
  const { t } = useLang();
  const [choice, setChoice] = useState<"campaign" | "video" | "practice">("campaign");
  const [justMade, setJustMade] = useState<string | undefined>();
  const [approved, setApproved] = useState<Approved[]>([]);
  const [shared, setShared] = useState<{ name?: string; text?: string; url?: string } | null>(null);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const text = p.get("text") ?? "", url = p.get("url") ?? "", title = p.get("title") ?? "";
    const link = url || (text.match(/https?:\/\/\S+/)?.[0] ?? "");
    setShared(text || url || title ? { name: title, text, url: link } : {});
  }, []);
  useEffect(() => {
    api("/api/home").then((home) => {
      const fetched: Approved[] = home.campaigns.filter((c: Card) => c.status === "approved")
        .map((c: Card) => ({ id: c.id, name: c.name, practice: !!c.is_practice, language: c.language ?? null }));
      // A practice campaign made a moment ago may not be in a slow answer yet: keep it.
      setApproved((list) => [...list.filter((c) => !fetched.some((f) => f.id === c.id)), ...fetched]);
    }).catch(() => undefined);
  }, []);
  const practiceCreated = (campaign: Approved) => {   // straight to the upload form, no waiting for a reload
    setApproved((list) => [campaign, ...list.filter((c) => c.id !== campaign.id)]);
    setJustMade(campaign.id);
    setChoice("video");
  };
  return (
    <div className="page" style={{ maxWidth: 720 }}>
      <h1>{t("create")}</h1>
      <div className="segmented" role="tablist" aria-label="What to add">
        <button role="tab" aria-selected={choice === "campaign"} onClick={() => setChoice("campaign")}>{t("addCampaign")}</button>
        <button role="tab" aria-selected={choice === "video"} onClick={() => setChoice("video")}>{t("addVideo")}</button>
        <button role="tab" aria-selected={choice === "practice"} onClick={() => setChoice("practice")}>Practice</button>
      </div>
      {choice === "campaign" && shared && <AddCampaign key={JSON.stringify(shared)} embedded initial={shared} />}
      {choice === "video" && <UploadVideo campaigns={approved} initialId={justMade} />}
      {choice === "practice" && <PracticeForm onCreated={practiceCreated} />}
    </div>
  );
}

// ---------------------------------------------------------------- Profile: accounts, queue, money, saved, settings

type Account = {
  id: string; platform: string; handle: string; status: string; status_note: string | null; niche: string | null; language?: string | null;
  audience_tier1_pct?: number | null; audience_countries?: Record<string, number> | null; audience_source?: string | null; audience_updated_at?: string | null;
};
const LANGUAGES: Record<string, string> = { ar: "Arabic", en: "English" };
const SOURCE_LABEL: Record<string, string> = { manual: "typed in", api: "from the platform", screenshot: "from a screenshot", estimate: "estimated" };
const WHERE_TO_LOOK: Record<string, string> = {
  tiktok: "TikTok Studio > Analytics > Viewers", instagram_reels: "Instagram > Professional dashboard > Total followers",
  youtube_shorts: "YouTube Studio > Analytics > Audience",
};

function AudienceField({ account, onSaved }: { account: Account; onSaved: () => void }) {
  const [value, setValue] = useState(account.audience_tier1_pct === null || account.audience_tier1_pct === undefined ? "" : String(account.audience_tier1_pct));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "No connection.");
  const save = async (body: object) => {
    setError("");
    try { await api(`/api/accounts/${account.id}/audience`, { method: "PUT", body }); onSaved(); } catch (e) { fail(e); }
  };
  const readScreenshot = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const data = new FormData();
      data.append("image", file);
      const response = await fetch(`${uploadBase()}/api/accounts/${account.id}/audience/screenshot`, { method: "POST", body: data, credentials: "include" });
      const body = await response.json();
      if (!response.ok) throw new ApiError(body.error ?? "Could not read the screenshot.", response.status);
      setValue(String(body.audience_tier1_pct)); onSaved();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const fromPlatform = async () => {
    setBusy(true); setError("");
    try { const row = await api(`/api/accounts/${account.id}/audience/refresh`, { body: {} }); setValue(String(row.audience_tier1_pct)); onSaved(); }
    catch (e) { fail(e); } finally { setBusy(false); }
  };
  const top = Object.entries(account.audience_countries ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 4);
  return (
    <div className="stack small" style={{ gap: 6 }}>
      <div className="row wrap" style={{ gap: 6 }}>
        <label htmlFor={`lang-${account.id}`} className="muted">Posts in</label>
        <select id={`lang-${account.id}`} className="input" style={{ width: "auto", minHeight: 36, padding: "4px 10px" }} value={account.language ?? ""}
          onChange={(e) => save({ language: e.target.value || null, tier1_pct: account.audience_tier1_pct ?? null })}>
          <option value="">Not set</option>
          {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label htmlFor={`t1-${account.id}`} className="muted">US/UK/CA/AU audience %</label>
        <input id={`t1-${account.id}`} className="input" style={{ width: 80, minHeight: 36, padding: "4px 10px" }} inputMode="numeric" placeholder="?"
          value={value} onChange={(e) => setValue(e.target.value)} onBlur={() => save({ tier1_pct: value.trim() === "" ? null : Number(value) })} />
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <label className="btn ghost" style={{ minHeight: 36 }}>
          {busy ? "Reading…" : "Read from a screenshot"}
          <input type="file" accept="image/*" className="sr-only" aria-label={`Audience screenshot for @${account.handle}`} onChange={(e) => readScreenshot(e.target.files?.[0])} />
        </label>
        {account.platform !== "tiktok" && <button className="btn ghost" style={{ minHeight: 36 }} disabled={busy} onClick={fromPlatform}>Read from the platform</button>}
      </div>
      <span className="muted">
        {account.audience_source ? `Audience ${SOURCE_LABEL[account.audience_source] ?? account.audience_source}` : `Screenshot: ${WHERE_TO_LOOK[account.platform] ?? "the platform's audience page"}`}
        {top.length > 0 && ` · ${top.map(([c, v]) => `${c} ${Math.round(v)}%`).join(", ")}`}
      </span>
      {error && <span className="error" role="alert">{error}</span>}
    </div>
  );
}

export function Profile() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [form, setForm] = useState({ platform: "tiktok", handle: "", niche: "podcast", language: "" });
  const [error, setError] = useState("");
  const [net, setNet] = useState<any>(null);

  const load = useCallback(() => {
    api("/api/reference").then((ref) => { setAccounts(ref.accounts); setCategories(ref.categories.map((c: any) => c.name)); }).catch(() => setError("Could not load."));
    api("/api/home").then((home) => setNet(home.net_per_hour)).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    setError("");
    try {
      await api("/api/accounts", { body: { platform: form.platform, handle: form.handle.trim(), niche: form.niche, language: form.language || null } });
      setForm({ ...form, handle: "" });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That did not work.");
    }
  };

  const byNiche = accounts.reduce<Record<string, Account[]>>((acc, a) => { (acc[a.niche ?? "any niche"] ||= []).push(a); return acc; }, {});

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <h1>{t("profile")}</h1>
      {net && (
        <section className="card row wrap" aria-label="Your numbers" style={{ gap: 24 }}>
          <div className="stack" style={{ gap: 0 }}><span className="big-num">{net.this_week.with_pending_per_hour === null ? "—" : money(net.this_week.with_pending_per_hour)}</span><span className="small muted">Net per hour this week</span></div>
          <div className="stack" style={{ gap: 0 }}><span className="big-num">{money(net.month_to_date.paid_usd)}</span><span className="small muted">Paid this month</span></div>
          <span className="badge">Confidence: {net.confidence}</span>
        </section>
      )}
      <nav className="card" style={{ padding: 0 }} aria-label="Your places">
        <Link className="list-link" href="/queue">Posting queue<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/review-later">{t("reviewLater")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/ledger">{t("ledger")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/alerts">{t("alerts")}<span aria-hidden="true">›</span></Link>
        <Link className="list-link" href="/more/settings">{t("settings")}<span aria-hidden="true">›</span></Link>
      </nav>
      <section className="stack" aria-label="Your accounts">
        <h2>Your accounts</h2>
        <p className="small muted">One account per platform for each niche. The system never stores passwords, only the handle. Set the language each account posts in, and its US/UK/Canada/Australia audience share: many campaigns only pay for those views, and the ranking uses both. A screenshot of the platform's audience page is enough.</p>
        {Object.entries(byNiche).map(([niche, list]) => (
          <div key={niche} className="card stack">
            <strong style={{ textTransform: "capitalize" }}>{niche.replace("_", " ")}</strong>
            {list.map((a) => (
              <div key={a.id} className="stack" style={{ gap: 4, paddingBottom: 8, borderBottom: "1px solid var(--surface-2)" }}>
                <div className="row between">
                  <span>@{a.handle}</span>
                  <span className="row small"><span className="muted">{PLATFORM_LABEL[a.platform] ?? a.platform}</span><span className={`badge ${a.status === "active" ? "pass" : "review"}`}>{a.status}</span></span>
                </div>
                <AudienceField account={a} onSaved={load} />
                <AccountLook accountId={a.id} />
              </div>
            ))}
          </div>
        ))}
        <div className="card stack">
          <strong>Add an account</strong>
          <div className="row wrap">
            <div className="field grow"><label htmlFor="a-platform">Platform</label>
              <select id="a-platform" className="input" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
                {["tiktok", "instagram_reels", "youtube_shorts"].map((p) => <option key={p} value={p}>{PLATFORM_LABEL[p]}</option>)}
              </select></div>
            <div className="field grow"><label htmlFor="a-niche">Niche</label>
              <select id="a-niche" className="input" value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })}>
                {(categories.length ? categories : ["podcast"]).map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
              </select></div>
            <div className="field grow"><label htmlFor="a-lang">Posts in</label>
              <select id="a-lang" className="input" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
                <option value="">Not set</option>
                {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></div>
            <div className="field grow"><label htmlFor="a-handle">Handle</label>
              <input id="a-handle" className="input" placeholder="@yourhandle" value={form.handle} onChange={(e) => setForm({ ...form, handle: e.target.value })} /></div>
          </div>
          <p className="small muted">Before adding: switch the account to a professional (Creator or Business) account so the paid-partnership label is available, and link it to the marketplace.</p>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn primary" disabled={!form.handle.trim()} onClick={add}>Add account</button>
        </div>
      </section>
    </div>
  );
}
