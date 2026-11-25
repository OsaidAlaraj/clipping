import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ push: vi.fn(), path: "/" }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push, replace: nav.push }), usePathname: () => nav.path, useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, prefetch: _p, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import ExploreScreen, { ListingPage } from "@/components/Explore";
import Feed from "@/components/Feed";
import Home from "@/components/Home";
import Queue from "@/components/Queue";
import Shell from "@/components/Shell";
import ToastHost from "@/components/ToastHost";
import { readOutbox, reviewAction } from "@/lib/api";
import { fetchData, FRESH_MS, mutate, peek, useData } from "@/lib/data";
import { LangProvider } from "@/lib/i18n";
import { useSwipe } from "@/lib/useSwipe";
import { clip, feedRoutes, mockApi, swipe } from "./helpers";

const tick = (ms = 0) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const never = () => new Promise<never>(() => undefined);
const feedOf = (n: number) => Array.from({ length: n }, (_, i) => clip(`c${i + 1}`, { campaign_name: `Show ${i + 1}` }));
const FEED_META = { kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary" };
/** Hold every request whose path contains the fragment until `release()` (never, for a request that never answers). Call after mockApi. */
function hold(fragment: string, method = "POST") {
  const base = globalThis.fetch;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.stubGlobal("fetch", (input: any, init?: any) => ((init?.method ?? "GET").toUpperCase() === method && String(input).includes(fragment) ? gate.then(() => base(input, init)) : base(input, init)));
  return release;
}
const open = async (routes: Record<string, any>, before?: () => void) => {
  const mock = mockApi(routes);
  before?.();
  render(<LangProvider initial="en"><Feed onExit={() => undefined} /><ToastHost /></LangProvider>);
  const card = await screen.findByTestId("clip-card");
  return { mock, card };
};

describe("cache (stale while revalidate)", () => {
  it("two screens asking for the same path share one request", async () => {
    const mock = mockApi({ "GET /api/home": { n: 1 } });
    await Promise.all([fetchData("/api/home"), fetchData("/api/home")]);
    expect(mock.calls.filter((c) => c.path === "/api/home")).toHaveLength(1);
  });

  it("a copy newer than the fresh window is reused, an older one is fetched again", async () => {
    const mock = mockApi({ "GET /api/home": { n: 1 } });
    await fetchData("/api/home");
    await fetchData("/api/home");
    expect(mock.calls).toHaveLength(1);
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + FRESH_MS + 1);
    await fetchData("/api/home");
    expect(mock.calls).toHaveLength(2);
  });

  it("a screen opened a second time shows the old data in its first render, then the new data", async () => {
    mockApi({ "GET /api/probe": { v: "old" } });
    const Probe = () => <p>{useData<{ v: string }>("/api/probe").data?.v ?? "blank"}</p>;
    const first = render(<Probe />);
    await screen.findByText("old");
    first.unmount();
    mockApi({ "GET /api/probe": { v: "new" } });
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + FRESH_MS + 1);
    const second = render(<Probe />);
    expect(second.container.textContent).toBe("old"); // first paint: no blank, no spinner
    await screen.findByText("new");
  });

  it("slow 4G: a dropped request is retried quietly and the old data stays", async () => {
    const mock = mockApi({ "GET /api/probe": { v: 2 } });
    mutate("/api/probe", () => ({ v: 1 }));
    mock.state.offline = true;
    const pending = fetchData("/api/probe", { force: true });
    await tick(100);
    mock.state.offline = false;
    expect(peek<{ v: number }>("/api/probe")).toEqual({ v: 1 }); // still showing the old copy
    await expect(pending).resolves.toEqual({ v: 2 });
    expect(peek<{ v: number }>("/api/probe")).toEqual({ v: 2 });
  });

  it("a refused request keeps the old data and reports the error", async () => {
    mockApi({ "GET /api/probe": { __status: 500, body: { error: "Boom" } } });
    mutate("/api/probe", () => ({ v: 1 }));
    await expect(fetchData("/api/probe", { force: true })).rejects.toThrow("Boom");
    expect(peek("/api/probe")).toEqual({ v: 1 });
  });
});

describe("DW-7.1 screens answer at once", () => {
  it("Home shows a skeleton in its very first render, with the same structure as the real screen", () => {
    vi.stubGlobal("fetch", vi.fn(never));
    const view = render(<Home />);
    expect(view.container.querySelector("[aria-busy='true'] .sk")).toBeTruthy();
    expect(view.container.querySelector(".stories")).toBeTruthy();
  });

  it("Explore and Queue show skeletons while their data is on its way", () => {
    vi.stubGlobal("fetch", vi.fn(never));
    const a = render(<ExploreScreen />);
    expect(a.container.querySelector("[aria-busy='true'] .sk")).toBeTruthy();
    a.unmount();
    const b = render(<Queue />);
    expect(b.container.querySelector("[aria-busy='true'] .sk")).toBeTruthy();
  });

  it("the review feed shows its dark skeleton, not a blank page", () => {
    vi.stubGlobal("fetch", vi.fn(never));
    const view = render(<LangProvider initial="en"><Feed /></LangProvider>);
    expect(view.container.querySelector(".feed[aria-busy='true'] .sk")).toBeTruthy();
  });

  it("slow 4G: a screen seen before opens with its data while the slow refresh is still running", () => {
    mutate("/api/queue", () => ({ kill_switch: false, items: [clip("q1", { status: "accepted" })] }));
    vi.stubGlobal("fetch", vi.fn(never)); // the network never answers
    render(<Queue />);
    expect(screen.getByText("Hook for q1")).toBeTruthy();
  });

  it("touching a tab warms the data of the screen behind it", () => {
    nav.path = "/";
    const mock = mockApi({ "GET /api/counts": { to_review: 0, alerts: 0 }, "GET /api/listings?sort=rank": { listings: [] }, "GET /api/me": { niches: [], all_niches: [] } });
    render(<LangProvider initial="en"><Shell><p>x</p></Shell></LangProvider>);
    const before = mock.calls.length;
    fireEvent.pointerEnter(screen.getAllByRole("link", { name: "Explore" })[0]);
    const paths = mock.calls.slice(before).map((c) => c.path);
    expect(paths).toEqual(expect.arrayContaining(["/api/listings?sort=rank", "/api/me"]));
  });

  it("offline actions show a floating saved notice, are retried by the shell, and end with All saved", async () => {
    const mock = mockApi({ "GET /api/counts": { to_review: 0, alerts: 0 }, "POST /api/clips/": { id: "r1", decision: "accept" } });
    mock.state.offline = true;
    await reviewAction("accept", "c9");
    expect(readOutbox()).toHaveLength(1);
    render(<LangProvider initial="en"><Shell><p>x</p></Shell></LangProvider>);
    const pill = await screen.findByText(/1 saved/);
    expect(pill.className).toContain("sync-pill");
    mock.state.offline = false;
    fireEvent(window, new Event("online"));
    await screen.findByText("All saved");
    expect(readOutbox()).toHaveLength(0);
  });
});

describe("DW-7.2 optimistic actions", () => {
  it("accept and save for later update the screen before the server answers", async () => {
    const { card } = await open(feedRoutes(feedOf(4)), () => { hold("/accept"); hold("/later"); });
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    expect(await screen.findByText("1 of 3")).toBeTruthy(); // c1 left while its request is still open
    expect(screen.getByText("Show 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review later" }));
    expect(await screen.findByText("1 of 2")).toBeTruthy();
    expect(screen.getByText("Show 3")).toBeTruthy();
  });

  it("accept offers Undo too, and Undo puts the clip back at once", async () => {
    const { mock, card } = await open(feedRoutes(feedOf(3)));
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    expect(await screen.findByText("Accepted")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("1 of 3")).toBeTruthy();
    await waitFor(() => expect(mock.posts("/api/reviews/undo")).toHaveLength(1));
  });

  it("save for later says it was saved and offers Undo", async () => {
    await open(feedRoutes(feedOf(3)));
    fireEvent.click(screen.getByRole("button", { name: "Review later" }));
    expect(await screen.findByText("Saved for later")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("a refused save for later puts the clip back where it was and says so", async () => {
    await open(feedRoutes(feedOf(3), { "POST /api/clips/c2/later": { __status: 403, body: { error: "This clip is closed." } } }));
    fireEvent.keyDown(window, { key: "ArrowDown" });        // c1 is skipped, so c2 is on screen
    expect(await screen.findByText("Show 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review later" }));
    await screen.findByText("This clip is closed.");
    expect(screen.getByText("The clip is back in your feed.")).toBeTruthy();
    expect(screen.getByText("1 of 3")).toBeTruthy();
    expect(screen.getByText("Show 2")).toBeTruthy();
  });

  it("a refused reject rolls back with the server's reason", async () => {
    const { card } = await open(feedRoutes(feedOf(2), { "POST /api/clips/c1/reject": { __status: 403, body: { error: "Already decided." } } }));
    swipe(card, [260, 300], [100, 300]);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("radio", { name: "Duplicate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject clip" }));
    await screen.findByText("Already decided.");
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(screen.getByText("Show 1")).toBeTruthy();
  });

  it("Undo pressed before the server has answered still works once it does", async () => {
    let answer: () => void = () => undefined;
    const { mock, card } = await open(feedRoutes(feedOf(3)), () => { answer = hold("/accept"); });
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByText("1 of 3")).toBeTruthy(); // back at once
    expect(mock.posts("/api/reviews/undo")).toHaveLength(0); // waits for the decision to exist
    answer();
    await waitFor(() => expect(mock.posts("/api/reviews/undo")).toHaveLength(1));
  });

  it("a refused Undo takes the clip out again with the reason", async () => {
    const { card } = await open(feedRoutes(feedOf(3), { "POST /api/reviews/undo": { __status: 400, body: { error: "The 10-second Undo window has passed." } } }));
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await screen.findByText("The 10-second Undo window has passed.");
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });

  it("Home: approve shows at once, and a refusal puts the card back with the reason", async () => {
    const card = (id: string) => ({ id, name: `Campaign ${id}`, status: "pending_approval", status_reason: null, marketplace: "Content Rewards", niche: "podcast", rate_per_1k: 1.5,
      fee_known: true, est_net_rate_per_1k: 1.35, budget_left_pct: 80, hours_to_deadline: 20, compliance_status: "pass", platforms: ["tiktok"], clips_ready: 0, unclear_fields: [] });
    const bucket = { paid_usd: "0", pending_usd: "0", hours: 0, paid_per_hour: null, with_pending_per_hour: null, fee_unknown: false };
    mockApi({
      "GET /api/home": { stories: [], today: { hours_logged: 0, hours_cap: 5, clips_accepted: 0, posts_done: 0, posts_due: 0 }, net_per_hour: { this_week: bucket, month_to_date: bucket, confidence: "low" },
        attention: [], campaigns: [card("p1")], kill_switch: { active: false } },
      "GET /api/progress": { active: false, items: [] },
      "POST /api/campaigns/p1/approve": { __status: 403, body: { error: "Hard-blocked." } },
    });
    render(<><Home /><ToastHost /></>);
    const first = await screen.findByTestId("campaign-card");
    swipe(first, [100, 300], [260, 300]);
    expect(screen.queryByTestId("campaign-card")).toBeNull(); // it left the decisions at once, before the server has said anything
    expect(screen.getByTestId("campaign-row").textContent).toContain("Campaign p1");
    await screen.findByText("Hard-blocked.");
    await waitFor(() => expect(screen.getByTestId("campaign-card").textContent).toContain("rules: pass")); // and is back, with the reason
  });

  it("Not for me leaves the page at once, can be undone, and the list is restored", async () => {
    const row = { id: "1", title: "Campaign 1", niche: "podcast", platforms: ["tiktok"], rank: { reasons: [], modelled: false, account: null } };
    mutate("/api/listings?sort=rank", () => ({ listings: [row], market: null, last_refresh: null }));
    const detail = { ...row, rate_per_1k: 2, rules: [], join: {}, verdicts: [], categories: ["podcast"] };
    const mock = mockApi({ "GET /api/listings/1": detail, "POST /api/listings/1/dismiss": { ok: true } });
    render(<><ListingPage id="1" /><ToastHost /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Not for me" }));
    expect(nav.push).toHaveBeenCalledWith("/explore");
    expect(peek<any>("/api/listings?sort=rank").listings).toHaveLength(0); // gone from the list before the server answered
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(peek<any>("/api/listings?sort=rank").listings).toHaveLength(1);
    await waitFor(() => expect(mock.posts("dismiss?undo=true")).toHaveLength(1));
  });

  it("Not for me refused by the server brings the campaign back and says why", async () => {
    mutate("/api/listings?sort=rank", () => ({ listings: [{ id: "1", title: "C" }], market: null, last_refresh: null }));
    mockApi({ "GET /api/listings/1": { id: "1", title: "C", rank: { reasons: [], modelled: false, account: null }, rules: [], join: {}, verdicts: [], categories: [] },
      "POST /api/listings/1/dismiss": { __status: 500, body: { error: "Try again later." } } });
    render(<><ListingPage id="1" /><ToastHost /></>);
    fireEvent.click(await screen.findByRole("button", { name: "Not for me" }));
    await screen.findByText(/Try again later\. It is back in the list\./);
    expect(peek<any>("/api/listings?sort=rank").listings).toHaveLength(1);
  });
});

describe("DW-7.3 the feed plays the next clip with no visible load", () => {
  const videos = () => [...document.querySelectorAll<HTMLVideoElement>(".feed video")];

  it("keeps the clip on screen, and the next two, mounted and buffered", async () => {
    const { card } = await open(feedRoutes(feedOf(6)));
    expect(videos().map((v) => v.getAttribute("src"))).toEqual(["/api/media/c1.mp4?exp=1&sig=x", "/api/media/c2.mp4?exp=1&sig=x", "/api/media/c3.mp4?exp=1&sig=x"]);
    expect(videos().map((v) => v.preload)).toEqual(["auto", "auto", "metadata"]); // the next clip is fully buffered
    expect(document.querySelectorAll(".feed-card.idle")).toHaveLength(2);
    expect(card.querySelector("video")).toBe(videos()[0]);
  });

  it("a swipe reuses the already-loaded video element of the next clip, so nothing reloads", async () => {
    const { card } = await open(feedRoutes(feedOf(5)));
    const nextVideo = videos()[1];
    const play = vi.spyOn(nextVideo, "play");
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    await screen.findByText("Show 2");
    const now = screen.getByTestId("clip-card").querySelector("video");
    expect(now).toBe(nextVideo); // the same element: it was already buffered
    expect(now!.getAttribute("src")).toBe("/api/media/c2.mp4?exp=1&sig=x");
    await waitFor(() => expect(play).toHaveBeenCalled());
    expect(videos()).toHaveLength(3);
  });

  it("only the visible clip has a progress bar and controls; buffered ones are hidden from assistive tech", async () => {
    await open(feedRoutes(feedOf(4)));
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect([...document.querySelectorAll(".feed-card.idle")].every((el) => el.getAttribute("aria-hidden") === "true")).toBe(true);
  });

  it("a very long feed (1,000 clips) mounts four videos at most and still moves through it", async () => {
    await open(feedRoutes(feedOf(1000)));
    expect(videos().length).toBeLessThanOrEqual(4);
    for (let i = 0; i < 40; i += 1) fireEvent.keyDown(window, { key: "ArrowDown" });
    // Six clips rotate at the front; each is skipped twice and the third skip parks it, so 40 skips park c1 to c12 and leave c17 first.
    expect(await screen.findByText("Show 17")).toBeTruthy();
    expect(screen.getByText("1 of 988")).toBeTruthy();
    expect(videos().length).toBeLessThanOrEqual(4);
  });

  it("a skipped clip returns after five other clips, and the third skip moves it to Review Later", async () => {
    const { mock } = await open(feedRoutes(feedOf(8)));
    fireEvent.keyDown(window, { key: "ArrowDown" });                    // skip c1: [c2..c6, c1, c7, c8]
    for (let i = 0; i < 5; i += 1) { await screen.findByText(`Show ${i + 2}`); fireEvent.keyDown(window, { key: "a" }); }
    expect(await screen.findByText("Show 1")).toBeTruthy();             // back after five others
    expect(mock.posts("/api/clips/c1/skip")[0].body.anchor_id).toBe("c6");
    fireEvent.keyDown(window, { key: "ArrowDown" });                    // second skip: it comes back once more
    await screen.findByText("Show 7");
    fireEvent.keyDown(window, { key: "ArrowDown" });                    // c7 skipped, then c8 is the only other clip
    await screen.findByText("Show 8");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    await screen.findByText("Show 1");
    fireEvent.keyDown(window, { key: "ArrowDown" });                    // third skip of c1
    await screen.findByText("Skipped three times: moved to Review Later.");
    expect(mock.posts("/api/clips/c1/skip")).toHaveLength(3);
    expect(screen.queryByText("Show 1")).toBeNull();
  });

  it("the server's order is shown as it comes: a clip already skipped is not moved again by the screen", async () => {
    await open(feedRoutes([clip("c1", { campaign_name: "Show 1" }), clip("c2", { campaign_name: "Show 2", skip_count: 1 })]));
    expect(screen.getByText("Show 1")).toBeTruthy();
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });

  it("a feed seen a moment ago is on screen straight away, and the fresh one replaces it before anything is decided", async () => {
    mutate("/api/feed", () => ({ clips: feedOf(2), ...FEED_META }));
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + FRESH_MS + 1);
    mockApi(feedRoutes(feedOf(5)));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    expect(screen.getByText("1 of 2")).toBeTruthy(); // no skeleton, no wait
    await screen.findByText("1 of 5");
  });

  it("once a clip is decided, a fresher list from the server does not reshuffle the screen", async () => {
    mutate("/api/feed", () => ({ clips: feedOf(3), ...FEED_META }));
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + FRESH_MS + 1);
    mockApi(feedRoutes(feedOf(9)));
    const release = hold("/api/feed", "GET");
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    await screen.findByTestId("clip-card");
    fireEvent.keyDown(window, { key: "a" });
    await screen.findByText("1 of 2");
    release(); // the fresh 9-clip list arrives only now
    await tick(60);
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });
});

describe("swiping stays at frame rate", () => {
  it("a drag moves the card through its transform without re-rendering React on every move", async () => {
    let renders = 0;
    function Pad() {
      renders += 1;
      const { bind, ref, intent } = useSwipe({}, { tilt: true });
      return <div data-testid="pad" data-intent={intent ?? ""} ref={ref as React.RefObject<HTMLDivElement>} {...bind} />;
    }
    render(<Pad />);
    const pad = screen.getByTestId("pad");
    const base = renders;
    fireEvent.pointerDown(pad, { clientX: 100, clientY: 200, pointerId: 1 });
    for (let x = 105; x <= 200; x += 5) fireEvent.pointerMove(pad, { clientX: x, clientY: 200, pointerId: 1 });
    await tick(60);
    expect(pad.style.transform).toContain("translate3d(100px");
    expect(pad.getAttribute("data-intent")).toBe("right");
    expect(renders - base).toBeLessThanOrEqual(2); // 20 moves, one re-render when the flag appeared
    fireEvent.pointerUp(pad, { clientX: 200, clientY: 200, pointerId: 1 });
    expect(pad.style.transform).toBe(""); // snapped back
  });
});

describe("long lists", () => {
  it("Explore draws a page of campaigns at a time and adds the next page when the end comes near", async () => {
    let reveal: () => void = () => undefined;
    vi.stubGlobal("IntersectionObserver", class { constructor(cb: any) { reveal = () => cb([{ isIntersecting: true }]); } observe() {} disconnect() {} });
    const listings = Array.from({ length: 200 }, (_, i) => ({ id: `l${i}`, title: `Campaign ${i}`, niche: "podcast", platforms: ["tiktok"], board_slug: "vyro", rate_per_1k: 2,
      rank: { reasons: [], modelled: false, account: null } }));
    mockApi({ "GET /api/me": { niches: ["podcast"], all_niches: ["podcast"] }, "GET /api/listings": { listings, market: null, last_refresh: null } });
    render(<ExploreScreen />);
    await screen.findAllByTestId("listing-card");
    expect(screen.getAllByTestId("listing-card")).toHaveLength(30);
    await tick(30); // the niche filter settles first
    act(() => reveal());
    await waitFor(() => expect(screen.getAllByTestId("listing-card")).toHaveLength(60));
  });
});

describe("seeing the whole clip", () => {
  it("Full screen is the first button on the rail, unmutes, and asks the video for full screen", async () => {
    const { card } = await open(feedRoutes(feedOf(2)));
    const rail = card.querySelector(".rail")!;
    expect(rail.querySelector("button")!.getAttribute("aria-label")).toBe("Full screen");
    const video = card.querySelector("video") as HTMLVideoElement;
    const request = vi.fn(() => Promise.resolve());
    (video as any).requestFullscreen = request;
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(request).toHaveBeenCalledTimes(1);
    expect(video.muted).toBe(false);
  });

  it("on iPhone, where only the video's own player can go full screen, it falls back to webkitEnterFullscreen", async () => {
    const { card } = await open(feedRoutes(feedOf(2)));
    const video = card.querySelector("video") as any;
    video.requestFullscreen = undefined;
    video.webkitEnterFullscreen = vi.fn();
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(video.webkitEnterFullscreen).toHaveBeenCalledTimes(1);
  });

  it("a refused requestFullscreen also falls back to webkitEnterFullscreen", async () => {
    const { card } = await open(feedRoutes(feedOf(2)));
    const video = card.querySelector("video") as any;
    video.requestFullscreen = () => Promise.reject(new Error("denied"));
    video.webkitEnterFullscreen = vi.fn();
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    await waitFor(() => expect(video.webkitEnterFullscreen).toHaveBeenCalled());
  });

  it("the Arabic label is the right-to-left one", async () => {
    mockApi(feedRoutes(feedOf(2)));
    render(<LangProvider initial="ar"><Feed /></LangProvider>);
    await screen.findByTestId("clip-card");
    expect(screen.getByRole("button", { name: "ملء الشاشة" })).toBeTruthy();
  });

  it("the caption block can be hidden and shown again without deciding anything", async () => {
    const { mock, card } = await open(feedRoutes(feedOf(2)));
    const bottom = card.querySelector(".bottom")!;
    fireEvent.click(screen.getByRole("button", { name: "Hide text" }));
    expect(bottom.className).toContain("text-hidden");
    expect(bottom.getAttribute("aria-hidden")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Show text" }));
    expect(bottom.className).not.toContain("text-hidden");
    expect(mock.posts("/accept").length + mock.posts("/reject").length).toBe(0);
  });
});
