"use client";

import Link from "next/link";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, NetworkError } from "@/lib/api";
import { useLang } from "@/lib/i18n";
import {
  applyEdit, BULK_WORDS, clock, countTouched, dialectLabel, Doc, Edit, findSpans, LOW_CONFIDENCE, MAX_TEXT, paragraphs, providerLabel,
  QueueItem, remapQueue, TranscriptData, typed, Word, wordAt,
} from "@/lib/transcript";

// The correction screen. Edits show on screen at once and are saved in the background, one at a time, each on top of the
// version the last one made. A refusal rolls the screen back and says why; losing the connection keeps the edits and retries;
// a transcript changed somewhere else is merged where the words still match. Undo cancels an edit that has not been sent, or
// saves a "go back to the version before" on top of the one that was.

type Notice = { text: string; kind: "info" | "error"; undo: boolean; key: number };
type Snap = { doc: Doc; queue: QueueItem[] | null };
type Entry = { edit: Edit; was?: string; confirm: boolean; before: Snap; revertOf?: Entry; baseUsed?: number; sent: boolean };

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : "No connection.");
const dismissKey = (item: QueueItem) => `${item.text}|${item.term ?? ""}`;

function useTranscript(sourceId: string) {
  const [data, setData] = useState<TranscriptData | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [doc, setDocState] = useState<Doc>({ words: [], speakers: {} });
  const [queue, setQueueState] = useState<QueueItem[] | null>(null);
  const [pending, setPending] = useState(0);
  const [offline, setOffline] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [keyTerms, setKeyTerms] = useState<string[]>([]);
  const eng = useRef({
    doc: { words: [], speakers: {} } as Doc, queue: null as QueueItem[] | null, version: 0, history: [] as Entry[], pending: [] as Entry[],
    running: false, rebases: 0, dismissed: new Set<string>(), timer: 0 as unknown as ReturnType<typeof setTimeout>, notices: 0, campaignId: "",
  });

  const setDoc = useCallback((next: Doc) => { eng.current.doc = next; setDocState(next); }, []);
  const setQueue = useCallback((next: QueueItem[] | null) => { eng.current.queue = next; setQueueState(next); }, []);
  const notify = useCallback((text: string, kind: Notice["kind"] = "info", undo = false) => {
    setNotice({ text, kind, undo, key: ++eng.current.notices });
  }, []);
  const syncCounts = useCallback(() => {
    setPending(eng.current.pending.length);
    setCanUndo(eng.current.history.length > 0);
  }, []);

  const refreshQueue = useCallback(async () => {
    try {
      const rows = await api<QueueItem[]>(`/api/transcripts/${sourceId}/review-queue`);
      const s = eng.current;
      if (s.pending.length || s.running) return;   // a save is on its way: the list is refreshed when it finishes
      setQueue(rows.filter((r) => !s.dismissed.has(dismissKey(r))));
    } catch {
      if (eng.current.queue === null) setQueue([]);
    }
  }, [sourceId, setQueue]);

  const load = useCallback(async () => {
    setStatus("loading");
    try {
      const d = await api<TranscriptData>(`/api/transcripts/${sourceId}`);
      Object.assign(eng.current, { version: d.version, campaignId: d.campaign_id, history: [], pending: [], rebases: 0 });
      setData(d);
      setDoc({ words: d.words, speakers: d.speakers });
      setKeyTerms(d.key_terms ?? []);
      setStatus("ready");
      syncCounts();
      void refreshQueue();
    } catch (err) {
      setLoadError(errorText(err));
      setStatus(err instanceof ApiError && err.status === 404 ? "missing" : "error");
    }
  }, [sourceId, setDoc, syncCounts, refreshQueue]);
  useEffect(() => { void load(); }, [load]);

  // ---- the save queue
  const rollback = useCallback((entry: Entry, err: unknown) => {
    const s = eng.current;
    const dropped = s.pending.splice(Math.max(0, s.pending.indexOf(entry)));
    s.history = s.history.filter((h) => !dropped.includes(h));
    setDoc(entry.before.doc);
    setQueue(entry.before.queue);
    syncCounts();
    notify(`${errorText(err)} That change was undone.`, "error");
  }, [setDoc, setQueue, syncCounts, notify]);

  /** Another device saved first: take its version, put our unsent edits on top where their words still match. */
  const rebase = useCallback(async (): Promise<boolean> => {
    const s = eng.current;
    if (++s.rebases > 3) { rollback(s.pending[0], new ApiError("The transcript keeps changing somewhere else.", 409)); return false; }
    let fresh: TranscriptData;
    try {
      fresh = await api<TranscriptData>(`/api/transcripts/${sourceId}`);
    } catch (err) {
      if (err instanceof NetworkError) { setOffline(true); return false; }
      rollback(s.pending[0], err);
      return false;
    }
    let next: Doc = { words: fresh.words, speakers: fresh.speakers };
    let lost = 0;
    const kept: Entry[] = [];
    for (const entry of s.pending) {
      const edit = entry.edit;
      const fits = !entry.revertOf && !(edit.op === "word" && next.words[edit.index]?.text !== entry.was);
      const applied = fits ? applyEdit(next, edit) : null;
      if (!applied?.ok) { lost += 1; continue; }
      kept.push({ ...entry, before: { doc: next, queue: s.queue }, sent: false });
      next = applied.doc;
    }
    Object.assign(s, { pending: kept, history: [], version: fresh.version });
    setDoc(next);
    setQueue(null);
    syncCounts();
    void refreshQueue();
    notify(lost ? `Updated with changes from another device. ${lost} of your edits could not be merged because that part changed.`
      : "Updated with changes from another device. Your edits were kept.", lost ? "error" : "info");
    return true;
  }, [sourceId, rollback, setDoc, setQueue, syncCounts, refreshQueue, notify]);

  const pump = useCallback(async () => {
    const s = eng.current;
    if (s.running) return;
    s.running = true;
    setOffline(false);
    try {
      while (s.pending.length) {
        const entry = s.pending[0];
        entry.sent = true;
        try {
          const edit = entry.revertOf ? { op: "revert", to_version: entry.revertOf.baseUsed } : entry.edit;
          const res = await api<{ version: number }>(`/api/transcripts/${sourceId}`, {
            method: "PUT", body: { base_version: s.version, edits: [edit], confirm_bulk: entry.confirm },
          });
          entry.baseUsed = s.version;
          s.version = res.version;
          s.rebases = 0;
          s.pending.shift();
          syncCounts();
        } catch (err) {
          if (err instanceof NetworkError) {
            entry.sent = false;
            setOffline(true);
            clearTimeout(s.timer);
            s.timer = setTimeout(() => void pump(), 5000);
            return;
          }
          if (err instanceof ApiError && err.code === "stale") { entry.sent = false; if (!(await rebase())) return; continue; }
          rollback(entry, err);
        }
      }
    } finally {
      s.running = false;   // offline: the next edit, the timer or the browser coming back online tries again
    }
    if (!s.pending.length) void refreshQueue();
  }, [sourceId, rebase, rollback, syncCounts, refreshQueue]);

  useEffect(() => {
    const retry = () => { clearTimeout(eng.current.timer); if (eng.current.pending.length) void pump(); };
    const warn = (ev: BeforeUnloadEvent) => { if (eng.current.pending.length) { ev.preventDefault(); ev.returnValue = ""; } };
    window.addEventListener("online", retry);
    window.addEventListener("beforeunload", warn);
    return () => { window.removeEventListener("online", retry); window.removeEventListener("beforeunload", warn); clearTimeout(eng.current.timer); };
  }, [pump]);

  // ---- what the screen calls
  /** Apply the edit on screen now and queue it for saving. Returns false when it does not fit the words as they are. */
  const commit = useCallback((edit: Edit, label: string, confirm = false): boolean => {
    const s = eng.current;
    const applied = applyEdit(s.doc, edit);
    if (!applied.ok) { notify("Nothing was changed: that part of the transcript is different now.", "error"); return false; }
    const entry: Entry = {
      edit, confirm, sent: false, before: { doc: s.doc, queue: s.queue },
      was: edit.op === "word" ? s.doc.words[edit.index]?.text : undefined,
    };
    setDoc(applied.doc);
    if (s.queue) setQueue(remapQueue(s.queue, applied.map));
    s.history.push(entry);
    s.pending.push(entry);
    syncCounts();
    notify(label, "info", true);
    void pump();
    return true;
  }, [setDoc, setQueue, syncCounts, notify, pump]);

  const undo = useCallback(() => {
    const s = eng.current;
    const entry = s.history.pop();
    if (!entry) return;
    const now: Snap = { doc: s.doc, queue: s.queue };
    setDoc(entry.before.doc);
    setQueue(entry.before.queue);
    const at = s.pending.indexOf(entry);
    if (at !== -1 && !entry.sent) s.pending.splice(at, 1);   // never sent: just forget it
    else s.pending.push({ edit: { op: "revert", to_version: 0 }, revertOf: entry, confirm: false, before: now, sent: false });
    syncCounts();
    notify("Undone.");
    void pump();
  }, [setDoc, setQueue, syncCounts, notify, pump]);

  const keepItem = useCallback((item: QueueItem) => {
    eng.current.dismissed.add(dismissKey(item));
    setQueue((eng.current.queue ?? []).filter((q) => q !== item));
  }, [setQueue]);

  const addKeyTerm = useCallback(async (raw: string) => {
    const term = typed(raw).slice(0, 100);
    if (!term || keyTerms.some((k) => k.toLowerCase() === term.toLowerCase())) return;
    setKeyTerms((k) => [...k, term]);
    notify(`Added “${term}” as a key term for this campaign.`);
    try {
      const res = await api<{ key_terms: string[] }>(`/api/campaigns/${eng.current.campaignId}/key-terms`, { body: { term } });
      setKeyTerms(res.key_terms);
      void refreshQueue();
    } catch (err) {
      setKeyTerms((k) => k.filter((x) => x !== term));
      notify(`${errorText(err)} The key term was not added.`, "error");
    }
  }, [keyTerms, notify, refreshQueue]);

  return {
    data, status, loadError, doc, queue, pending, offline, canUndo, notice, keyTerms, commit, undo, keepItem, addKeyTerm, load,
    dismissNotice: useCallback(() => setNotice(null), []), notify,
  };
}

// ------------------------------------------------------------------ small pieces

const Skel = ({ w = "100%", h = 14 }: { w?: string | number; h?: number }) => <i className="sk" style={{ width: w, height: h }} />;

export function TranscriptSkeleton() {
  return (
    <div className="page tx" aria-busy="true" aria-label="Loading the transcript">
      <div className="page-head"><div className="stack" style={{ gap: 6 }}><h1>Fix transcript</h1><Skel w={180} /></div></div>
      <div className="tx-player"><div className="card"><span className="tx-play sk" /><Skel w={64} /><Skel h={8} /></div></div>
      <div className="tx-grid" data-tab="fix">
        <div className="card tx-fix stack">{[0, 1, 2].map((n) => <div key={n} className="stack" style={{ gap: 8 }}><Skel /><Skel w="70%" /><Skel h={44} /></div>)}</div>
        <div className="card tx-reader stack">{[0, 1, 2, 3].map((n) => <div key={n} className="stack" style={{ gap: 8 }}><Skel w={90} h={12} /><Skel /><Skel /><Skel w="60%" /></div>)}</div>
      </div>
    </div>
  );
}

const Icon = {
  play: <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>,
  pause: <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>,
};

function toggleAudio(a: HTMLAudioElement | null, onFail?: () => void) {
  if (!a) return;
  if (a.paused) a.play()?.catch?.(() => onFail?.());
  else a.pause();
}

function Player({ audioRef, src, fallbackDuration, onTime }: { audioRef: React.RefObject<HTMLAudioElement | null>; src: string | null; fallbackDuration: number; onTime: (t: number) => void }) {
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const tick = () => { setTime(a.currentTime); onTime(a.currentTime); };
    const meta = () => setDuration(Number.isFinite(a.duration) ? a.duration : 0);
    const sync = () => setPlaying(!a.paused);
    const events: [string, () => void][] = [["timeupdate", tick], ["seeked", tick], ["loadedmetadata", meta], ["play", sync], ["pause", sync], ["ended", sync]];
    events.forEach(([n, f]) => a.addEventListener(n, f));
    return () => events.forEach(([n, f]) => a.removeEventListener(n, f));
  }, [audioRef, onTime]);
  const total = duration || fallbackDuration;
  const toggle = () => toggleAudio(audioRef.current, () => setFailed(true));
  return (
    <div className="tx-player">
      <div className="card">
        <audio ref={audioRef} src={src ?? undefined} preload="metadata" onError={() => setFailed(true)} />
        <button className="tx-play" onClick={toggle} disabled={!src} aria-label={playing ? "Pause" : "Play"}>{playing ? Icon.pause : Icon.play}</button>
        <span className="tx-clock">{clock(time)} / {clock(total)}</span>
        <input className="tx-seek" type="range" min={0} max={Math.max(total, 1)} step={0.1} value={Math.min(time, Math.max(total, 1))} disabled={!src} aria-label="Position"
          onChange={(ev) => { const a = audioRef.current; if (a) { a.currentTime = Number(ev.target.value); setTime(a.currentTime); } }} />
      </div>
      {failed && <p className="small muted" role="status" style={{ paddingTop: 6 }}>The audio could not be played. You can still fix the text.</p>}
    </div>
  );
}

function WordInput({ initial, onCommit, onCancel }: { initial: string; onCommit: (text: string) => void; onCancel: () => void }) {
  const done = useRef(false);
  const finish = (fn: () => void) => { if (!done.current) { done.current = true; fn(); } };
  return (
    <input className="tx-edit" aria-label="Edit word" defaultValue={initial} dir="auto" maxLength={MAX_TEXT} size={Math.max(4, initial.length + 1)} autoFocus
      onFocus={(ev) => ev.currentTarget.select()}
      onKeyDown={(ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); finish(() => onCommit(ev.currentTarget.value)); }
        else if (ev.key === "Escape") { ev.preventDefault(); finish(onCancel); }
      }}
      onBlur={(ev) => finish(() => onCommit(ev.currentTarget.value))} />
  );
}

type PProps = {
  words: Word[]; start: number; end: number; who: string | null; selected: number; editing: number; lang: string;
  onPick: (i: number) => void; onEdit: (i: number) => void; onCommitWord: (i: number, text: string) => void; onCancelEdit: () => void; onPlayAt: (t: number) => void;
};
const wordAtTarget = (target: EventTarget) => { const i = (target as HTMLElement).closest?.("[data-w]")?.getAttribute("data-w"); return i == null ? null : Number(i); };

const Paragraph = memo(function Paragraph(p: PProps) {
  const first = p.words[p.start];
  const items: React.ReactNode[] = [];
  for (let i = p.start; i < p.end; i++) {
    const w = p.words[i];
    items.push(i === p.editing
      ? <WordInput key={i} initial={w.text} onCommit={(text) => p.onCommitWord(i, text)} onCancel={p.onCancelEdit} />
      : <button key={i} id={`w-${i}`} type="button" tabIndex={-1} className="tx-w" data-w={i} aria-pressed={i === p.selected}
          data-low={w.confidence !== null && w.confidence < LOW_CONFIDENCE ? "" : undefined}>{w.text}</button>);
    items.push(" ");
  }
  return (
    <div className="tx-para">
      <div className="tx-meta">
        {p.who && <span className="tx-who" dir="auto">{p.who}</span>}
        <button type="button" className="tx-stamp" onClick={() => p.onPlayAt(first.start)} aria-label={`Play from ${clock(first.start)}`}>{clock(first.start)}</button>
      </div>
      <p className="tx-text" dir="auto" lang={p.lang}
        onClick={(ev) => { const i = wordAtTarget(ev.target); if (i !== null) p.onPick(i); }}
        onDoubleClick={(ev) => { const i = wordAtTarget(ev.target); if (i !== null) p.onEdit(i); }}>{items}</p>
    </div>
  );
}, (a, b) => {
  if (a.start !== b.start || a.end !== b.end || a.who !== b.who || a.selected !== b.selected || a.editing !== b.editing || a.lang !== b.lang) return false;
  if (a.words === b.words) return true;
  for (let i = a.start; i < a.end; i++) if (a.words[i] !== b.words[i]) return false;
  return true;
});

function FixItem({ item, words, onPlay, onFix, onKeep }: { item: QueueItem; words: Word[]; onPlay: () => void; onFix: (value: string) => void; onKeep: () => void }) {
  const span = item.span ?? 1;
  const [value, setValue] = useState(item.reason === "key_term" && item.term ? item.term : item.text);
  const around = (from: number, to: number) => words.slice(Math.max(0, from), to).map((w) => w.text).join(" ");
  const places = useMemo(() => (item.reason === "key_term" ? Math.max(1, Math.round(countTouched(words, item.text) / span)) : 1), [words, item, span]);
  const ready = typed(value) !== "";
  const confidence = item.confidence === null ? "" : ` (${Math.round(item.confidence * 100)}%)`;
  return (
    <li className="fix" data-testid="fix-item">
      <p className="fix-ctx" dir="auto"><bdi>{around(item.word_index - 4, item.word_index)}</bdi> <b><bdi>{item.text}</bdi></b> <bdi>{around(item.word_index + span, item.word_index + span + 4)}</bdi></p>
      <p className="small muted">{item.reason === "key_term" ? <>Close to your key term “<bdi>{item.term}</bdi>”</> : `Not sure this was heard right${confidence}`}</p>
      <div className="fix-row">
        <button type="button" className="icon-btn" onClick={onPlay} aria-label="Play from here">{Icon.play}</button>
        <input className="input" data-fix-input aria-label={`Correct “${item.text}”`} dir="auto" value={value} maxLength={MAX_TEXT} onChange={(ev) => setValue(ev.target.value)}
          onKeyDown={(ev) => { if (ev.key === "Enter" && ready) { ev.preventDefault(); onFix(value); } }} />
      </div>
      <div className="fix-row">
        <button type="button" className="btn primary grow" disabled={!ready} onClick={() => onFix(value)}>{places > 1 ? `Fix all ${places}` : "Fix"}</button>
        <button type="button" className="btn grow" onClick={onKeep}>Looks right</button>
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ the screen

export default function Transcript({ sourceId }: { sourceId: string }) {
  const { t } = useLang();
  const tx = useTranscript(sourceId);
  const { data, doc, queue, commit, undo, notify } = tx;
  const words = doc.words;
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [tab, setTab] = useState<"fix" | "read">("fix");
  const [selected, setSelected] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [tool, setTool] = useState<null | "find" | "speakers">(null);
  const [find, setFind] = useState("");
  const [withText, setWithText] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [confirm, setConfirm] = useState<null | { find: string; replace: string; matchCase: boolean; count: number }>(null);
  const latest = useRef({ words, selected, editing });
  latest.current = { words, selected, editing };

  const paras = useMemo(() => paragraphs(words), [words]);
  const names = doc.speakers;
  const lastWord = words.length ? words[words.length - 1].end : 0;

  // ---- playing
  const playFrom = useCallback((time: number) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, time);
    void a.play()?.catch?.(() => undefined);
  }, []);
  const nowEl = useRef<HTMLElement | null>(null);
  const showNow = useCallback((time: number) => {
    const i = wordAt(latest.current.words, time);
    const el = i >= 0 ? document.getElementById(`w-${i}`) : null;
    if (el === nowEl.current) return;
    nowEl.current?.removeAttribute("data-now");
    el?.setAttribute("data-now", "");
    nowEl.current = el;
  }, []);
  const pick = useCallback((i: number) => { setSelected(i); const w = latest.current.words[i]; if (w) playFrom(w.start - 0.3); }, [playFrom]);
  const startEdit = useCallback((i: number) => { setSelected(i); setEditing(i); }, []);
  const cancelEdit = useCallback(() => setEditing(null), []);
  const commitWord = useCallback((i: number, text: string) => {
    setEditing(null);
    const word = latest.current.words[i];
    if (!word || typed(text) === word.text) return;
    commit({ op: "word", index: i, text }, typed(text) ? `Changed “${word.text}” to “${typed(text)}”.` : `Removed “${word.text}”.`);
  }, [commit]);

  // ---- the fix queue
  const focusNextFix = useCallback(() => {
    setTimeout(() => document.querySelector<HTMLInputElement>("[data-fix-input]")?.focus(), 0);
  }, []);
  const requestReplace = useCallback((f: string, r: string, mc: boolean, label: string) => {
    const count = countTouched(latest.current.words, f, mc);
    if (count === 0) { notify("No matches. Nothing was changed.", "error"); return; }
    if (count > BULK_WORDS) { setConfirm({ find: f, replace: r, matchCase: mc, count }); return; }
    setSelected(null);
    commit({ op: "replace", find: f, replace: r, match_case: mc }, label);
  }, [commit, notify]);
  const fix = (item: QueueItem, value: string) => {
    if (item.reason === "key_term") requestReplace(item.text, value, false, `Changed “${item.text}” to “${typed(value)}”.`);
    else commit({ op: "word", index: item.word_index, text: value }, typed(value) === item.text ? `Marked “${item.text}” as right.` : `Changed “${item.text}” to “${typed(value)}”.`);
    focusNextFix();
  };
  const keep = (item: QueueItem) => {
    if (item.reason === "key_term") tx.keepItem(item);
    else commit({ op: "word", index: item.word_index, text: item.text }, `Marked “${item.text}” as right.`);
    focusNextFix();
  };
  const playItem = (item: QueueItem) => { setSelected(item.word_index); playFrom((words[item.word_index]?.start ?? 0) - 1.5); };

  // ---- find and replace highlights, set on the page directly so typing never re-renders the transcript
  useEffect(() => {
    if (tool !== "find" || !find.trim()) return;
    const marked: HTMLElement[] = [];
    for (const [a, b] of findSpans(words, find, matchCase)) for (let k = a; k < b; k++) {
      const el = document.getElementById(`w-${k}`);
      if (el) { el.setAttribute("data-match", ""); marked.push(el); }
    }
    return () => marked.forEach((el) => el.removeAttribute("data-match"));
  }, [tool, find, matchCase, words]);
  const matches = useMemo(() => (tool === "find" ? countTouched(words, find, matchCase) : 0), [tool, words, find, matchCase]);

  // ---- keyboard (desktop)
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const target = ev.target as HTMLElement | null;
      const typing = !!target?.closest("input, textarea, select, [contenteditable='true']");
      const mod = ev.ctrlKey || ev.metaKey;
      if (typing || ev.altKey) return;
      if (mod) { if (ev.key.toLowerCase() === "z" && !ev.shiftKey) { ev.preventDefault(); undo(); } return; }
      const onWord = !!target?.closest("[data-w]");
      const plain = target === document.body || onWord;
      const { words: list, selected: sel } = latest.current;
      const a = audioRef.current;
      if (ev.key === " " && (plain || !target?.closest("button, a, [role='tab'], summary"))) {
        ev.preventDefault();
        toggleAudio(a);
      } else if ((ev.key === "ArrowRight" || ev.key === "ArrowLeft") && plain && list.length) {
        ev.preventDefault();
        const rtl = sel !== null && getComputedStyle(document.getElementById(`w-${sel}`) ?? document.body).direction === "rtl";
        const step = (ev.key === "ArrowRight") !== rtl ? 1 : -1;
        const next = Math.min(list.length - 1, Math.max(0, (sel ?? (step > 0 ? -1 : list.length)) + step));
        setSelected(next);
        const el = document.getElementById(`w-${next}`);
        el?.focus({ preventScroll: true });
        el?.scrollIntoView?.({ block: "nearest" });
        if (a) a.currentTime = Math.max(0, list[next].start - 0.3);
      } else if ((ev.key === "Enter" || ev.key === "e" || ev.key === "F2") && plain && sel !== null) {
        ev.preventDefault();
        setEditing(sel);
      } else if (ev.key === "n" && plain) {
        const first = document.querySelector<HTMLInputElement>("[data-fix-input]");
        if (first) { ev.preventDefault(); first.focus(); first.select(); first.closest("li")?.querySelector<HTMLButtonElement>(".icon-btn")?.click(); }
      } else if (ev.key === "/" && plain) {
        ev.preventDefault();
        setTool("find");
        setTimeout(() => document.getElementById("tx-find")?.focus(), 0);
      } else if (ev.key === "Escape" && plain) {
        setSelected(null);
      }
    };
    const onKeyUp = (ev: KeyboardEvent) => { if (ev.key === " " && (ev.target as HTMLElement | null)?.closest?.("[data-w]")) ev.preventDefault(); };   // no click from the space bar on a word
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKeyUp);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("keyup", onKeyUp); };
  }, [undo]);

  // ---- notices fade on their own
  const { notice, dismissNotice } = tx;
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(dismissNotice, notice.kind === "error" ? 9000 : 6000);
    return () => clearTimeout(timer);
  }, [notice, dismissNotice]);

  if (tx.status === "loading") return <TranscriptSkeleton />;
  if (tx.status !== "ready" || !data) {
    return (
      <div className="page">
        <div className="page-head"><h1>Fix transcript</h1><Link className="btn ghost" href="/">Back</Link></div>
        <section className="card stack" role={tx.status === "error" ? "alert" : "status"}>
          <strong>{tx.status === "missing" ? "The transcript is not ready yet" : "The transcript could not be opened"}</strong>
          <p className="small muted">{tx.status === "missing" ? "It appears here once the footage has been transcribed. Check back in a few minutes." : tx.loadError}</p>
          {tx.status === "error" && <button className="btn" onClick={() => void tx.load()}>Try again</button>}
        </section>
      </div>
    );
  }

  const selWord = selected !== null ? words[selected] : undefined;
  const keyTerm = selWord ? typed(selWord.text.replace(/^[\p{P}\p{S}]+|[\p{P}\p{S}]+$/gu, "")) : "";
  const alreadyKey = keyTerm !== "" && tx.keyTerms.some((k) => k.toLowerCase() === keyTerm.toLowerCase());
  const sync = tx.offline ? "Offline. Will save when you are back." : tx.pending > 0 ? "Saving…" : "Saved";
  let previous: string | null | undefined;
  const readerParas = paras.map((p) => {
    const who = p.speaker !== null && p.speaker !== previous ? names[p.speaker] ?? null : null;
    previous = p.speaker;
    return (
      <Paragraph key={p.start} words={words} start={p.start} end={p.end} who={who} lang={data.language}
        selected={selected !== null && selected >= p.start && selected < p.end ? selected : -1}
        editing={editing !== null && editing >= p.start && editing < p.end ? editing : -1}
        onPick={pick} onEdit={startEdit} onCommitWord={commitWord} onCancelEdit={cancelEdit} onPlayAt={(time) => playFrom(time)} />
    );
  });
  const speakerIds = Object.keys(names);

  return (
    <div className="page tx">
      <div className="page-head">
        <div className="stack" style={{ gap: 2, minWidth: 0 }}>
          <h1>Fix transcript</h1>
          <span className="small muted tx-sub">{data.campaign_name} · {providerLabel(data.provider)} · {dialectLabel(data.dialect)}</span>
        </div>
        <Link className="btn ghost" href={`/campaign?id=${data.campaign_id}`}>Back</Link>
      </div>

      <Player audioRef={audioRef} src={data.media_url} fallbackDuration={lastWord} onTime={showNow} />

      <div className="row between tx-bar">
        <span className="tx-status" role="status" data-state={tx.offline ? "offline" : tx.pending > 0 ? "saving" : "saved"}>{sync}</span>
        <button className="btn" onClick={undo} disabled={!tx.canUndo}>{t("undo")}</button>
      </div>

      <div className="tx-sel" aria-live="polite">
        {selWord ? (
          <>
            <bdi className="tx-sel-word" dir="auto">{selWord.text}</bdi>
            <button className="btn" onClick={() => setEditing(selected)}>{t("edit")}</button>
            <button className="btn" disabled={!keyTerm || alreadyKey} onClick={() => void tx.addKeyTerm(keyTerm)}>{alreadyKey ? "Key term ✓" : "Key term"}</button>
          </>
        ) : <span className="small muted">Tap a word to play from it.</span>}
      </div>

      <div className="segmented tx-tabs" role="tablist" aria-label="Transcript views">
        <button role="tab" aria-selected={tab === "fix"} onClick={() => setTab("fix")}>Fix first{queue && queue.length > 0 ? ` · ${queue.length}` : ""}</button>
        <button role="tab" aria-selected={tab === "read"} onClick={() => setTab("read")}>Transcript</button>
      </div>

      <div className="tx-grid" data-tab={tab}>
        <section className="card tx-fix" aria-label="Fix first">
          <h2>Fix first</h2>
          {queue === null && <div className="stack" aria-busy="true">{[0, 1].map((n) => <div key={n} className="stack" style={{ gap: 8, paddingTop: 12 }}><Skel /><Skel w="60%" /><Skel h={44} /></div>)}</div>}
          {queue !== null && queue.length === 0 && (
            <div className="stack" style={{ paddingTop: 8 }}>
              <strong>Nothing needs fixing</strong>
              <p className="small muted">No word looks doubtful and every key term is spelled right. Read through the transcript, or find a word to change everywhere.</p>
              <button className="btn" onClick={() => { setTab("read"); setTool("find"); }}>Find and replace</button>
            </div>
          )}
          {queue !== null && queue.length > 0 && (
            <ul className="fix-list">
              {queue.map((item) => (
                <FixItem key={`${item.word_index}:${item.text}`} item={item} words={words} onPlay={() => playItem(item)} onFix={(v) => fix(item, v)} onKeep={() => keep(item)} />
              ))}
            </ul>
          )}
        </section>

        <section className="card tx-reader" aria-label="Transcript">
          <div className="row wrap tx-tools">
            <button className="btn" aria-pressed={tool === "find"} onClick={() => setTool(tool === "find" ? null : "find")}>Find and replace</button>
            <button className="btn" aria-pressed={tool === "speakers"} onClick={() => setTool(tool === "speakers" ? null : "speakers")}>Speakers</button>
          </div>
          {tool === "find" && (
            <div className="stack tx-tool" role="search" aria-label="Find and replace">
              <div className="row wrap">
                <div className="field grow"><label htmlFor="tx-find">Find</label><input id="tx-find" className="input" dir="auto" value={find} maxLength={MAX_TEXT} onChange={(ev) => setFind(ev.target.value)} /></div>
                <div className="field grow"><label htmlFor="tx-with">Replace with</label><input id="tx-with" className="input" dir="auto" value={withText} maxLength={MAX_TEXT} onChange={(ev) => setWithText(ev.target.value)} /></div>
              </div>
              <div className="row between wrap">
                <label className="check"><input type="checkbox" checked={matchCase} onChange={(ev) => setMatchCase(ev.target.checked)} /><span>Match case</span></label>
                <span className="small muted" role="status">{!find.trim() ? "Type a word or phrase." : matches === 0 ? "No matches." : `${matches} ${matches === 1 ? "word" : "words"} match.`}</span>
              </div>
              <button className="btn primary block" disabled={matches === 0}
                onClick={() => requestReplace(find, withText, matchCase, `Replaced “${find.trim()}” with “${typed(withText)}”.`)}>Replace all</button>
            </div>
          )}
          {tool === "speakers" && (
            <div className="stack tx-tool" aria-label="Speakers">
              {speakerIds.length === 0 && <p className="small muted">No speakers were detected in this transcript.</p>}
              {speakerIds.map((id, n) => (
                <div className="field" key={`${id}:${names[id]}`}>
                  <label htmlFor={`sp-${id}`}>Speaker {n + 1}</label>
                  <input id={`sp-${id}`} className="input" dir="auto" defaultValue={names[id]} maxLength={60}
                    onKeyDown={(ev) => { if (ev.key === "Enter") ev.currentTarget.blur(); }}
                    onBlur={(ev) => { if (ev.target.value.trim() !== names[id]) commit({ op: "speaker", speaker: id, name: ev.target.value }, `Renamed “${names[id]}” to “${typed(ev.target.value) || "default"}”.`); }} />
                </div>
              ))}
            </div>
          )}
          <p className="small muted tx-keys">Space play · ← → move · Enter edit · N next fix · / find · Ctrl+Z undo</p>
          <div className="tx-words" lang={data.language}>{readerParas}</div>
        </section>
      </div>

      {tx.notice && (
        <div className="toast" role={tx.notice.kind === "error" ? "alert" : "status"} key={tx.notice.key}>
          <span>{tx.notice.text}</span>
          {tx.notice.undo && tx.canUndo ? <button className="btn" onClick={undo}>{t("undo")}</button> : <button className="btn ghost" onClick={tx.dismissNotice}>Dismiss</button>}
          <i className="timer" style={{ animationDuration: tx.notice.kind === "error" ? "9s" : "6s" }} />
        </div>
      )}

      {confirm && (
        <div className="scrim" onClick={() => setConfirm(null)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="Confirm replace" onClick={(ev) => ev.stopPropagation()} onKeyDown={(ev) => { if (ev.key === "Escape") setConfirm(null); }}>
            <h1>Change {confirm.count} words?</h1>
            <p className="small muted">“{confirm.find.trim()}” will become “{typed(confirm.replace) || "nothing"}” in {confirm.count} places. You can undo it.</p>
            <button className="btn primary block" autoFocus onClick={() => { setSelected(null); commit({ op: "replace", find: confirm.find, replace: confirm.replace, match_case: confirm.matchCase }, `Changed ${confirm.count} words.`, true); setConfirm(null); }}>Change {confirm.count} words</button>
            <button className="btn block" onClick={() => setConfirm(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
