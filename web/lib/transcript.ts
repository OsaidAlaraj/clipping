// The correction screen's pure logic: what an edit does to the words (the same rules as backend/clipping/transcripts.py,
// so the screen can show it at once and the server confirms it later), the paragraphs the words are shown in, and time text.

export type Word = { text: string; start: number; end: number; confidence: number | null; speaker: string | null };
export type Speakers = Record<string, string>;
export type Doc = { words: Word[]; speakers: Speakers };

export type Edit =
  | { op: "word"; index: number; text: string }
  | { op: "replace"; find: string; replace: string; match_case?: boolean }
  | { op: "speaker"; speaker: string; name: string }
  | { op: "revert"; to_version: number };

export type QueueItem = { word_index: number; text: string; confidence: number | null; reason: "key_term" | "low_confidence"; term?: string; span?: number };

export type TranscriptData = {
  id: string; version: number; provider: string; dialect: string; words: Word[]; speakers: Speakers;
  source_id: string; campaign_id: string; campaign_name: string; language: string; media_url: string; key_terms: string[];
};

export const BULK_WORDS = 100;
export const MAX_TEXT = 200;
export const MAX_SPEAKER_NAME = 60;
export const LOW_CONFIDENCE = 0.8;

const ARABIC_FOLD: Record<string, string> = { "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي" };
const DROP = /[\p{P}\p{S}\p{Mn}\p{Cf}\p{Cc}\sـ]/gu;
const CONTROL = /[\x00-\x1f\x7f]/g;

/** The comparable form of a word: no case, punctuation, spaces or Arabic vowel marks (alef and ya variants match when finding). */
export function normalise(text: string, foldArabic = false, keepCase = false): string {
  let out = text.normalize("NFKC");
  if (!keepCase) out = out.toLowerCase();
  if (foldArabic) out = out.replace(/[أإآٱى]/g, (c) => ARABIC_FOLD[c]);
  return out.replace(DROP, "");
}

/** What the owner typed, as the server will read it: control characters become spaces and spaces collapse. */
export function typed(value: string): string {
  return value.replace(CONTROL, " ").split(/\s+/).filter(Boolean).join(" ");
}

const isEdge = (c: string) => /[\p{P}\p{S}]/u.test(c);
function edges(text: string): [string, string] {
  const chars = [...text];
  let start = 0;
  while (start < chars.length && isEdge(chars[start])) start++;
  let end = chars.length;
  while (end > start && isEdge(chars[end - 1])) end--;
  return [chars.slice(0, start).join(""), chars.slice(end).join("")];
}

function spread(tokens: string[], start: number, end: number, speaker: string | null): Word[] {
  const step = (end - start) / tokens.length;
  const round = (n: number) => Math.round(n * 1000) / 1000;
  return tokens.map((text, n) => ({ text, start: round(start + n * step), end: round(start + (n + 1) * step), confidence: 1, speaker }));
}

/** Speaker names for every speaker in the words, in order of first appearance; a blank name becomes "Speaker N". */
export function speakerNames(words: Word[], given: Speakers = {}): Speakers {
  const ids = [...new Set(words.filter((w) => w.speaker !== null).map((w) => w.speaker as string))];
  return Object.fromEntries(ids.map((id, n) => [id, (given[id] ?? "").trim().slice(0, MAX_SPEAKER_NAME) || `Speaker ${n + 1}`]));
}

/** Spans [from, to) of words matching a phrase, ignoring case, punctuation and Arabic marks. */
export function findSpans(words: Word[], find: string, matchCase = false): [number, number][] {
  const needle = typed(find).split(" ").filter(Boolean).map((t) => normalise(t, true, matchCase));
  if (needle.length === 0 || needle.some((t) => t === "")) return [];
  const keys = words.map((w) => normalise(w.text, true, matchCase));
  const spans: [number, number][] = [];
  for (let i = 0; i <= words.length - needle.length; ) {
    if (needle.every((t, n) => keys[i + n] === t)) { spans.push([i, i + needle.length]); i += needle.length; } else i++;
  }
  return spans;
}

/** How many words a replace would touch (the server asks for a confirmation above BULK_WORDS). */
export const countTouched = (words: Word[], find: string, matchCase = false) => findSpans(words, find, matchCase).reduce((n, [a, b]) => n + b - a, 0);

export type Applied = { doc: Doc; map: (number | null)[]; ok: boolean };

/** Apply one edit to a copy of the document. `map` sends each old word position to its new one (null: changed or removed).
 *  `ok` is false when the edit does not fit (for example the word is gone); the document is then unchanged. */
export function applyEdit(doc: Doc, edit: Edit): Applied {
  const same = (): Applied => ({ doc, map: doc.words.map((_, i) => i), ok: false });
  const words = doc.words;
  if (edit.op === "speaker") {
    if (!words.some((w) => w.speaker === edit.speaker)) return same();
    const speakers = speakerNames(words, { ...doc.speakers, [edit.speaker]: typed(edit.name).slice(0, MAX_SPEAKER_NAME) });
    return { doc: { words, speakers }, map: words.map((_, i) => i), ok: true };
  }
  if (edit.op === "word") {
    const word = words[edit.index];
    if (!word) return same();
    const tokens = typed(edit.text).split(" ").filter(Boolean);
    const next = words.slice();
    if (tokens.length === 1 && tokens[0] === word.text) {
      next[edit.index] = { ...word, confidence: 1 };
      return { doc: { words: next, speakers: doc.speakers }, map: words.map((_, i) => (i === edit.index ? null : i)), ok: true };
    }
    const added = tokens.length ? spread(tokens, word.start, word.end, word.speaker) : [];
    next.splice(edit.index, 1, ...added);
    const speakers = speakerNames(next, doc.speakers);
    return { doc: { words: next, speakers }, map: words.map((_, i) => (i < edit.index ? i : i === edit.index ? null : i + added.length - 1)), ok: true };
  }
  if (edit.op === "replace") {
    const spans = findSpans(words, edit.find, edit.match_case === true);
    const map: (number | null)[] = [];
    const next: Word[] = [];
    let at = 0;
    for (const [a, b] of spans) {
      for (; at < a; at++) { map[at] = next.length; next.push(words[at]); }
      const tokens = typed(edit.replace).split(" ").filter(Boolean);
      if (tokens.length) { tokens[0] = edges(words[a].text)[0] + tokens[0]; tokens[tokens.length - 1] += edges(words[b - 1].text)[1]; }
      const added = tokens.length === b - a
        ? tokens.map((text, n) => ({ ...words[a + n], text, confidence: 1 }))
        : tokens.length ? spread(tokens, words[a].start, words[b - 1].end, words[a].speaker) : [];
      for (let k = a; k < b; k++) map[k] = null;
      next.push(...added);
      at = b;
    }
    for (; at < words.length; at++) { map[at] = next.length; next.push(words[at]); }
    return { doc: { words: next, speakers: speakerNames(next, doc.speakers) }, map, ok: spans.length > 0 };
  }
  return same();   // a revert is decided by the server's history, not worked out here
}

// ------------------------------------------------------------------ paragraphs

export type Para = { start: number; end: number; speaker: string | null };
const SENTENCE_END = ".?!؟";

/** Group words into the paragraphs shown on screen: a new one at a speaker change, a pause over a second, a sentence end
 *  once it is long enough, or at 60 words. */
export function paragraphs(words: Word[]): Para[] {
  const out: Para[] = [];
  words.forEach((word, i) => {
    const last = out[out.length - 1];
    const before = words[i - 1];
    const longEnough = last && i - last.start >= 12 && SENTENCE_END.includes(before?.text.slice(-1) ?? "");
    if (last && before.speaker === word.speaker && word.start - before.end <= 1 && !longEnough && i - last.start < 60) last.end = i + 1;
    else out.push({ start: i, end: i + 1, speaker: word.speaker });
  });
  return out;
}

/** m:ss, or h:mm:ss for long files. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** The index of the word being spoken at time t (the last word that started at or before it), or -1. */
export function wordAt(words: Word[], t: number): number {
  let lo = 0, hi = words.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found !== -1 && t <= words[found].end + 0.3 ? found : -1;
}

/** WER as a percent for display, e.g. 0.042 -> "4.2%". */
export const percent = (wer: number) => `${(Math.round(wer * 1000) / 10).toFixed(1)}%`;

/** Move queue rows to their words' new positions; a row whose words were changed or removed is dropped. */
export function remapQueue(queue: QueueItem[], map: (number | null)[]): QueueItem[] {
  return queue.flatMap((item) => {
    const span = item.span ?? 1;
    const moved = Array.from({ length: span }, (_, k) => map[item.word_index + k]);
    return moved.some((n) => n === null || n === undefined) ? [] : [{ ...item, word_index: moved[0] as number }];
  });
}

const PROVIDERS: Record<string, string> = {
  deepgram: "Deepgram Nova-3", deepgram_basic: "Deepgram basic", faster_whisper: "Whisper on this laptop", fake: "Test provider",
};
export const providerLabel = (provider: string) => PROVIDERS[provider] ?? provider.replaceAll("_", " ");

/** "ar-SA" -> "Arabic (Saudi Arabia)"; anything the browser cannot name stays as the code. */
export function dialectLabel(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}
