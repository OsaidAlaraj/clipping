"use client";

// "What I understood": the spec the brief extractor read, layer by layer, for the owner to check before the campaign is created.
// Every item shows the sentence it came from. Red items need an answer; conflicts show both readings side by side.
// All look-and-feel is in `S` and the existing classes (card, badge, banner, btn, input), so a restyle touches one place.
import { useState } from "react";

import { counts, editItem, itemsOf, keyName, LAYERS, previewDecisions, resolveConflict, textToValue, valueToText, words,
  type Lang, type Spec, type SpecItem, type SpecLink } from "@/lib/briefSpec";

const S = {
  stack: { display: "grid", gap: 12 } as const,
  card: { display: "grid", gap: 10 } as const,
  row: { display: "grid", gap: 6, paddingBlockStart: 10, borderBlockStart: "1px solid var(--line)" } as const,
  head: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 } as const,
  quote: { margin: 0, paddingInlineStart: 10, borderInlineStart: "3px solid var(--line)", fontSize: "0.78rem", color: "var(--muted)" } as const,
  unclear: { background: "var(--danger-fill)", color: "var(--danger)", borderRadius: 16, padding: 12, display: "grid", gap: 8 } as const,
  soft: { background: "var(--surface-2)", borderRadius: 16, padding: 12, display: "grid", gap: 8 } as const,
  pair: { display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" } as const,
  half: { background: "var(--warn-fill)", color: "var(--warn)", borderRadius: 14, padding: 10, display: "grid", gap: 6 } as const,
  actions: { display: "flex", gap: 8, flexWrap: "wrap" } as const,
};

function Quote({ text, w }: { text: string | null; w: ReturnType<typeof words> }) {
  return text ? <blockquote dir="auto" style={S.quote} aria-label={w.fromBrief}>{text}</blockquote> : null;
}

function ValueEditor({ item, onSave, w }: { item: SpecItem; onSave: (value: any) => void; w: ReturnType<typeof words> }) {
  const isDict = item.value && typeof item.value === "object" && !Array.isArray(item.value);
  const [text, setText] = useState(valueToText(item.value));
  const [fields, setFields] = useState<Record<string, string>>(isDict ? Object.fromEntries(Object.entries(item.value).map(([k, v]) => [k, valueToText(v)])) : {});
  const save = () => {
    if (isDict) {
      const next: Record<string, any> = {};
      for (const [k, v] of Object.entries(item.value as Record<string, any>)) next[k] = textToValue(fields[k] ?? "", v);
      onSave(next);
    } else onSave(textToValue(text, item.value));
  };
  return (
    <div style={S.stack}>
      {isDict
        ? Object.keys(item.value).map((k) => (
          <label key={k} className="field small"><span>{k.replace(/_/g, " ")}</span>
            <input className="input" dir="auto" value={fields[k] ?? ""} onChange={(e) => setFields({ ...fields, [k]: e.target.value })} /></label>))
        : <input className="input" dir="auto" aria-label={keyName(item.key)} value={text} onChange={(e) => setText(e.target.value)} />}
      <div style={S.actions}><button className="btn primary" onClick={save}>{w.save}</button></div>
    </div>
  );
}

function ItemRow({ item, spec, onChange, lang }: { item: SpecItem; spec: Spec; onChange: (s: Spec) => void; lang: Lang }) {
  const w = words(lang);
  const [editing, setEditing] = useState(false);
  const label = w.keys[keyName(item.key)] ?? keyName(item.key);
  if (item.dismissed) {
    return <div style={S.row}><span className="small muted"><s>{label}</s></span>
      <div style={S.actions}><button className="btn ghost" onClick={() => onChange(editItem(spec, item.id, { dismissed: false }))}>{w.restore}</button></div></div>;
  }
  if (item.status === "unclear") return <UnclearRow item={item} label={label} spec={spec} onChange={onChange} w={w} />;
  if (item.status === "conflict") {
    const alt = (item.alternatives ?? [])[0];
    return (
      <div style={S.row}>
        <div style={S.head}><strong>{label}</strong><span className="badge review">{w.status.conflict}</span></div>
        <div style={S.pair}>
          <div style={S.half}><span className="small">{w.ruleReading}</span><strong dir="auto">{valueToText(item.value)}</strong><Quote text={item.source_quote} w={w} />
            <button className="btn" onClick={() => onChange(resolveConflict(spec, item.id, "rule"))}>{w.useThis}</button></div>
          {alt && <div style={S.half}><span className="small">{w.aiReading}</span><strong dir="auto">{valueToText(alt.value)}</strong><Quote text={alt.source_quote} w={w} />
            <button className="btn" onClick={() => onChange(resolveConflict(spec, item.id, "alt"))}>{w.useThis}</button></div>}
        </div>
      </div>
    );
  }
  return (
    <div style={S.row}>
      <div style={S.head}><span className="small muted">{label}</span><span className="badge pass">{Math.round(item.confidence * 100)}%</span></div>
      {editing
        ? <ValueEditor item={item} w={w} onSave={(value) => { onChange(editItem(spec, item.id, { value })); setEditing(false); }} />
        : <strong dir="auto">{valueToText(item.value)}</strong>}
      <Quote text={item.source_quote} w={w} />
      {!editing && <div style={S.actions}>
        <button className="btn ghost" onClick={() => setEditing(true)}>{w.edit}</button>
        <button className="btn ghost" onClick={() => onChange(editItem(spec, item.id, { dismissed: true }))}>{w.remove}</button></div>}
    </div>
  );
}

function UnclearRow({ item, label, spec, onChange, w }: { item: SpecItem; label: string; spec: Spec; onChange: (s: Spec) => void; w: ReturnType<typeof words> }) {
  const [text, setText] = useState("");
  const needed = (spec.blocking ?? []).includes(item.key);
  const answer = () => text.trim() && onChange(editItem(spec, item.id, { value: textToValue(text, item.value === "unspecified" ? "" : item.value) }));
  return (
    <div style={needed ? S.unclear : S.soft} role={needed ? "alert" : undefined}>
      <div style={S.head}><strong>{label}</strong><span className="badge blocked">{needed ? w.unclearTitle : w.status.unclear}</span></div>
      <span className="small">{w.notStated}</span>
      <input className="input" dir="auto" aria-label={`${label}: ${w.answerHere}`} placeholder={w.answerHere} value={text} onChange={(e) => setText(e.target.value)} />
      <div style={S.actions}>
        <button className="btn primary" disabled={!text.trim()} onClick={answer}>{w.save}</button>
        <button className="btn ghost" onClick={() => onChange(editItem(spec, item.id, { dismissed: true }))}>{w.notApplicable}</button>
      </div>
    </div>
  );
}

function LinkRow({ link, w, onPaste }: { link: SpecLink; w: ReturnType<typeof words>; onPaste: (url: string, text: string) => void }) {
  const [text, setText] = useState("");
  const waiting = link.fetch.status === "needs_paste";
  return (
    <div style={waiting ? S.unclear : S.row}>
      <div style={S.head}>
        <span dir="ltr" className="small" style={{ overflowWrap: "anywhere" }}>{link.text || link.url}</span>
        <span className={`badge ${waiting ? "blocked" : "info"}`}>{w.linkStatus[link.fetch.status] ?? link.fetch.status}</span>
      </div>
      <span className="small muted">{w.kinds[link.kind] ?? link.kind}</span>
      {waiting && <>
        <span className="small">{link.fetch.note}</span>
        <textarea className="input" dir="auto" style={{ minHeight: 100 }} aria-label={`${w.pasteHere} ${link.url}`} placeholder={w.pasteHere} value={text} onChange={(e) => setText(e.target.value)} />
        <div style={S.actions}><button className="btn primary" disabled={!text.trim()} onClick={() => { onPaste(link.url, text); setText(""); }}>{w.addText}</button></div>
      </>}
    </div>
  );
}

export function BriefReview({ spec, onChange, onPasteLink, onConfirm, busy = false, error = "", lang = "en", linksKept = 0 }: {
  spec: Spec; onChange: (s: Spec) => void; onPasteLink: (url: string, text: string) => void; onConfirm: () => void;
  busy?: boolean; error?: string; lang?: Lang; linksKept?: number;
}) {
  const w = words(lang);
  const c = counts(spec);
  const decisions = previewDecisions(spec, lang);
  return (
    <section style={S.stack} dir={lang === "ar" ? "rtl" : "ltr"} aria-label={w.understood}>
      <div className="card" style={S.card}>
        <h2 style={{ margin: 0 }}>{w.understood}</h2>
        <p className="small muted" style={{ margin: 0 }}>
          {c.found} {w.itemsFound}{c.unclear > 0 && <> · <strong style={{ color: "var(--danger)" }}>{c.unclear} {w.needAnswers}</strong></>}{linksKept > 0 && <> · {linksKept} {w.linksKept}</>}
        </p>
        {spec.warnings.map((m) => <div key={m} className="banner warn" role="status">{m}</div>)}
      </div>
      {decisions.length > 0 && (
        <div className="card" style={S.card}>
          <strong>{w.willHappen}</strong>
          <ul style={{ margin: 0, paddingInlineStart: 18, display: "grid", gap: 6 }}>{decisions.map((d) => <li key={d} className="small">{d}</li>)}</ul>
        </div>
      )}
      {LAYERS.map((layer) => {
        const items = itemsOf(spec, layer);
        if (!items.length) return null;
        return (
          <div key={layer} className="card" style={S.card}>
            <strong>{w.layers[layer]}</strong>
            {items.map((item) => <ItemRow key={item.id} item={item} spec={spec} onChange={onChange} lang={lang} />)}
          </div>
        );
      })}
      {spec.links.length > 0 && (
        <div className="card" style={S.card}>
          <strong>{w.links}</strong>
          {spec.links.map((l) => <LinkRow key={l.url} link={l} w={w} onPaste={onPasteLink} />)}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn primary block" disabled={busy || c.unclear > 0} onClick={onConfirm}>
        {busy ? w.reading : c.unclear > 0 ? w.confirmBlocked : w.confirm}
      </button>
    </section>
  );
}
