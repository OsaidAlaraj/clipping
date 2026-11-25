"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";

// The account's look (its template library) and posting plan (persona), shown on Profile. Edits apply on screen at once and
// save in the background; the next clip of the account uses the saved version.

export type Spec = {
  font: string; primary: string; accent: string; caption_position: "lower" | "middle"; title_card: boolean; progress_bar: boolean;
  zooms: boolean; font_size: number; cover: "band" | "outline"; caption_style?: string;
};
export type CaptionStyleInfo = { key: string; label: string; about: string };
type Version = { version: number; note: string | null };
export type Look = Spec & { template_id: string; name: string; active: boolean; version: number; versions: Version[] };
type Preset = { name: string; spec: Spec };
export type Persona = {
  posts_per_week: number;
  best_hours: { hours: number[]; basis: "results" | "typical"; tz: "utc" | "local"; posts_counted: number };
};

const FONTS = ["Arial", "Tahoma", "Segoe UI", "Verdana", "Trebuchet MS", "Georgia"];
const SAVE_DELAY_MS = 350;
const SIZE_MIN = 2.5, SIZE_MAX = 6.5;
const PERSONA_MIN_POSTS = 6;

/** Hours of the day as the owner reads them: results are kept in UTC, so they are moved to the device's own time. */
export function hourLabels(hours: number[], tz: "utc" | "local"): string[] {
  return hours.map((h) => (tz === "utc" ? new Date(Date.UTC(2026, 0, 1, h)).getHours() : h)).sort((a, b) => a - b)
    .map((h) => `${String(h).padStart(2, "0")}:00`);
}

const contrast = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.299 * r + 0.587 * g + 0.114 * b > 140 ? "#101010" : "#FFFFFF";
};

const SAMPLE = ["This", "is", "the", "one", "thing"];
/** The sample caption of a named style, drawn the way the render draws it: words per line, which one is lit, case, weight. */
function SampleCaption({ style, accent, size }: { style: string; accent: string; size: number }) {
  const auto = style === "auto";
  const key = auto ? "karaoke_clean" : style;
  const word = (w: string, lit: boolean, i: number) => <span key={i} style={lit ? { color: accent } : undefined}>{w}{" "}</span>;
  if (key === "bold_pop") return <span style={{ fontSize: size * 1.25, fontWeight: 800, textTransform: "uppercase" }}>{word("This", false, 0)}{word("is", true, 1)}</span>;
  if (key === "emphasis") return <span style={{ fontSize: size }}>{word("only", false, 0)}{word("100", true, 1)}{word("dollars", false, 2)}</span>;
  if (key === "stacked") return <span style={{ fontSize: size * 1.1 }}>{word("This", false, 0)}{word("is", false, 1)}<br />{word("the", true, 2)}{word("one", false, 3)}</span>;
  if (key === "minimal") return <span style={{ fontSize: size * 0.85, fontWeight: 500 }}>{SAMPLE.map((w, i) => word(w, false, i))}</span>;
  return <span style={{ fontSize: size }}>{SAMPLE.slice(0, 3).map((w, i) => word(w, i === 1, i))}</span>;
}

/** A small picture of the look: the label, the title card, one caption and the progress bar, drawn from the spec as it is edited. */
export function LookPreview({ spec }: { spec: Spec }) {
  const height = 200;
  const caption = (spec.font_size / 100) * height;
  const styleKey = spec.caption_style ?? "auto";
  return (
    <div role="img" aria-label="Preview of this look" dir="auto"
      style={{ width: 113, height, flex: "none", position: "relative", overflow: "hidden", borderRadius: 14, fontFamily: `"${spec.font}", sans-serif`,
        background: "linear-gradient(170deg, #6b7280 0%, #2d323c 100%)", color: spec.primary }}>
      <span style={{ position: "absolute", insetInlineStart: 5, top: 5, background: "#000", color: "#fff", fontSize: 7, fontWeight: 700, padding: "1px 3px" }}>#ad</span>
      {spec.title_card && (
        <span style={{ position: "absolute", insetInline: 12, top: "11%", textAlign: "center", background: spec.accent, color: contrast(spec.accent),
          fontSize: 9, fontWeight: 700, padding: "2px 4px", lineHeight: 1.25 }}>The hook line</span>
      )}
      <span data-testid="sample-caption" data-style={styleKey} style={{ position: "absolute", insetInline: 8, textAlign: "center", fontWeight: 700, lineHeight: 1.15,
        textShadow: "0 0 2px #000, 0 0 2px #000, 0 1px 3px #000",
        ...(spec.caption_position === "lower" ? { bottom: "16%" } : { top: "50%", transform: "translateY(-50%)" }) }}>
        {styleKey === "speaker_label" && <span style={{ display: "block", margin: "0 auto 3px", width: "fit-content", background: spec.accent, color: contrast(spec.accent), fontSize: 8, padding: "1px 5px", borderRadius: 3 }}>Sara</span>}
        <SampleCaption style={styleKey === "speaker_label" ? "karaoke_clean" : styleKey} accent={spec.accent} size={caption} />
      </span>
      {spec.progress_bar && <i style={{ position: "absolute", insetInlineStart: 0, bottom: 0, height: 3, width: "42%", background: spec.accent }} />}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="stack" aria-hidden="true" style={{ gap: 8 }}>
      <div style={{ height: 52, borderRadius: 14, background: "var(--surface-2)" }} />
      <div style={{ height: 200, borderRadius: 14, background: "var(--surface-2)" }} />
    </div>
  );
}

export function AccountLook({ accountId }: { accountId: string }) {
  const [open, setOpen] = useState(false);
  const [looks, setLooks] = useState<Look[] | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [styles, setStyles] = useState<CaptionStyleInfo[]>([]);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState("");
  const pending = useRef<Partial<Spec>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const fail = (e: unknown) => { setState("error"); setMessage(e instanceof ApiError ? e.message : "No connection. Your change was not saved."); };

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setMessage("");   // a reload after a refused save keeps the refusal on screen
    try {
      const [lib, plan] = await Promise.all([api(`/api/accounts/${accountId}/templates`), api(`/api/accounts/${accountId}/persona`)]);
      setLooks(lib.templates); setPresets(lib.presets); setStyles(lib.styles ?? []); setPersona(plan);
      setSelected((now) => (lib.templates.some((l: Look) => l.template_id === now) ? now : (lib.templates.find((l: Look) => l.active) ?? lib.templates[0])?.template_id ?? null));
    } catch (e) { setLooks(null); fail(e); }
  }, [accountId]);

  useEffect(() => { if (open && looks === null) load(); }, [open, looks, load]);

  const main = looks?.find((l) => l.active);
  const current = looks?.find((l) => l.template_id === selected) ?? main;
  const urlFor = (look: Look) => (look === main ? `/api/accounts/${accountId}/template` : `/api/templates/${look.template_id}`);
  const merge = (saved: Look) => setLooks((all) => all && all.map((l) => (l.template_id === saved.template_id ? { ...l, ...saved } : l)));

  const flush = useCallback(async () => {
    const fields = pending.current;
    pending.current = {};
    if (!current || !Object.keys(fields).length) return;
    setState("saving");
    try { merge(await api(urlFor(current), { method: "PUT", body: fields })); setState("saved"); setMessage(""); }
    catch (e) { fail(e); load(true); }   // show what the server really has
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, main, accountId, load]);

  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => { clearTimeout(timer.current); if (Object.keys(pending.current).length) flushRef.current(); }, []);

  const change = (fields: Partial<Spec>, delay = 0) => {
    if (!current) return;
    setLooks((all) => all && all.map((l) => (l.template_id === current.template_id ? { ...l, ...fields } : l)));   // on screen at once
    pending.current = { ...pending.current, ...fields };
    clearTimeout(timer.current);
    timer.current = setTimeout(() => flushRef.current(), delay);
  };

  const choose = (id: string) => { clearTimeout(timer.current); flushRef.current(); setSelected(id); };   // save the edit to the look it was made on first

  const act = async (path: string, init: { method?: string; body: unknown }, then?: (r: any) => void) => {
    setState("saving");
    try { const r = await api(path, init); then?.(r); setState("saved"); setMessage(""); } catch (e) { fail(e); }
  };

  const addPreset = (name: string) => {
    const preset = presets.find((p) => p.name === name);
    if (!preset) return;
    const taken = new Set((looks ?? []).map((l) => l.name));
    let unique = preset.name, n = 2;
    while (taken.has(unique)) unique = `${preset.name} ${n++}`;
    act(`/api/accounts/${accountId}/templates`, { body: { name: unique, ...preset.spec } }, (made: Look) => { setLooks((all) => [...(all ?? []), made]); setSelected(made.template_id); });
  };

  const activeCount = looks?.filter((l) => l.active).length ?? 0;
  const hours = persona ? hourLabels(persona.best_hours.hours, persona.best_hours.tz) : [];
  const counted = persona?.best_hours.posts_counted ?? 0;

  return (
    <div className="stack" style={{ gap: 8 }}>
      <button type="button" className="sect-toggle" aria-expanded={open} aria-controls={`look-${accountId}`} onClick={() => setOpen(!open)}>
        <span>Look and posting plan</span><span className="chev" aria-hidden="true" data-open={open} />
      </button>
      {open && (
        <div id={`look-${accountId}`} className="stack" style={{ gap: 12, minHeight: 260 }}>
          {!looks && !message && <Skeleton />}
          {!looks && message && (
            <div className="stack" style={{ gap: 6 }}>
              <span className="error" role="alert">{message}</span>
              <button type="button" className="btn" onClick={() => load()}>Try again</button>
            </div>
          )}
          {looks && current && (
            <>
              {persona && (
                <p className="small" aria-label="Posting plan">
                  Post <strong>{persona.posts_per_week} a week</strong>, best around <strong>{hours.join(", ")}</strong>.{" "}
                  <span className="muted">{persona.best_hours.basis === "results" ? `From this account's own ${counted} posts.` : `Typical hours until the account has ${PERSONA_MIN_POSTS} posts of its own (${counted} so far).`}</span>
                </p>
              )}
              <div className="row wrap" style={{ gap: 6 }} role="group" aria-label="Looks in rotation">
                {looks.map((l) => (
                  <button key={l.template_id} type="button" className={`chip-pill${l.template_id === current.template_id ? " on" : ""}`} aria-pressed={l.template_id === current.template_id}
                    onClick={() => choose(l.template_id)} style={l.active ? undefined : { opacity: 0.6 }}>{l.name}{l.active ? "" : " (off)"}</button>
                ))}
                <select className="input" aria-label="Add a look" style={{ width: "auto", minHeight: 40, padding: "4px 12px", borderRadius: 999 }} value=""
                  onChange={(e) => addPreset(e.target.value)}>
                  <option value="">Add a look…</option>
                  {presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select>
              </div>
              <p className="small muted">The account cycles through its looks so its clips never look the same, while the font and colours keep them recognisable. Changes are used by its next clip.</p>
              <div className="row wrap" style={{ gap: 16, alignItems: "flex-start" }}>
                <LookPreview spec={current} />
                <div className="stack grow" style={{ gap: 10, minWidth: 220 }}>
                  <div className="row wrap" style={{ gap: 10 }}>
                    <div className="field grow" style={{ minWidth: 130 }}><label htmlFor={`f-${accountId}`}>Font</label>
                      <select id={`f-${accountId}`} className="input" value={current.font} onChange={(e) => change({ font: e.target.value })}>
                        {(FONTS.includes(current.font) ? FONTS : [current.font, ...FONTS]).map((f) => <option key={f} value={f}>{f}</option>)}
                      </select></div>
                    <div className="field grow" style={{ minWidth: 130 }}><label htmlFor={`p-${accountId}`}>Captions sit</label>
                      <select id={`p-${accountId}`} className="input" value={current.caption_position} onChange={(e) => change({ caption_position: e.target.value as Spec["caption_position"] })}>
                        <option value="lower">Lower third</option><option value="middle">Middle</option>
                      </select></div>
                  </div>
                  {styles.length > 0 && (
                    <div className="stack" style={{ gap: 6 }} role="radiogroup" aria-label="Caption style">
                      <span className="small muted">Caption style</span>
                      <div className="row wrap" style={{ gap: 6 }}>
                        {styles.map((st) => (
                          <button key={st.key} type="button" role="radio" aria-checked={(current.caption_style ?? "auto") === st.key} title={st.about}
                            className={`chip-pill${(current.caption_style ?? "auto") === st.key ? " on" : ""}`} onClick={() => change({ caption_style: st.key })}>{st.label}</button>
                        ))}
                      </div>
                      <span className="small muted">{styles.find((st) => st.key === (current.caption_style ?? "auto"))?.about}</span>
                      {looks.length > 1 && (
                        <button type="button" className="btn" style={{ minHeight: 44, alignSelf: "flex-start" }}
                          onClick={async () => {
                            const key = current.caption_style ?? "auto";
                            setState("saving");
                            try {
                              for (const l of looks) if ((l.caption_style ?? "auto") !== key) merge(await api(urlFor(l), { method: "PUT", body: { caption_style: key } }));
                              setState("saved");
                            } catch (e) { fail(e); load(true); }
                          }}>Use this style for all of this account's looks</button>
                      )}
                    </div>
                  )}
                  <div className="row wrap" style={{ gap: 10 }}>
                    <div className="field"><label htmlFor={`c1-${accountId}`}>Text colour</label>
                      <input id={`c1-${accountId}`} type="color" className="input" style={{ width: 74, padding: 4 }} value={current.primary} onChange={(e) => change({ primary: e.target.value.toUpperCase() }, SAVE_DELAY_MS)} /></div>
                    <div className="field"><label htmlFor={`c2-${accountId}`}>Accent colour</label>
                      <input id={`c2-${accountId}`} type="color" className="input" style={{ width: 74, padding: 4 }} value={current.accent} onChange={(e) => change({ accent: e.target.value.toUpperCase() }, SAVE_DELAY_MS)} /></div>
                    <div className="field grow" style={{ minWidth: 130 }}><label htmlFor={`s-${accountId}`}>Caption size</label>
                      <input id={`s-${accountId}`} type="range" min={SIZE_MIN} max={SIZE_MAX} step={0.1} value={current.font_size} style={{ minHeight: 46 }}
                        onChange={(e) => change({ font_size: Number(e.target.value) }, SAVE_DELAY_MS)} /></div>
                    <div className="field grow" style={{ minWidth: 130 }}><label htmlFor={`v-${accountId}`}>Cover text</label>
                      <select id={`v-${accountId}`} className="input" value={current.cover} onChange={(e) => change({ cover: e.target.value as Spec["cover"] })}>
                        <option value="band">On a colour band</option><option value="outline">Outlined</option>
                      </select></div>
                  </div>
                  <div className="row wrap" style={{ gap: 4 }}>
                    {([["title_card", "Title card"], ["progress_bar", "Progress bar"], ["zooms", "Emphasis zooms"]] as const).map(([key, label]) => (
                      <label key={key} className="row" style={{ minHeight: 44, gap: 8, paddingInlineEnd: 12 }}>
                        <input type="checkbox" checked={current[key]} onChange={(e) => change({ [key]: e.target.checked })} />{label}
                      </label>
                    ))}
                  </div>
                  <div className="row wrap" style={{ gap: 8 }}>
                    <label className="row" style={{ minHeight: 44, gap: 8, paddingInlineEnd: 12 }}>
                      <input type="checkbox" checked={current.active} disabled={current.active && activeCount < 2}
                        onChange={(e) => act(`/api/templates/${current.template_id}`, { method: "PUT", body: { active: e.target.checked } }, merge)} />Use in rotation
                    </label>
                    {current.version > 1 && (
                      <button type="button" className="btn" style={{ minHeight: 44 }} onClick={() => act(`/api/templates/${current.template_id}/revert`, { body: {} }, merge)}>
                        Go back to the previous look
                      </button>
                    )}
                    <span className="small muted" role="status" aria-live="polite">{state === "saving" ? "Saving…" : state === "saved" ? `Saved · version ${current.version}` : ""}</span>
                  </div>
                  {state === "error" && message && <span className="error" role="alert">{message}</span>}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
