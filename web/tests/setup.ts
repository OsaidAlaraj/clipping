import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { clearCache } from "@/lib/data";
import { dismissToast } from "@/lib/toast";

afterEach(() => {
  cleanup();
  clearCache();
  dismissToast();
  localStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.dir = "ltr";
  document.documentElement.lang = "en";
});

// Screens use the app router for Back and moves. Tests that care mock it themselves; the rest get a quiet default.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined, replace: () => undefined, back: () => undefined }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));

// jsdom has no media playback or matchMedia.
Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: () => Promise.resolve() });
Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: () => undefined });
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false })) as any;
}

// Older jsdom builds have no PointerEvent; pointer coordinates are needed for the gesture tests.
if (!(window as any).PointerEvent) {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: any = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  (window as any).PointerEvent = PointerEventPolyfill;
}
