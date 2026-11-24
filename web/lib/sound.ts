"use client";

import { useSyncExternalStore } from "react";

// Owner note 11: every clip player starts with sound on. Browsers refuse sound until the person has touched the page, so the
// first tap, click or key press anywhere in the app counts as that touch. Until then a player plays muted and shows the tap.
// The choice is kept in localStorage (wrapped: a blocked store falls back to memory) and shared by all players and swipes.

const KEY = "clipmind.sound";
let memory: boolean | null = null;
let gesture = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

/** The stored sound choice. On unless the person turned it off. */
export function soundOn(): boolean {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "on" || stored === "off") return stored === "on";
  } catch {
    // storage is blocked (private window, cleared site data): the in-memory choice is used instead
  }
  return memory ?? true;
}

export function setSoundOn(on: boolean): void {
  memory = on;
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    // remembered for this visit only
  }
  notify();
}

/** True once the person has touched the page; from then on browsers allow sound. */
export function gestureSeen(): boolean {
  return gesture;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The sound choice, live: every mounted player re-renders when it changes. */
export function useSound(): boolean {
  return useSyncExternalStore(subscribe, soundOn, () => true);
}

/** Whether the person has touched the page yet. A player that was refused sound retries when this turns true. */
export function useGesture(): boolean {
  return useSyncExternalStore(subscribe, gestureSeen, () => false);
}

if (typeof window !== "undefined") {
  const unlock = () => {
    if (gesture) return;
    gesture = true;
    notify();
  };
  for (const type of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(type, unlock, { capture: true, passive: true });
}
