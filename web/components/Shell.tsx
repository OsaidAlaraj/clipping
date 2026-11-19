"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { flushOutbox, OUTBOX_EVENT, readOutbox } from "@/lib/api";
import { hydrateCache, prefetch, useData } from "@/lib/data";
import { LangProvider, useLang } from "@/lib/i18n";
import ToastHost from "./ToastHost";

/** The ClipMind mark and name. On the dark side menu the square is lime; on light pages it is near-black. */
export function Logo({ size = 28, onDark = false }: { size?: number; onDark?: boolean }) {
  const [bg, fg] = onDark ? ["#CCF24A", "#0E0E10"] : ["#0E0E10", "#CCF24A"];
  return (
    <span className="logo">
      <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
        <rect width="100" height="100" rx="22" fill={bg} />
        <path d="M65.56 34.44A22 22 0 1 0 65.56 65.56" fill="none" stroke={fg} strokeWidth="11" strokeLinecap="round" />
        <circle cx="78" cy="50" r="5.5" fill={fg} />
      </svg>
      <span>ClipMind</span>
    </span>
  );
}

export const Icon = {
  home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 11l8-7 8 7v9H4z" /></svg>,
  explore: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></svg>,
  plus: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>,
  feed: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><rect x="6" y="3" width="12" height="18" rx="3" /><path d="M10.5 9.5l4 2.5-4 2.5z" /></svg>,
  profile: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>,
  bell: <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8" /><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" /></svg>,
  queue: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" /></svg>,
  more: <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>,
  check: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>,
  cross: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>,
  hooks: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 7h14M5 12h14M5 17h8" /></svg>,
  edit: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z" /></svg>,
  expand: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>,
  text: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h9" /><path d="M3 21L21 3" /></svg>,
  later: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 4h12v16l-6-4-6 4z" /></svg>,
};

/** The five places, identical on phone and desktop (decision, 2026-11-17). */
export const PLACES = [
  { href: "/", key: "home", icon: Icon.home, match: (p: string) => p === "/" || p.startsWith("/campaign") },
  { href: "/explore", key: "explore", icon: Icon.explore, match: (p: string) => p.startsWith("/explore") },
  { href: "/new", key: "create", icon: Icon.plus, match: (p: string) => p.startsWith("/new") || p.startsWith("/more/add") },
  { href: "/review", key: "review", icon: Icon.feed, match: (p: string) => p.startsWith("/review") || p.startsWith("/feed") },
  { href: "/profile", key: "profile", icon: Icon.profile, match: (p: string) => p.startsWith("/profile") || p.startsWith("/queue") || p.startsWith("/more") },
] as const;

/** The data each place opens with. Warmed when a finger or pointer reaches the tab, so the screen is ready by the time it opens. */
const PLACE_DATA: Record<string, string[]> = {
  "/": ["/api/home"],
  "/explore": ["/api/listings?sort=rank", "/api/me"],
  "/review": ["/api/feed"],
  "/profile": ["/api/queue"],
};
const warm = (href: string) => (PLACE_DATA[href] ?? []).forEach(prefetch);
const warmProps = (href: string) => ({ onPointerEnter: () => warm(href), onPointerDown: () => warm(href), onFocus: () => warm(href) });

function Navigation({ toReview }: { toReview: number }) {
  const path = usePathname() ?? "/";
  const { t } = useLang();
  const items = PLACES.map((place) => ({ ...place, label: t(place.key as any), active: place.match(path) }));
  return (
    <>
      <nav className="nav" aria-label="Main">
        {items.map((item) => (
          <Link key={item.href} href={item.href} className={item.key === "create" ? "create" : undefined} {...warmProps(item.href)}
            aria-current={item.active ? "page" : undefined} aria-label={item.active ? undefined : item.label}>
            {item.icon}
            <span className="label">{item.label}</span>
            {item.key === "review" && toReview > 0 && !item.active && <span className="count" aria-label={`${toReview} waiting`}>{toReview}</span>}
          </Link>
        ))}
      </nav>
      <nav className="side" aria-label="Main">
        <Link href="/" className="wordmark" aria-label="ClipMind"><Logo onDark /></Link>
        {items.filter((item) => item.key !== "create").map((item) => (
          <Link key={item.href} href={item.href} aria-current={item.active ? "page" : undefined} {...warmProps(item.href)}>
            {item.icon}<span>{item.label}</span>
            {item.key === "review" && toReview > 0 && <span className="count" aria-label={`${toReview} waiting`}>{toReview}</span>}
          </Link>
        ))}
        <Link href="/new" className="create" aria-current={path.startsWith("/new") ? "page" : undefined}>{Icon.plus}<span>{t("create")}</span></Link>
      </nav>
    </>
  );
}

export function TopBar({ alerts }: { alerts: number }) {
  const { t } = useLang();
  return (
    <div className="topbar">
      <Link href="/" className="wordmark" aria-label="ClipMind"><Logo /></Link>
      <span className="grow" />
      <Link href="/alerts" className="icon-btn" aria-label={alerts > 0 ? `${t("alerts")}, ${alerts} open` : t("alerts")}>
        {Icon.bell}
        {alerts > 0 && <span className="dot" />}
      </Link>
    </div>
  );
}

const RETRY_MS = [4_000, 8_000, 16_000, 30_000, 60_000];

export default function Shell({ children }: { children: React.ReactNode }) {
  const counts = useData<{ to_review: number; alerts: number }>("/api/counts").data ?? { to_review: 0, alerts: 0 };
  const [offline, setOffline] = useState(false);
  const [pending, setPending] = useState(0);
  const [justSaved, setJustSaved] = useState(false);
  const path = usePathname() ?? "/";
  const fullScreen = path.startsWith("/review") || path.startsWith("/feed") || path.startsWith("/signin");

  useEffect(() => {
    hydrateCache(); // after the first render, so the server and the browser agree on the first markup
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let savedTimer: ReturnType<typeof setTimeout> | undefined;
    // Saved actions are sent when the connection is back, and retried with growing gaps while it is not.
    const sync = async () => {
      if (timer) clearTimeout(timer);
      setOffline(!navigator.onLine);
      const before = readOutbox().length;
      if (navigator.onLine && before > 0) {
        const sent = await flushOutbox();
        if (sent > 0) { setJustSaved(true); if (savedTimer) clearTimeout(savedTimer); savedTimer = setTimeout(() => setJustSaved(false), 2500); }
      }
      const left = readOutbox().length;
      setPending(left);
      if (left > 0) { timer = setTimeout(sync, RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)]); attempt += 1; } else attempt = 0;
    };
    const onOutbox = () => setPending(readOutbox().length);
    const onVisible = () => { if (document.visibilityState === "visible") void sync(); };
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    window.addEventListener(OUTBOX_EVENT, onOutbox);
    document.addEventListener("visibilitychange", onVisible);
    // Once the page is idle, warm the data of the other places so the first tap on each is instant too.
    const idle = (window as any).requestIdleCallback ?? ((fn: () => void) => setTimeout(fn, 1200));
    idle(() => Object.keys(PLACE_DATA).forEach(warm));
    return () => {
      if (timer) clearTimeout(timer);
      if (savedTimer) clearTimeout(savedTimer);
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
      window.removeEventListener(OUTBOX_EVENT, onOutbox);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => { prefetch("/api/counts"); setPending(readOutbox().length); }, [path]);

  return (
    <LangProvider>
      <div className="shell">
        <Navigation toReview={counts.to_review} />
        <div className="app">
          {!fullScreen && <TopBar alerts={counts.alerts} />}
          {children}
        </div>
      </div>
      {/* Floating and quiet: it never moves the page. Offline and waiting actions are saved on this device. */}
      {(offline || pending > 0) && (
        <div className="sync-pill" role="status">
          {offline ? `Offline. ${pending > 0 ? `${pending} saved here` : "Review actions are saved here"} and sent when you reconnect.` : `${pending} saved, sending…`}
        </div>
      )}
      {!offline && pending === 0 && justSaved && <div className="sync-pill ok" role="status">All saved</div>}
      <ToastHost />
    </LangProvider>
  );
}
