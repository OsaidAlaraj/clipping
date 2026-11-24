import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Feed from "@/components/Feed";
import { flushOutbox, readOutbox } from "@/lib/api";
import { LangProvider } from "@/lib/i18n";
import { clip, feedRoutes, mockApi, swipe } from "./helpers";

const three = () => [
  clip("c1"),
  clip("c2", { campaign_name: "Second Show", compliance_status: "review", priority_reason: "Lower: needs a compliance look" }),
  clip("c3"),
];

async function open(routes: Record<string, any>, lang?: "ar") {
  const mock = mockApi(routes);
  const view = render(<LangProvider initial={lang ?? "en"}><Feed onExit={() => undefined} /></LangProvider>);
  const card = await screen.findByTestId("clip-card");
  await waitFor(() => expect(mock.posts("/api/sessions")).toHaveLength(1));
  return { mock, card, view };
}

async function rejectWith(card: Element, reason: string) {
  swipe(card, [260, 300], [100, 300]);
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("radio", { name: reason }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Reject clip" }));
}

describe("feed card", () => {
  it("shows campaign, account, platform, language, duration, compliance and the priority reason", async () => {
    await open(feedRoutes(three()));
    expect(screen.getByText("Founders Table")).toBeTruthy();
    expect(screen.getByText("@own_tt")).toBeTruthy();
    expect(screen.getByText("TikTok")).toBeTruthy();
    expect(screen.getByText("English")).toBeTruthy();
    expect(screen.getByText("13 s")).toBeTruthy();
    expect(screen.getByText("Rules OK")).toBeTruthy();
    expect(screen.getByText("Top-paying campaign")).toBeTruthy();
    expect(screen.getByText("1 of 3")).toBeTruthy();
    expect(screen.getByRole("progressbar")).toBeTruthy();
  });

  it("has labelled rail buttons, starts with sound on (owner note 11), and a tap turns sound off and back on", async () => {
    const { card } = await open(feedRoutes(three()));
    for (const label of ["Accept", "Reject", "Hooks", "Edit", "Review later"]) expect(screen.getByRole("button", { name: label })).toBeTruthy();
    const video = card.querySelector("video")!;
    expect(video.muted).toBe(false);
    expect(video.hasAttribute("playsinline")).toBe(true);
    swipe(card, [150, 300], [151, 301]); // a tap
    await waitFor(() => expect(card.querySelector("video")!.muted).toBe(true));
    swipe(card, [150, 300], [151, 301]); // and back
    await waitFor(() => expect(card.querySelector("video")!.muted).toBe(false));
  });

  it("when the browser refuses sound, the clip plays muted and shows the tap instead of failing", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementationOnce(() => Promise.reject(new DOMException("blocked", "NotAllowedError")));
    const { card } = await open(feedRoutes(three()));
    await waitFor(() => expect(card.querySelector("video")!.muted).toBe(true));
    expect(screen.getByText("Tap for sound")).toBeTruthy();
    play.mockRestore();
  });

  it("shows no likes, comments, follower counts or streaks, and hides pay on the card by default", async () => {
    const { view } = await open(feedRoutes(three()));
    expect(view.container.textContent).not.toMatch(/like|follower|comment|streak|per 1,000 views/i);
  });

  it("shows pay per clip on the card only when the setting says so", async () => {
    await open(feedRoutes(three(), {}, { pay_per_clip_display: "card" }));
    expect(screen.getByText(/\$1\.50 per 1,000 views/)).toBeTruthy();
  });
});

describe("accept and reject gestures", () => {
  it("accepting (the A key or the check button) records the decision and moves on to the next clip", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    await waitFor(() => expect(mock.posts("/accept")).toHaveLength(1));
    expect(mock.posts("/accept")[0].path).toBe("/api/clips/c1/accept");
    expect(mock.posts("/accept")[0].body.session_id).toBe("s1");
    await screen.findByText("Second Show");
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(mock.posts("/reject")).toHaveLength(0);
  });

  it("swipe left asks for a reason and does not reject without one", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    swipe(card, [260, 300], [100, 300]);
    const dialog = await screen.findByRole("dialog", { name: "Why reject this clip?" });
    expect(within(dialog).getAllByRole("radio")).toHaveLength(11);
    const submit = within(dialog).getByRole("button", { name: "Reject clip" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(mock.posts("/reject")).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("radio", { name: "Other (note required)" }));
    expect(submit.disabled).toBe(true); // "other" needs a note
    fireEvent.change(within(dialog).getByLabelText("Note"), { target: { value: "Guest asked us to skip it" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(within(dialog).getByRole("radio", { name: "Weak hook" }));
    fireEvent.click(submit);
    await waitFor(() => expect(mock.posts("/reject")).toHaveLength(1));
    expect(mock.posts("/reject")[0].body.reason_code).toBe("weak_hook");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closing the reason sheet rejects nothing", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    swipe(card, [260, 300], [100, 300]);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(mock.posts("/reject")).toHaveLength(0);
    expect(screen.getByText("1 of 3")).toBeTruthy();
  });

  it("Undo restores the rejected clip within the window", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    await rejectWith(card, "Duplicate");
    const undo = await screen.findByRole("button", { name: "Undo" });
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect((undo.parentElement!.querySelector(".timer") as HTMLElement).style.animationDuration).toBe("10s");
    fireEvent.click(undo);
    await waitFor(() => expect(mock.posts("/api/reviews/undo")).toHaveLength(1));
    expect(mock.posts("/api/reviews/undo")[0].body.review_id).toBe("review-1");
    await screen.findByText("1 of 3");
    expect(screen.getByText("Founders Table")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("the Undo offer ends when the window closes", async () => {
    const { card } = await open(feedRoutes(three(), {}, { undo_window_s: 0.15 }));
    await rejectWith(card, "Duplicate");
    await screen.findByRole("button", { name: "Undo" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo" })).toBeNull(), { timeout: 1500 });
    expect(screen.getByText("1 of 2")).toBeTruthy();
  });

  it("review later is neither an accept nor a reject", async () => {
    const { mock } = await open(feedRoutes(three()));
    fireEvent.click(screen.getByRole("button", { name: "Review later" }));
    await waitFor(() => expect(mock.posts("/later")).toHaveLength(1));
    expect(mock.posts("/accept").length + mock.posts("/reject").length).toBe(0);
    await screen.findByText("1 of 2");
  });

  it("swiping up skips a clip without deciding it: the next clip shows and the skip is reported with its place", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    swipe(card, [150, 400], [150, 250]);
    await waitFor(() => expect(screen.getByText("Second Show")).toBeTruthy());
    expect(screen.getByText("1 of 3")).toBeTruthy();
    expect(screen.getByText("Lower: needs a compliance look")).toBeTruthy();
    await waitFor(() => expect(mock.posts("/api/clips/c1/skip")).toHaveLength(1));
    expect(mock.posts("/api/clips/c1/skip")[0].body.anchor_id).toBe("c3");        // it goes back behind the last clip
    expect(mock.calls.filter((c) => /\/(accept|reject|later)$/.test(c.path))).toHaveLength(0);
  });

  it("a swipe from the left edge is ignored", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    swipe(card, [10, 300], [260, 300]);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(mock.posts("/accept")).toHaveLength(0);
  });

  it("a refused accept puts the clip back and says why", async () => {
    const { card } = await open(feedRoutes(three(), { "POST /api/clips/c1/accept": { __status: 403, body: { error: "This clip failed compliance." } } }));
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    await screen.findByText("This clip failed compliance.");
    expect(screen.getByText("1 of 3")).toBeTruthy();
  });
});

describe("hooks sheet and keyboard", () => {
  it("lists every hook and a tap picks one as a new version", async () => {
    const { mock } = await open(feedRoutes(three(), { "POST /api/clips/c1/edit": { id: "c1v2", status: "rendering" } }));
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    const dialog = await screen.findByRole("dialog", { name: "Hooks" });
    expect(within(dialog).getByText("Hook for c1")).toBeTruthy();
    fireEvent.click(within(dialog).getByText("Second hook for c1"));
    await waitFor(() => expect(mock.posts("/edit")).toHaveLength(1));
    expect(mock.posts("/edit")[0].body).toEqual({ hook_id: "c1-h2" });
    await screen.findByText(/Re-rendering as a new version/);
  });

  it("desktop shortcuts: A accepts, R opens reasons, Esc closes, arrows move, U undoes", async () => {
    const { mock } = await open(feedRoutes(three()));
    fireEvent.keyDown(window, { key: "ArrowDown" });
    await screen.findByText("Second Show");
    fireEvent.keyDown(window, { key: "ArrowUp" });          // nothing to go back to: skipped clips come back later
    expect(screen.getByText("1 of 3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "r" });
    await screen.findByRole("dialog", { name: "Why reject this clip?" });
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.keyDown(window, { key: "h" });
    await screen.findByRole("dialog", { name: "Hooks" });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "e" });
    await screen.findByRole("dialog", { name: "Edit clip" });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "r" });
    const again = await screen.findByRole("dialog");
    fireEvent.click(within(again).getByRole("radio", { name: "Weak hook" }));
    fireEvent.click(within(again).getByRole("button", { name: "Reject clip" }));
    await screen.findByRole("button", { name: "Undo" });
    fireEvent.keyDown(window, { key: "u" });
    await waitFor(() => expect(mock.posts("/api/reviews/undo")).toHaveLength(1));
    await screen.findByText("1 of 3");
    fireEvent.keyDown(window, { key: "a" });
    await waitFor(() => expect(mock.posts("/accept")).toHaveLength(1));
  });
});

describe("session", () => {
  it("starts when the feed opens and ends at its length with the summary", async () => {
    const mock = mockApi(feedRoutes(three(), {}, { session_length_min: 0.002 }));
    render(<LangProvider initial="en"><Feed onExit={() => undefined} /></LangProvider>);
    const summary = await screen.findByRole("region", { name: "Session summary" }, { timeout: 3000 });
    expect(mock.posts("/api/sessions")[0].path).toBe("/api/sessions");
    expect(mock.posts("/stop")).toHaveLength(1);
    for (const label of ["Clips reviewed", "Accepted", "Rejected", "Posts due", "Hours logged today"]) expect(within(summary).getByText(label)).toBeTruthy();
    expect(within(summary).getByText(/\$1\.50 per 1,000 views/)).toBeTruthy(); // pay is shown on the summary by default
    expect(screen.queryByTestId("clip-card")).toBeNull(); // the feed stops
  });

  it("Stop here saves the position and shows the summary", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    swipe(card, [150, 400], [150, 250]);
    await screen.findByText("Second Show");
    fireEvent.click(screen.getByRole("button", { name: "Stop here" }));
    await screen.findByRole("region", { name: "Session summary" });
    expect(mock.posts("/stop")[0].body.position).toEqual({ campaign_id: null, clip_id: "c2" });
  });

  it("resumes at the saved position", async () => {
    await open(feedRoutes(three(), { "POST /api/sessions": { id: "s1", resume_position: { clip_id: "c3" } } }));
    await screen.findByText("3 of 3");
  });

  it("says so when the kill switch is on", async () => {
    mockApi(feedRoutes([], {}, { kill_switch: true }));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    await screen.findByText("The kill switch is on");
  });
});

describe("offline and right-to-left", () => {
  it("saves review actions on the device while offline and sends them once later", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    mock.state.offline = true;
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    await waitFor(() => expect(readOutbox()).toHaveLength(1));
    expect(readOutbox()[0].path).toBe("/api/clips/c1/accept");
    await screen.findByText("1 of 2"); // the clip still leaves the feed at once
    expect(await flushOutbox()).toBe(0); // still offline: kept
    mock.state.offline = false;
    expect(await flushOutbox()).toBe(1);
    expect(readOutbox()).toHaveLength(0);
    const sent = mock.posts("/accept");
    expect(sent).toHaveLength(1);
    expect(sent[0].body.client_action_id).toBeTruthy(); // the server applies it once
  });

  it("an offline reject can be undone without any request", async () => {
    const { mock, card } = await open(feedRoutes(three()));
    mock.state.offline = true;
    await rejectWith(card, "Duplicate");
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await screen.findByText("1 of 3");
    expect(readOutbox()).toHaveLength(0);
    expect(mock.posts("/reject")).toHaveLength(0);
  });

  it("Arabic mirrors the layout but keeps swipe meanings", async () => {
    const { mock, card } = await open(feedRoutes(three()), "ar");
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
    expect(screen.getByRole("button", { name: "قبول" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "رفض" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "a" });   // the accept key: a swipe right now also posts (decision 31)
    await waitFor(() => expect(mock.posts("/accept")).toHaveLength(1));
    swipe(screen.getByTestId("clip-card"), [260, 300], [100, 300]); // left still rejects
    await screen.findByRole("dialog");
  });
});
