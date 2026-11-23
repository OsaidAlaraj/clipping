"""FFmpeg helpers. The binary comes from FFMPEG_PATH, the system PATH, or the imageio-ffmpeg package."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

from . import config


class FFmpegError(Exception):
    pass


@lru_cache(maxsize=1)
def exe() -> str:
    explicit = config.env("FFMPEG_PATH")
    if explicit:
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run([exe(), "-hide_banner", "-nostdin", "-y", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(readable_error(proc.stderr))
    return proc


def readable_error(stderr: str) -> str:
    """ffmpeg prints the whole input description before the reason; the owner gets the reason, in plain words when it is a known one."""
    text = stderr.lower()
    if "does not contain any stream" in text or "stream map" in text and "matches no streams" in text:
        return "The video has no audio track to work with."
    if "invalid data found" in text or "moov atom not found" in text:
        return "The video file is damaged or incomplete."
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    for ln in reversed(lines):   # the last line that says what went wrong, not a metadata dump
        if re.search(r"error|invalid|unable|cannot|could not|failed|no such|not found", ln, re.I):
            return ln[:300]
    return (lines[-1] if lines else "ffmpeg failed")[:300]


def probe(path: Path) -> dict:
    """Duration, size and audio presence, read from ffmpeg's own stream report (no ffprobe needed)."""
    proc = subprocess.run([exe(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise FFmpegError(f"cannot read media file: {path.name}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    width = height = fps = None
    for line in err.splitlines():
        if "Video:" in line:
            f = re.search(r"([\d.]+) fps", line)
            fps = float(f.group(1)) if f else None
            v = re.search(r",\s*(\d{2,5})x(\d{2,5})[\s,\[]", line + " ")
            if v:
                width, height = int(v.group(1)), int(v.group(2))
            break
    return {"duration_s": round(duration, 3), "width": width, "height": height, "fps": fps, "has_audio": "Audio:" in err}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_audio(src: Path, out: Path) -> Path:
    run(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)])
    return out


def silences(src: Path, noise_db: int = -35, min_s: float = 0.3) -> list[tuple[float, float]]:
    """Silent stretches, used to snap clip boundaries when no sentence boundary is near (FR-13)."""
    proc = subprocess.run([exe(), "-hide_banner", "-nostdin", "-i", str(src), "-vn", "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", proc.stderr)]
    return list(zip(starts, ends))


def frame_png(src: Path, at_s: float, out: Path) -> Path:
    run(["-ss", f"{at_s:.3f}", "-i", str(src), "-frames:v", "1", str(out)])
    return out


def frame_raw(src: Path, at_s: float, width: int, height: int, pix_fmt: str = "rgb24") -> bytes | None:
    """One frame at `at_s`, scaled to width x height, as raw pixels (None when the video has no frame there)."""
    proc = subprocess.run([exe(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(at_s, 0):.3f}", "-i", str(src), "-frames:v", "1",
                           "-vf", f"scale={width}:{height}:flags=fast_bilinear", "-pix_fmt", pix_fmt, "-f", "rawvideo", "-"],
                          capture_output=True, timeout=120)
    size = width * height * (1 if pix_fmt == "gray" else 3)
    return proc.stdout if proc.returncode == 0 and len(proc.stdout) == size else None


def skin_mask(frame):
    """Boolean mask of skin-toned pixels of an (h, w, 3) int16 RGB frame: the cheap stand-in for "a face" that the signals
    and the reframing share."""
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    spread = frame.max(axis=2) - frame.min(axis=2)
    return (r > 95) & (g > 40) & (b > 20) & (spread > 15) & (abs(r - g) > 15) & (r > g) & (r > b)


# ------------------------------------------------------------------ video encoder: NVENC when it works, x264 otherwise

NVENC_RECHECK_S = 120
_nvenc: dict = {"at": 0.0, "ok": False}


def nvenc_ready() -> bool:
    """True when the GPU encoder takes a job right now (cached for two minutes; a busy or missing GPU gives False)."""
    now = time.monotonic()
    if now - _nvenc["at"] > NVENC_RECHECK_S or not _nvenc["at"]:
        try:
            run(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
            _nvenc.update(at=now, ok=True)
        except Exception:
            _nvenc.update(at=now, ok=False)
    return _nvenc["ok"]


def video_encoder(choice: str = "auto") -> str:
    """The encoder to try first for the setting `auto`, `nvenc` or `x264`."""
    return "h264_nvenc" if choice == "nvenc" or (choice == "auto" and nvenc_ready()) else "libx264"


X264_CRF_OFFSET = 3     # measured (docs/bakeoff/final/polish): x264 medium at crf-3 matches NVENC p5 at cq crf+1 (VMAF-NEG within 0.5, similar size)


def encoder_args(encoder: str, preset: str = "medium", crf: int = 23) -> list[str]:
    """Output arguments of one video encoder (same quality target on both)."""
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf + 1), "-b:v", "0", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(max(crf - X264_CRF_OFFSET, 0))]


# ------------------------------------------------------------------ one-pass analysis (signals)

PCM_RATE = 16000
PCM_SECOND = PCM_RATE * 2                # bytes in one second of 16 kHz mono s16le
THUMB_W, THUMB_H = 64, 36                # frames are measured at this size: tiny, so decoding is the only cost
THUMB_BYTES = THUMB_W * THUMB_H * 3
SILENCE_LUFS = -70.0
FULL_DECODE_MAX_S = 1200                 # longer videos are sampled from keyframes only (about 2.4x faster)


def _stream(args: list[str], chunk: int, cwd: Path | None = None) -> Iterator[bytes]:
    """Run ffmpeg and yield its stdout in `chunk`-byte pieces (the last may be shorter). Raises FFmpegError when ffmpeg
    fails or produces nothing, which is what an unreadable or corrupted file looks like."""
    with tempfile.TemporaryFile() as err:   # a file, not a pipe: a corrupted video can print more than a pipe holds
        proc = subprocess.Popen([exe(), "-hide_banner", "-nostdin", "-v", "error", *args, "-"], cwd=cwd,
                                stdout=subprocess.PIPE, stderr=err)
        produced = 0
        try:
            while piece := proc.stdout.read(chunk):
                produced += 1
                yield piece
            if proc.wait() != 0 or not produced:
                err.seek(max(err.seek(0, 2) - 400, 0))
                raise FFmpegError(err.read().decode("utf-8", "replace") or "ffmpeg produced no data")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


def audio_pass(src: Path, on_second: Callable[[bytes], None]) -> list[float]:
    """Decode the audio once. `on_second` gets each second as 16 kHz mono s16le bytes; the return value is the EBU R128
    momentary loudness (LUFS, the loudest 100 ms reading) of each second, floored at -70."""
    scratch = config.data_dir() / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as folder:
        log = Path(folder) / "m.txt"   # relative name in the filter: a Windows drive letter would need escaping
        chain = f"ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file={log.name},aresample={PCM_RATE}"
        for piece in _stream(["-i", str(src.resolve()), "-vn", "-af", chain, "-ac", "1", "-f", "s16le"], PCM_SECOND, cwd=Path(folder)):
            on_second(piece)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    loudness: dict[int, float] = {}
    for at, level in re.findall(r"pts_time:(\d+(?:\.\d+)?)\s+lavfi\.r128\.M=(-?[\d.]+|-?inf)", text):
        value = SILENCE_LUFS if "inf" in level else max(float(level), SILENCE_LUFS)
        second = int(float(at))
        loudness[second] = max(loudness.get(second, SILENCE_LUFS), value)
    return [round(loudness.get(i, SILENCE_LUFS), 1) for i in range(max(loudness, default=-1) + 1)]


def video_thumbs(src: Path, duration_s: float) -> Iterator[bytes]:
    """One tiny RGB frame (64x36, THUMB_BYTES bytes) per second. Long videos decode keyframes only."""
    fast = ["-skip_frame", "nokey"] if duration_s > FULL_DECODE_MAX_S else []
    yield from _stream([*fast, "-i", str(src), "-an", "-vf", f"fps=1,scale={THUMB_W}:{THUMB_H}:flags=fast_bilinear,format=rgb24",
                        "-f", "rawvideo"], THUMB_BYTES)


def make_fixture(out: Path, seconds: int = 60, size: str = "640x360") -> Path:
    """Synthetic permitted footage for tests and the demo: a test pattern with a tone that drops to
    silence for the last second of every five (so silence detection has something to find)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
         "-t", str(seconds), "-af", "volume='if(lt(mod(t,5),4.2),0.5,0)':eval=frame",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out
