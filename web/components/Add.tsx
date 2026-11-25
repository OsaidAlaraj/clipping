"use client";

import dynamic from "next/dynamic";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, ApiError, uploadBase } from "@/lib/api";
import { fetchData, useData } from "@/lib/data";
import { useLang } from "@/lib/i18n";
import type { Card } from "./Home";
import { pasteToMarkdown } from "@/lib/richPaste";
import type { Spec } from "@/lib/briefSpec";
import { BriefReview } from "./BriefReview";
import { PracticeForm, UploadVideo, type Approved } from "./Places";
import { Empty, PageHead, Sheet, UiIcon } from "./ui";

/** ONE place to add things. A video (file, link or YouTube) or a campaign (brief, link, screenshot, or a practice one).
 *  The same AddFlow runs in the sheet that the middle button opens from any screen, and full page at /new (the phone share target).
 *  Nothing else in the app has its own form for these jobs. */

type Kind = "video" | "campaign";
type Opts = { kind?: Kind; campaignId?: string };
const AddContext = createContext<{ open: (opts?: Opts) => void }>({ open: () => undefined });
export const useAdd = () => useContext(AddContext);

export function AddProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<Opts | null>(null);
  const path = usePathname();
  const { tr } = useLang();
  const open = useCallback((next?: Opts) => setOpts(next ?? {}), []);
  useEffect(() => setOpts(null), [path]);   // moving to another screen closes it
  const value = useMemo(() => ({ open }), [open]);
  return (
    <AddContext.Provider value={value}>
      {children}
      {opts && (
        <Sheet title={tr("Add", "إضافة")} onClose={() => setOpts(null)}>
          <AddFlow {...opts} />
        </Sheet>
      )}
    </AddContext.Provider>
  );
}

function guessMarketplace(url: string): string | null {
  const host = (() => { try { return new URL(url).hostname; } catch { return ""; } })();
  const known: [string, string][] = [["contentrewards.com", "content_rewards"], ["whop.com", "content_rewards"], ["vyro.com", "vyro"], ["clipping.net", "clipping_net"],
    ["elev8or.io", "elev8or"], ["clipflip.io", "clipflip"], ["tryshoot.com", "shoot"], ["clipaffiliates.com", "clipaffiliates"], ["clipping.io", "clipping_io"],
    ["promote.fun", "promote_fun"], ["clipster.com", "clipster"], ["clipstake.com", "clipstake"], ["evangelist.so", "evangelist"]];
  return known.find(([h]) => host === h || host.endsWith(`.${h}`))?.[1] ?? null;
}

type Shared = { name?: string; text?: string; url?: string };

/** Add a campaign: the brief as text, a link or a screenshot. The rules are read as a draft; nothing is produced before you confirm. */
export function AddCampaign({ initial, onCreated }: { initial?: Shared; onCreated?: (campaignId: string) => void }) {
  const { tr } = useLang();
  const reference = useData<any>("/api/reference").data;
  const [form, setForm] = useState({ marketplace: (initial?.url && guessMarketplace(initial.url)) || "content_rewards", name: initial?.name ?? "",
    brief_text: initial?.text ?? "", url: initial?.url ?? "", category: "podcast", licence_ref: "" });
  const [more, setMore] = useState(!!initial?.url);
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { lang } = useLang();
  const [spec, setSpec] = useState<Spec | null>(null);
  const [linksKept, setLinksKept] = useState(0);
  // Rich paste: HTML on the clipboard (Notion, Docs, Discord, a marketplace page) becomes Markdown that keeps its links.
  const onPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const html = event.clipboardData.getData("text/html");
    if (!html) return;
    const converted = pasteToMarkdown(html, event.clipboardData.getData("text/plain"));
    if (!converted.rich) return;
    event.preventDefault();
    const box = event.currentTarget;
    const [from, to] = [box.selectionStart, box.selectionEnd];
    setForm((f) => ({ ...f, brief_text: f.brief_text.slice(0, from) + converted.text + f.brief_text.slice(to) }));
    setLinksKept((n) => n + converted.links);
  };
  const readScreenshot = async (file: File | undefined) => {
    if (!file) return;
    setReading(true); setError("");
    try {
      const data = new FormData();
      data.append("image", file);
      const response = await fetch(`${uploadBase()}/api/capture/screenshot`, { method: "POST", body: data, credentials: "include" });
      const body = await response.json();
      if (!response.ok) throw new ApiError(body.error ?? tr("Could not read the screenshot.", "تعذّرت قراءة الصورة."), response.status);
      setForm((f) => ({ ...f, brief_text: [f.brief_text, body.text].filter(Boolean).join("\n\n") }));
    } catch (e) { setError(e instanceof ApiError ? e.message : tr("No connection.", "لا يوجد اتصال.")); } finally { setReading(false); }
  };
  const submit = async () => {   // read the brief into a spec for you to check; nothing is created yet
    setError(""); setBusy(true);
    try {
      const read = await api<Spec>("/api/campaigns/spec", { body: { brief_text: form.brief_text } });
      setSpec(read);
      const title = read.items.find((i) => i.key === "identity.name" && i.value);
      if (!form.name.trim() && title) setForm((f) => ({ ...f, name: String(title.value) }));
    } catch (e) { setError(e instanceof ApiError ? e.message : tr("No connection.", "لا يوجد اتصال.")); } finally { setBusy(false); }
  };
  const pasteLink = async (url: string, text: string) => {
    if (!spec) return;
    setError("");
    try { setSpec(await api<Spec>("/api/campaigns/spec/paste", { body: { spec, url, text } })); }
    catch (e) { setError(e instanceof ApiError ? e.message : tr("No connection.", "لا يوجد اتصال.")); }
  };
  const confirm = async () => {
    setError(""); setBusy(true);
    try {
      const result = await api("/api/campaigns", { body: { ...form, url: form.url || null, licence_ref: form.licence_ref || null, spec } });
      if (onCreated) onCreated(result.campaign.id); else window.location.assign(`/campaign?id=${result.campaign.id}`);
    } catch (e) { setError(e instanceof ApiError ? e.message : tr("No connection.", "لا يوجد اتصال.")); } finally { setBusy(false); }
  };
  const set = (key: string) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: event.target.value });
  if (spec) {
    return (
      <div className="stack" style={{ gap: 14 }}>
        <button className="btn ghost" onClick={() => setSpec(null)}>{tr("Back to the text", "رجوع إلى النص")}</button>
        <BriefReview spec={spec} onChange={setSpec} onPasteLink={pasteLink} onConfirm={confirm} busy={busy} error={error} lang={lang} linksKept={linksKept} />
      </div>
    );
  }
  return (
    <div className="stack" style={{ gap: 14 }}>
      <p className="small muted">{tr("Paste the brief. The rules are read as a draft, each with the sentence it came from. You confirm before anything is made.", "الصق البريف. تُقرأ القواعد كمسودّة، كل قاعدة مع الجملة التي أخذت منها. تؤكّد أنت قبل أن يُنتَج أي شيء.")}</p>
      <div className="field"><label htmlFor="c-name">{tr("Campaign name", "اسم الحملة")}</label><input id="c-name" className="input" dir="auto" value={form.name} onChange={set("name")} /></div>
      <div className="field"><label htmlFor="c-brief">{tr("Brief text", "نص البريف")}</label><textarea id="c-brief" className="input" style={{ minHeight: 160 }} dir="auto" value={form.brief_text} onChange={set("brief_text")} onPaste={onPaste} />
        {linksKept > 0 && <span className="small muted" role="status">{linksKept} {linksKept === 1 ? tr("link kept", "رابط محفوظ") : tr("links kept", "روابط محفوظة")}</span>}</div>
      <div className="row wrap">
        <label htmlFor="c-shot" className="btn">{reading ? tr("Reading…", "جارٍ القراءة…") : tr("Read from a screenshot", "اقرأ من لقطة شاشة")}</label>
        <input id="c-shot" type="file" accept="image/*" className="sr-only" onChange={(e) => readScreenshot(e.target.files?.[0])} />
        <button type="button" className="btn ghost" aria-expanded={more} onClick={() => setMore((m) => !m)}>{more ? tr("Fewer details", "تفاصيل أقل") : tr("Marketplace and link", "المنصّة والرابط")}</button>
      </div>
      {more && (
        <div className="stack" style={{ gap: 14 }}>
          <div className="row">
            <div className="field grow"><label htmlFor="c-market">{tr("Marketplace", "المنصّة")}</label>
              <select id="c-market" className="input" value={form.marketplace} onChange={set("marketplace")}>{(reference?.marketplaces ?? []).map((m: any) => <option key={m.slug} value={m.slug}>{m.name}</option>)}</select></div>
            <div className="field grow"><label htmlFor="c-cat">{tr("Category", "التصنيف")}</label>
              <select id="c-cat" className="input" value={form.category} onChange={set("category")}>{(reference?.categories ?? []).map((c: any) => <option key={c.name} value={c.name}>{c.name.replace("_", " ")}</option>)}</select></div>
          </div>
          <div className="field"><label htmlFor="c-url">{tr("Brief link (optional)", "رابط البريف (اختياري)")}</label><input id="c-url" className="input" inputMode="url" value={form.url} onChange={set("url")} /></div>
          <div className="field"><label htmlFor="c-lic">{tr("Licensed-client reference (finance or crypto only)", "مرجع العميل المرخَّص (للمالية والكريبتو فقط)")}</label><input id="c-lic" className="input" value={form.licence_ref} onChange={set("licence_ref")} /></div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn primary block" disabled={busy || !form.name.trim() || !form.brief_text.trim()} onClick={submit}>{busy ? tr("Reading the brief…", "جارٍ قراءة البريف…") : tr("Read the brief", "اقرأ البريف")}</button>
    </div>
  );
}

/** The one Add component. `campaignId` preselects the campaign for a video; `shared` comes from the phone's share menu. */
export function AddFlow({ kind, campaignId, shared, onAdded }: { kind?: Kind; campaignId?: string; shared?: Shared | null; onAdded?: () => void }) {
  const { tr } = useLang();
  const router = useRouter();
  const [approved, setApproved] = useState<Approved[] | null>(null);
  const [choice, setChoice] = useState<Kind | null>(kind ?? (shared && (shared.text || shared.url) ? "campaign" : null));
  const [practice, setPractice] = useState(false);
  const [justMade, setJustMade] = useState<string | undefined>();

  useEffect(() => {
    fetchData<any>("/api/home").then((home) => {
      const fetched: Approved[] = home.campaigns.filter((c: Card) => c.status === "approved")
        .map((c: Card) => ({ id: c.id, name: c.name, practice: !!c.is_practice, language: c.language ?? null }));
      // A practice campaign made a moment ago may not be in a slow answer yet: keep it.
      setApproved((list) => [...(list ?? []).filter((c) => !fetched.some((f) => f.id === c.id)), ...fetched]);
    }).catch(() => setApproved((list) => list ?? []));
  }, []);

  const shown: Kind | null = choice ?? (approved === null ? null : campaignId || approved.length > 0 ? "video" : "campaign");
  const pickKind = (next: Kind) => { setChoice(next); setPractice(false); };
  const practiceCreated = (campaign: Approved) => {   // straight to the video form, no waiting for a reload
    setApproved((list) => [campaign, ...(list ?? []).filter((c) => c.id !== campaign.id)]);
    setJustMade(campaign.id);
    setPractice(false);
    setChoice("video");
  };
  const campaignCreated = (id: string) => { onAdded?.(); router.push(`/campaign?id=${id}`); };

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="seg" role="tablist" aria-label={tr("What to add", "ماذا تريد أن تضيف")}>
        <button role="tab" aria-selected={shown === "video"} onClick={() => pickKind("video")}>{tr("Add video", "إضافة فيديو")}</button>
        <button role="tab" aria-selected={shown === "campaign"} onClick={() => pickKind("campaign")}>{tr("Add campaign", "إضافة حملة")}</button>
      </div>
      {shown === null && <i className="sk" style={{ height: 220, borderRadius: 16 }} aria-busy="true" />}
      {shown === "video" && approved !== null && (approved.length > 0 || campaignId
        ? <UploadVideo campaigns={approved.length > 0 ? approved : []} initialId={justMade ?? campaignId} />
        : <Empty icon={UiIcon.flag} title={tr("No approved campaign yet", "لا توجد حملة موافَق عليها بعد")}
            body={tr("A video is always cut for a campaign. Add one, or try a practice campaign first.", "الفيديو يُقصّ دائماً لحملة. أضف واحدة، أو جرّب حملة تجريبية أولاً.")}
            action={<button className="btn primary" onClick={() => pickKind("campaign")}>{tr("Add a campaign", "أضف حملة")}</button>} />)}
      {shown === "campaign" && !practice && (
        <>
          <AddCampaign key={JSON.stringify(shared ?? {})} initial={shared ?? undefined} onCreated={campaignCreated} />
          <button className="btn ghost" onClick={() => setPractice(true)}>{tr("No brief? Try a practice campaign", "ما عندك بريف؟ جرّب حملة تجريبية")}</button>
        </>
      )}
      {shown === "campaign" && practice && (
        <>
          <PracticeForm onCreated={practiceCreated} />
          <button className="btn ghost" onClick={() => setPractice(false)}>{tr("Back to a real campaign", "العودة إلى حملة حقيقية")}</button>
        </>
      )}
    </div>
  );
}

/** /new: the same flow as a page. It is the target of the phone's share menu (text and link arrive in the address). */
export function NewPlace() {
  const { tr } = useLang();
  const [shared, setShared] = useState<Shared | null | undefined>(undefined);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const text = p.get("text") ?? "", url = p.get("url") ?? "", title = p.get("title") ?? "";
    const link = url || (text.match(/https?:\/\/\S+/)?.[0] ?? "");
    setShared(text || url || title ? { name: title, text, url: link } : null);
  }, []);
  return (
    <div className="page" style={{ maxWidth: 640 }}>
      <PageHead title={tr("Add", "إضافة")} back="/" />
      {shared !== undefined && <AddFlow shared={shared} />}
    </div>
  );
}
