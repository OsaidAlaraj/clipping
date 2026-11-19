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


def detect_language(audio_path: str, samples: int = 3) -> str | None:
    """Language code from local Whisper on a few 30 s samples (the most common answer), or None when it cannot be told.
    Used at upload to pre-select the language and warn when the owner's choice disagrees."""
    try:
        from .adapters.transcription import load_whisper_model, read_wav
        import collections
        audio = read_wav(audio_path)
        model = load_whisper_model("small", "auto", "int8")
        rate, span = 16000, 30 * 16000
        votes = collections.Counter()
        for k in range(samples):
            at = int((k + 1) * len(audio) / (samples + 1))
            chunk = audio[at:at + span]
            if len(chunk) >= rate * 5:
                lang, prob, _ = model.detect_language(chunk)
                if prob >= 0.5:
                    votes[lang] += 1
        return votes.most_common(1)[0][0] if votes else None
    except Exception:
        return None
