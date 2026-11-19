import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/explore", useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Shell from "@/components/Shell";
import { mockApi } from "./helpers";

describe("bottom navigation", () => {
  it("keeps New in the middle with two places on each side", () => {
    mockApi({ "GET /api/counts": { to_review: 0, alerts: 0 } });
    const { container } = render(<Shell><p>x</p></Shell>);
    const nav = container.querySelector("nav.nav") as HTMLElement;
    const groups = Array.from(nav.children);
    expect(groups.map((g) => g.querySelectorAll("a").length)).toEqual([2, 1, 2]);
    expect(within(groups[1] as HTMLElement).getByRole("link").className).toContain("create");
  });
});
