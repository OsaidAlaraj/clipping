import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

/** Runs the real service worker file against a fake network: a failed data request must never get the app shell back. */
function loadWorker(networkFails: boolean) {
  const listeners: Record<string, (e: any) => void> = {};
  const cache = { match: vi.fn(async () => undefined), put: vi.fn(), keys: vi.fn(async () => []) };
  const env = {
    self: { addEventListener: (type: string, fn: (e: any) => void) => { listeners[type] = fn; }, skipWaiting: vi.fn(), clients: { claim: vi.fn() } },
    caches: { open: vi.fn(async () => cache), match: vi.fn(async () => undefined), keys: vi.fn(async () => []), delete: vi.fn() },
    location: { origin: "http://localhost:3000" },
    URL,
    Response,
    fetch: vi.fn(async () => { if (networkFails) throw new TypeError("Failed to fetch"); return new Response("{}", { status: 200 }); }),
  };
  new Function(...Object.keys(env), readFileSync(path.join(__dirname, "../public/sw.js"), "utf8"))(...Object.values(env));
  return { fetchHandler: listeners.fetch, env };
}

describe("service worker fallback", () => {
  it("answers a failed data request with a JSON error, never the app shell", async () => {
    const { fetchHandler } = loadWorker(true);
    let answer: Promise<Response> | undefined;
    fetchHandler({ request: new Request("http://localhost:3000/api/queue"), respondWith: (p: Promise<Response>) => { answer = p; } });
    const res = await answer!;
    expect(res.status).toBe(503);
    expect(res.headers.get("content-type")).toBe("application/json");
    expect((await res.json()).code).toBe("offline");
  });
});
