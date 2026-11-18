"use client";

import { useCallback, useRef, useState } from "react";

export type SwipeHandlers = {
  onLeft?: () => void;
  onRight?: () => void;
  onUp?: () => void;
  onDown?: () => void;
  onTap?: () => void;
};

/** Swipes start at least this far from the left edge, so they do not clash with Safari's back gesture. */
export const EDGE_GUARD_PX = 20;
export const SWIPE_X_PX = 80;
export const SWIPE_Y_PX = 90;
const TAP_PX = 8;
/** The accept/reject flag shows once the card has moved this far. */
const FLAG_PX = 30;

export type Intent = "right" | "left" | null;

/**
 * Pointer-based swipe detection. Swipe meanings are physical and do not flip in right-to-left layouts:
 * right always accepts or approves, left always rejects or parks.
 *
 * The dragged element follows the finger by writing its transform straight to the DOM once per frame (`ref`), so a drag never
 * re-renders React and stays at 60 fps. React state changes only when the flag (`intent`) appears or goes away.
 */
export function useSwipe(handlers: SwipeHandlers, options: { vertical?: boolean; tilt?: boolean } = {}) {
  const start = useRef<{ x: number; y: number; id: number } | null>(null);
  const ref = useRef<HTMLElement | null>(null);
  const frame = useRef(0);
  const pose = useRef({ x: 0, y: 0 });
  const [intent, setIntent] = useState<Intent>(null);
  const latest = useRef(handlers);
  latest.current = handlers;
  const vertical = options.vertical ?? true;
  const tilt = options.tilt ?? false;

  const paint = useCallback(() => {
    frame.current = 0;
    const el = ref.current;
    if (!el) return;
    const { x, y } = pose.current;
    el.style.transform = x || y ? `translate3d(${x}px, ${y * 0.4}px, 0)${tilt && !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? ` rotate(${x / 40}deg)` : ""}` : "";
  }, [tilt]);

  const settle = useCallback(() => {
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
    pose.current = { x: 0, y: 0 };
    setIntent(null);
    const el = ref.current;
    if (el) { el.style.transition = "transform 0.18s ease-out"; el.style.transform = ""; }
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    if (event.clientX < EDGE_GUARD_PX) {
      start.current = null; // too close to the left edge: leave it to the browser
      return;
    }
    start.current = { x: event.clientX, y: event.clientY, id: event.pointerId };
    if (ref.current) ref.current.style.transition = "none";
    try { event.currentTarget.setPointerCapture?.(event.pointerId); } catch { /* not capturable */ }
  }, []);

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      if (!start.current) return;
      const dx = event.clientX - start.current.x;
      const dy = event.clientY - start.current.y;
      pose.current = Math.abs(dx) >= Math.abs(dy) || !vertical ? { x: dx, y: 0 } : { x: 0, y: dy };
      if (!frame.current) frame.current = requestAnimationFrame(paint);
      const next: Intent = pose.current.x > FLAG_PX ? "right" : pose.current.x < -FLAG_PX ? "left" : null;
      setIntent((current) => (current === next ? current : next));
    },
    [vertical, paint]
  );

  const onPointerUp = useCallback(
    (event: React.PointerEvent) => {
      const from = start.current;
      start.current = null;
      settle();
      if (!from) return;
      const handle = latest.current;
      const dx = event.clientX - from.x;
      const dy = event.clientY - from.y;
      if (Math.abs(dx) < TAP_PX && Math.abs(dy) < TAP_PX) {
        handle.onTap?.();
      } else if (Math.abs(dx) >= Math.abs(dy)) {
        if (dx >= SWIPE_X_PX) handle.onRight?.();
        else if (dx <= -SWIPE_X_PX) handle.onLeft?.();
      } else if (vertical) {
        if (dy <= -SWIPE_Y_PX) handle.onUp?.();
        else if (dy >= SWIPE_Y_PX) handle.onDown?.();
      }
    },
    [vertical, settle]
  );

  const onPointerCancel = useCallback(() => {
    start.current = null;
    settle();
  }, [settle]);

  return { ref, intent, bind: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel } };
}
