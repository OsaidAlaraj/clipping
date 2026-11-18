"use client";

// Stale-while-revalidate cache for GET calls. A screen shows what it already knows at once and refreshes quietly.
// Calls for the same path share one request, tabs prefetch the next screen's data, and a lost connection keeps the old data on screen.

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { api, NetworkError, onSignedOut } from "./api";

type Entry = { data?: unknown; at: number; promise?: Promise<unknown>; error?: unknown };

const store = new Map<string, Entry>();
const listeners = new Map<string, Set<() => void>>();
const STORAGE_PREFIX = "clipping_cache:";
const PERSISTED = new Set(["/api/home", "/api/counts", "/api/queue", "/api/progress"]);
// The app does not know the operator id on the client, so every saved copy carries an epoch that changes on each
// sign-in, sign-out and 401 (stored on the device, so other tabs see it too). A copy from another epoch is discarded.
const EPOCH_KEY = "clipping_cache_epoch";

function currentEpoch(): string {
  try { return localStorage.getItem(EPOCH_KEY) ?? ""; } catch { return ""; }
}
/** A refresh newer than this is reused as it is (a tab bounce does not refetch). */
export const FRESH_MS = 8_000;
const RETRY_DELAYS = [400, 1200];

function notify(key: string) {
  listeners.get(key)?.forEach((fn) => fn());
}

function persist(key: string, data: unknown) {
  if (!PERSISTED.has(key)) return;
  try {
    sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify({ epoch: currentEpoch(), data }));
  } catch {
    /* storage unavailable or full: the memory copy still works */
  }
}

/** Load what a previous page view saved. Run after the first render so server and client markup agree. */
export function hydrateCache(): void {
  try {
    for (const key of PERSISTED) {
      const raw = sessionStorage.getItem(STORAGE_PREFIX + key);
      if (!raw) continue;
      const saved = JSON.parse(raw) as { epoch?: string; data?: unknown };
      if (saved.epoch !== currentEpoch() || saved.data === undefined) {
        sessionStorage.removeItem(STORAGE_PREFIX + key); // saved for someone else, or before the last sign-in change
      } else if (!store.get(key)?.data) {
        store.set(key, { data: saved.data, at: 0 });
        notify(key);
      }
    }
  } catch {
    /* storage unavailable */
  }
}

/** Forget everything (sign-out, a new account, tests). */
export function clearCache(): void {
  store.clear();
  try { localStorage.setItem(EPOCH_KEY, crypto.randomUUID()); } catch { /* storage unavailable */ }
  try { navigator.serviceWorker?.controller?.postMessage({ type: "clear-data" }); } catch { /* no service worker */ }
  try {
    for (const key of PERSISTED) sessionStorage.removeItem(STORAGE_PREFIX + key);
  } catch {
    /* storage unavailable */
  }
}

onSignedOut(clearCache);

/** Paths currently cached that start with the prefix (for updating every copy of a list at once). */
export function cachedKeys(prefix: string): string[] {
  return [...store.keys()].filter((key) => key.startsWith(prefix) && store.get(key)?.data !== undefined);
}

export function peek<T>(key: string): T | undefined {
  return store.get(key)?.data as T | undefined;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** One shared request per path. A dropped connection is retried quietly; old data stays visible meanwhile. */
export function fetchData<T>(key: string, { force = false }: { force?: boolean } = {}): Promise<T> {
  const entry = store.get(key) ?? { at: 0 };
  if (entry.promise) return entry.promise as Promise<T>;
  if (!force && entry.data !== undefined && Date.now() - entry.at < FRESH_MS) return Promise.resolve(entry.data as T);
  const promise = (async () => {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await api<T>(key);
      } catch (error) {
        if (!(error instanceof NetworkError) || attempt >= RETRY_DELAYS.length) throw error;
        await wait(RETRY_DELAYS[attempt]);
      }
    }
  })();
  store.set(key, { ...entry, promise });
  promise.then(
    (data) => {
      store.set(key, { data, at: Date.now() });
      persist(key, data);
      notify(key);
    },
    (error) => {
      const old = store.get(key);
      store.set(key, { data: old?.data, at: old?.at ?? 0, error });
      notify(key);
    },
  );
  return promise;
}

/** Warm the cache for a screen the person is about to open. Never throws. */
export function prefetch(key: string): void {
  fetchData(key).catch(() => undefined);
}

/** Change cached data at once (an optimistic update). The next refresh brings the server's truth. */
export function mutate<T>(key: string, update: (current: T | undefined) => T): void {
  const entry = store.get(key) ?? { at: 0 };
  const data = update(entry.data as T | undefined);
  store.set(key, { ...entry, data, at: entry.at, error: undefined });
  persist(key, data);
  notify(key);
}

/** Drop every cached path that starts with the prefix, so the next visit fetches again. */
export function invalidate(prefix: string): void {
  for (const [key, entry] of store) if (key.startsWith(prefix)) store.set(key, { ...entry, at: 0 });
}

function subscribe(key: string, fn: () => void) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key)!.add(fn);
  return () => { listeners.get(key)?.delete(fn); };
}

const NOTHING = () => undefined;

/**
 * Data for one path. `data` is the cached copy if there is one (so a screen never starts blank when it has been seen),
 * `loading` is true only while there is nothing to show yet, and `refreshing` while a newer copy is on its way.
 */
export function useData<T>(key: string | null) {
  const data = useSyncExternalStore(
    useCallback((fn) => (key ? subscribe(key, fn) : () => undefined), [key]),
    () => (key ? (store.get(key)?.data as T | undefined) : undefined),
    NOTHING as () => T | undefined,
  );
  const error = useSyncExternalStore(
    useCallback((fn) => (key ? subscribe(key, fn) : () => undefined), [key]),
    () => (key ? store.get(key)?.error : undefined),
    NOTHING,
  );
  const reload = useCallback(() => (key ? fetchData<T>(key, { force: true }).catch(() => undefined) : Promise.resolve(undefined)), [key]);
  useEffect(() => { if (key) fetchData<T>(key).catch(() => undefined); }, [key]);
  const change = useCallback((update: (current: T | undefined) => T) => { if (key) mutate(key, update); }, [key]);
  return { data, error, loading: data === undefined && !error, reload, mutate: change };
}
