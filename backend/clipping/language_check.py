"""Catch a wrong language choice: a long video transcribed in the wrong language comes back almost empty (the 109-minute English
podcast uploaded as Arabic gave a near-empty transcript, see docs/quality-diagnosis.md)."""
from __future__ import annotations

MIN_VIDEO_S = 120           # shorter videos can legitimately be quiet
MIN_WORDS_PER_MIN = 20      # real speech is 100 to 180; under this the language or the audio is wrong


def words_per_minute(words: list[dict], duration_s: float) -> float:
    return len(words) / (duration_s / 60) if duration_s > 0 else 0.0


def empty_transcript_message(words: list[dict], duration_s: float, language: str) -> str | None:
    """A message for the owner when the transcript is far too thin for the video's length, else None."""
    if duration_s < MIN_VIDEO_S:
        return None
    wpm = words_per_minute(words, duration_s)
    if wpm >= MIN_WORDS_PER_MIN:
        return None
    return (f"The transcript has only {len(words)} words for {duration_s / 60:.0f} minutes ({wpm:.0f} per minute). "
            f"The video may be in another language than '{language}'. Check the language and transcribe again.")


SUPPORTED = ("en", "ar")


def _sample(video, at_s: float, seconds: int = 30):
    """16 kHz mono float samples of `seconds` from `at_s`, read straight from the video (no full-length audio file)."""
    import numpy as np
    from . import ffmpeg
    import subprocess
    raw = subprocess.run([ffmpeg.exe(), "-v", "error", "-nostdin", "-ss", f"{at_s:.1f}", "-t", str(seconds), "-i", str(video), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def detect_language(video, duration_s: float | None = None, samples: int = 3) -> str | None:
    """'en' or 'ar' from local Whisper on a few 30 s samples spread over the video (the most common confident answer), or None when
    it cannot be told (no speech, a model error, another language). Used at upload to warn on a wrong choice and after a thin
    transcript to retry in the right language."""
    from . import config
    if config.use_fakes():
        return None          # tests and demos run without the real models
    try:
        import collections
        from . import ffmpeg
        from .adapters.transcription import cuda_available, load_whisper_model
        duration = duration_s or ffmpeg.probe(video)["duration_s"]
        device = "cuda" if cuda_available() else "cpu"
        model = load_whisper_model("large-v3-turbo", device, "float16" if device == "cuda" else "int8")
        votes: collections.Counter = collections.Counter()
        for k in range(samples):
            chunk = _sample(video, duration * (k + 1) / (samples + 1))
            if len(chunk) >= 16000 * 5:
                lang, prob, _ = model.detect_language(chunk)
                if prob >= 0.6:
                    votes[lang] += 1
        return votes.most_common(1)[0][0] if votes else None
    except Exception:
        return None


def mismatch_message(chosen: str, detected: str | None) -> str | None:
    if detected and detected in SUPPORTED and detected != chosen:
        return f"This video sounds like '{detected}', but '{chosen}' is selected. Transcribing in the wrong language gives an empty transcript."
    return None
