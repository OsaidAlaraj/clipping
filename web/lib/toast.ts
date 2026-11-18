"use client";

// One short message at the bottom of the screen, optionally with an action (Undo). Used by every screen except the review feed,
// which keeps its own Undo bar. A new message replaces the old one.

import { useSyncExternalStore } from "react";

export type Toast = { id: number; message: string; action?: { label: string; run: () => void }; seconds: number; tone: "info" | "error" };

let current: Toast | null = null;
let counter = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

export function showToast(message: string, options: { action?: Toast["action"]; seconds?: number; tone?: Toast["tone"] } = {}): void {
  if (timer) clearTimeout(timer);
  const seconds = options.seconds ?? (options.action ? 8 : 4);
  current = { id: ++counter, message, action: options.action, seconds, tone: options.tone ?? "info" };
  timer = setTimeout(dismissToast, seconds * 1000);
  emit();
}

export function dismissToast(): void {
  if (timer) clearTimeout(timer);
  current = null;
  emit();
}

export function useToast(): Toast | null {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => current, () => null);
}
