import { describe, expect, it } from "vitest";

import { plainAlert } from "@/lib/alertText";

describe("plainAlert", () => {
  it("explains a blocked clip in plain words and keeps the original", () => {
    const raw = "Practice · My first video: a clip failed compliance and was blocked (caption_starts_with_disclosure).";
    const out = plainAlert(raw);
    expect(out.text).toContain("#ad label");
    expect(out.raw).toBe(raw);
  });
  it("explains a failing service and a hard block", () => {
    expect(plainAlert("discovery adapter (clippingalpha) failed 3 times. Fallback: manual.").text).toContain("did not answer after 3 tries");
    expect(plainAlert("SpinKing (sample, blocked) was blocked: Gambling-linked campaign (hard rule 5).").text).toContain("cannot be approved");
  });
  it("leaves an unknown message as it is", () => {
    expect(plainAlert("Deadline soon").text).toBe("Deadline soon");
  });
});
