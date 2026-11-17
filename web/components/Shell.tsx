"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api, flushOutbox, readOutbox } from "@/lib/api";
import { LangProvider, useLang } from "@/lib/i18n";

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

function Navigation({ toReview }: { toReview: number }) {
  const path = usePathname() ?? "/";
  const { t } = useLang();
  const items = PLACES.map((place) => ({ ...place, label: t(place.key as any), active: place.match(path) }));
  return (
    <>
      <nav className="nav" aria-label="Main">
        {items.map((item) => (
          <Link key={item.href} href={item.href} className={item.key === "create" ? "create" : undefined}
            aria-current={item.active ? "page" : undefined} aria-label={item.active ? undefined : item.label}>
            {item.icon}
            <span className="label">{item.label}</span>
            {item.key === "review" && toReview > 0 && !item.active && <span className="count" aria-label={`${toReview} waiting`}>{toReview}</span>}
          </Link>
        ))}
      </nav>
      <nav className="side" aria-label="Main">
        <Link href="/" className="wordmark">clipping</Link>
        {items.filter((item) => item.key !== "create").map((item) => (
          <Link key={item.href} href={item.href} aria-current={item.active ? "page" : undefined}>
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
      <Link href="/" className="wordmark">clipping</Link>
      <span className="grow" />
      <Link href="/alerts" className="icon-btn" aria-label={alerts > 0 ? `${t("alerts")}, ${alerts} open` : t("alerts")}>
        {Icon.bell}
        {alerts > 0 && <span className="dot" />}
      </Link>
    </div>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const [counts, setCounts] = useState({ toReview: 0, alerts: 0 });
  const [offline, setOffline] = useState(false);
  const [pending, setPending] = useState(0);
  const path = usePathname() ?? "/";
  const fullScreen = path.startsWith("/review") || path.startsWith("/feed") || path.startsWith("/signin");

  useEffect(() => {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    const sync = async () => {
      setOffline(!navigator.onLine);
      if (navigator.onLine) await flushOutbox();
      setPending(readOutbox().length);
    };
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);

  useEffect(() => {
    api("/api/home")
      .then((home) => setCounts({ toReview: home.today.to_review, alerts: home.attention.length }))
      .catch(() => undefined);
    setPending(readOutbox().length);
  }, [path]);

  return (
    <LangProvider>
      <div className="shell">
        <Navigation toReview={counts.toReview} />
        <div className="app">
          {!fullScreen && <TopBar alerts={counts.alerts} />}
          {(offline || pending > 0) && (
            <div className="page" style={{ paddingBottom: 0 }}>
              <div className="banner warn" role="status">
                {offline ? "You are offline. Review actions are saved on this device and sent when you reconnect. Posting needs a connection." : null}
                {!offline && pending > 0 ? `${pending} saved action${pending === 1 ? "" : "s"} waiting to send.` : null}
              </div>
            </div>
          )}
          {children}
        </div>
      </div>
    </LangProvider>
  );
}
