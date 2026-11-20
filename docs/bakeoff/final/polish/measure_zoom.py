"""Measure punch-in zooms, old (triangular bump on loud seconds) against new (smootherstep ease on stressed words), on the same real
segments. Output: per language the zoom moments, how stressed the audio is at the zoom peak, and how smooth the zoom curve is
(frame-by-frame change of the scaled picture width at 1080 px, 30 fps, sizes rounded to even pixels exactly as the filter does)."""
import json
import os
import sys
import wave

import numpy as np

ROOT = os.environ.get("WORKTREE", r"L:\Projects\Clipping\.claude\worktrees\agent-a2d95be84a7183ce9")
D = os.environ.get("POLISH_DATA", r"L:\Projects\Clipping\.data\polish-work")
sys.path.insert(0, ROOT + r"\backend")
os.environ["CLIPPING_DATA_DIR"] = r"L:\Projects\Clipping\.data"
from clipping import polish, templates  # noqa: E402
from clipping.adapters import media  # noqa: E402

OFFSET = {"en": 600.0, "ar": 900.0}
SEG = json.load(open(rf"{D}\segments.json"))


def old_factor(t: float, times: list[float]) -> float:
    return 1 + media.ZOOM_DEPTH * sum(max(0.0, 1 - abs(t - x) / media.ZOOM_HALF_S) for x in times)


def smoothness(factor, duration: float) -> dict:
    ts = np.arange(0, duration, 1 / 30)
    w = np.array([int(1080 * factor(t) / 2) * 2 for t in ts])
    d1, d2 = np.diff(w), np.diff(w, 2)
    z = np.array([factor(t) for t in ts])
    return {"max_width_step_px": int(np.abs(d1).max()), "max_width_accel_px": int(np.abs(d2).max()), "peak_zoom": round(float(z.max()), 3),
            "share_of_time_zoomed_over_2pct": round(float((z > 1.02).mean()), 3)}


for lang in ("en", "ar"):
    words = [{**w, "start": w["start"] + OFFSET[lang], "end": w["end"] + OFFSET[lang]} for w in json.load(open(rf"{D}\{lang}_words.json", encoding="utf-8"))["words"]]
    t0, t1 = SEG[lang]
    start, end = OFFSET[lang] + t0, OFFSET[lang] + t1
    src = rf"L:\Projects\Clipping\.data\samples\{'en_cordkillers_ep377' if lang == 'en' else 'ar_kau_physiology_l38'}.mp4"
    env = polish.envelope(src, start, end - start)
    x = np.frombuffer(wave.open(rf"{D}\{lang}.wav").readframes(10**9), dtype=np.int16).astype(np.float32) / 32768
    db = [20 * np.log10(np.sqrt((x[i * 16000:(i + 1) * 16000] ** 2).mean()) + 1e-6) for i in range(len(x) // 16000)]
    emphasis = templates.emphasis_times([-90.0] * int(OFFSET[lang]) + db, start, end)
    old_times = media.zoom_times(emphasis, start, end - start)
    new = polish.stress_moments(words, start, end, env)
    speech = env[env > np.percentile(env, 40)]
    mean, sd = float(speech.mean()), float(speech.std())

    def stress_at(t: float) -> float:     # how far above the speech level the loudest 10 ms is within 0.35 s of t
        seg = env[max(int((t - 0.35) * 100), 0):int((t + 0.35) * 100)]
        return (float(seg.max()) - mean) / sd
    print(json.dumps({"lang": lang, "clip_s": round(end - start, 2),
                      "old": {"zooms": len(old_times), "times": old_times, "mean_stress_z_at_peak": round(float(np.mean([stress_at(t) for t in old_times])), 2) if old_times else None,
                              **smoothness(lambda t: old_factor(t, old_times), end - start)},
                      "new": {"zooms": len(new), "moments": new, "mean_stress_z_at_peak": round(float(np.mean([stress_at(t + media.ZOOM_IN_S) for t, _ in new])), 2) if new else None,
                              **smoothness(lambda t: media.punch_factor(t, new), end - start)}}, ensure_ascii=False))
