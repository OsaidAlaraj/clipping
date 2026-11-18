import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/new", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Feed from "@/components/Feed";
import Home from "@/components/Home";
import { NewPlace, UploadVideo, uploadProblem } from "@/components/Places";
import Queue, { Checklist } from "@/components/Queue";
import { LangProvider } from "@/lib/i18n";
import { clip, feedRoutes, mockApi } from "./helpers";

const practiceCard = { id: "p1", name: "Practice · My first video", status: "approved", marketplace: "Practice", niche: "podcast", rate_per_1k: null,
  fee_known: false, est_net_rate_per_1k: 0, budget_left_pct: null, hours_to_deadline: null, compliance_status: "pass", platforms: [],
  clips_ready: 2, unclear_fields: [], is_practice: true, language: "ar" };
const home = (extra: object = {}) => ({
  stories: [], today: { hours_logged: 0, hours_cap: 5, clips_accepted: 0, posts_done: 0, posts_due: 0, to_review: 0, review_later: 0 },
  net_per_hour: { this_week: { with_pending_per_hour: null, paid_usd: 0, hours: 0 }, month_to_date: { with_pending_per_hour: null, paid_usd: "0", hours: 0 }, confidence: "low" },
  attention: [], campaigns: [practiceCard], kill_switch: { active: false }, ...extra });

class FakeXHR {
  static sent: { url: string; form: FormData }[] = [];
  upload: any = {}; status = 200; responseText = "{}"; onload: any; onerror: any; url = ""; withCredentials = false;
  open(_m: string, url: string) { this.url = url; }
  send(form: FormData) { FakeXHR.sent.push({ url: this.url, form }); setTimeout(() => this.onload?.(), 0); }
}

describe("practice campaigns", () => {
  it("one tap creates a practice campaign and goes straight to its upload form, with no permission field", async () => {
    FakeXHR.sent = [];
    vi.stubGlobal("XMLHttpRequest", FakeXHR as any);
    const mock = mockApi({
      "GET /api/home": { campaigns: [] },
      "POST /api/campaigns/practice": (body: any) => ({ id: "p9", name: `Practice · ${body.name}`, is_practice: true, status: "approved" }),
    });
    render(<LangProvider initial="en"><NewPlace /></LangProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Practice" }));
    fireEvent.change(screen.getByLabelText("Language of the video"), { target: { value: "ar" } });
    fireEvent.change(screen.getByLabelText("Niche"), { target: { value: "gaming" } });
    fireEvent.click(screen.getByRole("button", { name: "Create practice campaign" }));
    await screen.findByText(/Practice: for trying the system/);
    expect(mock.posts("/api/campaigns/practice")[0].body).toEqual({ name: "My first video", niche: "gaming", language: "ar" });
    expect((screen.getByLabelText("Campaign") as HTMLSelectElement).value).toBe("p9");
    await waitFor(() => expect((screen.getByLabelText("Language") as HTMLSelectElement).value).toBe("ar"));
    expect(screen.queryByLabelText("Permission or licence reference")).toBeNull();
    fireEvent.change(document.getElementById("v-file")!, { target: { files: [new File([new Uint8Array(10)], "talk.mp4", { type: "video/mp4" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload and start" }));
    await screen.findByText(/Uploaded. Clips appear in Review/);
    expect(FakeXHR.sent[0].url.endsWith("/api/campaigns/p9/sources/upload")).toBe(true);
    expect(FakeXHR.sent[0].form.get("permission_ref")).toBe("");
    expect(screen.getByRole("link", { name: "Open Review" }).getAttribute("href")).toBe("/review?campaign=p9");
  });

  it("shows the refusal in plain words and lets the owner try again", async () => {
    mockApi({ "GET /api/home": { campaigns: [] }, "POST /api/campaigns/practice": { __status: 400, body: { error: "Give the practice campaign a name.", code: "name_required" } } });
    render(<LangProvider initial="en"><NewPlace /></LangProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Practice" }));
    fireEvent.click(screen.getByRole("button", { name: "Create practice campaign" }));
    await screen.findByText("Give the practice campaign a name.");
    expect((screen.getByRole("button", { name: "Create practice campaign" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("the Home card says Practice and shows no pay, budget or deadline", async () => {
    mockApi({ "GET /api/home": home() });
    const view = render(<Home />);
    await screen.findByText("Practice · My first video");
    expect(screen.getByText("Practice")).toBeTruthy();
    expect(screen.getByText(/No money, and nothing is posted/)).toBeTruthy();
    expect(view.container.textContent).not.toMatch(/per 1,000 views|Rate unclear|fee unknown|budget/i);
  });

  it("the feed labels a practice clip, shows why the moment was picked and uses the cover as the poster", async () => {
    mockApi(feedRoutes([clip("c1", { is_practice: true, moment_reason: "A story with a clear payoff (Hook: He quit)", cover_url: "/api/media/clips/c1/cover.jpg?exp=1&sig=x" })]));
    const view = render(<LangProvider initial="en"><Feed /></LangProvider>);
    await screen.findByText(/Why this moment: A story with a clear payoff/);
    expect(screen.getByText("Practice")).toBeTruthy();
    expect(view.container.querySelector("video")?.getAttribute("poster")).toContain("cover.jpg");
  });

  it("a practice clip in the queue has no posting steps, only the video and a way back", async () => {
    const practiceClip = clip("c1", { status: "accepted", is_practice: true });
    mockApi({
      "GET /api/queue": { kill_switch: false, items: [practiceClip] },
      "GET /api/clips/c1/checklist": { clip: practiceClip, server: {}, owner_ticks: ["caption_pasted"], warnings: [], scheduler_has_disclosure_toggle: false, campaign_needs_reapproval: false, practice: true },
    });
    render(<Queue />);
    await screen.findByText("Practice · not for posting");
    fireEvent.click(screen.getByText("Founders Table"));
    await screen.findByText(/This is a practice clip. It cannot be posted/);
    expect(screen.queryByRole("button", { name: "Approve for publishing" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark posted" })).toBeNull();
    expect(screen.getByRole("link", { name: "Download the approved video" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Move back to Review Later" })).toBeTruthy();
  });

  it("a real clip still shows the publish checklist", async () => {
    const real = clip("c2", { status: "accepted" });
    mockApi({ "GET /api/clips/c2/checklist": { clip: real, server: { brief_rechecked: { ok: true, label: "Brief re-checked" } }, owner_ticks: ["caption_pasted"], warnings: [], scheduler_has_disclosure_toggle: false, campaign_needs_reapproval: false, practice: false } });
    render(<Checklist clipId="c2" onClose={() => undefined} onChanged={() => undefined} />);
    await screen.findByRole("button", { name: "Approve for publishing" });
  });
});

describe("big uploads", () => {
  const sized = (bytes: number) => Object.defineProperty(new File(["x"], "big.mp4", { type: "video/mp4" }), "size", { value: bytes });

  it("blocks a video over 10 GB with a clear message and offers the link instead", () => {
    mockApi({});
    render(<LangProvider initial="en"><UploadVideo campaigns={[{ id: "c1", name: "Practice", practice: true }]} /></LangProvider>);
    fireEvent.change(document.getElementById("v-file")!, { target: { files: [sized(12 * 1024 ** 3)] } });
    expect(screen.getByRole("alert").textContent).toBe("This video is 12.0 GB. The most the system takes is 10 GB. Trim it, or send it as a link instead.");
    expect((screen.getByRole("button", { name: "Upload and start" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("warns but allows a video over 1.5 GB, and explains a dropped big upload", async () => {
    class DroppedXHR extends FakeXHR { send() { setTimeout(() => this.onerror?.(), 0); } }
    vi.stubGlobal("XMLHttpRequest", DroppedXHR as any);
    mockApi({});
    render(<LangProvider initial="en"><UploadVideo campaigns={[{ id: "c1", name: "Practice", practice: true }]} /></LangProvider>);
    fireEvent.change(document.getElementById("v-file")!, { target: { files: [sized(2 * 1024 ** 3)] } });
    expect(screen.getByRole("status").textContent).toMatch(/large video \(2.0 GB\)/);
    const go = screen.getByRole("button", { name: "Upload and start" }) as HTMLButtonElement;
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/upload of this 2.0 GB video stopped/));
  });

  it("small files and no file raise no warning", () => {
    expect(uploadProblem(null)).toBeNull();
    expect(uploadProblem(sized(5 * 1024 ** 2))).toBeNull();
  });
});
