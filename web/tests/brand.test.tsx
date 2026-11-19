import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import { Logo } from "@/components/Shell";
import { readFileSync } from "node:fs";

describe("brand", () => {
  it("shows the ClipMind name next to the mark", () => {
    render(<Logo />);
    expect(screen.getByText("ClipMind")).toBeTruthy();
  });
  it("the app manifest uses the ClipMind name", () => {
    const manifest = JSON.parse(readFileSync("public/manifest.webmanifest", "utf-8"));
    expect(manifest.name).toBe("ClipMind");
    expect(manifest.short_name).toBe("ClipMind");
  });
});
