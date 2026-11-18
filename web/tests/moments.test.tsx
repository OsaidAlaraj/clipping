import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/feed", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Feed from "@/components/Feed";
import { LangProvider } from "@/lib/i18n";
import { clip, feedRoutes, mockApi } from "./helpers";

const moment = (id: string, extra: object = {}) => ({
  id, source_id: "s1", campaign_id: "camp-1", campaign_name: "Founders Table", start_s: 75, end_s: 118, score: 0.31,
  reason: "Scored 0.31, below this episode's cut-off of 0.39.", layer: 2, profile: "podcast", engine: "own", ...extra,
});

describe("Filtered moments in Review", () => {
  it("shows the filtered count with each reason and its layer, so nothing is hidden", async () => {
    mockApi(feedRoutes([clip("c1")], { "GET /api/feed": { clips: [clip("c1")], kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary",
      filtered: [moment("m1"), moment("m2", { layer: 1, reason: "It mentions \"crypto\", which the brief says to avoid." })] } }));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Filtered 2" }));
    const rows = await screen.findAllByTestId("filtered-moment");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("1:15–1:58");
    expect(rows[0].textContent).toContain("Below this video's cut-off");
    expect(rows[1].textContent).toContain("Hard rule");
    expect(rows[1].textContent).toContain("brief says to avoid");
  });

  it("rescuing a moment removes it at once and posts the rescue", async () => {
    const mock = mockApi(feedRoutes([clip("c1")], {
      "GET /api/feed": { clips: [clip("c1")], kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary", filtered: [moment("m1")] },
      "POST /api/candidates/m1/rescue": { id: "m1", status: "new" },
    }));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Filtered 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rescue" }));
    expect(screen.queryAllByTestId("filtered-moment")).toHaveLength(0);          // gone before the server answers
    await screen.findByText(/Rescued\. It joins the feed/);
    expect(mock.posts("/api/candidates/m1/rescue")).toHaveLength(1);
  });

  it("a refused rescue puts the moment back and says why", async () => {
    mockApi(feedRoutes([clip("c1")], {
      "GET /api/feed": { clips: [clip("c1")], kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary", filtered: [moment("m1")] },
      "POST /api/candidates/m1/rescue": { __status: 400, body: { error: "That moment is not set aside.", code: "not_filtered" } },
    }));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Filtered 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rescue" }));
    await screen.findByText("That moment is not set aside.");
    await waitFor(() => expect(screen.getAllByTestId("filtered-moment")).toHaveLength(1));
  });

  it("is reachable from an empty feed, and absent when nothing was set aside", async () => {
    mockApi(feedRoutes([], { "GET /api/feed": { clips: [], kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary", filtered: [moment("m1")] } }));
    const view = render(<LangProvider initial="en"><Feed /></LangProvider>);
    await screen.findByText("Nothing to review");
    expect(screen.getByRole("button", { name: "Filtered 1" })).toBeTruthy();
    view.unmount();
    mockApi(feedRoutes([clip("c1")]));
    render(<LangProvider initial="en"><Feed /></LangProvider>);
    await screen.findByTestId("clip-card");
    expect(screen.queryByRole("button", { name: /Filtered/ })).toBeNull();
  });

  it("uses the Arabic labels", async () => {
    mockApi(feedRoutes([clip("c1")], { "GET /api/feed": { clips: [clip("c1")], kill_switch: false, undo_window_s: 10, session_length_min: 25, pay_per_clip_display: "summary", filtered: [moment("m1")] } }));
    render(<LangProvider initial="ar"><Feed /></LangProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "المستبعدة 1" }));
    expect(await screen.findByRole("button", { name: "إنقاذ" })).toBeTruthy();
  });
});
