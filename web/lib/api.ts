// Typed client for the Python API, plus the offline outbox for review actions.

export type Hook = { id: string; text: string; hook_type: string; chosen: boolean; rank: number; score: number | null };

/** How likely a clip is to reach the campaign's view minimum. `label` is the server's English sentence; the web builds the Arabic
 *  one from the numbers (see `qualityText` in i18n). `weak` clips are shown last. */
export type Quality = {
  p_clear: number | null; label: string; basis: "your posts" | "assumed"; weak?: boolean; min_views?: number | null;
  moment_percentile?: number | null; threshold?: number;
};

/** One compliance check. Brand checks (from a marketplace's rejection) also carry the brand, a readable rule and their severity. */
export type Check = { check: string; status: string; evidence: string; rule?: string; brand?: string; severity?: "block" | "warn" | "reminder" };

export type Clip = {
  id: string;
  status: string;
  campaign_id: string;
  campaign_name: string;
  account_handle: string | null;
  platform: string | null;
  language: string;
  duration_s: number | null;
  compliance_status: "pass" | "review" | "fail" | "pending";
  compliance_report: { deterministic: Check[]; rules: { rule: string; status: string; evidence: string }[]; note: string; legal_notice: string } | null;
  priority_reason?: string;
  caption_text: string | null;
  hashtags: string[];
  mentions: string[];
  video_url: string | null;
  hook_text: string | null;
  hooks: Hook[];
  park_reason: string | null;
  deadline: string | null;
  due_at: string | null;
  rate_per_1k: number | null;
  max_payout_per_clip: number | null;
  render_meta: Record<string, unknown>;
  start_s: number;
  end_s: number;
  source_id?: string | null;
  is_practice?: boolean;
  moment_reason?: string | null;
  cover_url?: string | null;
  quality?: Quality;
};

/** A moment the engine set aside (layer 1 hard rule, 2 below the episode's cut-off, 3 not among the top picks). */
export type FilteredMoment = {
  id: string; source_id: string; campaign_id: string; campaign_name: string; start_s: number; end_s: number; score: number | null;
  reason: string; layer: 1 | 2 | 3; profile: string | null; engine: string | null;
};

export type Review = { id: string; clip_id: string; decision: string; queued?: boolean };

export class ApiError extends Error {
  /** `data` is the whole JSON body of the refusal (for example the current version of a transcript that went stale). */
  constructor(message: string, public status: number, public code?: string, public data?: any) {
    super(message);
  }
}

/** True when the request never reached the server (offline, or the server is down). */
export class NetworkError extends Error {}

const TOKEN_KEY = "clipping_local_token";

function headers(): Record<string, string> {
  const out: Record<string, string> = { "Content-Type": "application/json" };
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) out.Authorization = `Bearer ${token}`;
  } catch {
    /* storage unavailable */
  }
  return out;
}

let signedOutHandler: (() => void) | undefined;
/** Register what runs when a 401 sends the person to sign in (the data cache clears itself). */
export function onSignedOut(handler: () => void): void { signedOutHandler = handler; }

export async function api<T = any>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
      headers: headers(),
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new NetworkError("No connection.");
  }
  if (response.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/signin")
      && !path.startsWith("/api/auth/")) {
    signedOutHandler?.();
    window.location.assign(`/signin?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    let code: string | undefined;
    let data: any;
    try {
      data = await response.json();
      message = data.error ?? (typeof data.detail === "string" ? data.detail : message);
      code = data.code;
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, response.status, code, data);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? await response.json() : await response.text()) as T;
}

// ---------------------------------------------------------------- offline outbox (spec section 7)
// Accept, reject and park are saved on the device and sent when online. Each action carries a
// client_action_id, so the server applies it once even if it is sent twice.

export type OutboxItem = { id: string; path: string; body: Record<string, unknown> };
const OUTBOX_KEY = "clipping_outbox";

export function readOutbox(): OutboxItem[] {
  try {
    return JSON.parse(localStorage.getItem(OUTBOX_KEY) ?? "[]");
  } catch {
    return [];
  }
}

export const OUTBOX_EVENT = "clipping:outbox";

function writeOutbox(items: OutboxItem[]): void {
  try {
    localStorage.setItem(OUTBOX_KEY, JSON.stringify(items));
  } catch {
    /* storage unavailable */
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(OUTBOX_EVENT)); // the "saved" notice follows the outbox at once
}

export function removeFromOutbox(id: string): boolean {
  const items = readOutbox();
  const next = items.filter((item) => item.id !== id);
  writeOutbox(next);
  return next.length !== items.length;
}

function actionId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `a-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Send a review action now, or queue it if there is no connection. */
export async function reviewAction(kind: "accept" | "reject" | "later", clipId: string, body: Record<string, unknown> = {}): Promise<Review> {
  const id = actionId();
  const item: OutboxItem = { id, path: `/api/clips/${clipId}/${kind}`, body: { ...body, client_action_id: id } };
  try {
    return await api<Review>(item.path, { body: item.body });
  } catch (error) {
    if (error instanceof NetworkError) {
      writeOutbox([...readOutbox(), item]);
      return { id, clip_id: clipId, decision: kind, queued: true };
    }
    throw error;
  }
}

/** Undo a decision. A decision still waiting in the outbox is simply removed. */
export async function undoReview(review: Review): Promise<void> {
  if (review.queued && removeFromOutbox(review.id)) return;
  await api("/api/reviews/undo", { body: { review_id: review.id } });
}

export async function flushOutbox(): Promise<number> {
  let sent = 0;
  for (const item of readOutbox()) {
    try {
      await api(item.path, { body: item.body });
    } catch (error) {
      if (error instanceof NetworkError) break; // still offline: keep the rest
      // The server refused it (for example the clip moved on). Drop it; the audit trail is on the server.
    }
    removeFromOutbox(item.id);
    sent += 1;
  }
  return sent;
}

/** Where large uploads go. The Next.js dev proxy holds request bodies in memory and refuses anything over 10 MB,
 *  so uploads go straight to the API: on the laptop at port 8787, through Tailscale via its /api route. */
export function uploadBase(): string {
  if (typeof window === "undefined") return "";
  const { hostname, port, protocol } = window.location;
  return (hostname === "localhost" || hostname === "127.0.0.1") && port === "3000" ? `${protocol}//${hostname}:8787` : "";
}

export const PLATFORM_LABEL: Record<string, string> = {
  tiktok: "TikTok",
  instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts",
  x: "X",
  facebook: "Facebook",
};

export function money(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return `$${Number(value).toFixed(2)}`;
}
