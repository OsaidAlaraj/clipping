"""FFmpeg helpers. The binary comes from FFMPEG_PATH, the system PATH, or the imageio-ffmpeg package."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

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
        raise FFmpegError(proc.stderr[-800:])
    return proc


def probe(path: Path) -> dict:
    """Duration, size and audio presence, read from ffmpeg's own stream report (no ffprobe needed)."""
    proc = subprocess.run([exe(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise FFmpegError(f"cannot read media file: {path.name}")
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    width = height = None
    for line in err.splitlines():
        if "Video:" in line:
            v = re.search(r",\s*(\d{2,5})x(\d{2,5})[\s,\[]", line + " ")
            if v:
                width, height = int(v.group(1)), int(v.group(2))
            break
    return {"duration_s": round(duration, 3), "width": width, "height": height, "has_audio": "Audio:" in err}


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


def make_fixture(out: Path, seconds: int = 60, size: str = "640x360") -> Path:
    """Synthetic permitted footage for tests and the demo: a test pattern with a tone that drops to
    silence for the last second of every five (so silence detection has something to find)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
         "-t", str(seconds), "-af", "volume='if(lt(mod(t,5),4.2),0.5,0)':eval=frame",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out
