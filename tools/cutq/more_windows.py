"""Add gaming (Markiplier), stream (Kai Cenat) and Arabic stand-up windows to windows.json. Gaming/stream: 4 slices of 110 s are
transcribed locally (faster-whisper) and one sentence-bounded 35-50 s window is taken from each. Comedy: pause-bounded windows from the stored transcript."""
import json, subprocess, sys, tempfile, os
from pathlib import Path
sys.path.insert(0, r"L:\Projects\Clipping\.claude\worktrees\cut-quality-3d8a7d\backend")
from clipping import ffmpeg
from faster_whisper import WhisperModel
HERE = Path(__file__).parent; OUT = Path(r"L:\Projects\Clipping\.data\tmp\cutq\tr"); OUT.mkdir(parents=True, exist_ok=True)
SH = r"L:\Projects\Clipping\.data\benchmark\shots"; END = ".?!"
wins = json.load(open(HERE / "windows.json", encoding="utf8")); wins = [w for w in wins if not w["id"].startswith(("gaming", "stream", "comedy"))]
model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=8, download_root="L:/Projects/Clipping/.data/cache/whisper")
for label, d, offs in (("gaming", "en_gaming", (300, 1500)), ("stream", "en_stream", (600, 3000))):
    src = rf"{SH}\{d}\ep.mp4"
    for k, off in enumerate(offs):
        wav = OUT / f"{label}_{k}.wav"
        ffmpeg.run(["-y", "-ss", str(off), "-t", "110", "-i", src, "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        import wave, numpy as np
        with wave.open(str(wav)) as f: pcm = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
        segs, _ = model.transcribe(pcm, language="en", word_timestamps=True, vad_filter=True)
        words = [{"text": w.word.strip(), "start": round(w.start + off, 2), "end": round(w.end + off, 2)} for s in segs for w in s.words]
        tf = OUT / f"{label}_{k}.json"; json.dump(words, open(tf, "w"), ensure_ascii=False)
        i = next((i for i in range(1, len(words)) if words[i-1]["text"][-1:] in END and words[i]["start"] > off + 8), None)
        j = next((j for j in range(i or 0, len(words)) if i is not None and words[j]["end"] - words[i]["start"] >= 30 and words[j]["text"][-1:] in END), None) if i is not None else None
        print(label, k, len(words), i, j, flush=True)
        if j is not None and words[j]["end"] - words[i]["start"] <= 55:
            wins.append({"id": f"{label}_{k}", "src": src, "transcript": str(tf), "language": "en", "start_s": words[i]["start"], "end_s": words[j]["end"]})
tf = [p for p in Path(r"L:\Projects\Clipping\.data\storage\transcripts").glob("02c0*.json")][0]
words = json.load(open(tf, encoding="utf8")); src = str(next(Path(r"L:\Projects\Clipping\.data\storage\sources").glob("02c0*.mp4")))
total = words[-1]["end"]
for k in range(4):
    t0 = total * (k + 0.5) / 4 * 0.9
    i = next((i for i in range(1, len(words)) if words[i]["start"] >= t0 and words[i]["start"] - words[i-1]["end"] > 0.5), None)
    j = next((j for j in range(i, len(words) - 1) if words[j]["end"] - words[i]["start"] >= 35 and words[j+1]["start"] - words[j]["end"] > 0.5), None) if i else None
    if j and words[j]["end"] - words[i]["start"] <= 55:
        wins.append({"id": f"comedy_{k}", "src": src, "transcript": str(tf), "language": "ar", "start_s": words[i]["start"], "end_s": words[j]["end"]})
json.dump(wins, open(HERE / "windows.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
print([w["id"] for w in wins])

