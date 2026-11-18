import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import { AccountLook, hourLabels, LookPreview } from "@/components/Look";
import { Profile } from "@/components/Places";
import { LangProvider } from "@/lib/i18n";
import { mockApi } from "./helpers";

process.env.TZ = "Asia/Riyadh";   // UTC+3 all year: the hours shown to the owner are checked against a known zone

const SPEC = { font: "Arial", primary: "#FFFFFF", accent: "#FFC400", caption_position: "lower", title_card: true, progress_bar: true, zooms: true, font_size: 4.2, cover: "band" };
const look = (id: string, name: string, extra: object = {}) => ({ ...SPEC, template_id: id, name, active: true, version: 1, versions: [{ version: 1, note: "Created" }], ...extra });
const PRESETS = [{ name: "Ocean", spec: { ...SPEC, font: "Tahoma", accent: "#35C4FF" } }, { name: "Mint", spec: { ...SPEC, accent: "#2EE59D" } }];
const PERSONA = { posts_per_week: 5, best_hours: { hours: [12, 18, 21], basis: "typical", tz: "local", posts_counted: 2 } };

function routes(extra: Record<string, any> = {}) {
  return {
    "GET /api/accounts/a1/templates": { templates: [look("t1", "Sunrise main"), look("t2", "Sunrise bright", { accent: "#FF8A3D", progress_bar: false })], presets: PRESETS },
    "GET /api/accounts/a1/persona": PERSONA,
    "PUT /api/accounts/a1/template": (body: any) => ({ ...look("t1", "Sunrise main"), ...body, version: 2, versions: [{ version: 2, note: "Edited" }, { version: 1, note: "Created" }] }),
    ...extra,
  };
}

async function opened() {
  render(<AccountLook accountId="a1" />);
  fireEvent.click(screen.getByRole("button", { name: /Look and posting plan/ }));
  await screen.findByLabelText("Posting plan");
}

afterEach(() => { vi.useRealTimers(); });

describe("an account's look and posting plan", () => {
  it("stays closed and quiet until the owner opens it, then shows the plan and the looks in rotation", async () => {
    const mock = mockApi(routes());
    render(<AccountLook accountId="a1" />);
    expect(mock.calls).toHaveLength(0);                                         // nothing is fetched for a closed panel
    const toggle = screen.getByRole("button", { name: /Look and posting plan/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const plan = await screen.findByLabelText("Posting plan");
    expect(plan.textContent).toMatch(/Post 5 a week, best around 12:00, 18:00, 21:00/);
    expect(plan.textContent).toMatch(/Typical hours until the account has 6 posts of its own \(2 so far\)/);
    const group = screen.getByRole("group", { name: "Looks in rotation" });
    expect(group.textContent).toContain("Sunrise main");
    expect(group.textContent).toContain("Sunrise bright");
    expect(screen.getByRole("img", { name: "Preview of this look" })).toBeTruthy();
  });

  it("shows results-based hours in the owner's own time", async () => {
    mockApi(routes({ "GET /api/accounts/a1/persona": { posts_per_week: 7, best_hours: { hours: [9, 20], basis: "results", tz: "utc", posts_counted: 12 } } }));
    await opened();
    expect(screen.getByLabelText("Posting plan").textContent).toMatch(/Post 7 a week, best around 12:00, 23:00\..*From this account's own 12 posts/);
    expect(hourLabels([20, 9], "utc")).toEqual(["12:00", "23:00"]);              // UTC+3, sorted
    expect(hourLabels([21, 12], "local")).toEqual(["12:00", "21:00"]);
  });

  it("applies an edit on screen at once and saves only what changed to the account template", async () => {
    const mock = mockApi(routes());
    await opened();
    const picture = () => screen.getByRole("img", { name: "Preview of this look" });
    expect(picture().textContent).toContain("The hook line");
    fireEvent.click(screen.getByLabelText("Title card"));                          // unticked: the preview changes before any save
    expect(picture().textContent).not.toContain("The hook line");
    fireEvent.change(screen.getByLabelText("Captions sit"), { target: { value: "middle" } });
    await waitFor(() => expect(mock.calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    const put = mock.calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/accounts/a1/template");
    expect(put.body).toEqual({ title_card: false, caption_position: "middle" });  // both quick edits, one save, nothing else
    expect(await screen.findByText(/Saved · version 2/)).toBeTruthy();
  });

  it("waits for the colour picker to settle before saving, and saves once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const mock = mockApi(routes());
    await opened();
    const accent = screen.getByLabelText("Accent colour");
    for (const colour of ["#112233", "#223344", "#334455"]) fireEvent.change(accent, { target: { value: colour } });
    expect(mock.calls.filter((c) => c.method === "PUT")).toHaveLength(0);          // still dragging
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    const puts = mock.calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ accent: "#334455" });
  });

  it("edits the look that is selected: the main one through the account route, the others by template", async () => {
    const mock = mockApi(routes({ "PUT /api/templates/t2": (body: any) => ({ ...look("t2", "Sunrise bright"), ...body, version: 2, versions: [] }) }));
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Sunrise bright" }));
    expect(screen.getByRole("button", { name: "Sunrise bright" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(screen.getByLabelText("Font"), { target: { value: "Tahoma" } });
    await waitFor(() => expect(mock.calls.some((c) => c.method === "PUT" && c.path === "/api/templates/t2")).toBe(true));
    expect(mock.calls.find((c) => c.path === "/api/templates/t2")!.body).toEqual({ font: "Tahoma" });
    expect(mock.calls.some((c) => c.path === "/api/accounts/a1/template")).toBe(false);
  });

  it("saves a pending edit to its own look before switching to another", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const mock = mockApi(routes({ "PUT /api/templates/t2": look("t2", "Sunrise bright") }));
    await opened();
    fireEvent.change(screen.getByLabelText("Accent colour"), { target: { value: "#445566" } });
    fireEvent.click(screen.getByRole("button", { name: "Sunrise bright" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    const puts = mock.calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].path).toBe("/api/accounts/a1/template");                         // the main look, where the edit was made
    expect(puts[0].body).toEqual({ accent: "#445566" });
  });

  it("adds a look from a preset with a name that is not taken", async () => {
    const mock = mockApi(routes({ "POST /api/accounts/a1/templates": (body: any) => ({ ...look("t3", body.name), ...body }) }));
    await opened();
    fireEvent.change(screen.getByLabelText("Add a look"), { target: { value: "Ocean" } });
    await waitFor(() => expect(mock.posts("/api/accounts/a1/templates")).toHaveLength(1));
    expect(mock.posts("/api/accounts/a1/templates")[0].body).toMatchObject({ name: "Ocean", font: "Tahoma", accent: "#35C4FF" });
    expect((await screen.findByRole("button", { name: "Ocean" })).getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(screen.getByLabelText("Add a look"), { target: { value: "Ocean" } });
    await waitFor(() => expect(mock.posts("/api/accounts/a1/templates")).toHaveLength(2));
    expect(mock.posts("/api/accounts/a1/templates")[1].body.name).toBe("Ocean 2");
  });

  it("goes back to the previous look only when there is one, and keeps one look in rotation", async () => {
    const mock = mockApi(routes({
      "GET /api/accounts/a1/templates": { templates: [look("t1", "Only", { version: 3, versions: [{ version: 3, note: "Edited" }] })], presets: PRESETS },
      "POST /api/templates/t1/revert": look("t1", "Only", { version: 4, accent: "#AA0000" }),
    }));
    await opened();
    expect((screen.getByLabelText("Use in rotation") as HTMLInputElement).disabled).toBe(true);   // the last look cannot be switched off
    fireEvent.click(screen.getByRole("button", { name: "Go back to the previous look" }));
    expect(await screen.findByText(/Saved · version 4/)).toBeTruthy();
    expect(mock.posts("/api/templates/t1/revert")[0].body).toEqual({});
  });

  it("hides the go-back button on a first version and switches a look out of the rotation", async () => {
    const mock = mockApi(routes({ "PUT /api/templates/t1": (body: any) => ({ ...look("t1", "Sunrise main"), ...body }) }));
    await opened();
    expect(screen.queryByRole("button", { name: "Go back to the previous look" })).toBeNull();
    const toggle = screen.getByLabelText("Use in rotation") as HTMLInputElement;
    expect(toggle.disabled).toBe(false);
    fireEvent.click(toggle);
    await waitFor(() => expect(mock.calls.some((c) => c.method === "PUT" && c.path === "/api/templates/t1" && c.body.active === false)).toBe(true));
    expect(await screen.findByRole("button", { name: "Sunrise main (off)" })).toBeTruthy();
  });

  it("shows the server's refusal and goes back to what is saved", async () => {
    let loads = 0;
    const mock = mockApi(routes({
      "GET /api/accounts/a1/templates": () => { loads++; return { templates: [look("t1", "Sunrise main")], presets: PRESETS }; },
      "PUT /api/accounts/a1/template": { __status: 400, body: { error: "The caption size must be between 2.5 and 6.5 percent of the frame height.", code: "bad_template" } },
    }));
    await opened();
    fireEvent.change(screen.getByLabelText("Caption size"), { target: { value: "6.5" } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/caption size must be between/);
    await waitFor(() => expect(loads).toBe(2));                                       // reloaded from the server
    expect(mock.calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("offers a retry when the library cannot be loaded", async () => {
    let up = false;
    mockApi(routes({ "GET /api/accounts/a1/templates": () => (up ? { templates: [look("t1", "Sunrise main")], presets: PRESETS } : { __status: 500, body: { error: "Server down." } }) }));
    render(<AccountLook accountId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: /Look and posting plan/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("Server down.");
    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Posting plan")).toBeTruthy();
  });

  it("keeps a unknown font selectable and draws the preview from the spec", async () => {
    mockApi(routes({ "GET /api/accounts/a1/templates": { templates: [look("t1", "Odd", { font: "Cairo", caption_position: "middle", progress_bar: false })], presets: [] } }));
    await opened();
    expect((screen.getByLabelText("Font") as HTMLSelectElement).value).toBe("Cairo");
    const picture = screen.getByRole("img", { name: "Preview of this look" });
    expect(picture.style.fontFamily).toContain("Cairo");
    expect(picture.querySelector("i")).toBeNull();                                      // no progress bar
  });

  it("draws the title card in a readable colour on any accent", () => {
    const light = render(<LookPreview spec={{ ...SPEC, accent: "#FFFFFF" } as any} />);
    expect((within(light.container).getByText("The hook line") as HTMLElement).style.color).toBe("rgb(16, 16, 16)");
    const dark = render(<LookPreview spec={{ ...SPEC, accent: "#101030" } as any} />);
    expect((within(dark.container).getByText("The hook line") as HTMLElement).style.color).toBe("rgb(255, 255, 255)");
  });
});

describe("Profile", () => {
  it("offers the look and posting plan under every account", async () => {
    mockApi({
      "GET /api/reference": { accounts: [{ id: "a1", platform: "tiktok", handle: "pod_tt", status: "active", status_note: null, niche: "podcast" }], categories: [] },
      "GET /api/home": { net_per_hour: { this_week: { with_pending_per_hour: null, paid_usd: 0, hours: 0 }, month_to_date: { with_pending_per_hour: null, paid_usd: "0", hours: 0 }, confidence: "low" } },
    });
    render(<LangProvider initial="en"><Profile /></LangProvider>);
    await screen.findByText("@pod_tt");
    expect(screen.getByRole("button", { name: /Look and posting plan/ })).toBeTruthy();
  });
});
