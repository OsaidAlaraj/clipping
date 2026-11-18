import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let currentPath = "/";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => currentPath, useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@simplewebauthn/browser", () => ({ startRegistration: vi.fn(async () => ({ id: "c", response: {} })), startAuthentication: vi.fn(async () => ({ id: "c", response: {} })) }));

import { Settings } from "@/components/More";
import Shell from "@/components/Shell";
import SignIn from "@/components/SignIn";
import { LangProvider } from "@/lib/i18n";
import { hydrateCache, peek, fetchData } from "@/lib/data";
import { mockApi } from "./helpers";

const KEY = "clipping_cache:/api/counts";
const stubLocation = (extra: object) =>
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, pathname: "/queue", search: "", ...extra } });

describe("the cache never crosses from one operator to the next", () => {
  it("sign-out clears the cache and sessionStorage before leaving", async () => {
    mockApi({ "GET /api/counts": { to_review: 3, alerts: 0 }, "POST /api/auth/logout": {}, "GET /api/settings": { kill_switch: { active: false }, settings: [] } });
    await fetchData("/api/counts");
    expect(peek("/api/counts")).toBeTruthy();
    expect(sessionStorage.getItem(KEY)).not.toBeNull();
    const postMessage = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { controller: { postMessage } } });
    const assign = vi.fn(() => { expect(peek("/api/counts")).toBeUndefined(); expect(sessionStorage.getItem(KEY)).toBeNull(); });
    stubLocation({ assign });
    render(<LangProvider initial="en"><Settings /></LangProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/signin"));
    expect(postMessage).toHaveBeenCalledWith({ type: "clear-data" });
    delete (navigator as any).serviceWorker;
  });

  it("a saved copy from another operator is not shown", async () => {
    localStorage.setItem("clipping_cache_epoch", "operator-b");
    sessionStorage.setItem(KEY, JSON.stringify({ epoch: "operator-a", data: { to_review: 99, alerts: 5 } }));
    mockApi({ "GET /api/counts": { to_review: 1, alerts: 0 } });
    currentPath = "/explore";
    const view = render(<Shell><p>x</p></Shell>);
    hydrateCache();
    expect(peek("/api/counts")).toBeUndefined();
    expect(sessionStorage.getItem(KEY)).toBeNull();
    expect(view.container.textContent).not.toContain("99");
  });

  it("a saved copy from the same operator still shows at once", () => {
    localStorage.setItem("clipping_cache_epoch", "operator-a");
    sessionStorage.setItem(KEY, JSON.stringify({ epoch: "operator-a", data: { to_review: 4, alerts: 0 } }));
    hydrateCache();
    expect(peek("/api/counts")).toEqual({ to_review: 4, alerts: 0 });
  });

  it("a 401 that sends the person to sign-in clears the cache", async () => {
    mockApi({ "GET /api/counts": { to_review: 3, alerts: 0 } });
    await fetchData("/api/counts");
    mockApi({ "GET /api/home": { __status: 401, body: { error: "Sign in", code: "signin_required" } } });
    const assign = vi.fn();
    stubLocation({ assign });
    await expect(fetchData("/api/home")).rejects.toThrow();
    expect(assign).toHaveBeenCalled();
    expect(peek("/api/counts")).toBeUndefined();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("a successful sign-in clears what the last person left", async () => {
    mockApi({ "GET /api/auth/status": { signed_in: false, has_passkeys: true }, "POST /api/auth/login": {} });
    sessionStorage.setItem(KEY, JSON.stringify({ epoch: "", data: { to_review: 9, alerts: 0 } }));
    const replace = vi.fn();
    stubLocation({ pathname: "/signin", replace });
    render(<SignIn />);
    const button = await screen.findByRole("button", { name: "Sign in with passkey" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });
});
