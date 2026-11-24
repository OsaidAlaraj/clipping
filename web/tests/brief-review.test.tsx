import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/more/add", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import { BriefReview } from "@/components/BriefReview";
import { AddCampaign } from "@/components/More";
import { blockingKeys, counts, editItem, previewDecisions, resolveConflict, textToValue, valueToText, type Lang, type Spec, type SpecItem } from "@/lib/briefSpec";
import { mockApi } from "./helpers";

const item = (id: string, key: string, value: any, extra: Partial<SpecItem> = {}): SpecItem => ({
  id, layer: key.split(".")[0], key, value, source_quote: value === null ? null : `quote for ${id}`, confidence: 0.9, status: value === null ? "unclear" : "stated", origin: "rule", ...extra });

const spec = (): Spec => ({
  version: 1, warnings: [], blocking: ["payout.model"],
  items: [
    item("a", "length.min_s", 10), item("b", "payout.model", null), item("c", "captions.disclosure_policy", "forbidden"),
    item("d", "payout.max_per_creator", 15000, { status: "conflict", alternatives: [{ value: 5000, source_quote: "ai quote", origin: "llm" }] }),
    item("e", "assets.logo", { requirement: "forbidden", link: null }),
  ],
  links: [
    { url: "https://www.notion.so/guide", text: "LOGO GUIDELINES", kind: "guidelines_doc", fetch: { status: "needs_paste", note: "This page needs a sign-in. Open it, copy its text and paste it here." } },
    { url: "https://drive.google.com/drive/folders/x", text: "LOGO", kind: "logo_asset_folder", fetch: { status: "classified_only", note: "" } },
  ],
});

function Harness({ onConfirm = () => {}, onPasteLink = () => {}, lang = "en" as Lang, initial = spec() }) {
  const [s, setS] = useState(initial);
  return <BriefReview spec={s} onChange={setS} onPasteLink={onPasteLink} onConfirm={onConfirm} lang={lang} linksKept={2} />;
}

describe("pure helpers", () => {
  it("answers, dismisses and resolves without touching other items", () => {
    let s = spec();
    expect(blockingKeys(s)).toEqual(["payout.model"]);
    s = editItem(s, "b", { value: "cpm" });
    expect(s.items.find((i) => i.id === "b")).toMatchObject({ value: "cpm", status: "stated", origin: "owner" });
    expect(s.blocking).toEqual([]);
    s = editItem(spec(), "b", { dismissed: true });
    expect(blockingKeys(s)).toEqual([]);
    const r = resolveConflict(spec(), "d", "alt");
    expect(r.items.find((i) => i.id === "d")).toMatchObject({ value: 5000, status: "stated", source_quote: "ai quote" });
    expect(r.items.find((i) => i.id === "a")!.value).toBe(10);
  });
  it("round-trips values through the edit box", () => {
    expect(valueToText(["tiktok", "instagram"])).toBe("tiktok, instagram");
    expect(textToValue("a, b", ["x"])).toEqual(["a", "b"]);
    expect(textToValue("12", 10)).toBe(12);
    expect(textToValue("", "x")).toBeNull();
    expect(valueToText({ pct: 50, countries: ["US", "CA"], label: null })).toBe("pct: 50 · countries: US, CA");
  });
  it("counts and previews the decisions in both languages", () => {
    expect(counts(spec())).toEqual({ found: 4, unclear: 1, conflicts: 1 });
    expect(previewDecisions(spec(), "en").join(" ")).toContain("ad label will be switched off");
    expect(previewDecisions(spec(), "en").join(" ")).toContain("at least 10 seconds");
    expect(previewDecisions(spec(), "ar").join(" ")).toContain("وسم الإعلان");
  });
});

describe("the review screen", () => {
  it("shows layers with each item's source quote and keeps the confirm button disabled until the red item is answered", () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    expect(screen.getByText("What I understood")).toBeTruthy();
    expect(screen.getByText("quote for a")).toBeTruthy();
    expect(screen.getByText("Needs your answer")).toBeTruthy();
    expect(screen.getByText("2 links kept", { exact: false })).toBeTruthy();
    const confirm = screen.getByRole("button", { name: "Answer the red items first" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Model: Your answer"), { target: { value: "cpm" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Done" })[0]);
    const ready = screen.getByRole("button", { name: "Create the campaign" }) as HTMLButtonElement;
    expect(ready.disabled).toBe(false);
    fireEvent.click(ready);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("lets the owner mark a red item not applicable", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Not applicable" }));
    expect((screen.getByRole("button", { name: "Create the campaign" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("edits an item inline and shows the conflict side by side", () => {
    render(<Harness />);
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    fireEvent.change(screen.getByLabelText("min_s"), { target: { value: "12" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Done" }).at(-1)!);
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("Read by rules")).toBeTruthy();
    expect(screen.getByText("Read by AI")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Use this" })[1]);
    expect(screen.queryByText("Read by AI")).toBeNull();
  });

  it("asks for the text of a link that needs a sign-in and hands it up", () => {
    const onPasteLink = vi.fn();
    render(<Harness onPasteLink={onPasteLink} />);
    expect(screen.getByText("Needs a paste")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Paste its text here https:\/\/www.notion.so\/guide/), { target: { value: "The logo must be visible." } });
    fireEvent.click(screen.getByRole("button", { name: "Add the text" }));
    expect(onPasteLink).toHaveBeenCalledWith("https://www.notion.so/guide", "The logo must be visible.");
  });

  it("renders in Arabic, right to left", () => {
    render(<Harness lang="ar" />);
    expect(screen.getByText("ما فهمته")).toBeTruthy();
    expect(screen.getByLabelText("ما فهمته").getAttribute("dir")).toBe("rtl");
    expect(screen.getByText("بحاجة لإجابتك")).toBeTruthy();
    expect(screen.getByRole("button", { name: "أجب عن البنود الحمراء أولاً" })).toBeTruthy();
  });
});

describe("Add campaign: rich paste, then the review screen, then create", () => {
  const html = '<div>Max <b>$2,500</b> <a href="https://drive.google.com/drive/folders/abc?utm_source=x">LOGO</a></div>';
  const clipboard = (data: Record<string, string>) => ({ getData: (type: string) => data[type] ?? "" });

  it("keeps the links from a pasted web page and says how many", async () => {
    mockApi({ "GET /api/reference": { marketplaces: [{ slug: "clipster", name: "Clipster" }], categories: [{ name: "podcast" }] } });
    render(<AddCampaign embedded />);
    const box = screen.getByLabelText("Brief text") as HTMLTextAreaElement;
    fireEvent.paste(box, { clipboardData: clipboard({ "text/html": html, "text/plain": "Max $2,500 LOGO" }) });
    await waitFor(() => expect(box.value).toContain("[LOGO](https://drive.google.com/drive/folders/abc)"));
    expect(screen.getByText("1 link kept")).toBeTruthy();
  });

  it("a plain-text paste is left alone", () => {
    mockApi({ "GET /api/reference": { marketplaces: [], categories: [] } });
    render(<AddCampaign embedded />);
    const box = screen.getByLabelText("Brief text") as HTMLTextAreaElement;
    expect(fireEvent.paste(box, { clipboardData: clipboard({ "text/plain": "just words" }) })).toBe(true);
    expect(screen.queryByText(/kept/)).toBeNull();
  });

  it("reads the brief into a spec, fills the name, and creates the campaign with the confirmed spec", async () => {
    const read: Spec = { ...spec(), blocking: [], items: spec().items.map((i) => (i.id === "b" ? { ...i, value: "cpm", status: "stated" as const } : i)).concat(item("n", "identity.name", "CSGOBIG [CS2]")) };
    const api = mockApi({
      "GET /api/reference": { marketplaces: [{ slug: "clipster", name: "Clipster" }], categories: [{ name: "podcast" }] },
      "POST /api/campaigns/spec": read,
      "POST /api/campaigns": { campaign: { id: "c9" } },
    });
    render(<AddCampaign embedded />);
    fireEvent.change(screen.getByLabelText("Brief text"), { target: { value: "ADD PROVIDED LOGO" } });
    fireEvent.change(screen.getByLabelText("Campaign name"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Read the brief" }));
    expect(await screen.findByText("What I understood")).toBeTruthy();
    expect(api.posts("/api/campaigns/spec")[0].body).toEqual({ brief_text: "ADD PROVIDED LOGO" });
    fireEvent.click(screen.getByRole("button", { name: "Create the campaign" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/api/campaigns") && c.body?.spec?.items?.length)).toBe(true));
  });
});
