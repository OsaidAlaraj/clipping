import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

import PostPanel from "@/components/PostPanel";
import { Countdown } from "@/components/PostStatus";
import Queue from "@/components/Queue";
import ToastHost from "@/components/ToastHost";
import { NetworkError, PLATFORM_LABEL, flushOutbox, readOutbox, sendOrQueue } from "@/lib/api";
import { LangProvider } from "@/lib/i18n";
import { clip, mockApi } from "./helpers";

// ------------------------------------------------------------------ fixtures

const TICKS = ["account_and_platform_correct", "disclosure_toggle_on", "caption_pasted", "hashtags_and_tags_checked"];
const server = (extra: Record<string, boolean> = {}) => ({
  account_owned_and_allowed: { ok: extra.account ?? true, label: "Your own active account, on a platform the campaign allows" },
  file_hash_matches_approved: { ok: extra.file ?? true, label: "File matches the version you accepted" },
  brief_rechecked: { ok: extra.brief ?? true, label: "Brief re-checked and unchanged for this batch" },
});
const option = (platform: string, extra: object = {}) => ({
  platform, label: PLATFORM_LABEL[platform], account: { id: `a-${platform}`, handle: `own_${platform}` }, connection_state: "connected", allowed: true,
  blocked_reason: null, manual_only: false, existing_target: null, checklist: { server: server(), owner_ticks: TICKS }, warnings: [], ...extra,
});
const OPTIONS = { platforms: [option("youtube_shorts"), option("instagram_reels"), option("tiktok", { connection_state: "not_connected", manual_only: true })] };
const target = (id: string, platform: string, state: string, extra: object = {}) => ({
  id, clip_id: "q1", platform, state, scheduled_at: "2026-11-18T10:00:00+01:00", url: null, reason: null, manual: false, account_id: `a-${platform}`,
  privacy: null, submit_by: null, ...extra,
});
const CLIP = clip("q1", { status: "accepted", hashtags: ["#FoundersTable", "#ad"] });
const POLL = 15;

function show(routes: Record<string, any>, { lang = "en", onChanged = vi.fn(), onUnavailable }: { lang?: "en" | "ar"; onChanged?: () => void; onUnavailable?: () => void } = {}) {
  const mock = mockApi({ "GET /api/clips/q1/post-options": OPTIONS, "GET /api/clips/q1/post-status": { targets: [] }, ...routes });
  const view = render(<LangProvider initial={lang}><PostPanel clip={CLIP} onChanged={onChanged} onUnavailable={onUnavailable} pollMs={POLL} /><ToastHost /></LangProvider>);
  return { mock, view, onChanged };
}

const tickAll = async () => { for (const box of (await screen.findAllByRole("checkbox")).filter((b) => /^(I am|The paid|The caption|Hashtags)/.test(b.parentElement?.textContent ?? ""))) fireEvent.click(box); };

// ------------------------------------------------------------------ DW-4.1: pick, one checklist, one tap, live states, cancel

describe("the posting screen", () => {
  it("DW_4_1 picks platforms, shows ONE combined checklist, confirms with one tap and follows each platform's state live", async () => {
    let created: any[] = [];
    let phase = "scheduled";
    const { mock, onChanged } = show({
      "GET /api/clips/q1/post-status": () => ({ targets: created.map((x) => (x.platform === "youtube_shorts" ? { ...x, state: phase, url: phase === "published" ? "https://www.youtube.com/shorts/abc" : null } : x)) }),
      "POST /api/clips/q1/post": (body: any) => {
        created = body.platforms.map((p: string, i: number) => target(`t${i}`, p, "scheduled", { manual: p === "tiktok" }));
        return { targets: created };
      },
    });
    // the picker: every allowed platform starts on; the unconnected one says it is posted by hand
    const yt = await screen.findByRole("checkbox", { name: /YouTube Shorts/ });
    const ig = screen.getByRole("checkbox", { name: /Instagram Reels/ });
    const tt = screen.getByRole("checkbox", { name: /TikTok/ });
    expect([yt, ig, tt].map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, true]);
    expect(screen.getByText("You post it by hand")).toBeTruthy();
    fireEvent.click(ig);                                                               // not Instagram this time
    // one combined checklist: server items are locked (no checkbox), the four ticks appear once for all platforms
    expect(screen.getAllByText("Your own active account, on a platform the campaign allows")).toHaveLength(1);
    expect(screen.queryByRole("checkbox", { name: /Your own active account/ })).toBeNull();
    const confirm = screen.getByRole("button", { name: "Confirm and post (2)" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText("Tick every item to continue.")).toBeTruthy();
    await tickAll();
    expect(confirm.disabled).toBe(false);
    expect(mock.posts("/post")).toHaveLength(0);                                       // nothing is sent before the tap
    fireEvent.click(confirm);
    expect(screen.getAllByText("Sending your request…")).toHaveLength(2);              // answered at once, before the server replies
    await screen.findByText("Waiting for you to post it");                             // TikTok has no connection: a hand-posted target
    const sent = mock.posts("/api/clips/q1/post");
    expect(sent).toHaveLength(1);                                                      // ONE request for both platforms
    expect(sent[0].body.platforms).toEqual(["youtube_shorts", "tiktok"]);
    expect(Object.keys(sent[0].body.ticks)).toEqual(["youtube_shorts", "tiktok"]);
    expect(Object.keys(sent[0].body.ticks.tiktok).sort()).toEqual([...TICKS].sort());
    expect(typeof sent[0].body.request_id).toBe("string");
    expect(within(screen.getByRole("article", { name: "YouTube Shorts" })).getByText(/Goes out at/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Confirm and post/ })).toBeNull();     // both platforms are taken; Instagram is still pickable
    expect(screen.getByRole("checkbox", { name: /Instagram Reels/ })).toBeTruthy();
    // live: the platform moves on by itself
    phase = "sending";
    await within(screen.getByRole("article", { name: "YouTube Shorts" })).findByText("Sending…");
    expect(within(screen.getByRole("article", { name: "YouTube Shorts" })).queryByRole("button", { name: /Cancel/ })).toBeNull();   // too late to cancel
    phase = "published";
    await within(screen.getByRole("article", { name: "YouTube Shorts" })).findByText("Posted");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("DW_4_1 Cancel stops an unsent target at once, and a target that already went out says so and stays as it is", async () => {
    let cancelled = false;
    show({
      "GET /api/clips/q1/post-status": () => ({ targets: [target("t1", "youtube_shorts", cancelled ? "cancelled" : "scheduled"), target("t2", "tiktok", "scheduled")] }),
      "POST /api/targets/t1/cancel": () => { cancelled = true; return target("t1", "youtube_shorts", "cancelled"); },
      "POST /api/targets/t2/cancel": () => ({ __status: 400, body: { error: "It has already been sent, so it cannot be cancelled.", code: "not_cancellable" } }),
    });
    const yt = await screen.findByRole("article", { name: "YouTube Shorts" });
    fireEvent.click(within(yt).getByRole("button", { name: "Cancel YouTube Shorts" }));
    expect(within(yt).getByText("Cancelled")).toBeTruthy();                            // optimistic
    expect(within(yt).queryByRole("button", { name: /Cancel/ })).toBeNull();
    await screen.findByText("Cancelled. Nothing was sent.");
    fireEvent.click(within(screen.getByRole("article", { name: "TikTok" })).getByRole("button", { name: "Cancel TikTok" }));
    await screen.findByText(/Could not cancel it\. It has already been sent/);
    await waitFor(() => expect(within(screen.getByRole("article", { name: "TikTok" })).queryByText("Cancelled")).toBeNull());   // put back
  });

  it("DW_4_1 a refusal keeps the owner's ticks and shows the server's reason; the owner can fix it and tap again", async () => {
    let refuse = true;
    const { mock } = show({ "POST /api/clips/q1/post": () => (refuse ? { __status: 400, body: { error: "The kill switch is on.", code: "kill_switch" } } : { targets: [target("t1", "youtube_shorts", "scheduled")] }) });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Instagram Reels/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /TikTok/ }));
    await tickAll();
    fireEvent.click(screen.getByRole("button", { name: "Confirm and post (1)" }));
    expect((await screen.findByRole("alert")).textContent).toContain("The kill switch is on.");
    expect(screen.queryByText("Sending your request…")).toBeNull();
    const button = screen.getByRole("button", { name: "Confirm and post (1)" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);                                               // ticks were kept
    refuse = false;
    fireEvent.click(button);
    await screen.findByRole("article", { name: "YouTube Shorts" });
    const ids = mock.posts("/api/clips/q1/post").map((c) => c.body.request_id);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
  });

  it("DW_4_1 a platform that cannot be chosen says why and cannot be ticked; an unchanged brief can be confirmed from here", async () => {
    const stale = { checklist: { server: server({ brief: false }), owner_ticks: TICKS } };
    const { mock } = show({
      "GET /api/clips/q1/post-options": { platforms: [option("youtube_shorts", stale), option("tiktok", { allowed: false, blocked_reason: "The campaign does not allow this platform." })] },
      "POST /api/campaigns/camp-1/recheck": { changed: false },
    });
    const tt = (await screen.findByRole("checkbox", { name: /TikTok/ })) as HTMLInputElement;
    expect(tt.disabled).toBe(true);
    expect(tt.checked).toBe(false);
    expect(screen.getByText("The campaign does not allow this platform.")).toBeTruthy();
    expect(screen.getByText("Fix the locked items first.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "I re-read the brief: unchanged" }));
    await waitFor(() => expect(mock.posts("/recheck")[0]?.body).toEqual({ unchanged_confirmed: true }));
  });

  it("DW_4_1 with no connection the tap is saved with its request id, then sent once when the connection returns", async () => {
    let created: any[] = [];
    const { mock } = show({
      "GET /api/clips/q1/post-status": () => ({ targets: created }),
      "POST /api/clips/q1/post": (body: any) => { created = body.platforms.map((p: string) => target(`t-${p}`, p, "scheduled")); return { targets: created }; },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: /Instagram Reels/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /TikTok/ }));
    await tickAll();
    mock.state.offline = true;
    fireEvent.click(screen.getByRole("button", { name: "Confirm and post (1)" }));
    await screen.findByText(/Saved on this device/);
    expect(screen.getByText("Waiting for a connection")).toBeTruthy();
    const [saved] = readOutbox();
    expect(saved.path).toBe("/api/clips/q1/post");
    expect(saved.id).toBe(saved.body.request_id);                                      // the same id the server uses to apply it once
    mock.state.offline = false;
    expect(await flushOutbox()).toBe(1);
    await screen.findByRole("article", { name: "YouTube Shorts" });
    expect(screen.queryByText(/Saved on this device/)).toBeNull();
    expect(readOutbox()).toEqual([]);
    expect(mock.posts("/api/clips/q1/post")).toHaveLength(1);
  });

  it("tells the Queue when the options cannot be loaded, so it can fall back", async () => {
    const onUnavailable = vi.fn();
    show({ "GET /api/clips/q1/post-options": () => ({ __status: 500, body: { error: "down" } }) }, { onUnavailable });
    await waitFor(() => expect(onUnavailable).toHaveBeenCalled());
    expect((await screen.findByRole("alert")).textContent).toBe("Could not load the posting options. Check your connection.");
  });

  it("opens inside the Queue sheet in place of the old one-platform approval", async () => {
    mockApi({
      "GET /api/queue": { kill_switch: false, items: [clip("q1", { status: "accepted" })] },
      "GET /api/clips/q1/checklist": { clip: clip("q1", { status: "accepted" }), server: server(), owner_ticks: TICKS, warnings: [], scheduler_has_disclosure_toggle: false, campaign_needs_reapproval: false, practice: false },
      "GET /api/clips/q1/post-options": OPTIONS, "GET /api/clips/q1/post-status": { targets: [] },
    });
    render(<Queue />);
    fireEvent.click(await screen.findByRole("button", { name: /Founders Table/ }));
    const dialog = await screen.findByRole("dialog", { name: "Publish checklist" });
    await within(dialog).findByRole("button", { name: /Confirm and post/ });
    expect(within(dialog).queryByRole("button", { name: "Approve for publishing" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Mark posted" })).toBeNull();
  });
});

describe("saving a tap offline (api)", () => {
  it("sends at once when online and keeps one saved copy per id when not", async () => {
    const mock = mockApi({ "POST /api/x": { ok: true } });
    expect(await sendOrQueue("/api/x", { request_id: "r1" }, "r1")).toEqual({ ok: true });
    mock.state.offline = true;
    expect(await sendOrQueue("/api/x", { request_id: "r1" }, "r1")).toEqual({ queued: true });
    expect(await sendOrQueue("/api/x", { request_id: "r1" }, "r1")).toEqual({ queued: true });
    expect(readOutbox()).toHaveLength(1);
    mockApi({ "POST /api/x": () => ({ __status: 400, body: { error: "no" } }) });
    await expect(sendOrQueue("/api/x", {}, "r2")).rejects.not.toBeInstanceOf(NetworkError);
    expect(readOutbox()).toHaveLength(1);                                              // a refusal is never saved for later
  });
});

// ------------------------------------------------------------------ DW-4.2: the manual path

function phone(extra: Record<string, any> = {}) {
  const share = vi.fn(async () => undefined);
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal("navigator", { canShare: vi.fn(() => true), share, clipboard: { writeText }, ...extra });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("coarse"), media: query, addEventListener() {}, removeEventListener() {} }));
  return { share, writeText };
}
const MANUAL = { "GET /api/clips/q1/post-status": { targets: [target("m1", "tiktok", "scheduled", { manual: true })] }, "GET /api/media/": { ok: true } };

describe("posting by hand", () => {
  it("DW_4_2 on a phone the video goes to the share sheet as a file with its caption", async () => {
    const { share } = phone();
    show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Share the video" }));
    await waitFor(() => expect(share).toHaveBeenCalled());
    const arg = (share.mock.calls[0] as any)[0];
    expect(arg.files).toHaveLength(1);
    expect(arg.files[0]).toBeInstanceOf(File);
    expect(arg.files[0].name).toBe("clip-q1.mp4");
    expect(arg.text).toBe(CLIP.caption_text);
  });

  it("DW_4_2 on a computer the video is downloaded and the file is never fetched for sharing", async () => {
    const anchors: { download: string; href: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { anchors.push({ download: this.download, href: this.getAttribute("href") ?? "" }); });
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn() } });
    const { mock } = show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Download the video" }));
    await screen.findByText("Download started.");
    expect(anchors).toEqual([{ download: "clip-q1.mp4", href: CLIP.video_url }]);
    expect(mock.calls.some((c) => c.path.startsWith("/api/media/"))).toBe(false);
  });

  it("DW_4_2 a phone that cannot share files falls back to the download, and a dismissed share sheet downloads nothing", async () => {
    const anchors: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { anchors.push(this.download); });
    const { share } = phone({ canShare: vi.fn(() => false) });
    show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Share the video" }));
    await screen.findByText("Could not share the video. Download it instead.");
    expect(share).not.toHaveBeenCalled();
    expect(anchors).toEqual(["clip-q1.mp4"]);
    anchors.length = 0;
    const abort = vi.fn(async () => { throw new DOMException("closed", "AbortError"); });
    vi.stubGlobal("navigator", { canShare: () => true, share: abort, clipboard: { writeText: vi.fn() } });
    fireEvent.click(screen.getByRole("button", { name: "Share the video" }));
    await waitFor(() => expect(abort).toHaveBeenCalled());
    expect(anchors).toEqual([]);
  });

  it("DW_4_2 copies the caption and the hashtags", async () => {
    const { writeText } = phone();
    show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Copy caption" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(CLIP.caption_text));
    fireEvent.click(screen.getByRole("button", { name: "Copy hashtags" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("#FoundersTable #ad"));
    expect(screen.getByText("Copied.")).toBeTruthy();
  });

  it("DW_4_2 a blocked clipboard shows the text selected for the owner to copy by hand", async () => {
    phone({ clipboard: { writeText: vi.fn(async () => { throw new DOMException("no", "NotAllowedError"); }) } });
    show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Copy caption" }));
    const area = (await screen.findByRole("textbox", { name: "Copy caption" })) as HTMLTextAreaElement;
    expect(area.readOnly).toBe(true);
    expect(area.value).toBe(CLIP.caption_text);
    expect(screen.getByText("Copying is blocked here. Select the text below and copy it yourself.")).toBeTruthy();
    expect(area.selectionEnd - area.selectionStart).toBe(CLIP.caption_text!.length);
  });

  it("DW_4_2 a browser with no clipboard at all gets the same select-and-copy box", async () => {
    vi.stubGlobal("navigator", {});
    show(MANUAL);
    fireEvent.click(await screen.findByRole("button", { name: "Copy hashtags" }));
    expect(((await screen.findByRole("textbox", { name: "Copy hashtags" })) as HTMLTextAreaElement).value).toBe("#FoundersTable #ad");
  });

  it("DW_4_2 records the pasted link and shows the post as live with its 30-minute countdown", async () => {
    const submitBy = new Date(Date.now() + 29 * 60_000).toISOString();
    let linked = false;
    const { mock, onChanged } = show({
      ...MANUAL,
      "GET /api/clips/q1/post-status": () => ({ targets: [linked ? target("m1", "tiktok", "published", { manual: true, url: "https://www.tiktok.com/@own/video/1", submit_by: submitBy }) : target("m1", "tiktok", "scheduled", { manual: true })] }),
      "POST /api/targets/m1/link": () => { linked = true; return target("m1", "tiktok", "published", { manual: true, url: "https://www.tiktok.com/@own/video/1", submit_by: submitBy }); },
    });
    fireEvent.change(await screen.findByLabelText("Link to the public post"), { target: { value: " https://www.tiktok.com/@own/video/1 " } });
    fireEvent.click(screen.getByRole("button", { name: "Save the link" }));
    await within(await screen.findByRole("article", { name: "TikTok" })).findByText("Posted");
    expect(mock.posts("/link")[0].body).toEqual({ url: "https://www.tiktok.com/@own/video/1" });
    expect(screen.getByTestId("submit-countdown").textContent).toMatch(/Submit the link to the marketplace within\s*(29|28):\d\d/);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("DW_4_2 refuses a link that is not a web address without asking the server, and shows the server's refusal of the wrong site", async () => {
    const { mock } = show({
      ...MANUAL,
      "POST /api/targets/m1/link": () => ({ __status: 400, body: { error: "Paste the link to the TikTok post.", code: "link_required" } }),
    });
    const field = await screen.findByLabelText("Link to the public post");
    fireEvent.change(field, { target: { value: "my video" } });
    fireEvent.click(screen.getByRole("button", { name: "Save the link" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Paste the full link to the post. It starts with https://");
    expect(mock.posts("/link")).toHaveLength(0);
    fireEvent.change(field, { target: { value: "https://elsewhere.example/x" } });
    expect(screen.queryByRole("alert")).toBeNull();                                    // typing clears the message
    fireEvent.click(screen.getByRole("button", { name: "Save the link" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That is not a TikTok link. Paste the link to the TikTok post.");
    expect(mock.posts("/link")).toHaveLength(1);
    expect(within(screen.getByRole("article", { name: "TikTok" })).queryByText("Posted")).toBeNull();
  });
});

// ------------------------------------------------------------------ DW-4.3: finish in the app, check, countdown

describe("what waits on the owner", () => {
  it("DW_4_3 a draft and a private upload each say 'Finish in the app' and take the public link", async () => {
    show({ "GET /api/clips/q1/post-status": { targets: [target("d1", "tiktok", "draft"), target("p1", "youtube_shorts", "private", { privacy: "private" })] } });
    const draft = await screen.findByRole("article", { name: "TikTok" });
    expect(within(draft).getByText("Finish in the app")).toBeTruthy();
    expect(within(draft).getByText("Open TikTok, add the caption, post it, then paste the public link below.")).toBeTruthy();
    expect(within(draft).getByText("Saved as a draft")).toBeTruthy();
    expect(within(draft).getByRole("button", { name: "Copy caption" })).toBeTruthy();   // TikTok never gets the caption from us
    expect(within(draft).queryByRole("button", { name: /Share|Download/ })).toBeNull();
    const priv = screen.getByRole("article", { name: "YouTube Shorts" });
    expect(within(priv).getByText("Open YouTube Shorts, make the video public, then paste the public link below.")).toBeTruthy();
    expect(within(priv).getByLabelText("Link to the public post")).toBeTruthy();
  });

  it("DW_4_3 a post that needs a check says what to verify and lets the owner say it did not post", async () => {
    let cancelled = false;
    show({
      "GET /api/clips/q1/post-status": () => ({ targets: [target("n1", "instagram_reels", cancelled ? "cancelled" : "needs_check", { reason: "The connection dropped during the upload." })] }),
      "POST /api/targets/n1/cancel": () => { cancelled = true; return target("n1", "instagram_reels", "cancelled"); },
    });
    const card = await screen.findByRole("article", { name: "Instagram Reels" });
    expect(within(card).getByRole("alert").textContent).toContain("The connection dropped during the upload.");
    expect(within(card).getByRole("alert").textContent).toContain("Open Instagram Reels. If the video is there, paste its link below.");
    fireEvent.click(within(card).getByRole("button", { name: "It did not post" }));
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Instagram Reels" })).getByText("Cancelled")).toBeTruthy());
  });

  it("DW_4_3 the countdown shows the time left, ticks down every second and says when the 30 minutes are over", () => {
    vi.useFakeTimers();
    render(<Countdown until={new Date(Date.now() + 90_000).toISOString()} />);
    expect(screen.getByRole("timer").textContent).toBe("01:30");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByRole("timer").textContent).toBe("01:29");
    act(() => { vi.advanceTimersByTime(89_000); });
    expect(screen.queryByRole("timer")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("The 30 minutes are over");
  });

  it("DW_4_3 a countdown that has already run out shows the warning straight away", () => {
    render(<Countdown until={new Date(Date.now() - 1000).toISOString()} />);
    expect(screen.getByRole("status").textContent).toContain("The 30 minutes are over");
  });

  it("a failed post shows its reason, and a published one links to the post", async () => {
    show({ "GET /api/clips/q1/post-status": { targets: [target("f1", "tiktok", "failed", { reason: "TikTok refused the file." }), target("ok1", "youtube_shorts", "published", { url: "https://www.youtube.com/shorts/abc" })] } });
    expect(within(await screen.findByRole("article", { name: "TikTok" })).getByText("TikTok refused the file.")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "YouTube Shorts" })).getByRole("link", { name: "Open the post" }).getAttribute("href")).toBe("https://www.youtube.com/shorts/abc");
  });
});

// ------------------------------------------------------------------ DW-4.4: Arabic, right to left, 375 px

describe("Arabic and the stylesheet", () => {
  it("DW_4_4 shows the picker, the checklist and the button in Arabic, laid out right to left", async () => {
    show({}, { lang: "ar" });
    const yt = await screen.findByRole("checkbox", { name: /يوتيوب/ });
    expect(yt).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /إنستغرام/ })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /تيك توك/ })).toBeTruthy();
    expect(screen.getByText("تنشره بيدك")).toBeTruthy();
    expect(screen.getByText("قبل النشر")).toBeTruthy();
    expect(screen.getByText("أنشر من الحساب الصحيح وعلى المنصة الصحيحة")).toBeTruthy();
    expect(screen.getByText("حسابك الفعّال، على منصة تسمح بها الحملة")).toBeTruthy();
    expect(screen.getByRole("button", { name: "أكّد وانشر (3)" })).toBeTruthy();
    expect(document.querySelector("section.post")?.getAttribute("dir")).toBe("rtl");
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.body.textContent).not.toMatch(/Confirm and post|Before you post/);
  });

  it("DW_4_4 shows each state, the finish-in-the-app note and the manual tools in Arabic", async () => {
    show({ "GET /api/clips/q1/post-status": { targets: [target("d1", "tiktok", "draft"), target("m1", "youtube_shorts", "scheduled", { manual: true }), target("s1", "instagram_reels", "scheduled")] } }, { lang: "ar" });
    const draft = await screen.findByRole("article", { name: "تيك توك" });
    expect(within(draft).getByText("أكمله داخل التطبيق")).toBeTruthy();
    expect(within(draft).getByText("حُفظ كمسوّدة")).toBeTruthy();
    expect(within(draft).getByRole("button", { name: "انسخ الوصف" })).toBeTruthy();
    expect(within(draft).getByLabelText("رابط المنشور العام")).toBeTruthy();
    const manual = screen.getByRole("article", { name: "يوتيوب" });
    expect(within(manual).getByText("بانتظار أن تنشره بيدك")).toBeTruthy();
    expect(within(manual).getByRole("button", { name: "نزّل الفيديو" })).toBeTruthy();
    const waiting = screen.getByRole("article", { name: "إنستغرام" });
    expect(within(waiting).getByRole("button", { name: "إلغاء إنستغرام" })).toBeTruthy();
    expect(within(waiting).getByText(/يُنشر عند/)).toBeTruthy();
  });

  it("DW_4_4 the server's reasons a platform cannot be chosen are shown in Arabic", async () => {
    show({ "GET /api/clips/q1/post-options": { platforms: [option("tiktok", { allowed: false, blocked_reason: "The campaign does not allow this platform." }), option("youtube_shorts", { allowed: false, blocked_reason: "You have no active YouTube Shorts account." })] } }, { lang: "ar" });
    expect(await screen.findByText("الحملة لا تسمح بهذه المنصة.")).toBeTruthy();
    expect(screen.getByText("ليس لديك حساب فعّال على هذه المنصة.")).toBeTruthy();
  });

  it("DW_4_4 the Arabic countdown and refusal texts exist", async () => {
    render(<LangProvider initial="ar"><Countdown until={new Date(Date.now() + 61_000).toISOString()} /></LangProvider>);
    expect(screen.getByTestId("submit-countdown").textContent).toContain("أرسل الرابط إلى المنصّة خلال");
    expect(screen.getByRole("timer").getAttribute("dir")).toBe("ltr");                  // digits keep their order
  });

  const CSS = readFileSync(path.resolve(import.meta.dirname, "../app/globals.css"), "utf-8");
  const block = (selector: string) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return [...CSS.matchAll(new RegExp(`^[ \\t]*${escaped}\\s*\\{([^}]*)\\}`, "gm"))].map((m) => m[1]).join(" ");
  };
  const postRules = [...CSS.matchAll(/^[ \t]*(\.post[^{}\n]*)\{([^}]*)\}/gm)].map((m) => ({ selector: m[1].trim(), body: m[2] }));

  it.each([
    [".post", /min-width:\s*0/],
    [".post-pick", /min-height:\s*56px/],
    [".post-pick-main", /min-width:\s*0/],
    [".post-pick-main", /overflow-wrap:\s*anywhere/],
    [".post-target", /overflow-wrap:\s*anywhere/],
    [".post-target", /min-width:\s*0/],
    [".post-actions", /flex-wrap:\s*wrap/],
    [".post-actions .btn", /min-height:\s*44px/],
    [".post-link-row", /flex-wrap:\s*wrap/],
    [".post-link-row .input", /min-width:\s*0/],
    [".post-copy .input", /width:\s*100%/],
  ])("DW_4_4 stylesheet: %s keeps %s so nothing overflows at 375 px", (selector, pattern) => {
    expect(block(selector), `${selector} is missing or lost its rule`).toMatch(pattern);
  });

  it("DW_4_4 stylesheet: tap targets are at least 44 px (checkbox rows 56 px, buttons 46 px, checklist rows 44 px)", () => {
    expect(block(".btn")).toMatch(/min-height:\s*46px/);
    expect(block(".check")).toMatch(/min-height:\s*44px/);
    expect(block(".post-pick")).toMatch(/min-height:\s*56px/);
    expect(block(".post-pick input")).toMatch(/width:\s*24px/);
  });

  it("DW_4_4 stylesheet: the posting rules use logical properties only and fix no width wider than a 343 px phone column", () => {
    expect(postRules.length).toBeGreaterThan(8);
    for (const { selector, body } of postRules) {
      expect(body, selector).not.toMatch(/(^|[;\s])(left|right|margin-left|margin-right|padding-left|padding-right)\s*:|text-align:\s*(left|right)/);
      for (const match of body.matchAll(/(?<![-\w])(?:min-|max-)?width:\s*(\d+)px/g)) expect(Number(match[1]), selector).toBeLessThanOrEqual(343);
    }
    expect(block(".post-pick .badge")).toMatch(/margin-inline-start:\s*auto/);
  });

  it("DW_4_4 stylesheet: the posting animation stops for people who ask for less motion", () => {
    expect(CSS).toMatch(/prefers-reduced-motion:\s*reduce\)\s*\{\s*\.post-target\s*\{\s*animation:\s*none/);
  });
});
