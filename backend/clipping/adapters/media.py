"""A5 rendering and A6 captions and overlay. Own code on FFmpeg (spec 10.2)."""
from __future__ import annotations

from pathlib import Path

from .. import ffmpeg
from .base import Adapter, register


def crop_filter(src_w: int, src_h: int, out_w: int, out_h: int, focus_x: float | None = None) -> str:
    """Crop to 9:16. focus_x (0..1) is the speaker's horizontal position when a face was found;
    None means centre crop, which is also the fallback."""
    target = out_w / out_h
    if src_w / src_h > target:
        crop_w = int(round(src_h * target / 2) * 2)
        centre = (focus_x if focus_x is not None else 0.5) * src_w
        x = int(min(max(centre - crop_w / 2, 0), src_w - crop_w))
        crop = f"crop={crop_w}:{src_h}:{x}:0"
    else:
        crop_h = int(round(src_w / target / 2) * 2)
        crop = f"crop={src_w}:{min(crop_h, src_h)}:0:{max((src_h - crop_h) // 2, 0)}"
    return f"{crop},scale={out_w}:{out_h},setsar=1"


def detect_face_x(src: Path, at_s: float) -> float | None:
    """Horizontal position of the largest face, if OpenCV is installed. Optional: without it the
    render uses the centre crop fallback."""
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    try:
        frame_path = src.with_suffix(".face.png")
        ffmpeg.frame_png(src, at_s, frame_path)
        image = cv2.imread(str(frame_path))
        frame_path.unlink(missing_ok=True)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 1.1, 5)
        if len(faces) == 0:
            return None
        x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
        return float((x + w / 2) / image.shape[1])
    except Exception:
        return None


@register
class FFmpegRender(Adapter):
    stage, provider = "render", "ffmpeg"

    def health(self) -> dict:
        try:
            return {"ok": True, "provider": self.provider, "binary": Path(ffmpeg.exe()).name}
        except Exception as exc:
            return {"ok": False, "provider": self.provider, "error": str(exc)}

    def render(self, src: str, start_s: float, end_s: float, out: str, centre_only: bool = False) -> dict:
        """Cut [start, end], reframe to 9:16 and normalise loudness."""
        src_path, out_path = Path(src), Path(out)
        info = ffmpeg.probe(src_path)
        w, h = int(self.cfg.get("width", 1080)), int(self.cfg.get("height", 1920))
        focus = None if centre_only else detect_face_x(src_path, (start_s + end_s) / 2)
        vf = crop_filter(info["width"], info["height"], w, h, focus)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["-ss", f"{start_s:.3f}", "-i", str(src_path), "-t", f"{end_s - start_s:.3f}", "-vf", vf]
        if info["has_audio"]:
            args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-ar", "44100"]
        args += ["-c:v", "libx264", "-preset", str(self.cfg.get("preset", "veryfast")), "-crf", str(self.cfg.get("crf", 23)),
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
        ffmpeg.run(args)
        return {"path": str(out_path), "reframe": "face" if focus is not None else "centre_crop",
                "loudness_normalised": bool(info["has_audio"]), "width": w, "height": h}


@register
class CentreCropRender(FFmpegRender):
    """Declared fallback for A5."""
    stage, provider = "render", "centre_crop"

    def render(self, src: str, start_s: float, end_s: float, out: str, centre_only: bool = True) -> dict:
        return super().render(src, start_s, end_s, out, centre_only=True)


# ------------------------------------------------------------------ captions and overlay (ASS subtitles)

def _ts(seconds: float) -> str:
    seconds = max(seconds, 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(words: list[dict], clip_start: float, clip_end: float, max_words: int = 4, max_s: float = 1.8) -> list[dict]:
    """Group words into short caption lines, timed relative to the clip."""
    chunks: list[dict] = []
    current: list[dict] = []
    for word in words:
        if word["end"] <= clip_start or word["start"] >= clip_end:
            continue
        if current and (len(current) >= max_words or word["end"] - current[0]["start"] > max_s
                        or current[-1]["text"].rstrip()[-1:] in ".?!؟"):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    duration = clip_end - clip_start
    return [{"start": max(c[0]["start"] - clip_start, 0), "end": min(c[-1]["end"] - clip_start, duration),
             "text": " ".join(w["text"] for w in c)} for c in chunks]


def build_ass(width: int, height: int, duration: float, chunks: list[dict], label: str | None,
              label_timing: tuple[float, float] | None, hook: str | None, font: str = "Arial") -> str:
    cap_size, label_size, hook_size = int(height * 0.042), int(height * 0.026), int(height * 0.036)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{font},{cap_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{max(cap_size // 12, 2)},0,2,{width // 12},{width // 12},{int(height * 0.16)},1",
        f"Style: Label,{font},{label_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,{max(label_size // 5, 2)},0,7,{width // 24},{width // 24},{int(height * 0.035)},1",
        f"Style: Hook,{font},{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,{max(hook_size // 5, 2)},0,8,{width // 10},{width // 10},{int(height * 0.12)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if label and label_timing:
        lines.append(f"Dialogue: 2,{_ts(label_timing[0])},{_ts(label_timing[1])},Label,,0,0,0,,{_esc(label)}")
    if hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(3.0, duration))},Hook,,0,0,0,,{_esc(hook)}")
    for chunk in chunks:
        lines.append(f"Dialogue: 0,{_ts(chunk['start'])},{_ts(chunk['end'])},Caption,,0,0,0,,{_esc(chunk['text'])}")
    return "\n".join(lines) + "\n"


@register
class FFmpegCaptions(Adapter):
    stage, provider = "captions", "ffmpeg"

    def _burn(self, video: str, out: str, chunks: list[dict], label: str | None, hook: str | None) -> dict:
        video_path, out_path = Path(video), Path(out)
        info = ffmpeg.probe(video_path)
        duration = info["duration_s"]
        timing = (0.0, duration + 1.0) if label else None  # runs past the last frame: full clip duration
        ass = build_ass(info["width"], info["height"], duration, chunks, label, timing, hook, self.cfg.get("font", "Arial"))
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(ass, encoding="utf-8-sig")
        # Run from the file's folder so the filter needs no Windows drive-letter escaping.
        ffmpeg.run(["-i", str(video_path.resolve()), "-vf", f"ass={ass_path.name}", "-c:v", "libx264", "-preset",
                    str(self.cfg.get("preset", "veryfast")), "-crf", str(self.cfg.get("crf", 23)), "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(out_path.resolve())], cwd=ass_path.parent)
        return {"path": str(out_path), "ass_path": str(ass_path), "duration_s": duration,
                "overlay": {"text": label, "start_s": 0.0, "end_s": duration} if label else None,
                "captions": len(chunks)}

    def burn_captions(self, video: str, words: list[dict], style: dict, out: str) -> dict:
        chunks = caption_chunks(words, style.get("clip_start", 0.0), style.get("clip_end", 1e9))
        return self._burn(video, out, chunks, None, style.get("hook"))

    def burn_overlay(self, video: str, label: str, timing: dict, out: str) -> dict:
        return self._burn(video, out, [], label, None)

    def burn_all(self, video: str, words: list[dict], style: dict, label: str, out: str) -> dict:
        """Captions, hook title and the full-duration disclosure label in one encode."""
        if not label or not label.strip():
            raise ValueError("disclosure label is missing")
        chunks = caption_chunks(words, style.get("clip_start", 0.0), style.get("clip_end", 1e9))
        return self._burn(video, out, chunks, label, style.get("hook"))


@register
class PlainTextCaptions(FFmpegCaptions):
    """Declared fallback for A6: the disclosure label only, no word captions."""
    stage, provider = "captions", "plain_text"

    def burn_all(self, video: str, words: list[dict], style: dict, label: str, out: str) -> dict:
        if not label or not label.strip():
            raise ValueError("disclosure label is missing")
        return self._burn(video, out, [], label, None)
