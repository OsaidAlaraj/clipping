"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { api, ApiError, money, PLATFORM_LABEL } from "@/lib/api";
import { mutate, peek, useData } from "@/lib/data";
import { plainAlert } from "@/lib/alertText";
import { showToast } from "@/lib/toast";
import { useSwipe } from "@/lib/useSwipe";
import ProgressCards, { useProgress } from "./Progress";
import { HomeSkeleton } from "./Skeleton";

export type Card = {
  id: string; name: string; status: string; status_reason: string | null; marketplace: string; niche: string | null;
  rate_per_1k: number | null; fee_known: boolean; fee_pct?: number | null; est_net_rate_per_1k: number; budget_left_pct: number | null;
  hours_to_deadline: number | null; compliance_status: string; platforms: string[] | null; clips_ready: number;
  max_payout_per_clip?: number | null; unclear_fields: string[]; min_views_to_qualify?: number | null;
  is_practice?: boolean; language?: string | null;
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
  const { ref, intent, bind } = useSwipe({ onRight: onApprove, onLeft: onPark, onTap: onOpen }, { vertical: false });
  const badge = card.compliance_status === "pass" ? "pass" : card.compliance_status === "blocked" ? "blocked" : "review";
  const low = card.budget_left_pct !== null && card.budget_left_pct < 25;
  return (
    <article className="card campaign stack" data-testid="campaign-card" {...bind} ref={ref as React.RefObject<HTMLElement>} role="button" tabIndex={0}
      aria-label={`${card.name}. Swipe right to approve, left to park, tap to open.`}
      onKeyDown={(event) => { if (event.key === "Enter") onOpen(); }}
      style={{ outline: intent === "right" ? "2px solid var(--fg)" : intent === "left" ? "2px solid var(--urgent)" : undefined }}>
      <div className="row">
        <span className="avatar-sm" style={{ background: tintFor(card.id) }} aria-hidden="true">{initials(card.name)}</span>
        <div className="grow stack" style={{ gap: 0 }}>
          <strong>{card.name}</strong>
          <span className="small muted">{card.marketplace}{card.niche ? ` · ${card.niche}` : ""}</span>
        </div>
        <span className={`badge ${card.is_practice ? "info" : badge}`}>{card.is_practice ? "Practice" : card.status === "approved" ? "approved" : card.status === "paused" ? "parked" : `rules: ${card.compliance_status}`}</span>
      </div>
      {card.is_practice ? (
        <p className="small muted">For trying the system. No money, and nothing is posted.</p>
      ) : (
        <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
          <span className="big-num">{card.rate_per_1k !== null ? money(card.rate_per_1k) : "Rate unclear"}</span>
          <span className="small muted">{card.rate_per_1k !== null ? "per 1,000 views" : ""}{card.fee_known ? "" : " · fee unknown"}</span>
        </div>
      )}
      {!card.is_practice && <div className="stack" style={{ gap: 6 }}>
        {card.budget_left_pct !== null && <div className="bar" aria-hidden="true"><i className={low ? "low" : undefined} style={{ width: `${card.budget_left_pct}%` }} /></div>}
        <div className="meta">
          <span>{card.budget_left_pct !== null ? `${card.budget_left_pct}% budget left` : "Budget unknown"}</span>
          <span>{deadlineText(card.hours_to_deadline)}</span>
          <span>{(card.platforms ?? []).map((p) => PLATFORM_LABEL[p] ?? p).join(", ") || "Platforms unclear"}</span>
          {card.max_payout_per_clip ? <span>Max {money(card.max_payout_per_clip)} per clip</span> : null}
        </div>
      </div>}
      <div className="swipe-hint">
        <span>← Park</span>
        <span>{card.status === "approved" ? `${card.clips_ready} to review` : "Tap to read the rules"}</span>
        <span>Approve →</span>
      </div>
    </article>
  );
}

/** Stories with clips left come first (most clips first); finished ones go last. Ties keep the server's order. */
export function orderStories<T extends { clips_ready: number }>(stories: T[]): T[] {
  return stories.map((story, i) => ({ story, i })).sort((a, b) => b.story.clips_ready - a.story.clips_ready || a.i - b.i).map((x) => x.story);
}

export default function Home() {
  const router = useRouter();
  const home = useData<HomeData>("/api/home");
  const data = home.data ?? null;
  const progress = useProgress();

  // Approve and park show at once. The server confirms in the background; a refusal puts the card back with the reason.
  const act = async (id: string, action: "approve" | "park") => {
    const before = peek<HomeData>("/api/home");
    const status = action === "approve" ? "approved" : "paused";
    mutate<HomeData>("/api/home", (h) => ({ ...h!, campaigns: h!.campaigns.map((c) => (c.id === id ? { ...c, status } : c)) }));
    try {
      await api(`/api/campaigns/${id}/${action}`, { method: "POST" });
      showToast(action === "approve" ? "Campaign approved." : "Campaign parked.");
      void home.reload();
    } catch (error) {
      if (before) mutate<HomeData>("/api/home", () => before);
      showToast(error instanceof ApiError ? error.message : "That did not work. Nothing changed.", { tone: "error" });
    }
  };

  if (!data || !progress.settled) {
    return home.error && !data ? <div className="page"><h1>Home</h1><p className="muted">Could not load. Showing nothing until you are back online.</p></div> : <HomeSkeleton />;
  }
  const { today, net_per_hour: net } = data;
  const week = net.this_week, month = net.month_to_date;
  const perHour = (b: any) => (b.with_pending_per_hour === null ? "—" : `$${Number(b.with_pending_per_hour).toFixed(2)}`);

  const toReview = today.to_review ?? data.stories.reduce((n, st) => n + st.clips_ready, 0);
  const approved = data.campaigns.filter((c) => c.status === "approved").length;
  const waiting = data.campaigns.filter((c) => c.status !== "approved" && c.status !== "paused" && !c.is_practice).length;
  const processing = (progress.data?.items ?? []).filter((i) => i.state === "active").length;
  // One main thing to do: review what is ready, else post what is accepted, else bring in a video, else find work.
  const primary = toReview > 0 ? { href: "/review", label: `Review ${toReview} ${toReview === 1 ? "clip" : "clips"}`, why: "Clips are ready for your decision." }
    : today.posts_due > 0 ? { href: "/queue", label: `Post ${today.posts_due} ${today.posts_due === 1 ? "clip" : "clips"}`, why: "Accepted clips are waiting to go out." }
    : approved > 0 ? { href: "/new", label: "Add a video", why: processing ? "Clips are being made from your last video." : "Nothing to review. Give it a video to cut." }
    : { href: "/explore", label: "Find a campaign", why: "No approved campaign yet." };
  const decisions: { key: string; text: string; href: string }[] = [
    ...(waiting > 0 ? [{ key: "rules", text: `${waiting} ${waiting === 1 ? "campaign is" : "campaigns are"} waiting for your approval`, href: "#campaigns" }] : []),
    ...(toReview > 0 && primary.href !== "/review" ? [{ key: "review", text: `${toReview} clips to review`, href: "/review" }] : []),
    ...(today.posts_due > 0 && primary.href !== "/queue" ? [{ key: "post", text: `${today.posts_due} clips ready to post`, href: "/queue" }] : []),
  ];

  return (
    <div className="page home-grid">
      <h1 className="sr-only">Home</h1>
      <div className="stack home-main" style={{ gap: 16 }}>
        {data.kill_switch.active && <div className="banner block" role="alert">The kill switch is on. No new jobs run and ready-to-post clips are hidden.</div>}

        <section className="card ink stack" aria-label="What to do now" data-testid="primary-action" style={{ gap: 10 }}>
          <span className="small" style={{ color: "var(--accent)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Next</span>
          <p style={{ fontWeight: 600, margin: 0 }}>{primary.why}</p>
          <Link className="btn primary block" href={primary.href}>{primary.label}</Link>
        </section>

        {(decisions.length > 0 || data.attention.length > 0) && (
          <section className="stack" aria-label="Needs you" style={{ gap: 8 }}>
            <h2>Needs you</h2>
            {decisions.map((d) => <Link key={d.key} className="card row between" href={d.href}><span>{d.text}</span><span aria-hidden="true">→</span></Link>)}
            {data.attention.length > 0 && (
              <details className="attention stack" aria-label="Attention" open={data.attention.length <= 2}>
                <summary className="row between"><strong>Alerts ({data.attention.length})</strong><Link href="/alerts" className="small">All alerts</Link></summary>
                {data.attention.map((alert) => {
                  const plain = plainAlert(alert.message);
                  return (
                    <div key={alert.id} className={`banner ${alert.severity}`}>
                      <p>{plain.text}</p>
                      {plain.text !== plain.raw && <details className="small"><summary>Technical details</summary><p>{plain.raw}</p></details>}
                    </div>
                  );
                })}
              </details>
            )}
          </section>
        )}

        <ProgressCards data={progress.data} />

        <section className="card stack" aria-label="Net pay per hour" style={{ gap: 8 }}>
          <div className="row between"><h2>Net pay per hour</h2><span className="badge">Confidence: {net.confidence}</span></div>
          <div className="row between"><span className="muted">This week</span><strong>{perHour(week)}</strong></div>
          <div className="row between"><span className="muted">Month to date</span><strong>{perHour(month)}</strong></div>
          <p className="small muted">Includes pending payouts. Paid so far this month: {money(month.paid_usd)} over {month.hours} h.{month.fee_unknown ? " Some fees are unknown, so this is before those fees." : ""}</p>
        </section>

        <details className="card stack" aria-label="Today">
          <summary className="row between"><strong>Today</strong><span className="small muted">{today.hours_logged.toFixed(1)} / {today.hours_cap} h · {today.clips_accepted} accepted · {today.posts_done} posted</span></summary>
          <div className="tiles" style={{ marginTop: 10 }}>
            <div className="tile">
              <div className="num">{today.hours_logged.toFixed(1)}<small> / {today.hours_cap}h</small></div>
              <div className="small muted">Hours today</div>
            </div>
            <div className="tile"><div className="num">{today.clips_accepted}</div><div className="small muted">Clips accepted</div></div>
            <div className="tile"><div className="num">{today.posts_done}</div><div className="small muted">Posts done</div></div>
            <div className="tile"><div className="num">{today.posts_due}</div><div className="small muted">Posts due</div></div>
          </div>
        </details>

        <section id="campaigns" className="stack" aria-label="Campaigns" style={{ gap: 10 }}>
          <div className="row between"><h2>Campaigns</h2><Link href="/explore" className="small">Find more</Link></div>
          {data.stories.length > 0 && (
            <div aria-label="Active campaigns" className="stories">
              {orderStories(data.stories).map((story) => {
                const byBudget = story.budget_left_pct !== null;
                const p = byBudget ? story.budget_left_pct! : story.hours_to_deadline !== null ? Math.max(0, Math.min(100, (story.hours_to_deadline / (14 * 24)) * 100)) : 100;
                return (
                  <Link key={story.id} className="story" href={`/review?campaign=${story.id}`}
                    aria-label={`${story.name}: ${byBudget ? `${story.budget_left_pct}% budget left` : deadlineText(story.hours_to_deadline)}, ${story.clips_ready} clips to review`}>
                    <span className={`ring ${story.clips_ready === 0 ? "done" : p < 25 ? "low" : ""}`} style={{ ["--p" as any]: p }}>
                      <span className="avatar" style={{ background: tintFor(story.id) }}>{initials(story.name)}</span>
                    </span>
                    <span className="name">{story.name}</span>
                  </Link>
                );
              })}
            </div>
          )}
          {data.campaigns.length === 0 && <p className="muted">No campaigns yet. Tap ➕ to add one by pasting its brief.</p>}
          {data.campaigns.map((card) => (
            <CampaignCard key={card.id} card={card}
              onApprove={() => (card.status === "approved" ? showToast("Already approved.") : act(card.id, "approve"))}
              onPark={() => act(card.id, "park")}
              onOpen={() => router.push(card.status === "approved" ? `/review?campaign=${card.id}` : `/campaign?id=${card.id}`)} />
          ))}
        </section>
      </div>
    </div>
  );
}
