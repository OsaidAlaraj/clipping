"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export type ProgressStage = "uploaded" | "transcribing" | "moments" | "clips" | "ready";
export type ProgressItem = {
  id: string; source_id: string | null; campaign_id: string | null; name: string; video: string | null; is_practice: boolean;
  state: "active" | "ready" | "failed"; stage: ProgressStage; clips_done: number; clips_planned: number;
  eta_s: number | null; error: string | null; note: string | null;
};
export type ProgressData = { items: ProgressItem[]; active: boolean };

export const STEPS: { stage: ProgressStage; label: string }[] = [
  { stage: "uploaded", label: "Uploaded" }, { stage: "transcribing", label: "Transcribing" }, { stage: "moments", label: "Choosing moments" },
  { stage: "clips", label: "Making clips" }, { stage: "ready", label: "Ready" },
];
export const POLL_MS = 5000;
const STORE_KEY = "clipping_progress_v1";
const STORE_MAX_AGE_MS = 10 * 60 * 1000;

// The last answer is kept so Home paints at once when it is opened again, then refreshes in the background.
let cache: ProgressData | null = null;
export function resetProgressCache(): void { cache = null; }

function readStored(): ProgressData | null {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    return saved && Date.now() - saved.at < STORE_MAX_AGE_MS ? saved.data : null;
  } catch { return null; }
}
function writeStored(data: ProgressData): void {
  try { localStorage.setItem(STORE_KEY, JSON.stringify({ at: Date.now(), data })); } catch { /* storage unavailable */ }
}

/** Polls /api/progress every 5 s while a card is in progress and the tab is visible. Previous data stays on screen
 *  while refetching and when a request fails. `settled` turns true after the first answer (or failure). */
export function useProgress(): { data: ProgressData | null; settled: boolean } {
  const [data, setData] = useState<ProgressData | null>(cache);
  const [settled, setSettled] = useState(cache !== null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const visible = () => document.visibilityState !== "hidden";
    const stop = () => { if (timer) clearTimeout(timer); timer = undefined; };

    if (!cache) {
      const stored = readStored();
      if (stored) { cache = stored; setData(stored); setSettled(true); }
    }

    const tick = async () => {
      stop();
      if (!visible()) return;   // resumes on visibilitychange
      try {
        const next = await api<ProgressData>("/api/progress");
        if (!alive) return;
        if (JSON.stringify(next) !== JSON.stringify(cache)) { cache = next; setData(next); writeStored(next); }
      } catch { /* keep what is on screen; the next tick tries again while a card is in progress */ }
      if (!alive) return;
      setSettled(true);
      if (cache?.active && visible()) timer = setTimeout(tick, POLL_MS);
    };
    const onVisibility = () => { if (visible()) void tick(); else stop(); };

    void tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { alive = false; stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, []);

  return { data, settled };
}

export function percent(item: ProgressItem): number {
  if (item.stage === "ready") return 100;
  if (item.stage === "clips") return item.clips_planned ? Math.round(50 + 45 * Math.min(item.clips_done / item.clips_planned, 1)) : 50;
  return { uploaded: 5, transcribing: 20, moments: 40 }[item.stage];
}

export function etaText(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 60) return "Less than a minute left";
  return `About ${Math.ceil(seconds / 60)} min left`;
}

function Steps({ item }: { item: ProgressItem }) {
  const current = STEPS.findIndex((s) => s.stage === item.stage);
  return (
    <ol className="pg-steps" aria-label="Steps">
      {STEPS.map((step, i) => {
        const kind = i === current ? (item.state === "failed" ? "failed" : "current") : i < current ? "done" : "todo";
        return <li key={step.stage} className={`pg-step ${kind}`} aria-current={i === current ? "step" : undefined}>{step.label}</li>;
      })}
    </ol>
  );
}

export function ProgressCard({ item }: { item: ProgressItem }) {
  const failed = item.state === "failed", ready = item.state === "ready";
  const label = failed ? "Stopped" : ready ? "Ready" : STEPS.find((s) => s.stage === item.stage)!.label;
  const pct = percent(item);
  const body = (
    <>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="grow stack" style={{ gap: 0 }}>
          <strong dir="auto" className="pg-title">{item.name}</strong>
          {item.video && <span dir="auto" className="small muted pg-title">{item.video}</span>}
        </div>
        <span className={`badge ${failed ? "fail" : ready ? "pass" : "info"}`}>{label}</span>
      </div>
      <Steps item={item} />
      <div className={`bar pg-bar${failed ? " failed" : ""}`} role="progressbar" aria-label="Progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <div className="meta pg-meta">
        {ready ? <span>Ready — {item.clips_done} {item.clips_done === 1 ? "clip" : "clips"}</span>
          : item.stage === "clips" || item.clips_planned > 0 ? <span>{item.clips_done} of {item.clips_planned} clips</span>
          : <span>Clips come after the moments</span>}
        {!ready && !failed && <span>{etaText(item.eta_s)}</span>}
      </div>
      {ready && item.note && <p className="small muted">{item.note}</p>}
    </>
  );
  if (ready) {
    return <Link className="card stack pg-card" href={item.campaign_id ? `/review?campaign=${item.campaign_id}` : "/review"} aria-label={`${item.name}. Ready, ${item.clips_done} clips. Open Review.`} data-testid="progress-card">{body}</Link>;
  }
  return (
    <article className="card stack pg-card" aria-label={`${item.name}. ${failed ? "Stopped" : label}.`} data-testid="progress-card">
      {body}
      {failed && (
        <div className="banner block" role="alert">
          <div dir="auto">{item.error}</div>
          <Link href="/alerts" className="small">See Alerts</Link>
        </div>
      )}
    </article>
  );
}

export default function ProgressCards({ data }: { data: ProgressData | null }) {
  if (!data || data.items.length === 0) return null;
  return (
    <section className="stack" aria-label="Upload progress" data-testid="progress-section">
      <h2>Your videos</h2>
      {data.items.map((item) => <ProgressCard key={item.id} item={item} />)}
    </section>
  );
}
