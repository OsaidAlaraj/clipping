import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let currentPath = "/";
vi.mock("next/navigation", () => ({ usePathname: () => currentPath, useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
const startRegistration = vi.fn(async () => ({ id: "cred", response: {} }));
const startAuthentication = vi.fn(async () => ({ id: "cred", response: {} }));
vi.mock("@simplewebauthn/browser", () => ({ startRegistration: (...a: any[]) => (startRegistration as any)(...a), startAuthentication: (...a: any[]) => (startAuthentication as any)(...a) }));

import { NewPlace, Profile, UploadVideo } from "@/components/Places";
import Shell, { PLACES } from "@/components/Shell";
import SignIn from "@/components/SignIn";
import { LangProvider } from "@/lib/i18n";
import { mockApi } from "./helpers";

const homeData = {
  stories: [], today: { hours_logged: 0, hours_cap: 5, clips_accepted: 0, posts_done: 0, posts_due: 0, to_review: 7, review_later: 0 },
  net_per_hour: { this_week: { with_pending_per_hour: null, paid_usd: 0, hours: 0 }, month_to_date: { with_pending_per_hour: null, paid_usd: "0", hours: 0 }, confidence: "low" },
  attention: [{ id: "a1", severity: "warn", message: "Deadline soon" }],
  campaigns: [{ id: "c1", name: "Founders Table", status: "approved", marketplace: "Content Rewards" }, { id: "c2", name: "Draft one", status: "pending_approval" }],
  kill_switch: { active: false },
};

describe("five places, same on phone and desktop", () => {
  it("shows Home, Explore, New, Review and Profile in both navigations, with the review count and alert dot", async () => {
    mockApi({ "GET /api/home": homeData });
    currentPath = "/explore";
    const view = render(<Shell><p>content</p></Shell>);
    expect(PLACES.map((p) => p.href)).toEqual(["/", "/explore", "/new", "/review", "/profile"]);
    const [phone, desktop] = screen.getAllByRole("navigation", { name: "Main" });
    expect(within(phone).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/", "/explore", "/new", "/review", "/profile"]);
    expect(within(desktop).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/", "/", "/explore", "/review", "/profile", "/new"]);
    expect(within(phone).getByRole("link", { name: /Explore/ }).getAttribute("aria-current")).toBe("page");
    await waitFor(() => expect(within(desktop).getByLabelText("7 waiting")).toBeTruthy());
    expect(screen.getByRole("link", { name: "Alerts, 1 open" }).getAttribute("href")).toBe("/alerts");
    expect(view.container.textContent).not.toMatch(/follower|likes|streak/i);
  });

  it("hides the top bar on the full-screen review", () => {
    mockApi({ "GET /api/home": homeData });
    currentPath = "/review";
    render(<Shell><p>video</p></Shell>);
    expect(screen.queryByRole("link", { name: /Alerts/ })).toBeNull();
  });

  it("sends a signed-out request to the sign-in page", async () => {
    mockApi({ "GET /api/home": { __status: 401, body: { error: "Sign in with your passkey.", code: "signin_required" } } });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, pathname: "/queue", search: "", assign } });
    currentPath = "/queue";
    render(<Shell><p>x</p></Shell>);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/signin?next=%2Fqueue"));
  });
});

describe("adding footage", () => {
  it("uploads a file from the device with progress, language and permission", async () => {
    const sent: any[] = [];
    class FakeXHR {
      upload: any = {}; status = 200; responseText = "{}"; onload: any; onerror: any; url = "";
      open(_m: string, url: string) { this.url = url; }
      withCredentials = false;
      send(form: FormData) { sent.push({ url: this.url, form, withCredentials: this.withCredentials }); this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 }); setTimeout(() => this.onload?.(), 0); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR as any);
    render(<LangProvider initial="en"><UploadVideo campaigns={[{ id: "c1", name: "Founders Table" }]} /></LangProvider>);
    const go = screen.getByRole("button", { name: "Upload and start" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    const file = new File([new Uint8Array(10)], "IMG_0042.mov", { type: "video/quicktime" });
    fireEvent.change(document.getElementById("v-file")!, { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Permission or licence reference"), { target: { value: "Drive folder in brief" } });
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    await screen.findByText(/Uploaded. Clips appear in Review/);
    expect(sent[0].url.endsWith("/api/campaigns/c1/sources/upload")).toBe(true);
    expect(sent[0].withCredentials).toBe(true);
    expect(sent[0].form.get("permission_ref")).toBe("Drive folder in brief");
    expect(sent[0].form.get("language")).toBe("en");
  });

  it("warns before a YouTube link and only sends it after the owner confirms the brief permits it", async () => {
    const mock = mockApi({ "POST /api/campaigns/c1/sources/link": { job_id: "j1", kind: "youtube" } });
    render(<LangProvider initial="en"><UploadVideo campaigns={[{ id: "c1", name: "Founders Table" }]} /></LangProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "From a link" }));
    fireEvent.change(screen.getByLabelText("Video link"), { target: { value: "https://youtu.be/abc" } });
    fireEvent.change(screen.getByLabelText("Permission or licence reference"), { target: { value: "Brief section 2" } });
    expect(screen.getByText(/YouTube's terms forbid downloading/)).toBeTruthy();
    const go = screen.getByRole("button", { name: "Fetch and start" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /explicitly permits/ }));
    fireEvent.click(go);
    await waitFor(() => expect(mock.posts("/sources/link")).toHaveLength(1));
    expect(mock.posts("/sources/link")[0].body).toEqual({ url: "https://youtu.be/abc", language: "en", permission_ref: "Brief section 2", youtube_permitted: true });
  });

  it("the New page offers a campaign or a video, and footage needs an approved campaign", async () => {
    mockApi({ "GET /api/home": homeData, "GET /api/reference": { marketplaces: [], categories: [] } });
    render(<LangProvider initial="en"><NewPlace /></LangProvider>);
    fireEvent.click(screen.getByRole("tab", { name: "Add video" }));
    await waitFor(() => expect((screen.getByLabelText("Campaign") as HTMLSelectElement).options).toHaveLength(1));
    expect((screen.getByLabelText("Campaign") as HTMLSelectElement).value).toBe("c1");
  });
});

describe("profile", () => {
  it("groups accounts by niche and adds one with a niche", async () => {
    const accounts = [{ id: "a1", platform: "tiktok", handle: "pod_tt", status: "active", status_note: null, niche: "podcast" }];
    const mock = mockApi({
      "GET /api/reference": { accounts, categories: [{ name: "podcast" }, { name: "gaming" }] },
      "GET /api/home": homeData,
      "POST /api/accounts": { id: "a2" },
    });
    render(<LangProvider initial="en"><Profile /></LangProvider>);
    await screen.findByText("@pod_tt");
    for (const name of ["Posting queue", "Review Later", "Ledger", "Alerts", "Settings"]) expect(screen.getByRole("link", { name: new RegExp(name) })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Niche"), { target: { value: "gaming" } });
    fireEvent.change(screen.getByLabelText("Posts in", { selector: "#a-lang" }), { target: { value: "ar" } });
    fireEvent.change(screen.getByLabelText("Handle"), { target: { value: "@game_tt" } });
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    await waitFor(() => expect(mock.posts("/api/accounts")[0].body).toEqual({ platform: "tiktok", handle: "@game_tt", niche: "gaming", language: "ar" }));
  });

  it("an account's language is saved and its audience comes from a screenshot", async () => {
    const accounts = [{ id: "a1", platform: "tiktok", handle: "pod_tt", status: "active", status_note: null, niche: "podcast",
      audience_countries: { SA: 40, US: 20, EG: 10 }, audience_source: "screenshot", audience_tier1_pct: 20 }];
    const mock = mockApi({ "GET /api/reference": { accounts, categories: [] }, "GET /api/home": homeData, "PUT /api/accounts/a1/audience": { id: "a1" } });
    render(<LangProvider initial="en"><Profile /></LangProvider>);
    await screen.findByText("@pod_tt");
    const apiFetch = globalThis.fetch;
    const fetchMock = vi.fn((url: any, init?: any) => String(url).endsWith("/audience/screenshot")
      ? Promise.resolve({ ok: true, json: async () => ({ audience_tier1_pct: 35 }) } as Response) : apiFetch(url, init));
    vi.stubGlobal("fetch", fetchMock);
    expect(screen.getByText(/Audience from a screenshot · SA 40%, US 20%, EG 10%/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Read from the platform" })).toBeNull();   // TikTok has no audience API for us
    fireEvent.change(screen.getByLabelText("Posts in", { selector: "#lang-a1" }), { target: { value: "ar" } });
    await waitFor(() => expect(mock.calls.find((c) => c.method === "PUT")?.body).toEqual({ language: "ar", tier1_pct: 20 }));
    const file = new File([new Uint8Array([137, 80])], "viewers.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Audience screenshot for @pod_tt"), { target: { files: [file] } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/accounts/a1/audience/screenshot"))).toBe(true));
    await waitFor(() => expect((screen.getByLabelText("US/UK/CA/AU audience %") as HTMLInputElement).value).toBe("35"));
    vi.unstubAllGlobals();
  });
});

describe("passkey sign-in", () => {
  it("first device: enter the setup code, create the passkey, then go on", async () => {
    const replace = vi.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, pathname: "/signin", search: "?next=/queue", replace } });
    const mock = mockApi({
      "GET /api/auth/status": { mode: "passkey", signed_in: false, has_passkeys: false },
      "POST /api/auth/register/options": { challenge: "abc", rp: { id: "localhost" } },
      "POST /api/auth/register/verify": { ok: true },
    });
    render(<SignIn />);
    const create = (await screen.findByRole("button", { name: "Create passkey" })) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Setup code"), { target: { value: "abcde-12345" } });
    fireEvent.click(create);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/queue"));
    expect(mock.posts("/register/options")[0].body).toEqual({ setup_code: "ABCDE-12345" });
    expect(startRegistration).toHaveBeenCalledWith({ optionsJSON: { challenge: "abc", rp: { id: "localhost" } } });
  });

  it("returning device: one tap with the passkey", async () => {
    const replace = vi.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, pathname: "/signin", search: "", replace } });
    mockApi({
      "GET /api/auth/status": { mode: "passkey", signed_in: false, has_passkeys: true },
      "POST /api/auth/login/options": { challenge: "xyz" },
      "POST /api/auth/login/verify": { ok: true },
    });
    render(<SignIn />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in with passkey" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(startAuthentication).toHaveBeenCalled();
  });
});
