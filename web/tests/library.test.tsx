import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Library, { type LibraryClip } from "@/components/Library";

import { mockApi } from "./helpers";

// Owner note 13: accepted clips as a scroll of reels, with the collected numbers, grouped and filtered by campaign name.

function reel(id: string, extra: Partial<LibraryClip> = {}): LibraryClip {
  return {
    id, campaign_id: "c1", campaign_name: "Founders Table", hook_text: `Hook ${id}`, platform: "tiktok", status: "posted",
    updated_at: "2026-11-20T10:00:00Z", duration_s: 20, post_url: `https://example.com/${id}`, posted_at: "2026-11-21T10:00:00Z",
    views_h48: 500, views_d7: 1200, watch_time_s: 42, net_usd: 12.5, payout_status: "pending", video_url: `/api/media/${id}.mp4`, cover_url: null,
    ...extra,
  };
}

const BODY = {
  clips: [reel("a"), reel("b", { campaign_id: "c2", campaign_name: "Gym Mornings", views_h48: null, net_usd: null, payout_status: null })],
  campaigns: [{ id: "c1", name: "Founders Table", count: 1 }, { id: "c2", name: "Gym Mornings", count: 1 }],
  total: 2, sort: "newest",
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("accepted clips library", () => {
  it("shows each clip with its collected numbers instead of likes and comments", async () => {
    mockApi({ "GET /api/library": BODY });
    render(<Library />);
    expect(await screen.findByText("Hook a")).toBeTruthy();
    expect(screen.getAllByText("Founders Table").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Views, 48 h").length).toBe(2);
    expect(screen.getByText("500")).toBeTruthy();
    expect(screen.getByText("$12.50")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/like|comment|follower/i);
  });

  it("searches after a short pause, not on every letter", async () => {
    const api = mockApi({ "GET /api/library": BODY });
    render(<Library />);
    await screen.findByText("Hook a");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search clips" }), { target: { value: "gym" } });
    await waitFor(() => expect(api.calls.some((c) => c.path.includes("q=gym"))).toBe(true));
    expect(api.calls.filter((c) => c.path.includes("q=g")).length).toBe(1);
  });

  it("selecting a campaign chip asks for that campaign only", async () => {
    const api = mockApi({ "GET /api/library": BODY });
    render(<Library />);
    await screen.findByText("Hook a");
    fireEvent.click(screen.getByRole("button", { name: /Gym Mornings/ }));
    await waitFor(() => expect(api.calls.some((c) => c.path.includes("campaign_ids=c2"))).toBe(true));
    expect(screen.getByRole("button", { name: /Gym Mornings/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("the sort menu sends its choice to the server", async () => {
    const api = mockApi({ "GET /api/library": BODY });
    render(<Library />);
    await screen.findByText("Hook a");
    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "earned" } });
    await waitFor(() => expect(api.calls.some((c) => c.path.includes("sort=earned"))).toBe(true));
  });

  it("says so when there are no accepted clips yet", async () => {
    mockApi({ "GET /api/library": { clips: [], campaigns: [], total: 0, sort: "newest" } });
    render(<Library />);
    expect(await screen.findByText("No accepted clips yet")).toBeTruthy();
  });

  it("a clip with no video file says so instead of a broken player", async () => {
    mockApi({ "GET /api/library": { ...BODY, clips: [reel("z", { video_url: null })] } });
    render(<Library />);
    expect(await screen.findByText("No video file")).toBeTruthy();
    expect(document.querySelector("video")).toBeNull();
  });
});
