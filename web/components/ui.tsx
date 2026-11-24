"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useLang } from "@/lib/i18n";

/** Small shared building blocks: the page head with a Back that goes where you came from, the bottom sheet, the empty state. */

export const UiIcon = {
  back: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>,
  chev: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>,
  video: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="3" /><path d="M16 10.5l5-3v9l-5-3z" /></svg>,
  flag: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 21V4M5 4h11l-2 4 2 4H5" /></svg>,
  inbox: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 13l3-8h12l3 8v6H3z" /><path d="M3 13h5l1 3h6l1-3h5" /></svg>,
};

/** How many in-app moves happened. When there are none (the page was opened directly) Back goes to the parent instead of leaving the app. */
const nav = { moves: 0, last: "" };
export function noteNavigation(path: string) {
  if (nav.last && nav.last !== path) nav.moves += 1;
  nav.last = path;
}

export function useBack(fallback: string) {
  const router = useRouter();
  return useCallback(() => {
    if (nav.moves > 0) { nav.moves -= 1; router.back(); } else router.push(fallback);
  }, [router, fallback]);
}

export function BackLink({ fallback }: { fallback: string }) {
  const { t } = useLang();
  const back = useBack(fallback);
  return (
    <Link href={fallback} className="back" aria-label={t("back")} onClick={(event) => { event.preventDefault(); back(); }}>
      <span className="flip" style={{ display: "contents" }}>{UiIcon.back}</span>
    </Link>
  );
}

/** One head for every screen: optional Back, the title, optional actions. */
export function PageHead({ title, back, sub, children }: { title: string; back?: string; sub?: string; children?: React.ReactNode }) {
  return (
    <header>
      <div className="head">
        {back && <BackLink fallback={back} />}
        <h1>{title}</h1>
        {children}
      </div>
      {sub && <p className="sub">{sub}</p>}
    </header>
  );
}

export function Empty({ title, body, action, icon }: { title: string; body?: string; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="empty-state">
      {icon && <span className="mark">{icon}</span>}
      <h2>{title}</h2>
      {body && <p>{body}</p>}
      {action}
    </div>
  );
}

/** A bottom sheet (a dialog on desktop). Escape or the scrim closes it; dragging the handle down closes it; focus goes in and comes back. */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const { t } = useLang();
  const [closing, setClosing] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; dy: number; t: number } | null>(null);
  const opener = useRef<Element | null>(null);

  const close = useCallback(() => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(onClose, window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 190);
  }, [closing, onClose]);

  useEffect(() => {
    opener.current = document.activeElement;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const first = panel.current?.querySelector<HTMLElement>("input, textarea, select, button:not(.grab):not([data-close])");
    (first ?? panel.current)?.focus({ preventScroll: true });
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab" && panel.current) {   // keep focus inside while the sheet is open
        const nodes = [...panel.current.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select, textarea, [tabindex]:not([tabindex='-1'])")].filter((n) => n.offsetParent !== null);
        if (nodes.length === 0) return;
        const a = nodes[0], z = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === a) { event.preventDefault(); z.focus(); }
        else if (!event.shiftKey && document.activeElement === z) { event.preventDefault(); a.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); document.body.style.overflow = previous; (opener.current as HTMLElement | null)?.focus?.({ preventScroll: true }); };
  }, [close]);

  const down = (event: React.PointerEvent) => { drag.current = { y: event.clientY, dy: 0, t: performance.now() }; (event.target as Element).setPointerCapture?.(event.pointerId); };
  const move = (event: React.PointerEvent) => {
    const d = drag.current; if (!d || !panel.current) return;
    d.dy = Math.max(0, event.clientY - d.y);
    panel.current.style.animation = "none";
    panel.current.style.transform = `translateY(${d.dy}px)`;
  };
  const up = () => {
    const d = drag.current; drag.current = null;
    if (!d || !panel.current) return;
    const fast = d.dy / Math.max(1, performance.now() - d.t) > 0.6;
    if (d.dy > 110 || (fast && d.dy > 30)) { panel.current.style.transition = "transform 180ms cubic-bezier(0.4,0,1,1)"; panel.current.style.transform = "translateY(100%)"; window.setTimeout(onClose, 170); }
    else { panel.current.style.transition = "transform 260ms cubic-bezier(0.2,0.8,0.2,1)"; panel.current.style.transform = ""; }
  };

  return (
    <div className={`scrim${closing ? " closing" : ""}`} onClick={close}>
      <div ref={panel} className={`sheet${closing ? " closing" : ""}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        <div className="grab-zone" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}><div className="grab" aria-hidden="true" /></div>
        <div className="row between">
          <h1>{title}</h1>
          <button className="btn ghost" data-close onClick={close}>{t("close")}</button>
        </div>
        {children}
      </div>
    </div>
  );
}
