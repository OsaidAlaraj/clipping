"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError, money, PLATFORM_LABEL } from "@/lib/api";
import { useSwipe } from "@/lib/useSwipe";

export type Card = {
  id: string; name: string; status: string; status_reason: string | null; marketplace: string; niche: string | null;
  rate_per_1k: number | null; fee_known: boolean; fee_pct?: number | null; est_net_rate_per_1k: number; budget_left_pct: number | null;
  hours_to_deadline: number | null; compliance_status: string; platforms: string[] | null; clips_ready: number;
  max_payout_per_clip?: number | null; unclear_fields: string[]; min_views_to_qualify?: number | null;
};
type HomeData = {
  stories: { id: string; name: string; budget_left_pct: number | null; hours_to_deadline: number | null; clips_ready: number }[];
  today: { hours_logged: number; hours_cap: number; clips_accepted: number; posts_done: number; posts_due: number; to_review?: number };
  net_per_hour: { this_week: any; month_to_date: any; confidence: string };
  attention: { id: string; severity: string; message: string }[];
  campaigns: Card[];
  kill_switch: { active: boolean };
};

const TINTS = ["#EEF8D2", "#E7E3FF", "#FFE8D6", "#DDEBFF", "#FFF4C7", "#F1E4FF"];
export function tintFor(id: string): string {
  let sum = 0;
  for (const ch of id) sum += ch.charCodeAt(0);
  return TINTS[sum % TINTS.length];
}
export function initials(name: string): string {
  return name.replace(/\(.*?\)/g, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");
}
export function deadlineText(hours: number | null): string {
  if (hours === null) return "No deadline";
  if (hours < 0) return "Deadline passed";
  return hours < 72 ? `${hours} h left` : `${Math.round(hours / 24)} days left`;
}

export function CampaignCard({ card, onApprove, onPark, onOpen }: { card: Card; onApprove: () => void; onPark: () => void; onOpen: () => void }) {
  const { offset, bind } = useSwipe({ onRight: onApprove, onLeft: onPark, onTap: onOpen }, { vertical: false });
  const badge = card.compliance_status === "pass" ? "pass" : card.compliance_status === "blocked" ? "blocked" : "review";
  const low = card.budget_left_pct !== null && card.budget_left_pct < 25;
  return (
    <article className="card campaign stack" data-testid="campaign-card" {...bind} role="button" tabIndex={0}
      aria-label={`${card.name}. Swipe right to approve, left to park, tap to open.`}
      onKeyDown={(event) => { if (event.key === "Enter") onOpen(); }}
      style={{ transform: `translateX(${offset.x}px)`, outline: offset.x > 30 ? "2px solid var(--fg)" : offset.x < -30 ? "2px solid var(--urgent)" : undefined }}>
      <div className="row">
        <span className="avatar-sm" style={{ background: tintFor(card.id) }} aria-hidden="true">{initials(card.name)}</span>
        <div className="grow stack" style={{ gap: 0 }}>
          <strong>{card.name}</strong>
          <span className="small muted">{card.marketplace}{card.niche ? ` · ${card.niche}` : ""}</span>
        </div>
        <span className={`badge ${badge}`}>{card.status === "approved" ? "approved" : card.status === "paused" ? "parked" : `rules: ${card.compliance_status}`}</span>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="big-num">{card.rate_per_1k !== null ? money(card.rate_per_1k) : "Rate unclear"}</span>
        <span className="small muted">{card.rate_per_1k !== null ? "per 1,000 views" : ""}{card.fee_known ? "" : " · fee unknown"}</span>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        {card.budget_left_pct !== null && <div className="bar" aria-hidden="true"><i className={low ? "low" : undefined} style={{ width: `${card.budget_left_pct}%` }} /></div>}
        <div className="meta">
          <span>{card.budget_left_pct !== null ? `${card.budget_left_pct}% budget left` : "Budget unknown"}</span>
          <span>{deadlineText(card.hours_to_deadline)}</span>
          <span>{(card.platforms ?? []).map((p) => PLATFORM_LABEL[p] ?? p).join(", ") || "Platforms unclear"}</span>
          {card.max_payout_per_clip ? <span>Max {money(card.max_payout_per_clip)} per clip</span> : null}
        </div>
      </div>
      <div className="swipe-hint">
        <span>← Park</span>
        <span>{card.status === "approved" ? `${card.clips_ready} to review` : "Tap to read the rules"}</span>
        <span>Approve →</span>
      </div>
    </article>
  );
}

export default function Home() {
  const [data, setData] = useState<HomeData | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(() => api<HomeData>("/api/home").then(setData).catch(() => setMessage("Could not load. Showing nothing until you are back online.")), []);
  useEffect(() => { load(); }, [load]);

  const act = async (id: string, action: "approve" | "park") => {
    try {
      await api(`/api/campaigns/${id}/${action}`, { method: "POST" });
      setMessage(action === "approve" ? "Campaign approved." : "Campaign parked.");
      load();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "That did not work.");
    }
  };

  if (!data) return <div className="page"><h1>Home</h1><p className="muted">{message || "Loading…"}</p></div>;
  const { today, net_per_hour: net } = data;
  const week = net.this_week, month = net.month_to_date;
  const perHour = (b: any) => (b.with_pending_per_hour === null ? "—" : `$${Number(b.with_pending_per_hour).toFixed(2)}`);

  return (
    <div className="page home-grid">
      <h1 className="sr-only">Home</h1>
      <div className="stack home-main" style={{ gap: 14 }}>
        {data.kill_switch.active && <div className="banner block" role="alert">The kill switch is on. No new jobs run and ready-to-post clips are hidden.</div>}
        {message && <div className="banner" role="status">{message}</div>}

        <section aria-label="Active campaigns" className="stories">
          <Link href="/explore" className="story">
            <span className="ring add"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg></span>
            <span className="name">Find more</span>
          </Link>
          {data.stories.map((story) => {
            const byBudget = story.budget_left_pct !== null;
            const p = byBudget ? story.budget_left_pct! : story.hours_to_deadline !== null ? Math.max(0, Math.min(100, (story.hours_to_deadline / (14 * 24)) * 100)) : 100;
            return (
              <Link key={story.id} className="story" href={`/review?campaign=${story.id}`}
                aria-label={`${story.name}: ${byBudget ? `${story.budget_left_pct}% budget left` : deadlineText(story.hours_to_deadline)}, ${story.clips_ready} clips to review`}>
                <span className={`ring ${p < 25 ? "low" : ""}`} style={{ ["--p" as any]: p }}>
                  <span className="avatar" style={{ background: tintFor(story.id) }}>{initials(story.name)}</span>
                </span>
                <span className="name">{story.name}</span>
              </Link>
            );
          })}
        </section>

        <section aria-label="Today" className="card tiles">
          <div className="tile">
            <div className="num">{today.hours_logged.toFixed(1)}<small> / {today.hours_cap}h</small></div>
            <div className="small muted">Hours today</div>
          </div>
          <div className="tile"><div className="num">{today.clips_accepted}</div><div className="small muted">Clips accepted</div></div>
          <div className="tile"><div className="num">{today.posts_done}</div><div className="small muted">Posts done</div></div>
          <div className="tile"><div className="num">{today.posts_due}</div><div className="small muted">Posts due</div></div>
        </section>

        {today.posts_due > 0 && (
          <article className="card ink row wrap" aria-label="Ready to post">
            <div className="grow stack" style={{ gap: 4 }}>
              <span className="small" style={{ color: "var(--accent)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Ready to post · {today.posts_due} waiting</span>
              <span style={{ fontWeight: 600 }}>Accepted clips are waiting for their publish checklist.</span>
            </div>
            <Link className="btn primary" href="/queue">Open queue</Link>
          </article>
        )}

        {data.attention.length > 0 && (
          <section className="stack" aria-label="Attention">
            <div className="row between"><h2>Attention</h2><Link href="/alerts" className="small">All alerts</Link></div>
            {data.attention.map((alert) => <div key={alert.id} className={`banner ${alert.severity}`}>{alert.message}</div>)}
          </section>
        )}

        <section className="stack" aria-label="Campaigns">
          <h2>Campaigns by estimated net pay</h2>
          {data.campaigns.length === 0 && <p className="muted">No campaigns yet. Tap ➕ to add one by pasting its brief.</p>}
          {data.campaigns.map((card) => (
            <CampaignCard key={card.id} card={card}
              onApprove={() => (card.status === "approved" ? setMessage("Already approved.") : act(card.id, "approve"))}
              onPark={() => act(card.id, "park")}
              onOpen={() => { window.location.href = card.status === "approved" ? `/review?campaign=${card.id}` : `/campaign?id=${card.id}`; }} />
          ))}
        </section>
      </div>

      <aside className="stack home-side" aria-label="Your numbers" style={{ gap: 14 }}>
        <section className="card stack" aria-label="Net pay per hour">
          <div className="row between"><h2>Net pay per hour</h2><span className="badge">Confidence: {net.confidence}</span></div>
          <div className="row between"><span className="muted">This week</span><strong>{perHour(week)}</strong></div>
          <div className="row between"><span className="muted">Month to date</span><strong>{perHour(month)}</strong></div>
          <p className="small muted">Includes pending payouts. Paid so far this month: {money(month.paid_usd)} over {month.hours} h.{month.fee_unknown ? " Some fees are unknown, so this is before those fees." : ""}</p>
        </section>
      </aside>
    </div>
  );
}
