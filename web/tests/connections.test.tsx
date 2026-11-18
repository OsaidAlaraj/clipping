import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import { Connections } from "@/components/More";
import { clearCache } from "@/lib/data";
import { LangProvider } from "@/lib/i18n";
import { mockApi } from "./helpers";

const PLATFORMS = [{ platform: "youtube_shorts", label: "YouTube" }, { platform: "instagram_reels", label: "Instagram" }, { platform: "tiktok", label: "TikTok" }];
const conn = (id: string, platform: string, state: string, handle = "own_" + id) => ({
  id, account_id: "acc-" + id, platform, handle, state, scopes: [], expires_at: null, refresh_expires_at: null,
});
const listing = (connections: any[], vault_ready = true) => ({ vault_ready, platforms: PLATFORMS, connections });

const show = (lang: "en" | "ar") => render(<LangProvider initial={lang}><Connections /></LangProvider>);

beforeEach(() => { clearCache(); window.history.replaceState({}, "", "/profile"); });

describe("connected accounts in Profile", () => {
  it("DW-1.5 shows every platform with each connection's state and Connect and Disconnect (English)", async () => {
    mockApi({ "GET /api/connections": listing([conn("a", "youtube_shorts", "connected"), conn("b", "tiktok", "expiring"), conn("c", "tiktok", "needs_reconnect", "old_tt")]) });
    show("en");
    const yt = await screen.findByLabelText("YouTube");
    expect(within(yt).getByText("Connected")).toBeTruthy();
    expect(within(yt).getByRole("button", { name: "Disconnect" })).toBeTruthy();
    expect(screen.getByLabelText("Instagram").textContent).toContain("Not connected");
    expect(screen.getByRole("button", { name: "Connect Instagram" })).toBeTruthy();
    const tt = screen.getByLabelText("TikTok");
    expect(within(tt).getByText("Expiring soon")).toBeTruthy();
    expect(within(tt).getByText("Needs reconnect")).toBeTruthy();
    expect(within(tt).getByRole("button", { name: "Reconnect" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/token|secret/i);
  });

  it("DW-1.5 shows the same in Arabic, right to left", async () => {
    mockApi({ "GET /api/connections": listing([conn("a", "youtube_shorts", "connected"), conn("c", "tiktok", "needs_reconnect")]) });
    const view = show("ar");
    const yt = await screen.findByLabelText("يوتيوب");
    expect(within(yt).getByText("متصل")).toBeTruthy();
    expect(within(yt).getByRole("button", { name: "افصل" })).toBeTruthy();
    expect(within(screen.getByLabelText("تيك توك")).getByText("يحتاج إعادة ربط")).toBeTruthy();
    expect(screen.getByRole("button", { name: "اربط إنستغرام" })).toBeTruthy();
    expect(view.container.querySelector("section")?.getAttribute("dir")).toBe("rtl");
  });

  it("Connect starts the sign-in and sends the browser to the platform", async () => {
    const api = mockApi({ "GET /api/connections": listing([]), "POST /api/connections/tiktok/start": { url: "https://fake-oauth.test/tiktok/authorize?state=s" } });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, search: "" });
    show("en");
    fireEvent.click(await screen.findByRole("button", { name: "Connect TikTok" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://fake-oauth.test/tiktok/authorize?state=s"));
    expect(api.posts("/tiktok/start")[0].body).toEqual({ account_id: null });
    vi.unstubAllGlobals();
  });

  it("Disconnect asks once more, then removes the connection and refreshes the list", async () => {
    let rows = [conn("a", "youtube_shorts", "connected")];
    const api = mockApi({ "GET /api/connections": () => listing(rows), "POST /api/connections/a/disconnect": () => { rows = []; return { disconnected: true }; } });
    show("en");
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(api.posts("/disconnect")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(api.posts("/a/disconnect")).toHaveLength(1));
    await waitFor(() => expect(screen.getByLabelText("YouTube").textContent).toContain("Not connected"));
  });

  it("Cancel on the disconnect question keeps the connection", async () => {
    const api = mockApi({ "GET /api/connections": listing([conn("a", "youtube_shorts", "connected")]) });
    show("en");
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
    expect(api.posts("/disconnect")).toHaveLength(0);
  });

  it("with no vault key Connect is off and the reason is shown", async () => {
    mockApi({ "GET /api/connections": listing([], false) });
    show("en");
    expect(await screen.findByText(/TOKEN_VAULT_KEY/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Connect YouTube" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a refusal from the server is shown in plain words", async () => {
    mockApi({ "GET /api/connections": listing([]), "POST /api/connections/tiktok/start": { __status: 400, body: { error: "The token vault key is missing.", code: "vault_unavailable" } } });
    show("en");
    fireEvent.click(await screen.findByRole("button", { name: "Connect TikTok" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The token vault key is missing.");
  });

  it("the message the platform redirect brings back is shown", async () => {
    window.history.replaceState({}, "", "/profile?connect_error=" + encodeURIComponent("This sign-in link is not valid or was already used. Start again from Profile."));
    mockApi({ "GET /api/connections": listing([]) });
    show("en");
    expect((await screen.findByRole("alert")).textContent).toContain("Start again from Profile.");
  });

  it("a successful return says Connected", async () => {
    window.history.replaceState({}, "", "/profile?connected=tiktok");
    mockApi({ "GET /api/connections": listing([conn("a", "tiktok", "connected")]) });
    show("en");
    expect((await screen.findByRole("status")).textContent).toBe("Connected.");
  });
});
