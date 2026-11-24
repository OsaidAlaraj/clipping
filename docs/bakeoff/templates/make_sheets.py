"""Render the same 20 s clip of real footage with every caption style and build one contact sheet per style plus an overview.
Run from backend/:  ..\.venv\Scripts\python.exe ..\docs\bakeoff\templates\make_sheets.py <video> <start_s> <language> [hook text]
The words come from the project's own local Whisper (cached next to the sheets). Whisper gives no speakers, so for the
speaker-name style the speakers are SYNTHETIC: they alternate at every sentence end, with the names Sara and Omar. This only
shows how the label looks; it is not a diarization result."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("CLIPPING_USE_FAKES", "0")
sys.path.insert(0, str(HERE.parents[2] / "backend"))

from PIL import Image, ImageDraw  # noqa: E402

from clipping import ffmpeg  # noqa: E402
from clipping.adapters import caption_render, media, transcription  # noqa: E402


def words_for(video: Path, start: float, length: float, language: str) -> list[dict]:
    cache = HERE / f"words_{video.stem}_{int(start)}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir=str(HERE)) as tmp:
        audio = Path(tmp) / "a.wav"
        ffmpeg.run(["-ss", str(start), "-t", str(length), "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
        result = transcription.FasterWhisperTranscription({"config_json": {"model": "large-v3-turbo"}}).transcribe(str(audio), language)
    words = [{"text": w["text"], "start": w["start"] + start, "end": w["end"] + start, "speaker": None} for w in result["words"]]
    cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def main() -> None:
    video, start, language = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    hook = sys.argv[4] if len(sys.argv) > 4 else None
    length = 20.0
    words = words_for(video, start, length, language)
    speaker, turn = "0", 0
    for w in words:   # synthetic speakers, see the note at the top
        w["speaker"] = speaker
        if w["text"].rstrip()[-1:] in ".?!":
            turn += 1
            speaker = str(turn % 2)
    adapter = media.FFmpegRender({"config_json": {"width": 540, "height": 960, "preset": "veryfast", "crf": 23, "encoder": "x264"}})
    shots = {}
    for name in caption_render.STYLES:
        out = HERE / f"clip_{name}.mp4"
        clip = {"start_s": start, "end_s": start + length, "language": language, "platform": "tiktok", "crop": "follow_speaker", "audio": "speech",
                "hook_text": hook, "emphasis": [], "speaker_names": {"0": "Sara", "1": "Omar"}}
        adapter.render_clip(str(video), clip, words, {"caption_style": name, "karaoke": True}, "#ad", str(out))
        times = [w["start"] - start + 0.25 for w in words[2::max(len(words) // 6, 1)]][:6]
        frames = []
        for t in times:
            png = HERE / f"_{name}_{t:.1f}.png"
            ffmpeg.run(["-ss", f"{t:.2f}", "-i", str(out), "-frames:v", "1", str(png)])
            frames.append(Image.open(png).convert("RGB").resize((270, 480)))
            png.unlink()
        sheet = Image.new("RGB", (270 * len(frames), 480))
        for i, f in enumerate(frames):
            sheet.paste(f, (270 * i, 0))
        sheet.save(HERE / f"sheet_{name}.jpg", quality=88)
        shots[name] = sheet
    overview = Image.new("RGB", (270 * 6, (480 + 24) * len(shots)), "#111")
    draw = ImageDraw.Draw(overview)
    for row, (name, sheet) in enumerate(shots.items()):
        draw.text((8, row * 504 + 6), f"{name}  ({caption_render.STYLES[name]['label']})", fill="#fff")
        overview.paste(sheet.crop((0, 0, 270 * 6, 480)), (0, row * 504 + 24))
    overview.save(HERE / "overview.jpg", quality=85)
    for f in HERE.glob("clip_*.mp4"):
        f.with_suffix(".ass").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
