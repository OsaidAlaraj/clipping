import { afterEach, describe, expect, it, vi } from "vitest";

import { gestureSeen, setSoundOn, soundOn } from "@/lib/sound";

// Owner note 11: the sound choice is on by default, remembered, and survives a blocked store. The touch test runs last:
// after the first touch the module keeps it for the rest of the file.

afterEach(() => {
  vi.restoreAllMocks();
  try { localStorage.clear(); } catch { /* nothing to clear */ }
});

describe("sound choice", () => {
  it("is on by default, before anything is stored", () => {
    expect(soundOn()).toBe(true);
  });

  it("is remembered in localStorage and read back", () => {
    setSoundOn(false);
    expect(localStorage.getItem("clipmind.sound")).toBe("off");
    expect(soundOn()).toBe(false);
    setSoundOn(true);
    expect(localStorage.getItem("clipmind.sound")).toBe("on");
    expect(soundOn()).toBe(true);
  });

  it("still works when the store is blocked (private window, cleared site data)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
    expect(() => setSoundOn(false)).not.toThrow();
    expect(soundOn()).toBe(false);
    expect(() => setSoundOn(true)).not.toThrow();
    expect(soundOn()).toBe(true);
  });

  it("treats an unknown stored value as the default (on)", () => {
    localStorage.setItem("clipmind.sound", "maybe");
    expect(soundOn()).toBe(true);
  });

  it("counts the first touch anywhere in the app, which lets browsers play with sound", () => {
    expect(gestureSeen()).toBe(false);
    window.dispatchEvent(new Event("pointerdown"));
    expect(gestureSeen()).toBe(true);
  });
});
