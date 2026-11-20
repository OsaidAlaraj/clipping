"""Measure dead-air and filler tightening on real segments (run with the project venv; scratch data under .data/polish-work).
Prints one JSON line per language. Checks: how much is removed, that no cut touches a word, how quiet the cut edges are, audio
clicks at the joins, and that captions stay in sync (the tightened clip is transcribed again and compared with the caption times)."""
import json
import os
import sys
import wave

import numpy as np

ROOT = os.environ.get("WORKTREE", r"L:\Projects\Clipping\.claude\worktrees\agent-a2d95be84a7183ce9")
D = os.environ.get("POLISH_DATA", r"L:\Projects\Clipping\.data\polish-work")
sys.path.insert(0, ROOT + r"\backend")
os.environ["CLIPPING_DATA_DIR"] = r"L:\Projects\Clipping\.data"
from clipping import ffmpeg, polish  # noqa: E402

OFFSET = {"en": 600.0, "ar": 900.0}
SEG = json.load(open(rf"{D}\segments.json"))


def words_of(lang: str, variant: str) -> list[dict]:
    name = f"{lang}_words{'_fillerprompt' if variant == 'filler' else ''}.json"
    return [{**w, "start": w["start"] + OFFSET[lang], "end": w["end"] + OFFSET[lang]} for w in json.load(open(rf"{D}\{name}", encoding="utf-8"))["words"]]


def source(lang: str) -> str:
    return rf"L:\Projects\Clipping\.data\samples\{'en_cordkillers_ep377' if lang == 'en' else 'ar_kau_physiology_l38'}.mp4"


def fps_of(lang: str) -> float:
    from pathlib import Path
    return ffmpeg.probe(Path(source(lang)))["fps"] or 30.0


def audio_of(path: str, start: float = 0.0, dur: float = 1e6) -> np.ndarray:
    import subprocess
    out = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-ss", str(start), "-t", str(dur), "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768


def tighten_stats(lang: str, variant: str) -> dict:
    words = words_of(lang, variant)
    t0, t1 = SEG[lang]
    start, end = OFFSET[lang] + t0, OFFSET[lang] + t1
    env = polish.envelope(source(lang), start, end - start)
    plan = polish.plan_tighten(words, start, end, lang, env, fps_of(lang))
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    kept = [w for i, w in enumerate(words) if w in inside and i not in plan.dropped]
    n_fill = sum(polish.is_filler(words, i, lang) for i, w in enumerate(words) if w in inside)
    # a cut never touches a kept word's core (its middle 80 percent)
    touching = 0
    for c in plan.cuts:
        for w in kept:
            a, b = w["start"] - start, w["end"] - start
            core = (a + 0.1 * (b - a), b - 0.1 * (b - a))
            if c["start"] < core[1] and c["end"] > core[0]:
                touching += 1
    speech = env[env > np.percentile(env, 40)]
    edge_db = [float(env[min(int(t * 100), len(env) - 1)]) for c in plan.cuts for t in (c["start"], c["end"])]
    dur = end - start
    return {"lang": lang, "words": variant, "clip_s": round(dur, 2), "cuts": len(plan.cuts), "pause_cuts": sum(c["kind"] == "pause" for c in plan.cuts),
            "filler_cuts": sum(c["kind"] == "filler" for c in plan.cuts), "fillers_in_transcript": n_fill, "removed_s": round(plan.removed, 2),
            "new_s": round(plan.new_duration, 2), "removed_pct": round(100 * plan.removed / dur, 1),
            "words_per_s_before": round(len(inside) / dur, 2), "words_per_s_after": round(len(kept) / plan.new_duration, 2),
            "cut_touches_word_core": touching, "edge_db_max": round(max(edge_db), 1) if edge_db else None,
            "speech_median_db": round(float(np.median(speech)), 1), "gaps_over_0.4_before": sum(b["start"] - a["end"] > 0.4 for a, b in zip(inside, inside[1:])),
            "cuts_list": plan.cuts}


def clicks(path: str, joins: list[float]) -> dict:
    """Largest sample-to-sample jump within 5 ms of each join, against the 99.9th percentile of jumps in the rest of the clip."""
    a = audio_of(path)
    jump = np.abs(np.diff(a))
    ref = float(np.percentile(jump, 99.9))
    worst = 0.0
    for t in joins:
        i = int(t * 16000)
        seg = jump[max(i - 80, 0):i + 80]
        worst = max(worst, float(seg.max()) if len(seg) else 0.0)
    return {"join_jump_max": round(worst, 4), "clip_jump_p99.9": round(ref, 4), "ratio": round(worst / max(ref, 1e-9), 2)}


def sync_check(lang: str, video: str, plan_words: list[dict]) -> dict:
    """Transcribe the finished clip again; compare each word's new start with the start the captions use (same words, new timeline)."""
    from clipping.adapters import transcription as tr
    model = tr.load_whisper_model("large-v3-turbo", "cuda", "int8", 0)
    segs, _ = model.transcribe(audio_of(video), language=lang, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    heard = [(w.word.strip(), float(w.start)) for s in segs for w in s.words or [] if w.word.strip()]
    import difflib
    clean = polish._clean
    mine = [clean(w["text"]) for w in plan_words]
    theirs = [clean(h[0]) for h in heard]
    diffs = []
    for block in difflib.SequenceMatcher(None, mine, theirs, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            if mine[block.a + k]:
                diffs.append(heard[block.b + k][1] - plan_words[block.a + k]["start"])
    d = np.array(diffs)
    return {"matched_words": len(d), "of": len(plan_words), "median_offset_ms": round(1000 * float(np.median(d)), 0), "p90_abs_offset_ms": round(1000 * float(np.percentile(np.abs(d - np.median(d)), 90)), 0)} if len(d) else {}


if __name__ == "__main__":
    for lang in ("en", "ar"):
        for variant in ("standard", "filler"):
            print(json.dumps(tighten_stats(lang, variant), ensure_ascii=False))
    for lang in ("en", "ar"):
        video = rf"{D}\out\{lang}_new\{lang}_new.mp4"
        base = rf"{D}\out\{lang}_base\{lang}_base.mp4"
        words, t0, t1 = words_of(lang, "standard"), *SEG[lang]
        start, end = OFFSET[lang] + t0, OFFSET[lang] + t1
        plan = polish.plan_tighten(words, start, end, lang, polish.envelope(source(lang), start, end - start), fps_of(lang))
        joins = [plan.map(c["start"]) for c in plan.cuts]
        print(json.dumps({"lang": lang, "clicks_new": clicks(video, joins), "clicks_base_same_times": clicks(base, joins)}))
        print(json.dumps({"lang": lang, "sync_new": sync_check(lang, video, plan.words(words, start, end)), "sync_base": sync_check(lang, base, [{**w, "start": w["start"] - start} for w in words if start <= w["start"] < end])}, ensure_ascii=False))
