"""Music bed bake-off: picks the mix settings by measurement and proves the result on a real speech clip.

Run: <venv python> docs/bakeoff/final/music/run_music_bakeoff.py   (needs the tracks in .data/music)

Listening proxies, all measured on decoded stems (voice and ducked bed separately, 16 kHz mono):
  smr_db      speech-to-music ratio while the voice is speaking, in the speech band 300-3400 Hz (intelligibility; 18 dB or more is the target)
  gap_smr_db  the same ratio in the pauses (the bed must stay audible there, so lower is better, up to a ceiling of 26 dB)
  duck_db     how far the compressor pulls the bed down when the voice starts (gap level minus speaking level)
  lufs_err    final clip integrated loudness minus the platform target
  peak_db     sample peak of the final clip (must stay under -1 dBFS)
A setting is feasible when smr >= 18, gap_smr <= 26, |lufs_err| <= 1 and peak <= -1; the winner has the lowest gap_smr (the most
present bed) among the feasible ones."""
from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from clipping import ffmpeg, music  # noqa: E402
from clipping.adapters import media  # noqa: E402

SRC = HERE.parent / "interview_en_new_yunet.mp4"
WORK = ROOT / ".data" / "tmp" / "music-bakeoff"   # drive L, git-ignored
MUSIC_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("L:/Projects/Clipping/.data/music")
RATE = 16000
TARGET = music.target_loudness("tiktok")[0]


def stems(mix: dict, track: dict, offset: float, seconds: float = 24.0) -> tuple[np.ndarray, np.ndarray]:
    """(voice, ducked bed) as float arrays: the voice after loudness normalisation, the bed after the sidechain."""
    WORK.mkdir(parents=True, exist_ok=True)
    gain = music.music_gain_db(track, mix)
    duck = music.duck_graph("[sc]", "[1:a]", "[md]", mix, gain, seconds)
    lufs, tp = music.target_loudness("tiktok")
    graph = f"[0:a]loudnorm=I={lufs}:TP={tp}:LRA=11,asplit=2[sp][sc];{duck}"
    paths = [WORK / "stem_voice.wav", WORK / "stem_bed.wav"]
    ffmpeg.run(["-t", str(seconds), "-i", str(SRC), "-ss", f"{offset}", "-stream_loop", "-1", "-i", track["path"], "-filter_complex", graph,
                "-map", "[sp]", "-t", str(seconds), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(paths[0]),
                "-map", "[md]", "-t", str(seconds), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(paths[1])])
    out = [np.frombuffer(p.read_bytes()[44:], dtype=np.int16).astype(np.float64) / 32768 for p in paths]
    return out[0], out[1]


def band(x: np.ndarray, lo=300.0, hi=3400.0) -> np.ndarray:
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    spec[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(spec, len(x))


def db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)


def metrics(voice: np.ndarray, bed: np.ndarray) -> dict:
    """Frame (50 ms) levels; speaking frames are those within 25 dB of the voice's loudest frames."""
    n = RATE // 20
    size = min(len(voice), len(bed))
    voice, bed = voice[:size], bed[:size]
    frames = size // n
    v, b = band(voice)[: frames * n].reshape(frames, n), band(bed)[: frames * n].reshape(frames, n)
    vl = 20 * np.log10(np.sqrt((v ** 2).mean(axis=1)) + 1e-9)
    bl = 20 * np.log10(np.sqrt((b ** 2).mean(axis=1)) + 1e-9)
    speaking = vl > np.percentile(vl, 95) - 25
    # the compressor needs about its release time to let go: leave 0.5 s after each voice frame out of the "pause" set
    pause = ~np.convolve(speaking.astype(float), np.ones(11), mode="same").astype(bool)
    if speaking.sum() < 10 or pause.sum() < 10:
        raise RuntimeError("clip has no clear speech and pause sections")
    smr = float(np.mean(vl[speaking]) - np.mean(bl[speaking]))
    gap_smr = float(np.mean(vl[speaking]) - np.mean(bl[pause]))
    return {"smr_db": round(smr, 1), "gap_smr_db": round(gap_smr, 1), "duck_db": round(float(np.mean(bl[pause]) - np.mean(bl[speaking])), 1),
            "speech_share": round(float(speaking.mean()), 2)}


def final_metrics(mix: dict, track: dict, offset: float, seconds: float = 24.0) -> dict:
    """Integrated loudness and peak of the whole mix as the render builds it (same graph, same filters)."""
    plan = {"gain_db": music.music_gain_db(track, mix)}
    saved = dict(music.MIX)
    music.MIX.update(mix)
    try:
        graph = music.audio_graph(plan, "tiktok", False, speech_in="0:a:0", music_in="1:a:0", duration_s=seconds)
    finally:
        music.MIX.clear()
        music.MIX.update(saved)
    wav = WORK / "final.wav"
    ffmpeg.run(["-t", str(seconds), "-i", str(SRC), "-ss", f"{offset}", "-stream_loop", "-1", "-i", track["path"], "-filter_complex", graph,
                "-map", "[a]", "-t", str(seconds), "-ar", "44100", "-c:a", "pcm_s16le", str(wav)])
    proc = subprocess.run([ffmpeg.exe(), "-hide_banner", "-nostdin", "-i", str(wav), "-af", "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)[-1])
    peak = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", proc.stderr)[-1])
    return {"lufs": lufs, "lufs_err": round(lufs - TARGET, 2), "peak_db": peak}


def feasible(m: dict) -> bool:
    return m["smr_db"] >= 18 and m["gap_smr_db"] <= 26 and abs(m["lufs_err"]) <= 1.0 and m["peak_db"] <= -1.0


def main() -> None:
    tracks = {t["id"]: t for t in music.tracks(MUSIC_DIR)}
    ref = tracks.get("mm-backbay-lounge") or next(iter(tracks.values()))
    # 1. the mix grid on one reference track
    grid = []
    for lufs, ratio, thr, rel in itertools.product((-19.0, -22.0, -25.0, -28.0), (4.0, 8.0, 14.0), (0.01, 0.03, 0.06), (350,)):
        mix = {**music.MIX, "music_lufs": lufs, "ratio": ratio, "threshold": thr, "release_ms": rel}
        voice, bed = stems(mix, ref, 20.0)
        row = {"mix": {"music_lufs": lufs, "ratio": ratio, "threshold": thr, "release_ms": rel}, **metrics(voice, bed), **final_metrics(mix, ref, 20.0)}
        row["feasible"] = feasible(row)
        grid.append(row)
        print(row, flush=True)
    ok = [r for r in grid if r["feasible"]]
    winner = min(ok, key=lambda r: (r["gap_smr_db"], -r["smr_db"])) if ok else max(grid, key=lambda r: r["smr_db"])
    mix = {**music.MIX, **winner["mix"]}
    # 2. every catalog track under the winning mix: does any of them mask the voice?
    per_track = []
    for t in tracks.values():
        voice, bed = stems(mix, t, 20.0)
        per_track.append({"track": t["id"], **metrics(voice, bed), **final_metrics(mix, t, 20.0)})
        per_track[-1]["feasible"] = feasible(per_track[-1])
        print(per_track[-1], flush=True)
    # 3. before and after on the real clip, through the real render adapter
    adapter = media.FFmpegRender({"config_json": {"width": 540, "height": 960, "preset": "veryfast", "crf": 26, "encoder": "x264"}})
    clip = {"start_s": 0.0, "end_s": 24.0, "language": "en", "platform": "tiktok", "crop": "centre", "audio": "speech", "hook_text": None, "emphasis": []}
    words = [{"text": "w", "start": 0, "end": 0.1}]
    before = adapter.render_clip(str(SRC), clip, words, {"title_card": False, "progress_bar": False, "zooms": False}, "#ad", str(HERE / "before_no_music.mp4"))
    profile = music.clip_profile([], 0, 24)
    pick = music.choose("podcasts", {**profile, "speech_share": 0.5}, 24.0, "tiktok", pool=list(tracks.values()), seed="proof")
    track = pick["track"]
    after_clip = {**clip, "music": {"path": track["path"], "offset_s": pick["offset_s"], "gain_db": music.music_gain_db(track, mix), "record": music.licence_record(track, pick["offset_s"], "tiktok")}}
    saved = dict(music.MIX)
    music.MIX.update(mix)
    try:
        after = adapter.render_clip(str(SRC), after_clip, words, {"title_card": False, "progress_bar": False, "zooms": False}, "#ad", str(HERE / "after_with_music.mp4"))
    finally:
        music.MIX.clear()
        music.MIX.update(saved)
    for name in ("before_no_music", "after_with_music"):
        for extra in (".ass",):
            (HERE / f"{name}{extra}").unlink(missing_ok=True)
    (HERE / "cover.png").unlink(missing_ok=True)
    report = {"target_lufs": TARGET, "reference_track": ref["id"], "winner": winner, "grid": grid, "per_track": per_track,
              "proof": {"track": after["music"], "before": {k: before[k] for k in ("encodes", "duration_s")}, "after": {k: after[k] for k in ("encodes", "duration_s")}}}
    (HERE / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("WINNER", winner)


if __name__ == "__main__":
    main()
