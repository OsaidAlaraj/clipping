"""Image sharpness: (A) which scaler / sharpening gives the best picture when a small source is blown up to the 9:16 frame, and
(B) which encoder settings lose the least of that picture. VMAF, SSIM and a Tenengrad sharpness ratio, all against a reference.
A: truth = a native-resolution crop of the 1080p source; the input is that crop scaled down 2x, each candidate scales it back up 2x.
B: truth = the 1080x1920 frame written lossless (x264 crf 0); each candidate encodes it.
Usage: measure_sharp.py A|B  -> JSON lines. Scratch files under .data/polish-work/sharp."""
import json
import os
import re
import subprocess
import sys
import time

import numpy as np

ROOT = os.environ.get("WORKTREE", r"L:\Projects\Clipping\.claude\worktrees\agent-a2d95be84a7183ce9")
sys.path.insert(0, ROOT + r"\backend")
os.environ["CLIPPING_DATA_DIR"] = r"L:\Projects\Clipping\.data"
from clipping import ffmpeg  # noqa: E402
from clipping.adapters import media  # noqa: E402

D = r"L:\Projects\Clipping\.data\polish-work\sharp"
os.makedirs(D, exist_ok=True)
S = r"L:\Projects\Clipping\.data\samples"
CLIPS = {"studio_1080p": (S + r"\en_cordkillers_ep377.mp4", 700.0, 8.0), "slides_720p": (S + r"\ar_kau_physiology_l38.mp4", 900.0, 8.0)}
F = ffmpeg.exe()


def run(args, cwd=D):
    r = subprocess.run([F, "-nostdin", "-y", "-hide_banner", *args], capture_output=True, text=True, errors="replace", cwd=cwd)
    if r.returncode:
        raise RuntimeError(r.stderr[-600:])
    return r.stderr


def quality(dist: str, ref: str, tag: str) -> dict:
    log = f"{tag}.json"
    err = run(["-i", dist, "-i", ref, "-lavfi", f"[0:v]setpts=PTS-STARTPTS[d];[1:v]setpts=PTS-STARTPTS[r];[d][r]libvmaf=log_fmt=json:log_path={log}:n_threads=4:feature=name=psnr|name=float_ssim", "-f", "null", "-"])
    m = json.load(open(os.path.join(D, log)))["pooled_metrics"]
    run(["-i", dist, "-i", ref, "-lavfi", f"[0:v]setpts=PTS-STARTPTS[d];[1:v]setpts=PTS-STARTPTS[r];[d][r]libvmaf=model=version=vmaf_v0.6.1neg:log_fmt=json:log_path={tag}_neg.json:n_threads=4", "-f", "null", "-"])
    neg = json.load(open(os.path.join(D, f"{tag}_neg.json")))["pooled_metrics"]["vmaf"]["mean"]
    return {"vmaf": round(m["vmaf"]["mean"], 2), "vmaf_neg": round(neg, 2), "ssim": round(m["float_ssim"]["mean"], 4), "psnr": round(m["psnr_y"]["mean"], 2)}


def tenengrad(path: str, w: int, h: int, n: int = 12) -> float:
    raw = subprocess.run([F, "-nostdin", "-loglevel", "error", "-i", path, "-vf", f"fps=2,scale={w}:{h}:flags=area,format=gray", "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True, cwd=D).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.float32)
    gx, gy = fr[:, 1:-1, 2:] - fr[:, 1:-1, :-2], fr[:, 2:, 1:-1] - fr[:, :-2, 1:-1]
    return float(np.sqrt(gx ** 2 + gy ** 2).mean())


def part_a():
    cands = {"bicubic (current)": "scale={w}:{h}:flags=bicubic", "bilinear": "scale={w}:{h}:flags=bilinear", "lanczos": "scale={w}:{h}:flags=lanczos",
             "spline": "scale={w}:{h}:flags=spline", "bicubic+unsharp0.5": "scale={w}:{h}:flags=bicubic,unsharp=5:5:0.5:5:5:0",
             "lanczos+unsharp0.4": "scale={w}:{h}:flags=lanczos,unsharp=5:5:0.4:5:5:0", "lanczos+unsharp0.8": "scale={w}:{h}:flags=lanczos,unsharp=5:5:0.8:5:5:0",
             "lanczos+cas0.5": "scale={w}:{h}:flags=lanczos,cas=0.5", "lanczos+cas0.8": "scale={w}:{h}:flags=lanczos,cas=0.8"}
    for name, (src, ss, dur) in CLIPS.items():
        info = ffmpeg.probe(__import__("pathlib").Path(src))
        sw, sh = info["width"], info["height"]
        cw = int(round(sh * 9 / 16 / 2) * 2)               # the 9:16 crop at native size
        truth = f"truth_{name}.mkv"
        run(["-ss", str(ss), "-t", str(dur), "-i", src, "-an", "-vf", f"crop={cw}:{sh}:{(sw - cw) // 2}:0,format=yuv420p", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", truth])
        lw, lh = (cw // 2) // 2 * 2, (sh // 2) // 2 * 2
        low = f"low_{name}.mkv"
        run(["-i", truth, "-vf", f"scale={lw}:{lh}:flags=lanczos", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", low])
        t_ref = tenengrad(truth, cw, sh)
        for cand, flt in cands.items():
            out = f"A_{name}_{abs(hash(cand)) % 10**6}.mkv"
            run(["-i", low, "-vf", flt.format(w=cw, h=sh) + ",format=yuv420p", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", out])
            q = quality(out, truth, out[:-4])
            print(json.dumps({"part": "A", "clip": name, "source": f"{sw}x{sh}", "candidate": cand, **q, "sharpness_vs_truth": round(tenengrad(out, cw, sh) / t_ref, 3)}))


def part_b():
    variants = {
        "nvenc p5 cq24 (current)": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "24", "-b:v", "0", "-profile:v", "high"],
        "nvenc p5 cq20": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-profile:v", "high"],
        "nvenc p7 hq cq22 aq": ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-profile:v", "high", "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20"],
        "nvenc p7 hq cq19 aq": ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-profile:v", "high", "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20"],
        "x264 veryfast crf23 (fallback now)": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"],
        "x264 veryfast crf20": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
        "x264 medium crf20": ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
        "x264 slow crf20": ["-c:v", "libx264", "-preset", "slow", "-crf", "20"],
        "x264 medium crf18": ["-c:v", "libx264", "-preset", "medium", "-crf", "18"],
    }
    for name, (src, ss, dur) in CLIPS.items():
        info = ffmpeg.probe(__import__("pathlib").Path(src))
        ref = f"ref_{name}.mkv"
        flt = media.crop_filter(info["width"], info["height"], 1080, 1920, 0.5)
        run(["-ss", str(ss), "-t", str(dur), "-i", src, "-an", "-vf", f"{flt},format=yuv420p", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", ref])
        t_ref = tenengrad(ref, 1080, 1920, 8)
        for vname, args in variants.items():
            out = f"B_{name}_{abs(hash(vname)) % 10**6}.mp4"
            t = time.time()
            run(["-i", ref, *args, "-pix_fmt", "yuv420p", out])
            secs = time.time() - t
            q = quality(out, ref, out[:-4])
            print(json.dumps({"part": "B", "clip": name, "variant": vname, **q, "sharpness_vs_ref": round(tenengrad(out, 1080, 1920, 8) / t_ref, 3),
                              "mb_per_min": round(os.path.getsize(os.path.join(D, out)) / 1e6 * 60 / dur, 1), "encode_s": round(secs, 1)}))


if __name__ == "__main__":
    {"A": part_a, "B": part_b}[sys.argv[1]]()
