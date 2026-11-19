import { describe, expect, it } from "vitest";

import { orderStories } from "@/components/Home";

describe("story order on Home", () => {
  it("puts stories with clips left first, most clips first, and finished ones last", () => {
    const ordered = orderStories([{ id: "a", clips_ready: 0 }, { id: "b", clips_ready: 2 }, { id: "c", clips_ready: 0 }, { id: "d", clips_ready: 5 }]);
    expect(ordered.map((s) => s.id)).toEqual(["d", "b", "a", "c"]);
  });
});
