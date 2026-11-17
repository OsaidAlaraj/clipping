import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EDGE_GUARD_PX, SWIPE_X_PX, useSwipe } from "@/lib/useSwipe";
import { swipe } from "./helpers";

function Pad(props: Parameters<typeof useSwipe>[0]) {
  const { bind, offset } = useSwipe(props);
  return <div data-testid="pad" data-x={offset.x} {...bind} style={{ width: 300, height: 500 }} />;
}

function setup() {
  const handlers = { onLeft: vi.fn(), onRight: vi.fn(), onUp: vi.fn(), onDown: vi.fn(), onTap: vi.fn() };
  render(<Pad {...handlers} />);
  return { handlers, pad: screen.getByTestId("pad") };
}

describe("swipe gestures", () => {
  it("right, left, up and down each call their own handler", () => {
    const { handlers, pad } = setup();
    swipe(pad, [100, 200], [100 + SWIPE_X_PX, 205]);
    swipe(pad, [200, 200], [200 - SWIPE_X_PX, 195]);
    swipe(pad, [150, 400], [152, 280]);
    swipe(pad, [150, 100], [148, 220]);
    expect(handlers.onRight).toHaveBeenCalledTimes(1);
    expect(handlers.onLeft).toHaveBeenCalledTimes(1);
    expect(handlers.onUp).toHaveBeenCalledTimes(1);
    expect(handlers.onDown).toHaveBeenCalledTimes(1);
    expect(handlers.onTap).not.toHaveBeenCalled();
  });

  it("a short drag does nothing and a touch without movement is a tap", () => {
    const { handlers, pad } = setup();
    swipe(pad, [100, 200], [100 + SWIPE_X_PX - 10, 200]);
    expect(handlers.onRight).not.toHaveBeenCalled();
    swipe(pad, [100, 200], [102, 201]);
    expect(handlers.onTap).toHaveBeenCalledTimes(1);
  });

  it("ignores swipes that start within 20 px of the left edge (Safari back gesture)", () => {
    const { handlers, pad } = setup();
    expect(EDGE_GUARD_PX).toBe(20);
    swipe(pad, [19, 200], [220, 200]);
    swipe(pad, [5, 200], [5, 400]);
    expect(Object.values(handlers).every((fn) => fn.mock.calls.length === 0)).toBe(true);
    swipe(pad, [20, 200], [220, 200]);
    expect(handlers.onRight).toHaveBeenCalledTimes(1);
  });
});
