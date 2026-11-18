import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push, replace: nav.push }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Home from "@/components/Home";
import ProgressCards, { etaText, percent, POLL_MS, resetProgressCache, useProgress, type ProgressData, type ProgressItem } from "@/components/Progress";
import { mockApi } from "./helpers";

const item = (extra: Partial<ProgressItem> = {}): ProgressItem => ({
  id: "s1", source_id: "s1", campaign_id: "c1", name: "Practice · My first video", video: "talk.mp4", is_practice: true, state: "active",
  stage: "transcribing", clips_done: 0, clips_planned: 0, eta_s: 150, error: null, note: null, ...extra,
});
const data = (...items: ProgressItem[]): ProgressData => ({ items, active: items.some((i) => i.state === "active") });
const home = () => ({
  stories: [], today: { hours_logged: 0, hours_cap: 5, clips_accepted: 0, posts_done: 0, posts_due: 0 },
  net_per_hour: { this_week: { with_pending_per_hour: null, paid_usd: 0, hours: 0 }, month_to_date: { with_pending_per_hour: null, paid_usd: "0", hours: 0 }, confidence: "low" },
  attention: [], campaigns: [], kill_switch: { active: false },
});
const polls = (mock: ReturnType<typeof mockApi>) => mock.calls.filter((c) => c.path === "/api/progress").length;

function Probe() {
  const { data: d } = useProgress();
  return <ProgressCards data={d} />;
}
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

beforeEach(() => { resetProgressCache(); });

describe("progress cards", () => {
  it("shows the five steps with the current one highlighted and the earlier ones done", () => {
    render(<ProgressCards data={data(item({ stage: "moments", eta_s: 120 }))} />);
    const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual(["Uploaded", "Transcribing", "Choosing moments", "Making clips", "Ready"]);
    expect(steps.map((s) => s.className.replace("pg-step ", ""))).toEqual(["done", "done", "current", "todo", "todo"]);
    expect(steps[2].getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("About 2 min left")).toBeTruthy();
    expect(screen.getByText("Clips come after the moments")).toBeTruthy();
  });

  it("shows how many clips are done of how many are planned, and a progress bar that follows them", () => {
    render(<ProgressCards data={data(item({ stage: "clips", clips_done: 1, clips_planned: 4, eta_s: 90 }))} />);
    expect(screen.getByText("1 of 4 clips")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(String(percent(item({ stage: "clips", clips_done: 1, clips_planned: 4 }))));
    expect(Number(screen.getByRole("progressbar").getAttribute("aria-valuenow"))).toBeGreaterThan(50);
  });

  it("a finished card says Ready with its clip count and opens Review for its campaign", () => {
    render(<ProgressCards data={data(item({ state: "ready", stage: "ready", clips_done: 3, clips_planned: 3, eta_s: null }))} />);
    expect(screen.getByText("Ready — 3 clips")).toBeTruthy();
    const card = screen.getByTestId("progress-card");
    expect(card.getAttribute("href")).toBe("/review?campaign=c1");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("100");
  });

  it("a finished card with one clip says clip, not clips", () => {
    render(<ProgressCards data={data(item({ state: "ready", stage: "ready", clips_done: 1, clips_planned: 1, eta_s: null }))} />);
    expect(screen.getByText("Ready — 1 clip")).toBeTruthy();
  });

  it("a failed card shows the reason in plain words and a link to Alerts", () => {
    render(<ProgressCards data={data(item({ state: "failed", stage: "transcribing", eta_s: null, error: "Source file hash mismatch. The job is stopped." }))} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Source file hash mismatch. The job is stopped.");
    expect(within(alert).getByRole("link", { name: "See Alerts" }).getAttribute("href")).toBe("/alerts");
    expect(screen.getByText("Stopped")).toBeTruthy();
    expect(screen.queryByText(/left$/)).toBeNull();
    expect(screen.getByRole("progressbar").className).toContain("failed");
  });

  it("Arabic and English names both lay out in their own direction", () => {
    render(<ProgressCards data={data(item({ id: "a", name: "مقطع تجريبي", video: "لقاء.mp4" }), item({ id: "b", name: "English name", video: null }))} />);
    expect(screen.getByText("مقطع تجريبي").getAttribute("dir")).toBe("auto");
    expect(screen.getByText("لقاء.mp4").getAttribute("dir")).toBe("auto");
    expect(screen.getByText("English name").getAttribute("dir")).toBe("auto");
  });

  it("renders nothing when there are no videos", () => {
    const view = render(<ProgressCards data={data()} />);
    expect(view.container.innerHTML).toBe("");
  });

  it("rounds the time left up to whole minutes and says so for under a minute", () => {
    expect([etaText(30), etaText(61), etaText(null)]).toEqual(["Less than a minute left", "About 2 min left", ""]);
  });
});

describe("polling", () => {
  it("polls every 5 seconds while a video is in progress, then stops once everything is done", async () => {
    vi.useFakeTimers();
    let n = 0;
    const mock = mockApi({ "GET /api/progress": () => (++n < 3 ? data(item({ clips_done: n })) : data(item({ state: "ready", stage: "ready", clips_done: 3, clips_planned: 3, eta_s: null }))) });
    render(<Probe />);
    await tick(0);
    expect(polls(mock)).toBe(1);
    await tick(POLL_MS);
    expect(polls(mock)).toBe(2);
    await tick(POLL_MS);
    expect(polls(mock)).toBe(3);
    expect(screen.getByText("Ready — 3 clips")).toBeTruthy();
    await tick(POLL_MS * 6);
    expect(polls(mock)).toBe(3);
  });

  it("never polls again when nothing was in progress to begin with", async () => {
    vi.useFakeTimers();
    const mock = mockApi({ "GET /api/progress": data() });
    render(<Probe />);
    await tick(POLL_MS * 4);
    expect(polls(mock)).toBe(1);
  });

  it("keeps the previous cards on screen while refetching and when a refetch fails", async () => {
    vi.useFakeTimers();
    let n = 0;
    mockApi({ "GET /api/progress": () => (++n === 2 ? { __status: 500, body: { error: "boom" } } : data(item())) });
    render(<Probe />);
    await tick(0);
    expect(screen.getByText("Practice · My first video")).toBeTruthy();
    await tick(POLL_MS);   // the failed refetch
    expect(screen.getByText("Practice · My first video")).toBeTruthy();
    await tick(POLL_MS);   // and it tries again
    expect(n).toBe(3);
  });

  it("pauses while the tab is hidden and fetches again at once when it comes back", async () => {
    vi.useFakeTimers();
    const mock = mockApi({ "GET /api/progress": data(item()) });
    render(<Probe />);
    await tick(0);
    const state = vi.spyOn(document, "visibilityState", "get");
    state.mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await tick(POLL_MS * 4);
    expect(polls(mock)).toBe(1);
    state.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await tick(0);
    expect(polls(mock)).toBe(2);
    await tick(POLL_MS);
    expect(polls(mock)).toBe(3);
  });

  it("stops polling when the screen is closed", async () => {
    vi.useFakeTimers();
    const mock = mockApi({ "GET /api/progress": data(item()) });
    const view = render(<Probe />);
    await tick(0);
    view.unmount();
    await tick(POLL_MS * 4);
    expect(polls(mock)).toBe(1);
  });

  it("shows the last answer at once when the screen is opened again", async () => {
    mockApi({ "GET /api/progress": data(item({ name: "Cached video" })) });
    const first = render(<Probe />);
    await screen.findByText("Cached video");
    first.unmount();
    render(<Probe />);
    expect(screen.getByText("Cached video")).toBeTruthy();   // before any request has come back
  });
});

describe("Home", () => {
  it("puts the progress cards on Home, and a tap on a ready one goes to Review for that campaign", async () => {
    mockApi({
      "GET /api/home": home(),
      "GET /api/progress": data(item({ id: "r", state: "ready", stage: "ready", clips_done: 2, clips_planned: 2, eta_s: null }),
        item({ id: "t", name: "Practice · Second", stage: "clips", clips_done: 1, clips_planned: 3 })),
    });
    render(<Home />);
    await screen.findByText("Ready — 2 clips");
    expect(screen.getByText("1 of 3 clips")).toBeTruthy();
    expect(screen.getAllByTestId("progress-card")[0].getAttribute("href")).toBe("/review?campaign=c1");
  });

  it("still shows Home when the progress request fails", async () => {
    mockApi({ "GET /api/home": home() });   // no /api/progress route: the mock answers 404
    render(<Home />);
    await screen.findByText("Net pay per hour");
    expect(screen.queryByTestId("progress-section")).toBeNull();
  });
});
