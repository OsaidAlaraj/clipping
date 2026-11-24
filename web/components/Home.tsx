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
import { useAdd } from "./Add";
import { Empty, PageHead, UiIcon } from "./ui";
import { useLang } from "@/lib/i18n";

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
  const { tr } = useLang();
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
        <span className={`badge ${card.is_practice ? "info" : badge}`}>{card.is_practice ? tr("Practice", "تجريبية") : card.status === "approved" ? "approved" : card.status === "paused" ? "parked" : `rules: ${card.compliance_status}`}</span>
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
          <span>{card.budget_left_pct !== null ? tr(`${card.budget_left_pct}% budget left`, `${card.budget_left_pct}% من الميزانية`) : tr("Budget unknown", "الميزانية غير معروفة")}</span>
          <span>{deadlineText(card.hours_to_deadline)}</span>
          <span>{(card.platforms ?? []).map((p) => PLATFORM_LABEL[p] ?? p).join(", ") || "Platforms unclear"}</span>
          {card.max_payout_per_clip ? <span>Max {money(card.max_payout_per_clip)} per clip</span> : null}
        </div>
      </div>}
      <div className="swipe-hint">
        <span>{tr("← Park", "← أجّل")}</span>
        <span>{card.status === "approved" ? `${card.clips_ready} to review` : tr("Tap to read the rules", "اضغط لقراءة القواعد")}</span>
        <span>{tr("Approve →", "وافق →")}</span>
      </div>
    </article>
  );
}

/** Stories with clips left come first (most clips first); finished ones go last. Ties keep the server's order. */
export function orderStories<T extends { clips_ready: number }>(stories: T[]): T[] {
  return stories.map((story, i) => ({ story, i })).sort((a, b) => b.story.clips_ready - a.story.clips_ready || a.i - b.i).map((x) => x.story);
}

export default function Home() {
  const { tr } = useLang();
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
      showToast(action === "approve" ? tr("Campaign approved.", "تمت الموافقة على الحملة.") : tr("Campaign parked.", "تم تأجيل الحملة."));
      void home.reload();
    } catch (error) {
      if (before) mutate<HomeData>("/api/home", () => before);
      showToast(error instanceof ApiError ? error.message : tr("That did not work. Nothing changed.", "ما زبطت. لم يتغيّر شيء."), { tone: "error" });
    }
  };

  if (!data || !progress.settled) {
    return home.error && !data ? <div className="page"><PageHead title={tr("Home", "الرئيسية")} /><p className="muted">{tr("Could not load. Showing nothing until you are back online.", "تعذّر التحميل. لن يظهر شيء حتى يعود الاتصال.")}</p></div> : <HomeSkeleton />;
  }
  const { today, net_per_hour: net } = data;
  const week = net.this_week, month = net.month_to_date;
  const perHour = (b: any) => (b.with_pending_per_hour === null ? "—" : `$${Number(b.with_pending_per_hour).toFixed(2)}`);

  const toReview = today.to_review ?? data.stories.reduce((n, st) => n + st.clips_ready, 0);
  const approved = data.campaigns.filter((c) => c.status === "approved");
  const waiting = data.campaigns.filter((c) => c.status !== "approved" && c.status !== "paused" && !c.is_practice);
  const parked = data.campaigns.filter((c) => c.status === "paused");
  const processing = (progress.data?.items ?? []).filter((i) => i.state === "active").length;
  const n = (count: number, one: string, many: string, arOne: string, arMany: string) => (count === 1 ? tr(`1 ${one}`, arOne) : tr(`${count} ${many}`, `${count} ${arMany}`));
  // One main thing to do: review what is ready, else post what is accepted, else bring in a video, else find work.
  const primary = toReview > 0 ? { href: "/review", label: tr(`Review ${toReview} ${toReview === 1 ? "clip" : "clips"}`, `راجع ${toReview} ${toReview === 1 ? "مقطع" : "مقاطع"}`),
      big: n(toReview, "clip is waiting", "clips are waiting", "مقطع بانتظارك", "مقاطع بانتظارك"), why: tr("Swipe right to post. Swipe left to skip.", "اسحب لليمين للنشر ولليسار للتخطّي.") }
    : today.posts_due > 0 ? { href: "/queue", label: tr(`Post ${today.posts_due} ${today.posts_due === 1 ? "clip" : "clips"}`, `انشر ${today.posts_due} ${today.posts_due === 1 ? "مقطع" : "مقاطع"}`),
      big: n(today.posts_due, "clip is ready to post", "clips are ready to post", "مقطع جاهز للنشر", "مقاطع جاهزة للنشر"), why: tr("Accepted clips are waiting to go out.", "المقاطع المقبولة بانتظار النشر.") }
    : approved.length > 0 ? { href: "#add", label: tr("Add a video", "أضف فيديو"), big: processing ? tr("Making your clips", "نجهّز مقاطعك") : tr("Nothing to review", "لا شيء للمراجعة"),
      why: processing ? tr("Clips are being made from your last video.", "نقص المقاطع من آخر فيديو أضفته.") : tr("Give it a video to cut.", "أعطِه فيديو ليقصّه.") }
    : { href: "/explore", label: tr("Find a campaign", "ابحث عن حملة"), big: tr("Start with a campaign", "ابدأ بحملة"), why: tr("No approved campaign yet.", "لا توجد حملة موافَق عليها بعد.") };
  const worst = data.attention.find((a) => a.severity === "block") ?? data.attention[0];
  const stripe = (id: string) => tintFor(id);

  return (
    <div className="page home-grid">
      <h1 className="sr-only">{tr("Home", "الرئيسية")}</h1>
      <div className="stack-lg home-main enter">
        {data.kill_switch.active && <div className="banner block" role="alert">{tr("The kill switch is on. No new jobs run and ready-to-post clips are hidden.", "مفتاح الإيقاف مفعّل. لا مهام جديدة وتختفي المقاطع الجاهزة للنشر.")}</div>}

        <section className="hero" aria-label={tr("What to do now", "ماذا تفعل الآن")} data-testid="primary-action">
          <span className="eyebrow">{tr("Next", "التالي")}</span>
          <p className="big">{primary.big}</p>
          <p className="why">{primary.why}</p>
          {primary.href === "#add" ? <AddButton label={primary.label} /> : <Link className="btn primary block" href={primary.href}>{primary.label}</Link>}
        </section>

        {data.stories.length > 0 && (
          <section className="section" aria-label="Active campaigns">
            <div className="stories">
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
          </section>
        )}

        {(waiting.length > 0 || (today.posts_due > 0 && primary.href !== "/queue") || (toReview > 0 && primary.href !== "/review") || data.attention.length > 0) && (
          <section className="section" aria-label={tr("Needs you", "يحتاجك")}>
            <h2>{tr("Needs you", "يحتاجك")}</h2>
            <div className="group">
              {toReview > 0 && primary.href !== "/review" && <Link className="item" href="/review"><span className="grow"><span className="title">{n(toReview, "clip to review", "clips to review", "مقطع للمراجعة", "مقاطع للمراجعة")}</span></span><span className="flip chev">{UiIcon.chev}</span></Link>}
              {today.posts_due > 0 && primary.href !== "/queue" && <Link className="item" href="/queue"><span className="grow"><span className="title">{n(today.posts_due, "clip ready to post", "clips ready to post", "مقطع جاهز للنشر", "مقاطع جاهزة للنشر")}</span></span><span className="flip chev">{UiIcon.chev}</span></Link>}
              {data.attention.length > 0 && (
                <Link className={`item${worst?.severity === "block" ? " urgent" : ""}`} href="/alerts">
                  <span className="grow"><span className="title">{n(data.attention.length, "alert", "alerts", "تنبيه", "تنبيهات")}</span><span className="note">{worst ? plainAlert(worst.message).text : ""}</span></span>
                  <span className="flip chev">{UiIcon.chev}</span>
                </Link>
              )}
            </div>
            {waiting.map((card) => (
              <CampaignCard key={card.id} card={card}
                onApprove={() => act(card.id, "approve")} onPark={() => act(card.id, "park")} onOpen={() => router.push(`/campaign?id=${card.id}`)} />
            ))}
          </section>
        )}

        <ProgressCards data={progress.data} />

        <section className="section" aria-label={tr("Earnings", "الأرباح")}>
          <div className="section-head"><h2>{tr("Net pay per hour", "صافي الأجر في الساعة")}</h2><span className="row"><span className="badge">{tr("Confidence", "الثقة")}: {net.confidence}</span><Link href="/ledger">{tr("Ledger", "السجل المالي")}</Link></span></div>
          <div className="group">
            <div className="item" style={{ display: "grid", gap: 2 }}>
              <span className="money xl">{perHour(week)}</span>
              <span className="small muted">{tr("This week, with pending payouts", "هذا الأسبوع، مع الدفعات المعلّقة")} · {tr("month", "الشهر")} {perHour(month)} · {tr("paid", "مدفوع")} {money(month.paid_usd)}{month.fee_unknown ? ` · ${tr("some fees unknown", "بعض العمولات غير معروفة")}` : ""}</span>
            </div>
            <div className="stats" aria-label="Today">
              <div><span className="num">{today.hours_logged.toFixed(1)}<small className="muted" style={{ fontSize: "0.8rem" }}> / {today.hours_cap}h</small></span><span className="lab">{tr("Hours today", "ساعات اليوم")}</span></div>
              <div><span className="num">{today.clips_accepted}</span><span className="lab">{tr("Accepted", "مقبولة")}</span></div>
              <div><span className="num">{today.posts_done}</span><span className="lab">{tr("Posted", "منشورة")}</span></div>
            </div>
          </div>
        </section>

        <section id="campaigns" className="section" aria-label="Campaigns">
          <div className="section-head"><h2>{tr("Campaigns", "الحملات")}</h2><Link href="/explore">{tr("Find more", "ابحث عن المزيد")}</Link></div>
          {data.campaigns.length === 0 ? (
            <Empty icon={UiIcon.flag} title={tr("No campaigns yet", "لا حملات بعد")} body={tr("Find one in Explore, or add one by pasting its brief.", "ابحث عن واحدة في استكشاف، أو أضف واحدة بلصق البريف.")}
              action={<div className="row wrap" style={{ justifyContent: "center" }}><Link className="btn primary" href="/explore">{tr("Explore", "استكشف")}</Link><AddButton kind="campaign" label={tr("Paste a brief", "الصق بريف")} plain /></div>} />
          ) : (
            <div className="group">
              {[...approved, ...parked].map((card) => (
                <Link key={card.id} className="item" href={`/campaign?id=${card.id}`} data-testid="campaign-row">
                  <span className="lead" style={{ background: stripe(card.id) }} aria-hidden="true">{initials(card.name)}</span>
                  <span className="grow"><span className="title">{card.name}</span>
                    <span className="note">{card.is_practice ? tr("Practice", "تجريبية") : card.status === "paused" ? tr("Parked", "مؤجّلة") : [card.rate_per_1k !== null ? `${money(card.rate_per_1k)} / 1k` : null, deadlineText(card.hours_to_deadline), card.budget_left_pct !== null ? tr(`${card.budget_left_pct}% budget left`, `${card.budget_left_pct}% من الميزانية`) : null].filter(Boolean).join(" · ")}</span></span>
                  {card.clips_ready > 0 && <span className="tail">{card.clips_ready}</span>}
                  <span className="flip chev">{UiIcon.chev}</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

/** The primary Add button: opens the same Add sheet as the middle button of the bar. */
function AddButton({ label, kind, plain = false }: { label: string; kind?: "video" | "campaign"; plain?: boolean }) {
  const add = useAdd();
  return <button className={plain ? "btn" : "btn primary block"} onClick={() => add.open(kind ? { kind } : {})}>{label}</button>;
}
