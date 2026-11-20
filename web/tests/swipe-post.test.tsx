import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import Feed from "@/components/Feed";
import { postBarLines } from "@/components/PostBar";
import type { SwipeResult } from "@/lib/api";
import { LangProvider } from "@/lib/i18n";
import { clip, feedRoutes, mockApi, swipe } from "./helpers";

const TARGETS = [
  { platform: "tiktok", label: "TikTok", handle: "own_tt", manual: false },
  { platform: "instagram_reels", label: "Instagram", handle: "own_ig", manual: false },
];
const withTargets = (id: string, extra = {}) => clip(id, { post_targets: TARGETS, ...extra });
const when = (minutes: number) => new Date(Date.now() + minutes * 60000).toISOString();

const result = (over: Partial<SwipeResult> = {}): SwipeResult => ({
  review: { id: "r1", clip_id: "c1", decision: "accept" }, clip_id: "c1", practice: false, warning: null, skipped: [],
  targets: [
    { id: "t1", platform: "tiktok", label: "TikTok", handle: "own_tt", manual: false, scheduled_at: when(0), state: "scheduled" },
    { id: "t2", platform: "instagram_reels", label: "Instagram", handle: "own_ig", manual: false, scheduled_at: when(20), state: "scheduled" },
  ], ...over,
});

async function open(clips: any[], extra: Record<string, any> = {}, lang: "en" | "ar" = "en", config: Record<string, any> = {}) {
  const mock = mockApi(feedRoutes(clips, extra, config));
  render(<LangProvider initial={lang}><Feed onExit={() => undefined} /></LangProvider>);
  const card = await screen.findByTestId("clip-card");
  await waitFor(() => expect(mock.posts("/api/sessions")).toHaveLength(1));
  return { mock, card };
}
const right = (card: Element) => swipe(card, [100, 300], [260, 300]);

describe("swipe right: accept and confirm to post (decision 31)", () => {
  it("shows where a swipe would post on the card, before the swipe", async () => {
    await open([withTargets("c1"), withTargets("c2")]);
    expect(screen.getByTestId("targets").textContent).toBe("Swipe right posts to TikTok @own_tt, Instagram @own_ig");
  });

  it("a swipe right calls the swipe endpoint (not the plain accept) and the bar says what was queued, with Undo", async () => {
    const { mock, card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": result() });
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("Posts to TikTok @own_tt now"));
    expect(bar.textContent).toContain("Instagram @own_ig in 20 min");
    expect(within(bar).getByRole("button", { name: "Undo" })).toBeTruthy();
    expect(mock.posts("/api/clips/c1/swipe")).toHaveLength(1);
    expect(mock.posts("/api/clips/c1/accept")).toHaveLength(0);
    expect(mock.posts("/api/clips/c1/swipe")[0].body.session_id).toBe("s1");
    expect(mock.posts("/api/clips/c1/swipe")[0].body.request_id).toBeTruthy();
    expect(screen.getByText("1 of 1")).toBeTruthy();                      // the clip left at once; the next one is showing
  });

  it("Undo cancels through the server and puts the clip back where it was", async () => {
    const { mock, card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": result(), "POST /api/clips/c1/swipe/undo": { cancelled: 2 } });
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("Posts to"));
    fireEvent.click(within(bar).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(mock.posts("/swipe/undo")).toHaveLength(1));
    expect(mock.posts("/swipe/undo")[0].body.review_id).toBe("r1");
    await screen.findByText("1 of 2");
    await screen.findByText("Undone. The posts were cancelled.");
    expect(screen.queryByTestId("post-bar")).toBeNull();
  });

  it("a refused Undo (window passed) keeps the clip gone and says why", async () => {
    const { card } = await open([withTargets("c1"), withTargets("c2")], {
      "POST /api/clips/c1/swipe": result(), "POST /api/clips/c1/swipe/undo": { __status: 400, body: { error: "The 10-second Undo window has passed.", code: "undo_expired" } } });
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("Posts to"));
    fireEvent.click(within(bar).getByRole("button", { name: "Undo" }));
    await screen.findByText("The 10-second Undo window has passed.");
    expect(screen.getByText("1 of 1")).toBeTruthy();
  });

  it("an account that is not connected shows 'Ready to post manually' with the caption to copy and the video to download", async () => {
    const manual = result({ targets: [
      { id: "t1", platform: "tiktok", label: "TikTok", handle: "own_tt", manual: false, scheduled_at: when(0), state: "scheduled" },
      { id: "t2", platform: "instagram_reels", label: "Instagram", handle: "own_ig", manual: true, scheduled_at: when(20), state: "scheduled" }] });
    const { card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": manual });
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("Ready to post manually: Instagram @own_ig"));
    expect(bar.textContent).toContain("Posts to TikTok @own_tt now");
    expect(within(bar).getByRole("button", { name: "Copy caption" })).toBeTruthy();
    const download = within(bar).getByRole("link", { name: "Download video" });
    expect(download.getAttribute("href")).toContain("/api/media/c1.mp4");
    expect(download.hasAttribute("download")).toBe(true);
  });

  it("a second swipe while the first manual bar is still open gets its own Undo (the bar is per clip)", async () => {
    const manual = (id: string) => result({ review: { id: `r-${id}`, clip_id: id, decision: "accept" }, clip_id: id, targets: [
      { id: `t-${id}`, platform: "tiktok", label: "TikTok", handle: "own_tt", manual: true, scheduled_at: when(0), state: "scheduled" }] });
    const { card } = await open([withTargets("c1"), withTargets("c2"), withTargets("c3")],
      { "POST /api/clips/c1/swipe": manual("c1"), "POST /api/clips/c2/swipe": manual("c2") }, "en", { undo_window_s: 1 });
    right(card);
    await waitFor(() => expect(within(screen.getByTestId("post-bar")).queryByRole("button", { name: "Undo" })).toBeTruthy());
    await waitFor(() => expect(within(screen.getByTestId("post-bar")).queryByRole("button", { name: "Undo" })).toBeNull(), { timeout: 3000 });
    right(screen.getAllByTestId("clip-card")[0]);
    await waitFor(() => expect(within(screen.getByTestId("post-bar")).queryByRole("button", { name: "Undo" })).toBeTruthy(), { timeout: 3000 });
  });

  it("a practice clip is accepted and the bar says nothing is posted", async () => {
    const { card } = await open([withTargets("c1", { is_practice: true }), withTargets("c2")], { "POST /api/clips/c1/swipe": result({ practice: true, targets: [] }) });
    right(card);
    await screen.findByText("Accepted. Practice clips are never posted.");
  });

  it("a compliance warning shows in the bar and the Undo stays", async () => {
    const { card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": result({ warning: "Compliance needs a look: caption starts with disclosure." }) });
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("Compliance needs a look"));
    expect(within(bar).getByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("a clip the server says failed compliance does not post: the clip stays and the compliance report opens", async () => {
    const { card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": { __status: 400, body: { error: "This clip did not pass the compliance check.", code: "compliance_blocked" } } });
    right(card);
    await screen.findByRole("dialog", { name: "Compliance report" });
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(screen.queryByTestId("post-bar")).toBeNull();
  });

  it("a clip already marked failed opens the report on a swipe and never calls the server", async () => {
    const { mock, card } = await open([withTargets("c1", { compliance_status: "fail" }), withTargets("c2")]);
    right(card);
    await screen.findByRole("dialog", { name: "Compliance report" });
    expect(mock.posts("/swipe")).toHaveLength(0);
  });

  it("offline, nothing is posted: the clip stays and the screen says so", async () => {
    const { mock, card } = await open([withTargets("c1"), withTargets("c2")]);
    mock.state.offline = true;
    right(card);
    await screen.findByText("No connection. Nothing was posted.");
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(screen.queryByTestId("post-bar")).toBeNull();
  });

  it("a refusal from the server (kill switch, brief) puts the clip back with the reason", async () => {
    const { card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": { __status: 403, body: { error: "The kill switch is on." } } });
    right(card);
    await screen.findByText("The kill switch is on.");
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });

  it("a stale brief check offers Re-check brief on the refusal and the owner can swipe again", async () => {
    const { mock, card } = await open([withTargets("c1"), withTargets("c2")], {
      "POST /api/clips/c1/swipe": { __status: 400, body: { error: "stale", code: "brief_stale" } }, "POST /api/campaigns/camp-1/recheck": { changed: false } });
    right(card);
    await screen.findByText("The brief check is older than 6 hours. Re-check it, then swipe again.");
    fireEvent.click(screen.getByRole("button", { name: "Re-check brief" }));
    await screen.findByText("Brief re-checked. Swipe right again to post.");
    expect(mock.posts("/api/campaigns/camp-1/recheck")[0].body).toEqual({});
    expect(screen.getByText("1 of 2")).toBeTruthy();       // the clip is back in the feed
  });

  it("when the owner must read the brief, the second step confirms it is unchanged (Arabic strings too)", async () => {
    const { mock, card } = await open([withTargets("c1"), withTargets("c2")], {
      "POST /api/clips/c1/swipe": { __status: 400, body: { error: "stale", code: "brief_stale" } },
      "POST /api/campaigns/camp-1/recheck": (body: any) => body.unchanged_confirmed ? { changed: false } : { __status: 400, body: { error: "Open the brief", code: "recheck_needed" } } }, "ar");
    right(card);
    fireEvent.click(await screen.findByRole("button", { name: "أعد فحص الملخّص" }));
    await screen.findByText("افتح الملخّص واقرأه، ثم أكّد أنه لم يتغيّر.");
    fireEvent.click(screen.getByRole("button", { name: "قرأته من جديد: لم يتغيّر" }));
    await waitFor(() => expect(mock.posts("/api/campaigns/camp-1/recheck")).toHaveLength(2));
    expect(mock.posts("/api/campaigns/camp-1/recheck")[1].body).toEqual({ unchanged_confirmed: true });
  });

  it("a clip whose gate failed on disclosure or language shows why and a swipe right sends nothing", async () => {
    const { mock, card } = await open([withTargets("c1", { quality: { p_clear: 0.5, label: "x", basis: "assumed", weak: true, swipe_blockers: ["no_disclosure"] } }), withTargets("c2")]);
    expect(screen.getByTestId("gate-blocked").textContent).toContain("Quality check failed");
    right(card);
    await screen.findByRole("alert");
    expect(mock.posts("/swipe")).toHaveLength(0);
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });

  it("the accept button and the A key still accept without posting", async () => {
    const { mock } = await open([withTargets("c1"), withTargets("c2")]);
    fireEvent.keyDown(window, { key: "a" });
    await waitFor(() => expect(mock.posts("/api/clips/c1/accept")).toHaveLength(1));
    expect(mock.posts("/swipe")).toHaveLength(0);
  });
});

describe("the bar text", () => {
  const t = (key: string) => ({ postsTo: "Posts to", postNow: "now", postIn: "in", minShort: "min", manualReady: "Ready to post manually", accepted: "Accepted",
    practiceNoPost: "P", noAccountsToPost: "N" } as any)[key] ?? key;
  it("says each platform and when, counted from the swipe", () => {
    const lines = postBarLines(result(), Date.now(), t as any);
    expect(lines.main).toMatch(/^Posts to TikTok @own_tt now · Instagram @own_ig in 20 min$/);
  });
  it("lists every platform the same way: there is no platform-specific text", () => {
    const many = result({ targets: ["youtube_shorts", "instagram_reels", "tiktok", "facebook_reels"].map((p, i) => (
      { id: `t${i}`, platform: p, label: p, handle: "h", manual: i === 1, scheduled_at: when(20 * i), state: "scheduled" })) });
    const lines = postBarLines(many, Date.now(), t as any);
    expect(lines.main).toContain("youtube_shorts @h now");
    expect(lines.main).toContain("facebook_reels @h in 60 min");
    expect(lines.manual).toBe("Ready to post manually: instagram_reels @h");
  });
});

describe("Arabic and layout", () => {
  it("shows the Arabic labels for the target line, the bar and the Undo, with the layout mirrored", async () => {
    const { card } = await open([withTargets("c1"), withTargets("c2")], { "POST /api/clips/c1/swipe": result() }, "ar");
    expect(document.documentElement.dir).toBe("rtl");
    expect(screen.getByTestId("targets").textContent).toContain("السحب لليمين ينشر على");
    right(card);
    const bar = await screen.findByTestId("post-bar");
    await waitFor(() => expect(bar.textContent).toContain("سيُنشر على"));
    expect(within(bar).getByRole("button", { name: "تراجع" })).toBeTruthy();
  });

  const CSS = readFileSync("app/globals.css", "utf-8");
  const rules = (selector: string) => [...CSS.matchAll(new RegExp(`^${selector.replace(/[.[\]]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "gm"))].map((m) => m[1]).join(" ");
  it("the bar is fixed (no layout shift), keeps 44 px touch targets and uses logical properties", () => {
    expect(rules(".swipebar")).toMatch(/position:\s*fixed/);
    expect(rules(".swipebar .btn")).toMatch(/min-height:\s*44px/);
    expect(rules(".swipebar")).toMatch(/inset-inline/);
    const all = [...CSS.matchAll(/^\.swipebar[^{\n]*\{([^}]*)\}/gm)].map((m) => m[1]).join(" ");
    expect(all).not.toMatch(/(^|[;\s])(left|right|margin-left|margin-right|padding-left|padding-right)\s*:/);
  });
});
