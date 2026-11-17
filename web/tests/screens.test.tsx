import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "@/components/Home";
import { ReviewLater, Settings } from "@/components/More";
import Queue from "@/components/Queue";
import { LangProvider } from "@/lib/i18n";
import { clip, mockApi, swipe } from "./helpers";

const bucket = { paid_usd: "86.40", pending_usd: "27.00", hours: 2, paid_per_hour: 43.2, with_pending_per_hour: 56.7, fee_unknown: false };
const card = (id: string, extra: object = {}) => ({
  id, name: `Campaign ${id}`, status: "pending_approval", status_reason: null, marketplace: "Content Rewards", niche: "podcast",
  rate_per_1k: 1.5, fee_known: true, est_net_rate_per_1k: 1.35, budget_left_pct: 80, hours_to_deadline: 20, compliance_status: "pass",
  platforms: ["tiktok", "instagram_reels"], clips_ready: 0, unclear_fields: [], ...extra,
});
const home = (extra: object = {}) => ({
  stories: [{ id: "a", name: "Founders Table", budget_left_pct: 80, hours_to_deadline: 200, clips_ready: 3 }],
  today: { hours_logged: 1.5, hours_cap: 5, clips_accepted: 4, posts_done: 1, posts_due: 2, to_review: 3, review_later: 0 },
  net_per_hour: { this_week: bucket, month_to_date: bucket, confidence: "low" },
  attention: [{ id: "al1", severity: "block", message: "Founders Table: the brief changed." }],
  campaigns: [card("p1"), card("a1", { status: "approved", clips_ready: 3 })],
  kill_switch: { active: false },
  pay_per_clip_display: "summary",
  ...extra,
});

describe("Home", () => {
  it("shows stories, today's counts, net pay per hour with its confidence label, alerts and ranked campaigns", async () => {
    mockApi({ "GET /api/home": home() });
    const view = render(<Home />);
    const story = await screen.findByRole("link", { name: /Founders Table: 80% budget left, 3 clips to review/ });
    expect(story.getAttribute("href")).toBe("/review?campaign=a");
    expect(screen.getByText("Hours today")).toBeTruthy();
    expect(screen.getByText("Clips accepted")).toBeTruthy();
    expect(screen.getByText("Posts done")).toBeTruthy();
    expect(screen.getByText("Posts due")).toBeTruthy();
    expect(screen.getByText("Confidence: low")).toBeTruthy();
    expect(screen.getAllByText("$56.70")).toHaveLength(2);
    expect(screen.getByText("Founders Table: the brief changed.")).toBeTruthy();
    expect(screen.getAllByTestId("campaign-card")).toHaveLength(2);
    expect(screen.getAllByText("$1.50")).toHaveLength(2);
    expect(screen.getAllByText("per 1,000 views")).toHaveLength(2);
    expect(screen.getAllByText("20 h left")).toHaveLength(2);
    expect(view.container.textContent).not.toMatch(/Max \$|follower|likes|streak/i); // pay per clip is off the card by default
  });

  it("shows pay per clip on the card when the setting sends it", async () => {
    mockApi({ "GET /api/home": home({ campaigns: [card("p1", { max_payout_per_clip: 150 })] }) });
    render(<Home />);
    await screen.findByText("Max $150.00 per clip");
  });

  it("swipe right approves a campaign and swipe left parks it", async () => {
    const mock = mockApi({ "GET /api/home": home(), "POST /api/campaigns/": { ok: true } });
    render(<Home />);
    const cards = await screen.findAllByTestId("campaign-card");
    swipe(cards[0], [100, 300], [260, 302]);
    await waitFor(() => expect(mock.posts("/approve")).toHaveLength(1));
    expect(mock.posts("/approve")[0].path).toBe("/api/campaigns/p1/approve");
    await screen.findByText("Campaign approved.");
    swipe(screen.getAllByTestId("campaign-card")[0], [260, 300], [100, 298]);
    await waitFor(() => expect(mock.posts("/park")).toHaveLength(1));
  });

  it("a campaign swipe from the left edge is ignored, and a refusal is shown", async () => {
    const mock = mockApi({ "GET /api/home": home(), "POST /api/campaigns/p1/approve": { __status: 403, body: { error: "This campaign is hard-blocked and cannot be approved." } } });
    render(<Home />);
    const cards = await screen.findAllByTestId("campaign-card");
    swipe(cards[0], [12, 300], [260, 300]);
    expect(mock.posts("/approve")).toHaveLength(0);
    swipe(cards[0], [100, 300], [260, 300]);
    await screen.findByText("This campaign is hard-blocked and cannot be approved.");
  });

  it("warns when the kill switch is on", async () => {
    mockApi({ "GET /api/home": home({ kill_switch: { active: true } }) });
    render(<Home />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/kill switch is on/);
  });
});

const checklist = (extra: object = {}, status = "accepted") => ({
  clip: clip("q1", { status }),
  server: {
    account_owned_and_allowed: { ok: true, label: "Your own active account, on a platform the campaign allows" },
    file_hash_matches_approved: { ok: true, label: "File matches the version you accepted" },
    brief_rechecked: { ok: true, label: "Brief re-checked and unchanged for this batch" },
  },
  owner_ticks: ["account_and_platform_correct", "disclosure_toggle_on", "caption_pasted", "hashtags_and_tags_checked"],
  warnings: [], scheduler_has_disclosure_toggle: false, campaign_needs_reapproval: false, ...extra,
});

async function openChecklist(routes: Record<string, any>) {
  const mock = mockApi({ "GET /api/queue": { kill_switch: false, items: [clip("q1", { status: "accepted" })] }, ...routes });
  render(<Queue />);
  fireEvent.click(await screen.findByRole("button", { name: /Founders Table/ }));
  return { mock, dialog: await screen.findByRole("dialog", { name: "Publish checklist" }) };
}

describe("Queue and publish checklist", () => {
  it("cannot be approved until every owner item is ticked", async () => {
    const { mock, dialog } = await openChecklist({ "GET /api/clips/q1/checklist": checklist(), "POST /api/clips/q1/publish-approve": clip("q1", { status: "ready_to_post" }) });
    const approve = (await within(dialog).findByRole("button", { name: "Approve for publishing" })) as HTMLButtonElement;
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes).toHaveLength(4);
    expect(approve.disabled).toBe(true);
    boxes.slice(0, 3).forEach((box) => fireEvent.click(box));
    expect(approve.disabled).toBe(true);
    fireEvent.click(boxes[3]);
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(mock.posts("/publish-approve")).toHaveLength(1));
    expect(Object.values(mock.posts("/publish-approve")[0].body.ticks)).toEqual([true, true, true, true]);
  });

  it("stays blocked while a server check fails, and offers the brief re-check", async () => {
    const state = checklist();
    state.server.brief_rechecked.ok = false;
    const { mock, dialog } = await openChecklist({ "GET /api/clips/q1/checklist": state, "POST /api/campaigns/camp-1/recheck": { changed: false } });
    const approve = (await within(dialog).findByRole("button", { name: "Approve for publishing" })) as HTMLButtonElement;
    within(dialog).getAllByRole("checkbox").forEach((box) => fireEvent.click(box));
    expect(approve.disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "I re-read it: unchanged" }));
    await waitFor(() => expect(mock.posts("/recheck")[0].body).toEqual({ unchanged_confirmed: true }));
  });

  it("shows soft-cap warnings without blocking, and says the brief changed when it did", async () => {
    const { dialog } = await openChecklist({ "GET /api/clips/q1/checklist": checklist({ warnings: ["@own_tt has already posted 2 of 2 today."], campaign_needs_reapproval: true }) });
    await within(dialog).findByText("@own_tt has already posted 2 of 2 today.");
    expect(within(dialog).getByText(/The brief changed/)).toBeTruthy();
  });

  it("Mark posted needs the link, the toggle and a separate confirm tap", async () => {
    const { mock, dialog } = await openChecklist({ "GET /api/clips/q1/checklist": checklist({}, "ready_to_post"), "POST /api/clips/q1/posted": { id: "post-1" } });
    const mark = (await within(dialog).findByRole("button", { name: "Mark posted" })) as HTMLButtonElement;
    expect(mark.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Link to the post"), { target: { value: "not a link" } });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    expect(mark.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Link to the post"), { target: { value: "https://example.com/p/1" } });
    expect(mark.disabled).toBe(false);
    fireEvent.click(mark);
    expect(mock.posts("/posted")).toHaveLength(0); // nothing is recorded until the confirm tap
    fireEvent.click(await within(dialog).findByRole("button", { name: "Yes, mark posted" }));
    await waitFor(() => expect(mock.posts("/posted")).toHaveLength(1));
    expect(mock.posts("/posted")[0].body).toEqual({ post_url: "https://example.com/p/1", confirm: true, disclosure_toggle_on: true });
    expect(within(dialog).getByText(/Nothing posts automatically/)).toBeTruthy();
  });

  it("says posting needs a connection when offline", async () => {
    const { mock, dialog } = await openChecklist({ "GET /api/clips/q1/checklist": checklist() });
    await within(dialog).findByRole("button", { name: "Approve for publishing" });
    within(dialog).getAllByRole("checkbox").forEach((box) => fireEvent.click(box));
    mock.state.offline = true;
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve for publishing" }));
    await within(dialog).findByText("No connection. Posting needs a connection.");
  });

  it("hides ready clips behind a notice while the kill switch is on", async () => {
    mockApi({ "GET /api/queue": { kill_switch: true, items: [] } });
    render(<Queue />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Ready-to-post clips are hidden/);
  });
});

describe("Review Later and Settings", () => {
  it("groups parked clips by campaign with closed campaigns last", async () => {
    const folders = [
      { campaign_id: "c1", campaign_name: "Open Show", deadline: null, closed: false, count: 2, clips: [clip("r1", { park_reason: "Check the name" }), clip("r2", { park_reason: "Later" })] },
      { campaign_id: "c2", campaign_name: "Closed Show", deadline: null, closed: true, count: 1, clips: [clip("r3", { park_reason: "Old" })] },
    ];
    const mock = mockApi({ "GET /api/review-later": { folders }, "POST /api/clips/": { id: "rv", decision: "accept" } });
    render(<LangProvider initial="en"><ReviewLater /></LangProvider>);
    const sections = await screen.findAllByRole("region");
    expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual(["Open Show", "Closed Show"]);
    expect(within(sections[0]).getByText("2 clips")).toBeTruthy();
    expect(within(sections[0]).getByText("Parked: Check the name")).toBeTruthy();
    expect(within(sections[1]).getByText("1 clip · closed")).toBeTruthy();
    expect(within(sections[1]).queryByRole("button", { name: "Accept" })).toBeNull();
    fireEvent.click(within(sections[0]).getAllByRole("button", { name: "Reject" })[0]);
    expect(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reject clip" })).toHaveProperty("disabled", true);
    fireEvent.click(within(sections[0]).getAllByRole("button", { name: "Accept" })[0]);
    await waitFor(() => expect(mock.posts("/accept")).toHaveLength(1));
  });

  it("edits a setting, shows a refused value, and switches language to right-to-left", async () => {
    const settings = [
      { key: "session_length_min", value: 25, default: 25, min: 10, max: 60, allowed: null, scope: "operator" },
      { key: "pay_per_clip_display", value: "summary", default: "summary", min: null, max: null, allowed: ["card", "summary", "off"], scope: "operator" },
    ];
    const mock = mockApi({
      "GET /api/settings": { settings, kill_switch: { active: false, reason: null } },
      "GET /api/reference": { adapters: [], accounts: [] },
      "PUT /api/settings/session_length_min": (body: any) => (body.value > 60 ? { __status: 400, body: { error: "setting session_length_min = 99 is outside 10 to 60" } } : {}),
      "POST /api/kill-switch": { active: true },
    });
    render(<LangProvider initial="en"><Settings /></LangProvider>);
    const input = await screen.findByLabelText("Session length (minutes) (10 to 60)");
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.click(input.parentElement!.querySelector("button")!);
    await screen.findByText("setting session_length_min = 99 is outside 10 to 60");
    expect(screen.getByLabelText("Pay per clip display")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Switch on now" }));
    await waitFor(() => expect(mock.posts("/api/kill-switch")[0].body.active).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "العربية" }));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
  });
});
