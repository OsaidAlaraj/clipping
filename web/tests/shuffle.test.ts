import { describe, expect, it } from "vitest";
import { shuffleAhead } from "@/lib/shuffle";

describe("shuffleAhead", () => {
  it("keeps seen clips and the current one in place and reorders the rest", () => {
    const list = [1, 2, 3, 4, 5, 6, 7, 8];
    let n = 0;
    const out = shuffleAhead(list, 2, () => ((n = (n + 0.37) % 1), n));
    expect(out.slice(0, 3)).toEqual([1, 2, 3]);
    expect([...out].sort()).toEqual(list);
    expect(out.slice(3)).not.toEqual([4, 5, 6, 7, 8]);
  });
  it("does nothing at the end of the list", () => {
    expect(shuffleAhead([1, 2], 1)).toEqual([1, 2]);
  });
});
