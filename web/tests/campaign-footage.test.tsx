import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/campaign", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import { CampaignDetail } from "@/components/More";
import { mockApi } from "./helpers";

const version = { version_no: 1, compliance_status: "pass", hard_blocks: [], unclear_fields: [], rules_json: { payout: { model: "cpm", rate_per_1k: 1, min_views_to_qualify: 0 }, platforms_allowed: ["tiktok"], music_policy: "any", content_rules: [] } };

describe("an approved campaign page accepts footage as a file OR a link", () => {
  it("opens the one Add sheet for this campaign instead of carrying its own form", async () => {
    mockApi({ "GET /api/campaigns/c1": { campaign: { name: "Founders Table", status: "approved", status_reason: null }, version, sources: [] },
      "GET /api/home": { campaigns: [] } });
    render(<CampaignDetail id="c1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add footage" }));
    expect(screen.queryByLabelText("File path on this laptop")).toBeNull();
  });
});
