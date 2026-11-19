import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import Feed, { ComplianceSheet } from "@/components/Feed";
import Queue, { RejectionForm } from "@/components/Queue";
import type { Quality } from "@/lib/api";
import { LangProvider, qualityText } from "@/lib/i18n";
import { clip, feedRoutes, mockApi } from "./helpers";

const OK: Quality = { p_clear: 0.62, label: "About 62% chance to reach 10,000 views (from your own posts)", basis: "your posts", weak: false, min_views: 10000 };
const WEAK: Quality = { p_clear: 0.12, label: "Weak: about 12% chance to reach 5,000 views (assumed: no view history yet). Shown last.", basis: "assumed", weak: true, min_views: 5000 };
const NONE: Quality = { p_clear: null, label: "No view minimum on this campaign", basis: "assumed", weak: false, min_views: null };

async function openFeed(clips: any[], lang: "en" | "ar" = "en") {
  mockApi(feedRoutes(clips));
  render(<LangProvider initial={lang}><Feed onExit={() => undefined} /></LangProvider>);
  return screen.findByTestId("clip-card");
}

// ------------------------------------------------------------------ DW-6.1: the quality line on the card

describe("quality on the review card", () => {
  it("says the chance of reaching the minimum and where the numbers come from", async () => {
    await openFeed([clip("c1", { quality: OK })]);
    const line = screen.getByTestId("quality");
    expect(line.textContent).toBe(OK.label);
    expect(line.className).not.toMatch(/weak/);
  });

  it("marks a weak clip and says why it is shown last", async () => {
    await openFeed([clip("c1", { quality: WEAK })]);
    const line = screen.getByTestId("quality");
    expect(line.className).toMatch(/weak/);
    expect(line.textContent).toMatch(/^Weak: about 12% chance to reach 5,000 views \(assumed/);
    expect(line.textContent).toMatch(/Shown last\.$/);
  });

  it("keeps the server's order: the weak clip comes after the strong ones and is not hidden", async () => {
    const card = await openFeed([clip("c1", { quality: OK }), clip("c2", { quality: OK }), clip("c3", { quality: WEAK })]);
    expect(screen.getByText("1 of 3")).toBeTruthy();
    expect(card).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });        // each skip sends the clip to the back, so two skips bring the weak one up
    fireEvent.keyDown(window, { key: "ArrowDown" });
    await waitFor(() => expect(screen.getByTestId("quality").textContent).toMatch(/^Weak/));
    expect(screen.getByText("1 of 3")).toBeTruthy();
  });

  it("says there is no minimum when the campaign has none, and shows nothing for an older server without quality", async () => {
    await openFeed([clip("c1", { quality: NONE })]);
    expect(screen.getByTestId("quality").textContent).toBe("No view minimum on this campaign");
  });

  it("renders nothing extra when the server sent no quality", async () => {
    await openFeed([clip("c1")]);
    expect(screen.queryByTestId("quality")).toBeNull();
  });

  it("speaks Arabic from the numbers, right to left, with the same facts", async () => {
    await openFeed([clip("c1", { quality: WEAK })], "ar");
    const line = screen.getByTestId("quality");
    expect(line.getAttribute("dir")).toBe("auto");
    expect(line.textContent).toBe("ضعيف: احتمال بلوغ 5,000 مشاهدة: حوالي 12% (تقدير: لا يوجد سجل مشاهدات بعد). يظهر في الآخر.");
    expect(document.documentElement.dir).toBe("rtl");
  });

  it("builds every Arabic variant: your posts, assumed, no minimum, tiny and huge chances", () => {
    expect(qualityText(OK, "ar")).toBe("احتمال بلوغ 10,000 مشاهدة: حوالي 62% (من منشوراتك)");
    expect(qualityText(NONE, "ar")).toBe("لا يوجد حدّ أدنى للمشاهدات في هذه الحملة");
    expect(qualityText({ ...OK, p_clear: 0.004 }, "ar")).toContain("أقل من 1%");
    expect(qualityText({ ...OK, p_clear: 0.999 }, "ar")).toContain("أكثر من 99%");
    expect(qualityText({ ...OK, min_views: null }, "ar")).toBe("لا يوجد حدّ أدنى للمشاهدات في هذه الحملة");
    expect(qualityText(WEAK, "en")).toBe(WEAK.label);
  });

  it("a clip refreshed after an edit keeps its quality line", async () => {
    const fresh = clip("c1", { quality: OK });
    mockApi({ ...feedRoutes([clip("c1", { quality: WEAK })]), "GET /api/clips/c1-new": fresh, "POST /api/clips/c1/edit": { id: "c1-new", status: "ready_for_review" } });
    render(<LangProvider initial="en"><Feed onExit={() => undefined} /></LangProvider>);
    await screen.findByTestId("clip-card");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(await screen.findByLabelText("Caption"), { target: { value: "#ad new caption" } });
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(screen.getByTestId("quality").textContent).toBe(OK.label));
  });
});

// ------------------------------------------------------------------ DW-6.2: brand checks in the compliance report

describe("brand checks in the compliance report", () => {
  const report = (extra: any[]) => ({
    deterministic: [{ check: "duration", status: "pass", evidence: "19.4 s" }, ...extra], rules: [], note: "Agent C checked the campaign rules.", legal_notice: "Not legal advice.", status: "review",
  });

  it("shows the rule, the marketplace's words and the verdict of each brand check, and a reminder as a note", async () => {
    const withBrand = clip("c1", {
      compliance_status: "review",
      compliance_report: report([
        { check: "brand_require_text_1", status: "review", evidence: "“#sampletag” is missing. The marketplace said: “missing #SampleTag”", brand: "founders table", rule: "Must contain “#sampletag”", severity: "warn" },
        { check: "brand_reminder_2", status: "note", evidence: "The marketplace said: “Not on brand”", brand: "founders table", rule: "Marketplace note: Not on brand", severity: "reminder" },
      ]) as any,
    });
    const errors = vi.spyOn(console, "error");
    render(<LangProvider initial="en"><ComplianceSheet clip={withBrand} onClose={() => undefined} /></LangProvider>);
    const rows = screen.getAllByTestId("brand-check");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Brand note: Must contain “#sampletag”");
    expect(rows[0].textContent).toContain("The marketplace said: “missing #SampleTag”");
    expect(within(rows[0]).getByText("Check").className).toMatch(/review/);
    expect(within(rows[1]).getByText("note")).toBeTruthy();
    expect(screen.getByText("Clip length")).toBeTruthy();
    expect(errors.mock.calls.filter((c) => String(c[0]).includes("unique \"key\""))).toHaveLength(0);   // two brand rows, two keys
  });

  it("shows the brand note in Arabic", () => {
    const withBrand = clip("c1", { compliance_report: report([{ check: "brand_banned_phrase_1", status: "fail", evidence: "Found “free”", brand: "b", rule: "Must not contain “free”", severity: "block" }]) as any });
    render(<LangProvider initial="ar"><ComplianceSheet clip={withBrand} onClose={() => undefined} /></LangProvider>);
    expect(screen.getByTestId("brand-check").textContent).toContain("ملاحظة العلامة: Must not contain “free”");
  });

  it("a report without brand checks looks exactly as before", () => {
    render(<LangProvider initial="en"><ComplianceSheet clip={clip("c1", { compliance_report: report([]) as any })} onClose={() => undefined} /></LangProvider>);
    expect(screen.queryAllByTestId("brand-check")).toHaveLength(0);
    expect(screen.getByText("Clip length")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ DW-6.2: recording a marketplace rejection

describe("marketplace rejection form", () => {
  const taught = { brand: "founders table", kept_as: "checks", added: 1, checks: [{ rule: "Must contain “#sampletag”" }] };

  async function open(routes: Record<string, any> = {}, lang: "en" | "ar" = "en") {
    const mock = mockApi({ "POST /api/clips/q1/rejection": taught, ...routes });
    render(<LangProvider initial={lang}><RejectionForm clipId="q1" /></LangProvider>);
    fireEvent.click(screen.getByRole("button", { name: lang === "en" ? "The marketplace rejected this clip" : "المنصّة رفضت هذا المقطع" }));
    return mock;
  }

  it("posts the marketplace's words and says which checks the brand's next clips will get", async () => {
    const mock = await open();
    const box = screen.getByLabelText("What did the marketplace say?") as HTMLTextAreaElement;
    expect(box.maxLength).toBe(500);
    expect((screen.getByRole("button", { name: "Save the reason" }) as HTMLButtonElement).disabled).toBe(true);   // nothing to save yet
    fireEvent.change(box, { target: { value: "  Rejected: missing #SampleTag  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save the reason" }));
    await waitFor(() => expect(screen.getByTestId("rejection-result").textContent).toContain("Must contain “#sampletag”"));
    expect(mock.posts("/api/clips/q1/rejection")[0].body).toEqual({ reason: "Rejected: missing #SampleTag" });
    expect(screen.getByTestId("rejection-result").textContent).toMatch(/^Saved\. The brand's next clips will be checked:/);
    expect(box.value).toBe("");
  });

  it("answers at once: the button reads Saving… and the box is locked until the server replies", async () => {
    let release: (v: any) => void = () => undefined;
    const pending = new Promise((resolve) => { release = resolve; });
    const original = globalThis.fetch;
    await open();
    const gate = vi.fn(async (...args: any[]) => { await pending; return (original as any)(...args); });
    vi.stubGlobal("fetch", gate);
    fireEvent.change(screen.getByLabelText("What did the marketplace say?"), { target: { value: "missing #x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the reason" }));
    expect((await screen.findByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("What did the marketplace say?") as HTMLTextAreaElement).disabled).toBe(true);
    release(undefined);
  });

  it("says a vague reason was kept as a reminder that blocks nothing", async () => {
    await open({ "POST /api/clips/q1/rejection": { brand: "b", kept_as: "reminder", added: 1, checks: [{ rule: "Marketplace note: Not on brand" }] } });
    fireEvent.change(screen.getByLabelText("What did the marketplace say?"), { target: { value: "Not on brand" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the reason" }));
    await waitFor(() => expect(screen.getByTestId("rejection-result").textContent).toBe("Saved as a reminder. It was too general to check, so it never blocks a clip."));
  });

  it("keeps what was typed and shows the refusal when the server says no", async () => {
    await open({ "POST /api/clips/q1/rejection": { __status: 400, body: { error: "Keep the reason under 500 characters.", code: "reason_too_long" } } });
    const box = screen.getByLabelText("What did the marketplace say?") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "too long" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the reason" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Keep the reason under 500 characters.");
    expect(box.value).toBe("too long");
    expect(screen.queryByTestId("rejection-result")).toBeNull();
  });

  it("explains an offline failure instead of failing silently", async () => {
    const mock = await open();
    mock.state.offline = true;
    fireEvent.change(screen.getByLabelText("What did the marketplace say?"), { target: { value: "missing #x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the reason" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/No connection/);
  });

  it("is available in Arabic", async () => {
    await open({}, "ar");
    expect(screen.getByLabelText("ماذا قالت المنصّة؟")).toBeTruthy();
    expect(screen.getByRole("button", { name: "احفظ السبب" })).toBeTruthy();
  });

  it("sits inside the publish checklist for a real clip and is absent for a practice clip", async () => {
    const state = (practice: boolean) => ({
      clip: clip("q1", { status: "accepted" }), server: { brief_rechecked: { ok: true, label: "Brief re-checked" } }, owner_ticks: [], warnings: [],
      scheduler_has_disclosure_toggle: false, campaign_needs_reapproval: false, practice,
    });
    for (const practice of [false, true]) {
      mockApi({ "GET /api/queue": { kill_switch: false, items: [clip("q1", { status: "accepted" })] }, "GET /api/clips/q1/checklist": state(practice) });
      const view = render(<Queue />);
      fireEvent.click(await screen.findByRole("button", { name: /Founders Table/ }));
      const dialog = await screen.findByRole("dialog", { name: "Publish checklist" });
      expect(within(dialog).queryAllByRole("button", { name: "The marketplace rejected this clip" })).toHaveLength(practice ? 0 : 1);
      view.unmount();
    }
  });
});

// ------------------------------------------------------------------ DW-6.4: stylesheet and no-overflow audit (375 px and desktop)

const CSS = readFileSync(path.resolve(import.meta.dirname, "../app/globals.css"), "utf-8");

/** Every declaration block written for a selector (inside or outside a media query), joined. */
function rules(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...CSS.matchAll(new RegExp(`^[ \\t]*${escaped}\\s*\\{([^}]*)\\}`, "gm"))].map((m) => m[1]).join(" ");
}

describe("review and correction screens: stylesheet audit", () => {
  const need: [string, RegExp][] = [
    // review feed: the bottom text wraps instead of overflowing a 375 px card
    [".feed .bottom .line", /flex-wrap:\s*wrap/],
    [".feed .quality", /overflow-wrap:\s*anywhere/],
    [".feed .caption", /-webkit-line-clamp:\s*2/],
    [".feed .top .name", /min-width:\s*0/],
    [".feed .top .name .title", /text-overflow:\s*ellipsis/],
    [".feed-card", /max-width:\s*640px/],
    [".feed", /overflow:\s*hidden/],
    // sheets scroll inside the screen and never grow past it
    [".sheet", /max-height:\s*86dvh/],
    [".sheet", /overflow-y:\s*auto/],
    [".toast", /max-width:\s*608px/],
    // correction screen
    [".tx-text", /overflow-wrap:\s*anywhere/],
    [".fix-ctx", /overflow-wrap:\s*anywhere/],
    [".tx-sel-word", /min-width:\s*0/],
    [".tx-sel-word", /text-overflow:\s*ellipsis/],
    [".tx-seek", /min-width:\s*0/],
    [".fix-row .input", /min-width:\s*0/],
    [".tx-grid", /minmax\(0,\s*1fr\)/],
    [".tx-sub", /text-overflow:\s*ellipsis/],
    // tap targets
    [".chip", /min-height:\s*40px/],
    [".tx-play", /width:\s*44px/],
  ];
  it.each(need)("%s keeps %s", (selector, pattern) => {
    expect(rules(selector), `${selector} is missing or lost its rule`).toMatch(pattern);
  });

  it("the weak badge uses logical properties only, so it mirrors in Arabic", () => {
    const body = rules(".feed .quality.weak") + rules(".feed .quality");
    expect(body.length).toBeGreaterThan(0);
    expect(body).not.toMatch(/\b(left|right|margin-left|margin-right|padding-left|padding-right|text-align:\s*(left|right))\b/);
    expect(rules(".feed .quality.weak")).toMatch(/justify-self:\s*start/);
  });

  it("the weak badge keeps AA contrast: dark brown on pale amber", () => {
    const body = rules(".feed .quality.weak");
    const lum = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const bg = body.match(/background:\s*(#[0-9A-Fa-f]{6})/)![1];
    const fg = body.match(/[^-]color:\s*(#[0-9A-Fa-f]{6})/)![1];
    const [hi, lo] = [lum(bg), lum(fg)].sort((a, b) => b - a);
    expect((hi + 0.05) / (lo + 0.05)).toBeGreaterThan(4.5);
  });

  it("no rule outside a media query fixes a width wider than a 375 px phone can show", () => {
    const base = CSS.replace(/@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^{}]*\}/g, "");   // drop media blocks (their rules are for wide screens or narrow fixes)
    const wide = [...base.matchAll(/(?<![-\w])width:\s*(\d+)px/g)].map((m) => Number(m[1])).filter((n) => n > 343);
    expect(wide).toEqual([]);
  });

  it("the page never scrolls sideways: only the intended strips scroll horizontally", () => {
    const scrolling = [...CSS.matchAll(/^[ \t]*([^{}\n]+)\{[^}]*overflow-x:\s*auto[^}]*\}/gm)].map((m) => m[1].trim());
    expect(scrolling.sort()).toEqual([".segmented", ".stories", ".thumbs"].sort());
  });
});
