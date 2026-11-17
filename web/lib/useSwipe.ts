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

/**
 * Pointer-based swipe detection. Swipe meanings are physical and do not flip in right-to-left layouts:
 * right always accepts or approves, left always rejects or parks.
 */
export function useSwipe(handlers: SwipeHandlers, options: { vertical?: boolean } = {}) {
  const start = useRef<{ x: number; y: number; id: number } | null>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const vertical = options.vertical ?? true;

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    if (event.clientX < EDGE_GUARD_PX) {
      start.current = null; // too close to the left edge: leave it to the browser
      return;
    }
    start.current = { x: event.clientX, y: event.clientY, id: event.pointerId };
  }, []);

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      if (!start.current) return;
      const dx = event.clientX - start.current.x;
      const dy = event.clientY - start.current.y;
      setOffset(Math.abs(dx) >= Math.abs(dy) || !vertical ? { x: dx, y: 0 } : { x: 0, y: dy });
    },
    [vertical]
  );

  const finish = useCallback(
    (event: React.PointerEvent) => {
      const from = start.current;
      start.current = null;
      setOffset({ x: 0, y: 0 });
      if (!from) return;
      const dx = event.clientX - from.x;
      const dy = event.clientY - from.y;
      if (Math.abs(dx) < TAP_PX && Math.abs(dy) < TAP_PX) {
        handlers.onTap?.();
      } else if (Math.abs(dx) >= Math.abs(dy)) {
        if (dx >= SWIPE_X_PX) handlers.onRight?.();
        else if (dx <= -SWIPE_X_PX) handlers.onLeft?.();
      } else if (vertical) {
        if (dy <= -SWIPE_Y_PX) handlers.onUp?.();
        else if (dy >= SWIPE_Y_PX) handlers.onDown?.();
      }
    },
    [handlers, vertical]
  );

  const onPointerCancel = useCallback(() => {
    start.current = null;
    setOffset({ x: 0, y: 0 });
  }, []);

  return { offset, bind: { onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel } };
}
