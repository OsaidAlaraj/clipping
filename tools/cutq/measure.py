"""Repeatable cut-quality metrics for a rendered set. Usage: python measure.py SETDIR [--json out.json]
Per clip: tightening cut edges on silence (audio, independent of the transcript), words cut, dead air left, average shot length,
join smoothness, face-in-crop rate and camera jitter on the OUTPUT frames (YuNet), start/end quality."""
import json, subprocess, sys
from pathlib import Path
import numpy as np, cv2
sys.path.insert(0, r"L:\Projects\Clipping\.claude\worktrees\cut-quality-3d8a7d\backend")
from clipping import ffmpeg, faces
from clipping.polish import envelope
HERE = Path(__file__).parent
wins = {w["id"]: w for w in json.load(open(HERE / "windows.json", encoding="utf8"))}
END = ".?!؟…"

def speech_level(env):
    return float(np.percentile(env[env > np.percentile(env, 40)], 50))

def audio_env(path):
    return envelope(path, 0.0, 1e4)

def runs(mask, min_s):
    out, k = [], 0
    while k < len(mask):
        if mask[k]:
            j = k
            while j < len(mask) and mask[j]: j += 1
            if (j - k) / 100 >= min_s: out.append((k / 100, j / 100))
            k = j
        else: k += 1
    return out

def frames(path, fps=3):
    cap = cv2.VideoCapture(str(path)); src = cap.get(cv2.CAP_PROP_FPS) or 30; n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out, prev, diffs = [], None, []
    i = 0
    while True:
        ok, f = cap.read()
        if not ok: break
        small = cv2.resize(f, (96, 170)).astype(np.float32)
        diffs.append(0.0 if prev is None else float(np.abs(small - prev).mean())); prev = small
        if i % max(1, round(src / fps)) == 0: out.append((i / src, f))
        i += 1
    cap.release(); return out, diffs, src

def measure(clip_id, d):
    mp4, meta = d / f"{clip_id}.mp4", json.load(open(d / f"{clip_id}.json"))
    w = wins[clip_id]; words = json.load(open(w["transcript"], encoding="utf8"))
    cs, ce = w["start_s"], w["end_s"]; r = {"id": clip_id, "dur": meta["duration_s"], "src_dur": ce - cs}
    cuts = (meta.get("tightened") or {}).get("cuts", []) or []
    # --- tightening cuts vs the ORIGINAL audio
    senv = envelope(w["src"], cs, ce - cs); lvl = speech_level(senv)
    pause_cuts = [c for c in cuts if c["kind"] != "outro"]
    edge_db = [float(senv[min(int(t * 100), len(senv) - 1)]) - lvl for c in pause_cuts for t in (c["start"], c["end"])]
    r["cuts"] = len(pause_cuts); r["removed_s"] = round(sum(c["end"] - c["start"] for c in pause_cuts), 2)
    r["edges_quiet_pct"] = round(100 * np.mean([e < -15 for e in edge_db]), 1) if edge_db else None
    inside = [x for x in words if x["end"] > cs and x["start"] < ce]
    cut_words = 0
    for c in pause_cuts:
        a, b = cs + c["start"], cs + c["end"]
        cut_words += sum(1 for x in inside if min(x["end"], b) - max(x["start"], a) > 0.05 and c["kind"] != "filler")
    r["words_cut_nonfiller"] = cut_words
    # --- dead air in the OUTPUT, and in the source window (for comparison)
    oenv = audio_env(mp4); olvl = speech_level(oenv)
    quiet = oenv < olvl - 22
    r["dead_air_s"] = round(sum(b - a for a, b in runs(quiet, 0.45)), 2); r["silences_gt045"] = len(runs(quiet, 0.45))
    r["longest_silence"] = round(max([b - a for a, b in runs(quiet, 0.2)] or [0]), 2)
    r["src_dead_air_s"] = round(sum(b - a for a, b in runs(senv < lvl - 22, 0.45)), 2)
    # --- start / end quality (output timeline from the source transcript, first and last word of the window)
    r["lead_in_s"] = round(inside[0]["start"] - cs, 2) if inside else None
    r["tail_s"] = round(ce - inside[-1]["end"], 2) if inside else None
    r["ends_sentence"] = bool(inside and inside[-1]["text"].rstrip()[-1:] in END)
    # --- video: shots, joins, face in crop, camera jitter
    fr, diffs, fps = frames(mp4); dif = np.array(diffs)
    hard = np.where(dif > 14)[0]; shots = np.diff([0, *hard, len(dif)]) / fps
    r["hard_cuts"] = int(len(hard)); r["avg_shot_s"] = round(float(shots.mean()), 2)
    removed = 0.0; joins = []
    for c in pause_cuts:
        t = c["start"] - removed; removed += c["end"] - c["start"]
        k = min(int(round(t * fps)), len(dif) - 1); joins.append(float(dif[max(k - 1, 0):k + 2].max()))
    r["join_diff_mean"] = round(float(np.mean(joins)), 2) if joins else None
    r["base_diff_med"] = round(float(np.median(dif)), 2)
    det = faces._load(); cx = []
    for t, f in fr:
        _, found = det.detect(cv2.resize(f, (faces.FRAME_W, faces.FRAME_H)))
        if found is None: cx.append(None); continue
        b = max(found, key=lambda q: q[2] * q[3]); cx.append(((b[0] + b[2] / 2) / faces.FRAME_W, b[0] / faces.FRAME_W, (b[0] + b[2]) / faces.FRAME_W))
    seen = [c for c in cx if c]
    r["face_found_pct"] = round(100 * len(seen) / len(cx), 1)
    r["face_inside_pct"] = round(100 * np.mean([c[1] >= 0.02 and c[2] <= 0.98 for c in seen]), 1) if seen else 0.0
    mv = [abs(a[0] - b[0]) for a, b in zip(seen, seen[1:])]
    r["cam_jumps"] = int(sum(m > 0.15 for m in mv)); r["cam_p95_move"] = round(float(np.percentile(mv, 95)), 3) if mv else 0
    r["zooms"] = meta.get("zooms")
    return r

if __name__ == "__main__":
    d = Path(sys.argv[1]); res = []
    for j in sorted(d.glob("*.json")):
        if j.stem in wins: res.append(measure(j.stem, d)); print(json.dumps(res[-1]), flush=True)
    if "--json" in sys.argv: json.dump(res, open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1)
