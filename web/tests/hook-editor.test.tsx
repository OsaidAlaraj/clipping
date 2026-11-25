import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Feed from "@/components/Feed";
import { autoSeconds, draftOf } from "@/components/HookEditor";
import { LangProvider } from "@/lib/i18n";
import { clip, feedRoutes, mockApi, swipe } from "./helpers";

async function open(clips = [clip("c1"), clip("c2")], routes: Record<string, any> = {}, lang?: "ar") {
  const mock = mockApi(feedRoutes(clips, routes));
  render(<LangProvider initial={lang ?? "en"}><Feed onExit={() => undefined} /></LangProvider>);
  const card = await screen.findByTestId("clip-card");
  await waitFor(() => expect(mock.posts("/api/sessions")).toHaveLength(1));
  return { mock, card };
}

const tap = (card: Element) => swipe(card, [150, 300], [151, 301]);
/** The first touch anywhere unlocks sound and restarts a muted player once; do it before the test watches play(). */
const touchOnce = async () => { fireEvent.pointerDown(document.body); await new Promise((r) => setTimeout(r, 0)); };
afterEach(() => vi.restoreAllMocks());

function controlled(video: HTMLVideoElement) {
  let paused = false;
  Object.defineProperty(video, "paused", { configurable: true, get: () => paused });
  const pause = vi.spyOn(video, "pause").mockImplementation(() => { paused = true; });
  const play = vi.spyOn(video, "play").mockImplementation(() => { paused = false; return Promise.resolve(); });
  play.mockClear();
  return { pause, play };
}

describe("tap pauses, mute is its own control", () => {
  it("a tap on the picture pauses, a second tap resumes, and the sound never changes", async () => {
    const { card } = await open();
    await touchOnce();
    const video = card.querySelector("video") as HTMLVideoElement;
    const { pause, play } = controlled(video);
    tap(card);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("paused")).toBeTruthy();
    expect(video.muted).toBe(false);
    tap(card);
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("paused")).toBeNull();
    expect(video.muted).toBe(false);
  });

  it("the Mute chip mutes without pausing, and a tap on the picture does not mute", async () => {
    const { card } = await open();
    await touchOnce();
    const video = card.querySelector("video") as HTMLVideoElement;
    const { pause } = controlled(video);
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    await waitFor(() => expect(video.muted).toBe(true));
    expect(pause).not.toHaveBeenCalled();
    expect(screen.queryByTestId("paused")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tap for sound" }));
    await waitFor(() => expect(video.muted).toBe(false));
    tap(card);
    expect(video.muted).toBe(false);
    expect(pause).toHaveBeenCalledTimes(1);
  });

  it("a sound change while the owner has paused does not restart the clip", async () => {
    const { card } = await open();
    await touchOnce();
    const video = card.querySelector("video") as HTMLVideoElement;
    const { play } = controlled(video);
    tap(card);
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    await waitFor(() => expect(video.muted).toBe(true));
    expect(play).not.toHaveBeenCalled();
  });
});

describe("hook editor with live preview", () => {
  const withMeta = (meta: Record<string, unknown>) => [clip("c1", { render_meta: { hook_id: "c1-h1", ...meta }, hook_text: "Hook for c1" }), clip("c2")];

  it("starts from the clip's own choices, or the automatic time at the top", () => {
    expect(autoSeconds("one two")).toBe(3);
    expect(autoSeconds("a b c d e f g h i j k l")).toBe(5);
    expect(draftOf(clip("x", { hook_text: "one two three" }) as any)).toMatchObject({ from_s: 0, for_s: 3, position: "top" });
    expect(draftOf(clip("x", { hook_text: "t", render_meta: { hook_from_s: 2, hook_for_s: 6, hook_position: "bottom" } }) as any)).toMatchObject({ from_s: 2, for_s: 6, position: "bottom" });
  });

  it("draws the hook over the video as the owner types, moves it with the position, and removes it when the sheet closes", async () => {
    const { card } = await open(withMeta({ hook_position: "middle" }));
    expect(screen.queryByTestId("hook-preview")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    const dialog = await screen.findByRole("dialog", { name: "Hooks" });
    const preview = () => card.querySelector('[data-testid="hook-preview"]') as HTMLElement;
    expect(preview().textContent).toBe("Hook for c1");
    expect(preview().className).toContain("at-middle");
    fireEvent.change(within(dialog).getByLabelText("Your own hook"), { target: { value: "Nobody told me this" } });
    expect(preview().textContent).toBe("Nobody told me this");
    fireEvent.click(within(dialog).getByRole("radio", { name: "Bottom" }));
    expect(preview().className).toContain("at-bottom");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(card.querySelector('[data-testid="hook-preview"]')).toBeNull());
  });

  it("the preview is full strength inside the window and a ghost outside it, following the video clock", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return frames.length; });
    const { card } = await open(withMeta({ hook_from_s: 2, hook_for_s: 3 }));
    const video = card.querySelector("video") as HTMLVideoElement;
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    await screen.findByRole("dialog", { name: "Hooks" });
    const preview = card.querySelector('[data-testid="hook-preview"]') as HTMLElement;
    const at = (seconds: number) => {
      Object.defineProperty(video, "currentTime", { configurable: true, value: seconds });
      act(() => frames.splice(0).forEach((cb) => cb(0)));
      return preview.dataset.state;
    };
    expect(at(0.5)).toBe("ghost");
    expect(at(2.2)).toBe("live");
    expect(at(5.4)).toBe("live");    // 2 s + 3 s + the fades
    expect(at(5.7)).toBe("ghost");
  });

  it("Apply sends the text, start, time and position once, as one new version", async () => {
    const { mock } = await open(withMeta({}), { "POST /api/clips/c1/edit": { id: "c1v2", status: "rendering" } });
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    const dialog = await screen.findByRole("dialog", { name: "Hooks" });
    fireEvent.change(within(dialog).getByLabelText("Your own hook"), { target: { value: "  POV: rent day  " } });
    fireEvent.change(within(dialog).getByLabelText("Shows from"), { target: { value: "1.5" } });
    fireEvent.change(within(dialog).getByLabelText("Stays for"), { target: { value: "6" } });
    fireEvent.click(within(dialog).getByRole("radio", { name: "Top" }));
    expect(mock.posts("/edit")).toHaveLength(0);                       // nothing is sent while editing
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply and re-render" }));
    await waitFor(() => expect(mock.posts("/edit")).toHaveLength(1));
    expect(mock.posts("/edit")[0].body).toEqual({ hook: { text: "POV: rent day", from_s: 1.5, for_s: 6, position: "top" } });
    await screen.findByText(/Re-rendering as a new version/);
  });

  it("an empty hook cannot be applied, and the list still picks a hook with one tap", async () => {
    const { mock } = await open(withMeta({}), { "POST /api/clips/c1/edit": { id: "c1v2", status: "rendering" } });
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    const dialog = await screen.findByRole("dialog", { name: "Hooks" });
    fireEvent.change(within(dialog).getByLabelText("Your own hook"), { target: { value: "   " } });
    expect((within(dialog).getByRole("button", { name: "Apply and re-render" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(dialog).getByText("Second hook for c1"));
    await waitFor(() => expect(mock.posts("/edit")).toHaveLength(1));
    expect(mock.posts("/edit")[0].body).toEqual({ hook_id: "c1-h2" });
  });

  it("speaks Arabic in the same component", async () => {
    await open(withMeta({}), {}, "ar");
    fireEvent.click(screen.getByRole("button", { name: "العناوين" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("عنوانك الخاص")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "طبّق وأعد التصيير" })).toBeTruthy();
    expect(within(dialog).getByRole("radio", { name: "أعلى" })).toBeTruthy();
  });
});

describe("hook editor smoothness (phone viewport 390 x 844)", () => {
  it("dragging a slider re-renders only the sheet and the overlay, never the feed, and each step costs well under one frame", async () => {
    Object.assign(window, { innerWidth: 390, innerHeight: 844 });
    let feedCommits = 0;
    const mock = mockApi(feedRoutes([clip("c1"), clip("c2"), clip("c3")]));
    const { Profiler } = await import("react");
    const { rerender: _unused } = render(
      <LangProvider initial="en"><Profiler id="feed" onRender={() => { feedCommits += 1; }}><Feed onExit={() => undefined} /></Profiler></LangProvider>,
    );
    await screen.findByTestId("clip-card");
    await waitFor(() => expect(mock.posts("/api/sessions")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Hooks" }));
    const dialog = await screen.findByRole("dialog", { name: "Hooks" });
    const slider = within(dialog).getByLabelText("Stays for");
    const before = feedCommits;
    const started = performance.now();
    const steps = 60;
    for (let i = 0; i < steps; i += 1) fireEvent.change(slider, { target: { value: String(1 + (i % 9) + (i % 2) * 0.1) } });
    const perStep = (performance.now() - started) / steps;
    // every step commits the sheet and the overlay together: one commit per step, no cascade through the feed's clip list
    expect(feedCommits - before).toBeLessThanOrEqual(steps);
    process.stdout.write(`hook slider: ${perStep.toFixed(2)} ms per step, ${feedCommits - before} commits for ${steps} steps (jsdom, 390 px wide)
`);
    expect(perStep).toBeLessThan(16);
  });
});
