import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/explore", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Shell from "@/components/Shell";
import { mockApi } from "./helpers";

describe("bottom navigation", () => {
  it("is five equal columns with New in the middle, and the active place is marked without changing its width", () => {
    mockApi({ "GET /api/counts": { to_review: 0, alerts: 0 } });
    const { container } = render(<Shell><p>x</p></Shell>);
    const nav = container.querySelector("nav.nav") as HTMLElement;
    const links = Array.from(nav.children) as HTMLElement[];
    expect(links.length).toBe(5);                                    // five equal columns
    expect(links[2].className).toContain("create");                  // New is the middle one
    expect(links.map((l) => l.getAttribute("aria-label"))).toEqual(["Home", "Explore", "New", "Review", "Profile"]);
    expect(links.filter((l) => l.getAttribute("aria-current") === "page").length).toBe(1);
  });
});

import { readFileSync } from "node:fs";
describe("bottom navigation stylesheet", () => {
  const css = readFileSync("app/globals.css", "utf-8");
  it("uses a five-column grid and the active state does not change the item's size", () => {
    expect(css).toMatch(/\.nav \{[^}]*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
    const active = css.match(/\.nav a\[aria-current="page"\] \{([^}]*)\}/)![1];
    expect(active).not.toMatch(/width|padding/);
  });
});
