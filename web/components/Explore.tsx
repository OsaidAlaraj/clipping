"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, money, PLATFORM_LABEL } from "@/lib/api";
import { initials, tintFor } from "./Home";

export type Rank = {
  modelled: boolean; score: number | null; fixed_first?: boolean; language_mismatch?: boolean; p25_per_clip?: number; mean_per_clip?: number; p25_per_hour?: number; mean_per_hour?: number;
  p_clear?: number | null; p_budget?: number; p_approve?: number; geo_share?: number; reasons: string[];
  account: { id: string; handle: string; platform: string } | null;
};
export type Listing = {
  id: string; title: string; brand: string | null; board_slug: string | null; agency: string | null; campaign_type: string | null;
  pay_model: string | null; platforms: string[]; niche: string | null; rate_per_1k: number | null; net_per_1k: number | null;
  budget_left: number | null; budget_total: number | null; drain_p50: string | null; alpha_score: number | null; open_join: boolean | null;
  audience: string | null; board_fee_pct: number | null; brief_grade: string | null; new_at: string | null; campaign_id: string | null;
  page_url: string | null; brief_url: string | null; min_payout: number | null; max_payout: number | null; payout_window_days: number | null;
  rank: Rank;
};

const BOARD_LABEL: Record<string, string> = {
  "content-rewards": "Content Rewards", "promote-fun": "Promote.fun", clipster: "Clipster", clipstake: "Clipstake", vyro: "Vyro", evangelist: "Evangelist",
};
const FEED_PLATFORM: Record<string, string> = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube", x: "X", facebook: "Facebook", twitch: "Twitch", kick: "Kick" };
const NICHE_LABEL: Record<string, string> = {
  podcast: "Podcast", streams: "Streams", gaming: "Gaming", sports: "Sports", "film-and-tv": "Film & TV", music: "Music",
  "brand-ads": "Brand ads", "ugc-and-apps": "UGC & apps", "behind-the-scenes": "Behind the scenes",
};
const SORTS = [["rank", "For you"], ["newest", "New"], ["ending", "Ending soon"], ["rate", "Highest pay"]] as const;

export function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  return Math.max(0, (new Date(iso).getTime() - Date.now()) / 86400000);
}
function daysText(iso: string | null): string {
  const d = daysLeft(iso);
  if (d === null) return "No run-out date";
  return d < 1 ? "Ends today" : `${Math.round(d)} days left`;
}
function pct(x: number | null | undefined): string {
  return x === undefined || x === null ? "—" : `${Math.round(x * 100)}%`;
}
function boardName(slug: string | null): string {
  return slug ? BOARD_LABEL[slug] ?? slug : "Captured";
}

type Filters = {
  platform: string; niches: string[]; board: string; minRate: string; minDays: string; openOnly: boolean; feeKnown: boolean; goodBrief: boolean;
  payModel: string; maxViews: string; maxHold: string; minBudget: string; noCountryRule: boolean;
};
const EMPTY: Filters = {
  platform: "", niches: [], board: "", minRate: "", minDays: "", openOnly: false, feeKnown: false, goodBrief: false,
  payModel: "", maxViews: "", maxHold: "", minBudget: "", noCountryRule: false,
};
const PAY_MODEL_LABEL: Record<string, string> = { cpm: "Per 1,000 views", milestone: "Milestones", hybrid: "Hybrid", flat: "Flat fee" };
const isNum = (v: string) => v.trim() !== "" && !Number.isNaN(Number(v));

function FilterSheet({ value, onChange, onClose, allNiches }: { value: Filters; onChange: (f: Filters) => void; onClose: () => void; allNiches: string[] }) {
  const [draft, setDraft] = useState(value);
  const toggleNiche = (n: string) => setDraft({ ...draft, niches: draft.niches.includes(n) ? draft.niches.filter((x) => x !== n) : [...draft.niches, n] });
  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Filters" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><h1>Filters</h1><button className="btn ghost" onClick={() => setDraft(EMPTY)}>Clear</button></div>
        <div className="field"><label htmlFor="f-platform">Where you post</label>
          <select id="f-platform" className="input" value={draft.platform} onChange={(e) => setDraft({ ...draft, platform: e.target.value })}>
            <option value="">Any platform</option>
            {Object.entries(FEED_PLATFORM).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
        <div className="stack"><span className="label">Niches</span>
          <div className="row wrap">{allNiches.map((n) => (
            <button key={n} className="chip-pill" aria-pressed={draft.niches.includes(n)} onClick={() => toggleNiche(n)}
              style={{ border: "1.5px solid var(--line)" }}>{NICHE_LABEL[n] ?? n}</button>
          ))}</div></div>
        <div className="field"><label htmlFor="f-board">Marketplace</label>
          <select id="f-board" className="input" value={draft.board} onChange={(e) => setDraft({ ...draft, board: e.target.value })}>
            <option value="">Any marketplace</option>
            {Object.entries(BOARD_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
        <div className="row wrap">
          <div className="field grow"><label htmlFor="f-rate">Min pay per 1,000 views ($)</label>
            <input id="f-rate" className="input" inputMode="decimal" value={draft.minRate} onChange={(e) => setDraft({ ...draft, minRate: e.target.value })} /></div>
          <div className="field grow"><label htmlFor="f-days">At least this many days of budget</label>
            <input id="f-days" className="input" inputMode="numeric" value={draft.minDays} onChange={(e) => setDraft({ ...draft, minDays: e.target.value })} /></div>
        </div>
        <div className="field"><label htmlFor="f-pay">How it pays</label>
          <select id="f-pay" className="input" value={draft.payModel} onChange={(e) => setDraft({ ...draft, payModel: e.target.value })}>
            <option value="">Any pay model</option>
            {Object.entries(PAY_MODEL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
        <div className="row wrap">
          <div className="field grow"><label htmlFor="f-views">Views needed to get paid, at most</label>
            <input id="f-views" className="input" inputMode="numeric" value={draft.maxViews} onChange={(e) => setDraft({ ...draft, maxViews: e.target.value })} /></div>
          <div className="field grow"><label htmlFor="f-hold">Days until paid, at most</label>
            <input id="f-hold" className="input" inputMode="numeric" value={draft.maxHold} onChange={(e) => setDraft({ ...draft, maxHold: e.target.value })} /></div>
          <div className="field grow"><label htmlFor="f-budget">Budget left, at least ($)</label>
            <input id="f-budget" className="input" inputMode="numeric" value={draft.minBudget} onChange={(e) => setDraft({ ...draft, minBudget: e.target.value })} /></div>
        </div>
        <label className="check"><input type="checkbox" checked={draft.noCountryRule} onChange={(e) => setDraft({ ...draft, noCountryRule: e.target.checked })} /><span>No audience country rule (good for Arabic clips)</span></label>
        <label className="check"><input type="checkbox" checked={draft.openOnly} onChange={(e) => setDraft({ ...draft, openOnly: e.target.checked })} /><span>Open to join, no application</span></label>
        <label className="check"><input type="checkbox" checked={draft.feeKnown} onChange={(e) => setDraft({ ...draft, feeKnown: e.target.checked })} /><span>Fee is known</span></label>
        <label className="check"><input type="checkbox" checked={draft.goodBrief} onChange={(e) => setDraft({ ...draft, goodBrief: e.target.checked })} /><span>Clear briefs only (grade A to C)</span></label>
        <button className="btn primary block" onClick={() => { onChange(draft); onClose(); }}>Show campaigns</button>
      </div>
    </div>
  );
}

function ListingCard({ l, i }: { l: Listing; i: number }) {
  const budget = l.budget_total ? Math.round(((l.budget_left ?? 0) / l.budget_total) * 100) : null;
  return (
    <Link href={`/listing?id=${l.id}`} className="tile-card" data-testid="listing-card">
      <div className="cover" style={{ background: tintFor(l.id), minHeight: [140, 110, 165][i % 3] }}>
        <span className="mk">{boardName(l.board_slug)}</span>
        {l.brief_grade && <span className="mk" style={{ insetInlineStart: "auto", insetInlineEnd: 10 }}>Brief {l.brief_grade}</span>}
        <span className="big-num">{l.rate_per_1k !== null ? money(l.rate_per_1k) : "?"}</span>
      </div>
      <div className="body">
        <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{l.title}</span>
        {budget !== null && <div className="bar" aria-hidden="true"><i className={budget < 25 ? "low" : undefined} style={{ width: `${budget}%` }} /></div>}
        <span className="small muted">{daysText(l.drain_p50)}{l.rank.modelled && l.rank.p_clear !== undefined && l.rank.p_clear !== null ? ` · ${pct(l.rank.p_clear)} reach the minimum` : ""}</span>
        {l.rank.reasons[0] && <span className="small" style={{ color: "var(--muted)" }}>{l.rank.reasons[0]}</span>}
      </div>
    </Link>
  );
}

function ListingTable({ rows }: { rows: Listing[] }) {
  return (
    <section className="card" style={{ padding: 8, overflowX: "auto" }} aria-label="Campaigns">
      <table className="ledger" style={{ minWidth: 960 }}>
        <thead><tr>
          <th scope="col">Campaign</th><th scope="col">Rate / 1K</th><th scope="col">Cautious $ / clip</th><th scope="col">Reach minimum</th>
          <th scope="col">Budget left</th><th scope="col">Days left</th><th scope="col">Audience</th><th scope="col">Fee</th><th scope="col">Brief</th>
        </tr></thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.id} data-testid="listing-row">
              <td><Link href={`/listing?id=${l.id}`} className="row" style={{ textDecoration: "none" }}>
                <span className="avatar-sm" style={{ background: tintFor(l.id), width: 36, height: 36 }} aria-hidden="true">{initials(l.title)}</span>
                <span className="stack" style={{ gap: 0 }}><strong>{l.title}</strong><span className="small muted">{boardName(l.board_slug)}{l.agency ? ` · ${l.agency}` : ""}</span></span>
              </Link></td>
              <td className="display">{l.rate_per_1k !== null ? money(l.rate_per_1k) : "?"}</td>
              <td>{l.rank.modelled ? money(l.rank.p25_per_clip) : "not modelled"}</td>
              <td>{l.rank.modelled ? pct(l.rank.p_clear) : "—"}</td>
              <td>{l.budget_left !== null ? `$${Math.round(l.budget_left).toLocaleString()}` : "—"}</td>
              <td>{daysLeft(l.drain_p50) === null ? "—" : Math.round(daysLeft(l.drain_p50)!)}</td>
              <td>{l.audience ?? "Any"}</td>
              <td>{l.board_fee_pct === null ? "Unknown" : `${Math.round(l.board_fee_pct * 100)}%`}</td>
              <td>{l.brief_grade ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function ExploreScreen() {
  const [data, setData] = useState<{ listings: Listing[]; market: any; last_refresh: string | null; hidden_out_of_reach?: number } | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [me, setMe] = useState<{ niches: string[]; all_niches: string[] } | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [sort, setSort] = useState<string>("rank");
  const [query, setQuery] = useState("");
  const [sheet, setSheet] = useState(false);
  const [view, setView] = useState<"cards" | "table">("cards");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia?.("(min-width: 900px)").matches) setView("table");
    api("/api/me").then((m) => { setMe(m); setFilters((f) => ({ ...f, niches: m.niches ?? [] })); }).catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    const p = new URLSearchParams({ sort });
    if (filters.platform) p.set("platform", filters.platform);
    if (filters.board) p.set("board", filters.board);
    if (filters.minRate && !Number.isNaN(Number(filters.minRate))) p.set("min_rate", filters.minRate);
    if (filters.minDays && !Number.isNaN(Number(filters.minDays))) p.set("min_days_left", filters.minDays);
    if (filters.openOnly) p.set("open_only", "true");
    if (filters.feeKnown) p.set("fee_known", "true");
    if (filters.goodBrief) p.set("min_grade", "C");
    if (filters.payModel) p.set("pay_model", filters.payModel);
    if (isNum(filters.maxViews)) p.set("max_min_views", filters.maxViews);
    if (isNum(filters.maxHold)) p.set("max_hold_days", filters.maxHold);
    if (isNum(filters.minBudget)) p.set("min_budget_left", filters.minBudget);
    if (filters.noCountryRule) p.set("no_country_rule", "true");
    if (showAll) p.set("show_out_of_reach", "true");
    if (query.trim()) p.set("q", query.trim());
    api(`/api/listings?${p.toString()}`).then(setData).catch((e) => setNote(e instanceof ApiError ? e.message : "Could not load campaigns."));
  }, [filters, sort, query, showAll]);
  useEffect(() => { const t = setTimeout(load, query ? 250 : 0); return () => clearTimeout(t); }, [load, query]);

  const shown = useMemo(() => (data?.listings ?? []).filter((l) => filters.niches.length === 0 || (l.niche !== null && filters.niches.includes(l.niche))), [data, filters.niches]);
  const active = [filters.platform && FEED_PLATFORM[filters.platform], ...filters.niches.map((n) => NICHE_LABEL[n] ?? n), filters.board && BOARD_LABEL[filters.board],
    filters.minRate && `≥ $${filters.minRate}/1K`, filters.minDays && `≥ ${filters.minDays} days`, filters.openOnly && "Open to join", filters.feeKnown && "Fee known",
    filters.goodBrief && "Clear briefs", filters.payModel && PAY_MODEL_LABEL[filters.payModel],
    isNum(filters.maxViews) && `≤ ${Number(filters.maxViews).toLocaleString()} views needed`, isNum(filters.maxHold) && `Paid within ${filters.maxHold} days`,
    isNum(filters.minBudget) && `≥ $${Number(filters.minBudget).toLocaleString()} left`, filters.noCountryRule && "No country rule"].filter(Boolean) as string[];

  const refresh = async () => {
    setNote("");
    try { await api("/api/discovery/refresh", { body: {} }); setNote("Checking marketplaces. New campaigns appear in a minute."); }
    catch (e) { setNote(e instanceof ApiError ? e.message : "No connection."); }
  };
  const saveNiches = async (niches: string[]) => {
    try { await api("/api/me/niches", { method: "PUT", body: { niches } }); setMe((m) => (m ? { ...m, niches } : m)); setFilters((f) => ({ ...f, niches })); }
    catch (e) { setNote(e instanceof ApiError ? e.message : "No connection."); }
  };

  const cpm = data?.market?.data?.cpm;
  return (
    <div className="page">
      <div className="row between wrap">
        <h1>Explore</h1>
        <div className="row small muted">
          {data?.last_refresh && <span>Updated {new Date(data.last_refresh).toLocaleString()}</span>}
          <button className="btn" onClick={refresh}>Check now</button>
        </div>
      </div>
      {cpm && <p className="small muted">Market: median ${Number(cpm.medianUsd).toFixed(2)} per 1,000 views, middle half ${Number(cpm.p25Usd).toFixed(2)} to ${Number(cpm.p75Usd).toFixed(2)}.</p>}
      {me && me.niches.length === 0 && (
        <section className="card stack" aria-label="Pick your niches">
          <strong>Which niches do you want to clip?</strong>
          <p className="small muted">Pick what you enjoy. People who clip footage they like keep going; Explore will show these first.</p>
          <div className="row wrap">{me.all_niches.map((n) => (
            <button key={n} className="chip-pill" style={{ border: "1.5px solid var(--line)" }}
              aria-pressed={filters.niches.includes(n)}
              onClick={() => setFilters({ ...filters, niches: filters.niches.includes(n) ? filters.niches.filter((x) => x !== n) : [...filters.niches, n] })}>
              {NICHE_LABEL[n] ?? n}</button>
          ))}</div>
          <button className="btn primary" disabled={filters.niches.length === 0} onClick={() => saveNiches(filters.niches)}>Save my niches</button>
        </section>
      )}
      <label className="row" style={{ background: "var(--surface)", borderRadius: 24, padding: "0 16px", minHeight: 48 }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></svg>
        <span className="sr-only">Search campaigns</span>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search brands, campaigns, agencies"
          style={{ flex: 1, border: 0, outline: 0, background: "transparent", minHeight: 46 }} />
        <button className="btn ghost" onClick={() => setSheet(true)} aria-label="Filters">Filters{active.length ? ` · ${active.length}` : ""}</button>
      </label>
      <div className="row wrap between">
        <div className="segmented" role="tablist" aria-label="Sort">
          {SORTS.map(([id, label]) => <button key={id} role="tab" aria-selected={sort === id} onClick={() => setSort(id)}>{label}</button>)}
        </div>
        <div className="segmented" role="group" aria-label="View">
          <button aria-pressed={view === "cards"} aria-selected={view === "cards"} onClick={() => setView("cards")}>Cards</button>
          <button aria-pressed={view === "table"} aria-selected={view === "table"} onClick={() => setView("table")}>Table</button>
        </div>
      </div>
      {active.length > 0 && <div className="row wrap" aria-label="Active filters">{active.map((a) => <span key={a} className="chip-pill on">{a}</span>)}</div>}
      {note && <div className="banner" role="status">{note}</div>}
      {!!data?.hidden_out_of_reach && !showAll && (
        <div className="row between small muted" role="status">
          <span>{data.hidden_out_of_reach} hidden: your clips would rarely reach their minimum views.</span>
          <button className="btn ghost" onClick={() => setShowAll(true)}>Show them</button>
        </div>
      )}
      {data && shown.length === 0 && <p className="muted">No campaigns match. Loosen a filter, or tap “Check now”.</p>}
      {view === "cards" ? <div className="masonry">{shown.map((l, i) => <ListingCard key={l.id} l={l} i={i} />)}</div> : <ListingTable rows={shown} />}
      <p className="small muted">Campaign data: ClippingAlpha (clippingalpha.com), CC BY 4.0. Each marketplace's own page is the source of truth. “Cautious $ / clip” is the 25th-percentile outcome for your best account, after fees, audience rules, minimums, approval and budget.</p>
      {sheet && me && <FilterSheet value={filters} allNiches={me.all_niches} onChange={setFilters} onClose={() => setSheet(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- one campaign, like a brand's profile

export function ListingPage({ id }: { id: string }) {
  const [l, setL] = useState<any>(null);
  const [tab, setTab] = useState<"rules" | "pay" | "why">("rules");
  const [category, setCategory] = useState("podcast");
  const [cats, setCats] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/api/listings/${id}`).then((x) => { setL(x); if (x.niche === "gaming" || x.niche === "streams") setCategory("gaming"); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load."));
    api("/api/reference").then((r) => setCats(r.categories.map((c: any) => c.name))).catch(() => undefined);
  }, [id]);

  const add = async () => {
    setBusy(true); setError("");
    try { const res = await api(`/api/listings/${id}/add`, { body: { category } }); window.location.assign(`/campaign?id=${res.campaign.id}`); }
    catch (e) { setError(e instanceof ApiError ? e.message : "No connection."); } finally { setBusy(false); }
  };
  const dismiss = async () => { await api(`/api/listings/${id}/dismiss`, { body: {} }).catch(() => undefined); window.location.assign("/explore"); };

  if (!l) return <div className="page"><p className="muted">{error || "Loading…"}</p></div>;
  const r: Rank = l.rank;
  return (
    <div className="page" style={{ maxWidth: 860 }}>
      {l.feed_note && <div className="banner" role="status">{l.feed_note} Rules and minimums will show once the brief is read.</div>}
      <section className="card stack" style={{ gap: 14 }}>
        <div className="row">
          <span className="avatar-sm" style={{ background: tintFor(l.id), width: 56, height: 56, fontSize: "1.1rem" }} aria-hidden="true">{initials(l.title)}</span>
          <div className="grow stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: "1.35rem" }}>{l.title}</h1>
            <span className="small muted">{boardName(l.board_slug)}{l.agency ? ` · by ${l.agency}` : ""}{l.niche ? ` · ${NICHE_LABEL[l.niche] ?? l.niche}` : ""}</span>
          </div>
          {l.brief_grade && <span className={`badge ${l.brief_grade <= "B" ? "pass" : l.brief_grade <= "C" ? "review" : "fail"}`}>Brief {l.brief_grade}</span>}
        </div>
        <div className="tiles">
          <div><div className="num display">{l.rate_per_1k !== null ? money(l.rate_per_1k) : "?"}</div><div className="small muted">per 1,000 views</div></div>
          <div><div className="num display">{r.modelled ? money(r.p25_per_clip) : "—"}</div><div className="small muted">cautious per clip</div></div>
          <div><div className="num display">{r.modelled ? pct(r.p_clear) : "—"}</div><div className="small muted">reach the minimum</div></div>
          <div><div className="num display">{daysLeft(l.drain_p50) === null ? "—" : Math.round(daysLeft(l.drain_p50)!)}</div><div className="small muted">days of budget</div></div>
        </div>
        {r.account && <p className="small muted">Worked out for @{r.account.handle} on {PLATFORM_LABEL[r.account.platform] ?? r.account.platform}.</p>}
        {l.campaign_id ? (
          <Link className="btn primary block" href={`/campaign?id=${l.campaign_id}`}>Open in my campaigns</Link>
        ) : (
          <div className="row wrap">
            <div className="field" style={{ flex: "1 1 160px" }}><label htmlFor="l-cat">Your niche for this</label>
              <select id="l-cat" className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
                {(cats.length ? cats : ["podcast"]).map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
              </select></div>
            <button className="btn primary" style={{ flex: "2 1 220px", alignSelf: "end" }} disabled={busy} onClick={add}>Add to my campaigns</button>
            <button className="btn" style={{ alignSelf: "end" }} onClick={dismiss}>Not for me</button>
          </div>
        )}
        <p className="small muted">Adding reads the brief and checks the rules. You still approve it before anything is produced.</p>
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      <div className="segmented" role="tablist" aria-label="Campaign details">
        <button role="tab" aria-selected={tab === "rules"} onClick={() => setTab("rules")}>Rules</button>
        <button role="tab" aria-selected={tab === "pay"} onClick={() => setTab("pay")}>Pay</button>
        <button role="tab" aria-selected={tab === "why"} onClick={() => setTab("why")}>Why this rank</button>
      </div>

      {tab === "rules" && (
        <section className="stack" aria-label="Rules">
          {(l.rules ?? []).length === 0 && <p className="muted">No rules were listed. Read the brief on the marketplace before adding.</p>}
          {(l.rules ?? []).map((g: any) => (
            <div key={g.key} className="card stack">
              <h2>{g.title}</h2>
              {(g.items ?? []).filter((i: any) => !i.chips).map((i: any, n: number) => <p key={n} className="small">{i.text}</p>)}
            </div>
          ))}
        </section>
      )}
      {tab === "pay" && (
        <section className="card stack" aria-label="Pay">
          <div className="row between"><span className="muted">Marketplace fee</span><strong>{l.board_fee_pct === null ? "Unknown" : `${Math.round(l.board_fee_pct * 100)}%`}</strong></div>
          <div className="row between"><span className="muted">Smallest payout</span><strong>{l.min_payout ? money(l.min_payout) : "—"}</strong></div>
          <div className="row between"><span className="muted">Most per clip</span><strong>{l.max_payout ? money(l.max_payout) : "No cap listed"}</strong></div>
          <div className="row between"><span className="muted">Payout window</span><strong>{l.payout_window_days ? `${l.payout_window_days} days` : "—"}</strong></div>
          <div className="row between"><span className="muted">Budget</span><strong>{l.budget_left !== null ? `$${Math.round(l.budget_left).toLocaleString()} of $${Math.round(l.budget_total ?? 0).toLocaleString()}` : "—"}</strong></div>
          <div className="row between"><span className="muted">Audience rule</span><strong>{l.audience ?? "None listed"}</strong></div>
        </section>
      )}
      {tab === "why" && (
        <section className="card stack" aria-label="Why this rank">
          {r.reasons.map((x) => <p key={x} className="small">• {x}</p>)}
          {r.modelled && <p className="small muted">Chance the clip is approved {pct(r.p_approve)} · budget lasts through payout {pct(r.p_budget)} · share of your views that count {pct(r.geo_share)}.</p>}
          {(l.verdicts ?? []).map((v: any) => <div key={v.key} className="row between small"><span className="muted">{v.label}</span><span>{v.verdict}{v.fact ? ` · ${v.fact}` : ""}</span></div>)}
        </section>
      )}
      <div className="row wrap">
        {(l.brief_url || l.page_url) && <a className="btn" href={l.brief_url || l.page_url} target="_blank" rel="noreferrer">Open on the marketplace</a>}
      </div>
      <p className="small muted">Campaign data: ClippingAlpha (clippingalpha.com), CC BY 4.0. The marketplace page is the source of truth.</p>
    </div>
  );
}
