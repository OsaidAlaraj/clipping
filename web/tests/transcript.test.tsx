import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

let search = new URLSearchParams("id=s1");
const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push, replace: nav.push }), usePathname: () => "/transcript", useSearchParams: () => search }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import Feed from "@/components/Feed";
import { clearCache } from "@/lib/data";
import { CampaignDetail, Settings } from "@/components/More";
import Transcript from "@/components/Transcript";
import {
  applyEdit, clock, countTouched, dialectLabel, findSpans, normalise, paragraphs, percent, providerLabel, remapQueue, speakerNames, typed, wordAt, type Doc, type QueueItem, type Word,
} from "@/lib/transcript";
import Page from "@/app/transcript/page";
import { clip, feedRoutes, mockApi } from "./helpers";

// ------------------------------------------------------------------ data

const w = (text: string, start: number, confidence: number | null = 0.95, speaker: string | null = "0"): Word => ({ text, start, end: start + 0.4, confidence, speaker });
const words = (): Word[] => [w("Hello", 0, 0.99), w("there", 0.5, 0.4), w("and", 1), w("welcome", 1.5, 0.95, "1"), w("to", 2, 0.95, "1"),
  w("acne", 2.5, 0.9, "1"), w("cola", 3, 0.7, "1")];
const data = (extra: object = {}) => ({
  id: "t1", version: 1, provider: "deepgram", dialect: "en", words: words(), utterances: [], speakers: { "0": "Speaker 1", "1": "Speaker 2" },
  source_id: "s1", campaign_id: "c1", campaign_name: "Founders Table", language: "en", media_url: "/api/media/sources/x.mp4?exp=1&sig=x", key_terms: [], ...extra,
});
const queue = (): QueueItem[] => [
  { word_index: 5, text: "acne cola", confidence: 0.7, reason: "key_term", term: "Acme Cola", span: 2 },
  { word_index: 1, text: "there", confidence: 0.4, reason: "low_confidence" },
];
const doc = (ws: Word[] = words()): Doc => ({ words: ws, speakers: speakerNames(ws) });

/** The server as the screen sees it: GET the transcript, GET the queue, PUT edits (each answered with the next version). */
function server(extra: Record<string, any> = {}, transcript: object = data(), rows: QueueItem[] = queue()) {
  let version = 1;
  const puts: any[] = [];
  const mock = mockApi({
    "GET /api/transcripts/s1/review-queue": rows,
    "GET /api/transcripts/s1": transcript,
    "PUT /api/transcripts/s1": (body: any) => { puts.push(body); return { version: ++version }; },
    "POST /api/campaigns/c1/key-terms": (body: any) => ({ key_terms: [body.term] }),
    ...extra,
  });
  return { ...mock, puts };
}

const open = async (extra: Record<string, any> = {}, transcript?: object, rows?: QueueItem[]) => {
  const s = server(extra, transcript, rows);
  render(<Transcript sourceId="s1" />);
  await screen.findByRole("heading", { name: "Fix transcript" });
  await screen.findAllByTestId("fix-item").catch(() => undefined);
  return s;
};
const word = (text: string) => screen.getAllByRole("button", { name: text, hidden: true }).find((b) => b.hasAttribute("data-w"))!;
const reader = () => within(screen.getByRole("region", { name: "Transcript" }));
const status = () => screen.getByText(/^(Saved|Saving…|Offline)/).textContent;
const saved = () => waitFor(() => expect(status()).toBe("Saved"));
const editWord = (text: string, to: string) => {
  fireEvent.doubleClick(word(text));
  const input = screen.getByLabelText("Edit word");
  fireEvent.change(input, { target: { value: to } });
  fireEvent.keyDown(input, { key: "Enter" });
};

// ------------------------------------------------------------------ pure logic

describe("what an edit does to the words (the same rules as the server)", () => {
  it("normalises case, punctuation and Arabic marks, and folds alef variants only when finding", () => {
    expect(normalise("“Hello,”")).toBe("hello");
    expect(normalise("مَرْحَبًا")).toBe("مرحبا");
    expect(normalise("أحمد")).not.toBe(normalise("احمد"));
    expect(normalise("أحمد", true)).toBe(normalise("احمد", true));
    expect(normalise("Acme", false, true)).toBe("Acme");
    expect(typed(" a\u0000b \n c ")).toBe("a b c");
  });

  it("changes a word, keeps its time, and vouches for it", () => {
    const out = applyEdit(doc(), { op: "word", index: 1, text: "everyone" });
    expect(out.ok).toBe(true);
    expect(out.doc.words[1]).toEqual({ text: "everyone", start: 0.5, end: 0.9, confidence: 1, speaker: "0" });
    expect(out.map).toEqual([0, null, 2, 3, 4, 5, 6]);
    expect(doc().words[1].text).toBe("there");
  });

  it("confirms a word typed the same, splits a typed phrase over the word's time, and deletes on blank", () => {
    expect(applyEdit(doc(), { op: "word", index: 1, text: "there" }).doc.words[1]).toMatchObject({ text: "there", confidence: 1 });
    const split = applyEdit(doc([w("acmecola", 2, 0.3)]), { op: "word", index: 0, text: " Acme  Cola " });
    expect(split.doc.words.map((x) => [x.text, x.start, x.end])).toEqual([["Acme", 2, 2.2], ["Cola", 2.2, 2.4]]);
    const gone = applyEdit(doc(), { op: "word", index: 0, text: " " });
    expect(gone.doc.words).toHaveLength(6);
    expect(gone.map[3]).toBe(2);
  });

  it("refuses an edit for a word that is not there", () => {
    expect(applyEdit(doc(), { op: "word", index: 99, text: "x" }).ok).toBe(false);
    expect(applyEdit(doc(), { op: "speaker", speaker: "nobody", name: "x" }).ok).toBe(false);
    expect(applyEdit(doc(), { op: "replace", find: "zzz", replace: "x" }).ok).toBe(false);
    expect(applyEdit(doc(), { op: "revert", to_version: 1 }).ok).toBe(false);
  });

  it("replaces a phrase ignoring case and punctuation, keeping the punctuation and the timing", () => {
    const text = [w("Visit", 0), w("ACNE", 1), w("Cola,", 2), w("now.", 3)];
    const out = applyEdit(doc(text), { op: "replace", find: "acne cola", replace: "Acme Cola" });
    expect(out.doc.words.map((x) => x.text)).toEqual(["Visit", "Acme", "Cola,", "now."]);
    expect(out.doc.words.map((x) => x.start)).toEqual([0, 1, 2, 3]);
    expect(out.map).toEqual([0, null, null, 3]);
    const longer = applyEdit(doc([w("acmecola", 4), w("rocks", 5)]), { op: "replace", find: "acmecola", replace: "Acme Cola" });
    expect(longer.doc.words.map((x) => [x.text, x.start, x.end])).toEqual([["Acme", 4, 4.2], ["Cola", 4.2, 4.4], ["rocks", 5, 5.4]]);
    expect(longer.map).toEqual([null, 2]);
    expect(applyEdit(doc([w("um", 0), w("yes", 1)]), { op: "replace", find: "um", replace: "" }).doc.words.map((x) => x.text)).toEqual(["yes"]);
  });

  it("matches case only when asked, and Arabic with marks and alef variants", () => {
    const text = [w("Acme", 0), w("acme", 1), w("أَحْمَد", 2), w("hello", 3)];
    expect(findSpans(doc(text).words, "acme")).toEqual([[0, 1], [1, 2]]);
    expect(findSpans(doc(text).words, "acme", true)).toEqual([[1, 2]]);
    expect(findSpans(doc(text).words, "احمد")).toEqual([[2, 3]]);
    expect(findSpans(doc(text).words, "—")).toEqual([]);
    expect(findSpans(doc(text).words, "  ")).toEqual([]);
  });

  it("counts the words a replace would touch: 100 is fine, 101 needs a confirmation", () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => w("um", i));
    expect(countTouched(many(100), "um")).toBe(100);
    expect(countTouched(many(101), "um")).toBe(101);
    expect(countTouched(Array.from({ length: 102 }, (_, i) => w(i % 2 ? "york" : "new", i)), "new york")).toBe(102);
  });

  it("renames a speaker and goes back to the default for a blank name", () => {
    const named = applyEdit(doc(), { op: "speaker", speaker: "1", name: " Guest " });
    expect(named.doc.speakers).toEqual({ "0": "Speaker 1", "1": "Guest" });
    expect(applyEdit(named.doc, { op: "speaker", speaker: "1", name: "" }).doc.speakers["1"]).toBe("Speaker 2");
    expect(speakerNames([w("a", 0, 1, null)])).toEqual({});
  });

  it("moves queue rows to their new positions and drops those whose words changed", () => {
    const rows = queue();
    const edit = applyEdit(doc(), { op: "word", index: 0, text: "Hi there" });
    expect(remapQueue(rows, edit.map).map((r) => r.word_index)).toEqual([6, 2]);
    const hit = applyEdit(doc(), { op: "word", index: 6, text: "Cola" });   // inside the two-word key term row
    expect(remapQueue(rows, hit.map).map((r) => r.text)).toEqual(["there"]);
  });

  it("groups words into paragraphs by speaker, pause, sentence end and length", () => {
    const spoken = [w("a", 0, 1, "0"), w("b", 1, 1, "0"), w("c", 5, 1, "0"), w("d", 5.5, 1, "1")];
    expect(paragraphs(spoken)).toEqual([{ start: 0, end: 2, speaker: "0" }, { start: 2, end: 3, speaker: "0" }, { start: 3, end: 4, speaker: "1" }]);
    const long = Array.from({ length: 70 }, (_, i) => w("x", i * 0.5, 1, "0"));
    expect(paragraphs(long).map((p) => p.end - p.start)).toEqual([60, 10]);
    const sentences = Array.from({ length: 20 }, (_, i) => w(i === 12 ? "end." : "x", i * 0.5, 1, "0"));
    expect(paragraphs(sentences).map((p) => p.end - p.start)).toEqual([13, 7]);
    expect(paragraphs([])).toEqual([]);
  });

  it("formats times and finds the word being spoken", () => {
    expect([clock(0), clock(65), clock(3725), clock(-3), clock(NaN)]).toEqual(["0:00", "1:05", "1:02:05", "0:00", "0:00"]);
    expect([wordAt(words(), 0.1), wordAt(words(), 0.6), wordAt(words(), 9), wordAt(words(), -1)]).toEqual([0, 1, -1, -1]);
    expect(percent(0.042)).toBe("4.2%");
    expect(providerLabel("faster_whisper")).toBe("Whisper on this laptop");
    expect(providerLabel("new_one")).toBe("new one");
    expect(dialectLabel("ar-SA")).toMatch(/Arabic/);
  });
});

// ------------------------------------------------------------------ the screen

describe("opening the screen", () => {
  it("shows a skeleton that matches the layout, then the fix queue first with the worst problems on top", async () => {
    server();
    const view = render(<Transcript sourceId="s1" />);
    expect(view.container.querySelector("[aria-busy='true']")).toBeTruthy();
    expect(view.container.querySelectorAll(".sk").length).toBeGreaterThan(5);
    const items = await screen.findAllByTestId("fix-item");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText(/Close to your key term/).textContent).toContain("Acme Cola");
    expect(within(items[1]).getByText(/Not sure this was heard right \(40%\)/)).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Fix first · 2" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText(/Founders Table · Deepgram Nova-3 · English/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back" }).getAttribute("href")).toBe("/campaign?id=c1");
    expect(status()).toBe("Saved");
  });

  it("says when nothing needs fixing and points to find and replace", async () => {
    await open({}, undefined, []);
    expect(await screen.findByText("Nothing needs fixing")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("region", { name: "Fix first" })).getByRole("button", { name: "Find and replace" }));
    expect(screen.getByRole("tab", { name: "Transcript" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByLabelText("Find")).toBeTruthy();
  });

  it("says plainly when the transcript is not ready yet, and offers a retry for other failures", async () => {
    mockApi({ "GET /api/transcripts/s1": { __status: 404, body: { error: "No transcript for this source.", code: "no_transcript" } } });
    const first = render(<Transcript sourceId="s1" />);
    expect(await screen.findByText("The transcript is not ready yet")).toBeTruthy();
    first.unmount();
    let calls = 0;
    mockApi({ "GET /api/transcripts/s1/review-queue": [], "GET /api/transcripts/s1": () => (++calls === 1 ? { __status: 500, body: { error: "Something broke." } } : data()) });
    render(<Transcript sourceId="s1" />);
    expect(await screen.findByText("Something broke.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("tab", { name: "Transcript" })).toBeTruthy();
  });

  it("opens from the route with the source id in the address, and says so when there is none", async () => {
    server();
    search = new URLSearchParams("id=s1");
    const view = render(<Page />);
    expect(await screen.findByRole("tab", { name: "Transcript" })).toBeTruthy();
    view.unmount();
    search = new URLSearchParams("");
    render(<Page />);
    expect(screen.getByText("No transcript selected.")).toBeTruthy();
    search = new URLSearchParams("id=s1");
  });
});

describe("DW-2.1 editing, saving in the background, and undo", () => {
  it("DW-2.1 a word edit shows on screen at once, saves in the background on the version it started from, and is acknowledged", async () => {
    const s = await open();
    editWord("there", "everyone");
    expect(word("everyone")).toBeTruthy();   // before any answer has come back
    expect(screen.queryByRole("button", { name: "there", hidden: true })).toBeNull();
    await saved();
    expect(s.puts).toEqual([{ base_version: 1, edits: [{ op: "word", index: 1, text: "everyone" }], confirm_bulk: false }]);
    expect(screen.getByText("Changed “there” to “everyone”.")).toBeTruthy();
  });

  it("DW-2.1 the next edit builds on the version the last save made", async () => {
    const s = await open();
    editWord("there", "you");
    editWord("and", "or");
    await waitFor(() => expect(s.puts).toHaveLength(2));
    expect(s.puts.map((p) => p.base_version)).toEqual([1, 2]);
    await saved();
  });

  it("DW-2.1 fixing from the queue: a doubtful word is a word edit, a key term near-miss replaces everywhere", async () => {
    let asked = 0;
    const s = await open({ "GET /api/transcripts/s1/review-queue": () => (++asked === 1 ? queue() : []) });
    const items = screen.getAllByTestId("fix-item");
    const keyInput = within(items[0]).getByLabelText("Correct “acne cola”") as HTMLInputElement;
    expect(keyInput.value).toBe("Acme Cola");   // the key term is already filled in
    fireEvent.click(within(items[0]).getByRole("button", { name: "Fix" }));
    expect(screen.getAllByTestId("fix-item")).toHaveLength(1);   // gone at once
    expect(word("Acme") && word("Cola")).toBeTruthy();
    const second = screen.getByTestId("fix-item");
    fireEvent.change(within(second).getByLabelText("Correct “there”"), { target: { value: "everyone" } });
    fireEvent.keyDown(within(second).getByLabelText("Correct “there”"), { key: "Enter" });
    expect(screen.queryAllByTestId("fix-item")).toHaveLength(0);
    await saved();
    expect(s.puts.map((p) => p.edits[0])).toEqual([
      { op: "replace", find: "acne cola", replace: "Acme Cola", match_case: false }, { op: "word", index: 1, text: "everyone" }]);
    expect(screen.getByText("Nothing needs fixing")).toBeTruthy();
  });

  it("DW-2.1 'Looks right' confirms a doubtful word for good and only hides a key term suggestion", async () => {
    const s = await open();
    const [key, low] = screen.getAllByTestId("fix-item");
    fireEvent.click(within(low).getByRole("button", { name: "Looks right" }));
    fireEvent.click(within(key).getByRole("button", { name: "Looks right" }));
    expect(screen.queryAllByTestId("fix-item")).toHaveLength(0);
    await saved();
    expect(s.puts).toHaveLength(1);
    expect(s.puts[0].edits).toEqual([{ op: "word", index: 1, text: "there" }]);
  });

  it("DW-2.1 find and replace changes every match, shows the count first, and saves one replace edit", async () => {
    const s = await open();
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.click(reader().getByRole("button", { name: "Find and replace" }));
    expect(screen.getByText("Type a word or phrase.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Find"), { target: { value: "Acne Cola" } });
    expect(screen.getByText("2 words match.")).toBeTruthy();
    expect(word("acne").hasAttribute("data-match")).toBe(true);
    fireEvent.change(screen.getByLabelText("Replace with"), { target: { value: "Acme Cola" } });
    fireEvent.click(screen.getByRole("button", { name: "Replace all" }));
    expect(word("Acme")).toBeTruthy();
    await saved();
    expect(s.puts[0].edits).toEqual([{ op: "replace", find: "Acne Cola", replace: "Acme Cola", match_case: false }]);
  });

  it("DW-2.1 a find with no matches changes nothing and says so", async () => {
    const s = await open();
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.click(reader().getByRole("button", { name: "Find and replace" }));
    fireEvent.change(screen.getByLabelText("Find"), { target: { value: "zebra" } });
    expect(screen.getByText("No matches.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Replace all" }) as HTMLButtonElement).disabled).toBe(true);
    expect(s.puts).toHaveLength(0);
  });

  it("DW-2.1 renaming a speaker shows the new name at once and saves a speaker edit", async () => {
    const s = await open();
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Speakers" }));
    const input = screen.getByLabelText("Speaker 1");
    fireEvent.change(input, { target: { value: "Host" } });
    fireEvent.blur(input);
    expect(screen.getAllByText("Host").length).toBeGreaterThan(0);
    await saved();
    expect(s.puts[0].edits).toEqual([{ op: "speaker", speaker: "0", name: "Host" }]);
  });

  it("DW-2.1 undo after the save is sent restores the old words at once and saves a revert to the version before", async () => {
    const s = await open();
    editWord("there", "everyone");
    await saved();
    fireEvent.click(screen.getAllByRole("button", { name: "Undo" })[0]);
    expect(word("there")).toBeTruthy();   // back on screen before the answer
    await waitFor(() => expect(s.puts).toHaveLength(2));
    expect(s.puts[1]).toEqual({ base_version: 2, edits: [{ op: "revert", to_version: 1 }], confirm_bulk: false });
    await saved();
    expect((screen.getAllByRole("button", { name: "Undo" })[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("DW-2.1 undo of an edit that has not been sent yet just forgets it, and nothing waits on the network", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const s = await open();
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", (url: any, init?: any) => (init?.method === "PUT" ? gate.then(() => real(url, init)) : real(url, init)));
    editWord("there", "you");          // goes out and waits
    editWord("and", "or");             // waits behind it
    expect(word("or")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Undo" })[0]);
    expect(word("and")).toBeTruthy();  // the second edit is undone while the first is still in flight
    expect(word("you")).toBeTruthy();
    release();
    await saved();
    expect(s.puts).toHaveLength(1);    // the second edit never left the device
  });

  it("DW-2.1 the keyboard undo shortcut works", async () => {
    const s = await open();
    editWord("there", "you");
    await saved();
    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });
    expect(word("there")).toBeTruthy();
    await waitFor(() => expect(s.puts).toHaveLength(2));
  });

  it("DW-2.1 a refused save rolls the screen back and says why", async () => {
    await open({ "PUT /api/transcripts/s1": { __status: 400, body: { error: "That word is no longer in the transcript.", code: "bad_edit" } } });
    editWord("there", "everyone");
    expect(word("everyone")).toBeTruthy();
    expect(await screen.findByText(/That word is no longer in the transcript\. That change was undone\./)).toBeTruthy();
    expect(word("there")).toBeTruthy();
    expect(screen.getAllByTestId("fix-item")).toHaveLength(2);   // the queue is back too
    await saved();
  });

  it("DW-2.1 losing the connection keeps the edit on screen, says so, and saves when the connection returns", async () => {
    const s = await open();
    s.state.offline = true;
    editWord("there", "everyone");
    await waitFor(() => expect(status()).toMatch(/^Offline/));
    expect(word("everyone")).toBeTruthy();
    s.state.offline = false;
    act(() => { window.dispatchEvent(new Event("online")); });
    await saved();
    expect(s.puts).toHaveLength(1);
  });

  it("DW-2.1 a transcript changed on another device is merged where the words still match, and the owner is told", async () => {
    let reads = 0;
    const s = await open({
      "GET /api/transcripts/s1": () => (++reads === 1 ? data() : data({ version: 2, words: [w("Hi", 0, 1), ...words().slice(1)] })),
      "PUT /api/transcripts/s1": (body: any) => (body.base_version === 1 ? { __status: 409, body: { error: "changed", code: "stale", version: 2 } } : { version: 3 }),
    });
    editWord("there", "everyone");
    expect(await screen.findByText(/Your edits were kept/)).toBeTruthy();
    await saved();
    expect(word("Hi") && word("everyone")).toBeTruthy();   // their change and ours
    expect(s.calls.filter((c) => c.method === "PUT").map((c) => c.body.base_version)).toEqual([1, 2]);
  });

  it("DW-2.1 an edit that no longer fits after another device's change is dropped and the owner is told", async () => {
    let reads = 0;
    await open({
      "GET /api/transcripts/s1": () => (++reads === 1 ? data() : data({ version: 2, words: [words()[0], w("their", 0.5), ...words().slice(2)] })),
      "PUT /api/transcripts/s1": { __status: 409, body: { error: "changed", code: "stale", version: 2 } },
    });
    editWord("there", "everyone");
    expect(await screen.findByText(/1 of your edits could not be merged/)).toBeTruthy();
    expect(word("their")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "everyone", hidden: true })).toBeNull();
    await saved();
  });

  it("DW-2.1 a replace over 100 words asks first, and only a confirmed one is sent with confirm_bulk", async () => {
    const um = Array.from({ length: 101 }, (_, i) => w("um", i, 0.95, "0"));
    const s = await open({}, data({ words: um }), []);
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.click(reader().getByRole("button", { name: "Find and replace" }));
    fireEvent.change(screen.getByLabelText("Find"), { target: { value: "um" } });
    fireEvent.change(screen.getByLabelText("Replace with"), { target: { value: "uh" } });
    fireEvent.click(screen.getByRole("button", { name: "Replace all" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm replace" });
    expect(within(dialog).getByText("Change 101 words?")).toBeTruthy();
    expect(s.puts).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(s.puts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Replace all" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Change 101 words" }));
    await saved();
    expect(s.puts[0].confirm_bulk).toBe(true);
  });

  it("DW-2.1 a replace of exactly 100 words needs no confirmation", async () => {
    const um = Array.from({ length: 100 }, (_, i) => w("um", i, 0.95, "0"));
    const s = await open({}, data({ words: um }), []);
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.click(reader().getByRole("button", { name: "Find and replace" }));
    fireEvent.change(screen.getByLabelText("Find"), { target: { value: "um" } });
    fireEvent.click(screen.getByRole("button", { name: "Replace all" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await saved();
    expect(s.puts[0].confirm_bulk).toBe(false);
  });
});

describe("key terms", () => {
  it("adds the selected word as a key term for the campaign at once, and takes it back if the server refuses", async () => {
    const s = await open();
    fireEvent.click(word("cola"));
    fireEvent.click(screen.getByRole("button", { name: "Key term" }));
    expect(screen.getByRole("button", { name: "Key term ✓" })).toBeTruthy();   // before the answer
    await waitFor(() => expect(s.posts("/key-terms")[0].body).toEqual({ term: "cola" }));
    await waitFor(() => expect(s.calls.filter((c) => c.path.endsWith("review-queue")).length).toBeGreaterThan(1));   // the queue is judged again
    s.state.offline = true;
    fireEvent.click(word("Hello"));
    fireEvent.click(screen.getByRole("button", { name: "Key term" }));
    expect(await screen.findByText(/The key term was not added/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Key term" })).toBeTruthy();
  });
});

describe("DW-2.4 keyboard, playing, and right-to-left text", () => {
  it("DW-2.4 clicking a word plays from just before it and selects it", async () => {
    await open();
    const play = vi.spyOn(HTMLMediaElement.prototype, "play");
    fireEvent.click(word("welcome"));
    expect(play).toHaveBeenCalled();
    expect(document.querySelector("audio")!.currentTime).toBeCloseTo(1.2, 3);
    expect(word("welcome").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("welcome", { selector: "bdi" })).toBeTruthy();
  });

  it("DW-2.4 space plays and pauses, arrows move between words, Enter edits, Escape cancels", async () => {
    await open();
    const play = vi.spyOn(HTMLMediaElement.prototype, "play");
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause");
    fireEvent.keyDown(document.body, { key: " " });
    expect(play).toHaveBeenCalledTimes(1);
    Object.defineProperty(document.querySelector("audio")!, "paused", { configurable: true, value: false });
    fireEvent.keyDown(document.body, { key: " " });
    expect(pause).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(word("Hello").getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(word("there").getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(word("Hello").getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(document.body, { key: "Enter" });
    const input = screen.getByLabelText("Edit word") as HTMLInputElement;
    expect(input.value).toBe("Hello");
    fireEvent.keyDown(input, { key: " " });   // typing a space does not toggle playback
    expect(pause).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByLabelText("Edit word")).toBeNull();
    expect(word("Hello")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(word("Hello").getAttribute("aria-pressed")).toBe("false");
  });

  it("DW-2.4 Enter in the editor saves; typing a letter shortcut inside a field does not trigger it", async () => {
    const s = await open();
    fireEvent.click(word("and"));
    fireEvent.keyDown(document.body, { key: "e" });
    const input = screen.getByLabelText("Edit word");
    fireEvent.keyDown(input, { key: "n" });   // typed into the field, so no jump to the queue
    fireEvent.change(input, { target: { value: "or" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(word("or")).toBeTruthy();
    await saved();
    expect(s.puts[0].edits[0]).toEqual({ op: "word", index: 2, text: "or" });
  });

  it("DW-2.4 N jumps to the next fix, slash opens find, and the shortcuts are listed for desktop", async () => {
    await open();
    fireEvent.keyDown(document.body, { key: "n" });
    expect(document.activeElement).toBe(screen.getAllByTestId("fix-item")[0].querySelector("input"));
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    fireEvent.keyDown(document.body, { key: "/" });
    expect(await screen.findByLabelText("Find")).toBeTruthy();
    expect(screen.getByText(/Space play · ← → move · Enter edit · N next fix/)).toBeTruthy();
  });

  it("DW-2.4 arabic mixed with english: paragraphs follow their own direction, words are isolated, and Arabic edits save as typed", async () => {
    const mixed = [w("قال", 0, 0.95, "0"), w("أَحْمَد", 0.5, 0.5, "0"), w("Acme", 1, 0.95, "0"), w("رائع،", 1.5, 0.95, "0")];
    const s = await open({}, data({ words: mixed, language: "ar", dialect: "ar-SA", speakers: { "0": "Speaker 1" } }), []);
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    const paragraph = word("Acme").closest("p")!;
    expect(paragraph.getAttribute("dir")).toBe("auto");
    expect(paragraph.getAttribute("lang")).toBe("ar");
    expect(screen.getByText(/Arabic \(Saudi Arabia\)/)).toBeTruthy();
    editWord("أَحْمَد", "محمد");
    expect(word("محمد")).toBeTruthy();
    await saved();
    expect(s.puts[0].edits[0]).toEqual({ op: "word", index: 1, text: "محمد" });
    fireEvent.click(reader().getByRole("button", { name: "Find and replace" }));
    fireEvent.change(screen.getByLabelText("Find"), { target: { value: "محمد" } });
    expect(word("محمد").hasAttribute("data-match")).toBe(true);
    expect(screen.getByLabelText("Find").getAttribute("dir")).toBe("auto");
  });

  it("DW-2.4 a long transcript shows every paragraph but re-renders only the one that changed", async () => {
    const long = Array.from({ length: 3000 }, (_, i) => w(`w${i}`, i * 0.5, 0.95, "0"));
    await open({}, data({ words: long }), []);
    fireEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(document.querySelectorAll("[data-w]")).toHaveLength(3000);
    const untouched = word("w2000");
    editWord("w10", "ten");
    expect(word("w2000")).toBe(untouched);   // the same element: nothing else was rebuilt
    await saved();
  });

  it("DW-2.4 the stylesheet has nothing that could push the page sideways at 375 px", () => {
    const css = readFileSync("app/globals.css", "utf8");
    const block = css.slice(css.indexOf("Transcript correction"));
    expect(block).toMatch(/\.tx-grid \{[^}]*grid-template-columns: minmax\(0, 1fr\)/);
    expect(block).toMatch(/\.tx-text \{[^}]*overflow-wrap: anywhere/);
    expect(block).toMatch(/\.fix-row \.input \{[^}]*min-width: 0/);
    expect(block).toMatch(/\.tx-seek \{[^}]*min-width: 0/);
    expect(block).not.toMatch(/(?<!max-)(?<!min-)width: \d{3,}px/);   // no fixed wide boxes
    expect(block).toMatch(/@media \(max-width: 899px\)[^}]*\.tx-grid\[data-tab="fix"\] \.tx-reader/);   // phone: one view at a time
  });
});

// ------------------------------------------------------------------ the ways in, and the accuracy in Settings

describe("DW-2.3 accuracy in Settings, and the ways to reach the screen", () => {
  it("DW-2.3 Settings lists the measured word error rate per provider and dialect", async () => {
    mockApi({
      "GET /api/settings": { settings: [], kill_switch: { active: false, reason: null } }, "GET /api/reference": { adapters: [], accounts: [] },
      "GET /api/transcripts/accuracy": [{ provider: "deepgram", dialect: "ar-SA", wer: 0.042, words: 1240 }, { provider: "faster_whisper", dialect: "ar-SA", wer: 0.2, words: 980 }],
    });
    render(<Settings />);
    const rows = await screen.findAllByTestId("accuracy-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/Deepgram Nova-3.*Arabic \(Saudi Arabia\).*4\.2%.*1,240 words checked/);
    expect(rows[1].textContent).toMatch(/Whisper on this laptop.*20\.0%/);
    expect(rows[1].querySelector(".bar > i")!.className).toBe("low");
  });

  it("DW-2.3 Settings shows a skeleton while loading, an invitation when there is nothing yet, and a calm message on failure", async () => {
    mockApi({ "GET /api/settings": { settings: [], kill_switch: { active: false, reason: null } }, "GET /api/reference": { adapters: [], accounts: [] }, "GET /api/transcripts/accuracy": [] });
    const view = render(<Settings />);
    expect(view.container.querySelector("[aria-label='Transcript accuracy'][aria-busy='true'] .sk")).toBeTruthy();
    expect(await screen.findByText("Fix a transcript and its accuracy appears here.")).toBeTruthy();
    view.unmount();
    clearCache();   // a second visit with nothing cached
    mockApi({ "GET /api/settings": { settings: [], kill_switch: { active: false, reason: null } }, "GET /api/reference": { adapters: [], accounts: [] } });
    render(<Settings />);
    expect(await screen.findByText(/Accuracy could not be loaded/)).toBeTruthy();
  });

  it("the campaign page lists each source with a Fix transcript link", async () => {
    const version = { version_no: 1, compliance_status: "pass", hard_blocks: [], unclear_fields: [], rules_json: { payout: { model: "cpm", rate_per_1k: 1, min_views_to_qualify: null, max_payout_per_clip: null, budget_total: null, quotes: {} }, platforms_allowed: [], music_policy: "allowed", content_rules: [] }, fee_pct: null };
    mockApi({ "GET /api/campaigns/c1": { campaign: { name: "Founders Table", status: "approved", status_reason: null }, version, sources: [{ id: "s1", language: "ar", duration_s: 125 }, { id: "s2", language: "en", duration_s: null }] } });
    render(<CampaignDetail id="c1" />);
    const links = await screen.findAllByRole("link", { name: /Fix transcript/ });
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/transcript?id=s1", "/transcript?id=s2"]);
    expect(screen.getByText("Arabic · 2:05")).toBeTruthy();
  });

  it("Review's edit sheet links to the clip's source transcript", async () => {
    mockApi(feedRoutes([clip("c1", { source_id: "s9" })]));
    render(<Feed onExit={() => undefined} />);
    await screen.findByTestId("clip-card");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect((await screen.findByRole("link", { name: /Fix transcript/ })).getAttribute("href")).toBe("/transcript?id=s9");
  });
});
