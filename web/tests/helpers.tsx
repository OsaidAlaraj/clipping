import { fireEvent } from "@testing-library/react";
import { vi } from "vitest";

import type { Clip } from "@/lib/api";

export type Call = { method: string; path: string; body: any };
type Handler = (body: any, path: string) => any;

/** Replace fetch with a small router. Keys are "METHOD /path-prefix". The longest matching key wins. */
export function mockApi(routes: Record<string, Handler | object>) {
  const calls: Call[] = [];
  const state = { offline: false };
  const fetchMock = vi.fn(async (input: any, init?: any) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(init.body) : undefined;
    if (state.offline) throw new TypeError("Failed to fetch");
    calls.push({ method, path, body });
    const key = Object.keys(routes).filter((k) => `${method} ${path}`.startsWith(k)).sort((a, b) => b.length - a.length)[0];
    const json = { "content-type": "application/json" };
    if (!key) return new Response(JSON.stringify({ error: `no mock for ${method} ${path}` }), { status: 404, headers: json });
    const route = (routes as any)[key];
    const result = typeof route === "function" ? (route as Handler)(body, path) : route;
    if (result && result.__status) return new Response(JSON.stringify(result.body), { status: result.__status, headers: json });
    return new Response(JSON.stringify(result ?? {}), { status: 200, headers: json });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, state, posts: (fragment: string) => calls.filter((c) => c.method === "POST" && c.path.includes(fragment)) };
}

export function clip(id: string, extra: Partial<Clip> = {}): Clip {
  return {
    id, status: "ready_for_review", campaign_id: "camp-1", campaign_name: "Founders Table", account_handle: "own_tt", platform: "tiktok",
    language: "en", duration_s: 13.4, compliance_status: "pass", compliance_report: null, priority_reason: "Top-paying campaign",
    caption_text: `#ad Hook for ${id} #FoundersTable`, hashtags: ["#FoundersTable"], mentions: [], video_url: `/api/media/${id}.mp4?exp=1&sig=x`,
    hook_text: `Hook for ${id}`,
    hooks: [{ id: `${id}-h1`, text: `Hook for ${id}`, hook_type: "question", chosen: false, rank: 1, score: 0.9 },
      { id: `${id}-h2`, text: `Second hook for ${id}`, hook_type: "stakes", chosen: false, rank: 2, score: 0.8 }],
    park_reason: null, deadline: null, due_at: null, rate_per_1k: 1.5, max_payout_per_clip: 150, render_meta: { hook_id: `${id}-h1` }, start_s: 5, end_s: 18.4,
    ...extra,
  };
}

export function swipe(element: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(element, { clientX: from[0], clientY: from[1], pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: (from[0] + to[0]) / 2, clientY: (from[1] + to[1]) / 2, pointerId: 1 });
  fireEvent.pointerMove(element, { clientX: to[0], clientY: to[1], pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: to[0], clientY: to[1], pointerId: 1 });
}

export const SUMMARY = {
  session_id: "s1", clips_reviewed: 2, accepted: 1, rejected: 1, parked: 0, posts_due: 1, minutes_logged: 25, hours_today: 0.42, length_setting: 25,
  pay_per_clip: [{ name: "Founders Table", rate_per_1k: 1.5, max_payout_per_clip: 150 }],
};

export function feedRoutes(clips: Clip[], overrides: Record<string, any> = {}, config: Record<string, any> = {}) {
  let n = 0;
  return {
    "GET /api/feed": { clips, kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary", ...config },
    "POST /api/sessions": { id: "s1", resume_position: null },
    "POST /api/sessions/s1/stop": SUMMARY,
    "POST /api/clips/": (_body: any, path: string) => ({ id: `review-${++n}`, clip_id: path.split("/")[3], decision: path.split("/")[4] }),
    "POST /api/reviews/undo": { id: "undo-1", decision: "undo" },
    ...overrides,
  };
}
