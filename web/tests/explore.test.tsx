import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push, replace: nav.push }), usePathname: () => "/explore", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import ExploreScreen, { ListingPage } from "@/components/Explore";
import { bookmarkletCode } from "@/components/More";
import { NewPlace } from "@/components/Add";
import { LangProvider } from "@/lib/i18n";
import { mockApi } from "./helpers";

const soon = new Date(Date.now() + 9 * 86400000).toISOString();
const listing = (id: string, extra: object = {}) => ({
  id, title: `Campaign ${id}`, brand: "Brand", board_slug: "content-rewards", agency: "Clip Farm", campaign_type: "clipping", pay_model: "cpm",
  platforms: ["tiktok", "instagram"], niche: "podcast", rate_per_1k: 2, net_per_1k: 1.8, budget_left: 700, budget_total: 1000, drain_p50: soon,
  alpha_score: 90, open_join: true, audience: null, board_fee_pct: 0.1, brief_grade: "B", new_at: null, campaign_id: null, page_url: "https://ca/x",
  brief_url: "https://contentrewards.com/c/x", min_payout: 6, max_payout: 1200, payout_window_days: 10,
  rank: { modelled: true, score: 10, p25_per_clip: 3.4, mean_per_clip: 7, p_clear: 0.62, p_budget: 0.9, p_approve: 0.85, geo_share: 1, reasons: ["new: budget is fresh"],
    account: { id: "a1", handle: "pod_tt", platform: "tiktok" } },
  ...extra,
});
const feed = {
  listings: [listing("1"), listing("2", { niche: "gaming", title: "Game clip", rank: { modelled: false, score: null, reasons: ["Pay model is not per 1,000 views: not modelled"], account: null } })],
  market: { data: { cpm: { medianUsd: 1, p25Usd: 1, p75Usd: 2 } } }, last_refresh: new Date().toISOString(),
};

describe("Explore", () => {
  it("first visit asks for niches, saves them, and filters to them", async () => {
    const mock = mockApi({ "GET /api/me": { niches: [], all_niches: ["podcast", "gaming", "sports"] }, "GET /api/listings": feed, "PUT /api/me/niches": { niches: ["gaming"] } });
    render(<ExploreScreen />);
    const ask = await screen.findByRole("region", { name: "Pick your niches" });
    expect((await screen.findAllByTestId("listing-card")).length).toBe(2);
    fireEvent.click(within(ask).getByRole("button", { name: "Gaming" }));
    fireEvent.click(within(ask).getByRole("button", { name: "Save my niches" }));
    await waitFor(() => expect(mock.calls.find((c) => c.method === "PUT")?.body).toEqual({ niches: ["gaming"] }));
    await waitFor(() => expect(screen.getAllByTestId("listing-card")).toHaveLength(1));
    expect(screen.getByText("Game clip")).toBeTruthy();
    expect(screen.getByText(/Typical pay across campaigns: \$1\.00 per 1,000 views/)).toBeTruthy();
    expect(screen.getByText(/ClippingAlpha \(clippingalpha\.com\), CC BY 4\.0/)).toBeTruthy();
  });

  it("filters go to the server, sort changes the order request, and the table shows cautious pay", async () => {
    const mock = mockApi({ "GET /api/me": { niches: ["podcast"], all_niches: ["podcast", "gaming"] }, "GET /api/listings": feed });
    render(<ExploreScreen />);
    await screen.findAllByTestId("listing-card");
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    const sheet = await screen.findByRole("dialog", { name: "Filters" });
    fireEvent.change(within(sheet).getByLabelText("Where you post"), { target: { value: "tiktok" } });
    fireEvent.change(within(sheet).getByLabelText(/Min pay per 1,000 views/), { target: { value: "1.5" } });
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /Open to join/ }));
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /Clear briefs/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Show campaigns" }));
    await waitFor(() => {
      const last = mock.calls.filter((c) => c.path.startsWith("/api/listings")).at(-1)!.path;
      expect(last).toContain("platform=tiktok"); expect(last).toContain("min_rate=1.5"); expect(last).toContain("open_only=true"); expect(last).toContain("min_grade=C");
    });
    fireEvent.click(screen.getByRole("tab", { name: "Ending soon" }));
    await waitFor(() => expect(mock.calls.filter((c) => c.path.startsWith("/api/listings")).at(-1)!.path).toContain("sort=ending"));
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const row = (await screen.findAllByTestId("listing-row"))[0];
    expect(within(row).getByText("$3.40")).toBeTruthy();
    expect(within(row).getByText("62%")).toBeTruthy();
  });

  it("new filters reach the server, and out-of-reach campaigns are hidden until asked for", async () => {
    const mock = mockApi({ "GET /api/me": { niches: [], all_niches: ["podcast"] }, "GET /api/listings": { ...feed, hidden_out_of_reach: 3 } });
    render(<ExploreScreen />);
    await screen.findAllByTestId("listing-card");
    expect(screen.getByText(/3 hidden: your clips would rarely reach/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show them" }));
    await waitFor(() => expect(mock.calls.filter((c) => c.path.startsWith("/api/listings")).at(-1)!.path).toContain("show_out_of_reach=true"));
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    const sheet = await screen.findByRole("dialog", { name: "Filters" });
    fireEvent.change(within(sheet).getByLabelText("How it pays"), { target: { value: "milestone" } });
    fireEvent.change(within(sheet).getByLabelText(/Views needed to get paid/), { target: { value: "10000" } });
    fireEvent.change(within(sheet).getByLabelText(/Days until paid/), { target: { value: "14" } });
    fireEvent.change(within(sheet).getByLabelText(/Budget left, at least/), { target: { value: "500" } });
    fireEvent.click(within(sheet).getByRole("checkbox", { name: /No audience country rule/ }));
    fireEvent.click(within(sheet).getByRole("button", { name: "Show campaigns" }));
    await waitFor(() => {
      const last = mock.calls.filter((c) => c.path.startsWith("/api/listings")).at(-1)!.path;
      for (const part of ["pay_model=milestone", "max_min_views=10000", "max_hold_days=14", "min_budget_left=500", "no_country_rule=true"]) expect(last).toContain(part);
    });
  });
});

describe("campaign profile", () => {
  it("shows the cautious numbers, rules and reasons, and adding goes to approval", async () => {
    const detail = { ...listing("1"), categories: ["gaming", "podcast"], rules: [{ key: "do", title: "DO", items: [{ text: "Tag @brand" }] }], wants: {}, join: {}, verdicts: [{ key: "pay", label: "Pay", verdict: "High", fact: "$2.00 per 1K" }] };
    const mock = mockApi({ "GET /api/listings/1": detail, "POST /api/listings/1/add": { campaign: { id: "c9" } } });
    render(<ListingPage id="1" />);
    await screen.findByText("Campaign 1");
    expect(screen.getByText("$3.40")).toBeTruthy();
    expect(screen.getByText("Tag @brand")).toBeTruthy();
    expect(screen.getByText(/Worked out for @pod_tt/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Why this rank" }));
    expect(screen.getByText(/new: budget is fresh/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open on the marketplace" }).getAttribute("href")).toBe("https://contentrewards.com/c/x");
    fireEvent.click(screen.getByRole("button", { name: "Add to my campaigns" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/campaign?id=c9"));
    expect(mock.posts("/add")[0].body).toEqual({ category: "podcast" });
  });
});

describe("quick capture", () => {
  it("shared text and link open the add-campaign form filled in, with the marketplace guessed", async () => {
    Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, search: "?title=Big+Campaign&text=We+pay+%242+per+1%2C000+views&url=https%3A%2F%2Fvyro.com%2Fc%2F1" } });
    mockApi({ "GET /api/home": { campaigns: [] }, "GET /api/reference": { marketplaces: [{ slug: "vyro", name: "Vyro" }, { slug: "content_rewards", name: "Content Rewards" }], categories: [] } });
    render(<LangProvider initial="en"><NewPlace /></LangProvider>);
    await waitFor(() => expect((screen.getByLabelText("Brief text") as HTMLTextAreaElement).value).toBe("We pay $2 per 1,000 views"));
    expect((screen.getByLabelText("Campaign name") as HTMLInputElement).value).toBe("Big Campaign");
    expect((screen.getByLabelText("Brief link (optional)") as HTMLInputElement).value).toBe("https://vyro.com/c/1");
    await waitFor(() => expect((screen.getByLabelText("Marketplace") as HTMLSelectElement).value).toBe("vyro"));
  });

  it("the save button opens this app with the page's link, title and text", () => {
    const code = bookmarkletCode("https://laptop.example.ts.net");
    expect(code.startsWith("javascript:")).toBe(true);
    expect(code).toContain("https://laptop.example.ts.net/new?url=");
    expect(code).toContain("encodeURIComponent(location.href)");
  });
});
