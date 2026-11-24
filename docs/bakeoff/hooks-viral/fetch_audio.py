"""For Shorts without captions: download the audio only (anonymous, slow, internal benchmark), transcribe it with the project's own local
Whisper, delete the audio at once and add the moment to moments.json. Records each video in .data/benchmark/manifest.json.
Run: .venv\\Scripts\\python.exe docs\\bakeoff\\hooks-viral\\fetch_audio.py [max_new]"""
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"L:\Projects\Clipping")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from clipping.adapters import transcription  # noqa: E402

DOC = Path(__file__).resolve().parents[2] / "market-style-study-2.md"
OUT = ROOT / ".data/benchmark/hooks-viral"
(OUT / "audio").mkdir(parents=True, exist_ok=True)
moments_path = OUT / "moments.json"
moments = json.loads(moments_path.read_text(encoding="utf-8")) if moments_path.exists() else []
have = {m["id"] for m in moments}
manifest_path = ROOT / ".data/benchmark/manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
rows = re.findall(r"\[([\w-]{11})\]\(https://www.youtube.com/shorts/[\w-]{11}\) \| (\w+)/(\w+) \| ([^|]+) \|", DOC.read_text(encoding="utf-8"))
asr = transcription.FasterWhisperTranscription({"config_json": {"model": "large-v3-turbo"}})
added = 0
for vid, niche, lang, chan in rows:
    if vid in have or niche not in ("business", "gaming", "podcast") or added >= limit:
        continue
    for old in (OUT / "audio").glob(f"{vid}.*"):
        old.unlink()
    subprocess.run([str(ROOT / ".venv/Scripts/yt-dlp.exe"), "-x", "--audio-format", "wav", "--postprocessor-args", "-ac 1 -ar 16000", "--max-filesize", "12M",
                    "--sleep-requests", "3", "-o", str(OUT / "audio" / f"{vid}.%(ext)s"), f"https://www.youtube.com/shorts/{vid}"], capture_output=True)
    wav = next(iter((OUT / "audio").glob(f"{vid}.*")), None)
    status, size = "download-failed", 0
    if wav:
        size = wav.stat().st_size
        try:
            text = " ".join(w["text"] for w in asr.transcribe(str(wav), lang)["words"])
            if len(text.split()) >= 25:
                moments.append({"id": vid, "niche": niche, "lang": lang, "channel": chan.strip(), "text": text})
                added += 1
            status = "deleted"
        except Exception as exc:
            status = f"asr-failed {exc!r}"[:60]
        wav.unlink(missing_ok=True)   # the audio is internal-only and never kept
    manifest.append({"url": f"https://www.youtube.com/shorts/{vid}", "platform": "youtube", "niche": niche, "channel": chan.strip(),
                     "date": datetime.date.today().isoformat(), "size": size, "status": status, "note": "audio only, transcribed for the hook benchmark, then deleted"})
    moments_path.write_text(json.dumps(moments, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(vid, niche, lang, status, len(moments), flush=True)
    time.sleep(4)
