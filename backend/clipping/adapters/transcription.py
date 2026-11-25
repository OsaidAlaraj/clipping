"""Transcription adapters (A3): Deepgram Nova-3, the original basic Deepgram call, local faster-whisper
and a fake. Every provider takes the same request and returns the same shape:

    transcribe(audio_path, language, dialect_hint=None, duration_s=0, keyterms=()) ->
        {"words": [{text, start, end, confidence, speaker}], "utterances": [{start, end, speaker, text}],
         "provider", "model", "language_code", "device"?, "degraded"?}

Everything vendor-specific stays in here: the dialect code, the key-term limit, request parameters, the response
format, CUDA library loading and the GPU to CPU fallback. Persistence belongs to transcripts.py."""
from __future__ import annotations

import importlib.util
import os
import sys
import wave
from decimal import Decimal
from pathlib import Path

from .. import config, job_progress
from .base import Adapter, AdapterError, check_response, http_client, register

SENTENCE_END = ".?!؟"

# Deepgram Nova-3 Arabic: the plain code detects the dialect; the regional codes pin it (Deepgram changelog 2026-01-27).
ARABIC_CODES = frozenset({"ar", "ar-AE", "ar-SA", "ar-QA", "ar-KW", "ar-SY", "ar-LB", "ar-PS", "ar-JO", "ar-EG",
                          "ar-SD", "ar-MA", "ar-DZ", "ar-TN", "ar-IQ", "ar-TD", "ar-IR"})
ENGLISH_CODES = frozenset({"en", "en-US", "en-GB", "en-AU", "en-IN", "en-NZ"})
LANGUAGE_CODES = {"ar": ARABIC_CODES, "en": ENGLISH_CODES}

DEFAULT_MAX_KEYTERMS = 50
MAX_KEYTERM_CHARS = 100


# ------------------------------------------------------------------ shared helpers

def resolve_language_code(language: str, dialect: str | None) -> str:
    """The code to send: the dialect when it fits the language (any letter case), else the plain language code."""
    codes = LANGUAGE_CODES.get(language)
    if codes is None:
        raise ValueError("language must be en or ar")
    if dialect:
        head, _, tail = dialect.strip().partition("-")
        candidate = head.lower() + (f"-{tail.upper()}" if tail else "")
        if candidate in codes:
            return candidate
    return language


def cap_keyterms(terms, limit: int) -> list[str]:
    """Key terms in priority order, trimmed, without blanks, overlong entries or case-insensitive duplicates,
    cut to exactly `limit` when there are more."""
    kept: list[str] = []
    seen: set[str] = set()
    for term in terms or ():
        text = " ".join(str(term).split())
        if limit <= 0 or len(kept) >= limit:
            break
        if text and len(text) <= MAX_KEYTERM_CHARS and text.casefold() not in seen:
            seen.add(text.casefold())
            kept.append(text)
    return kept


def utterances_from_words(words: list[dict], gap_s: float = 1.0) -> list[dict]:
    """Group words into utterances: a new one starts at a speaker change, a pause longer than gap_s or a sentence end."""
    groups: list[list[dict]] = []
    for word in words:
        last = groups[-1][-1] if groups else None
        if (last is not None and last.get("speaker") == word.get("speaker") and word["start"] - last["end"] <= gap_s
                and last["text"][-1:] not in SENTENCE_END):
            groups[-1].append(word)
        else:
            groups.append([word])
    return [{"start": g[0]["start"], "end": g[-1]["end"], "speaker": g[0].get("speaker"),
             "text": " ".join(w["text"] for w in g)} for g in groups]


def _price_per_minute(adapter: Adapter, request: dict) -> Decimal | None:
    price = adapter.cfg.get("price_per_min_usd")
    if price is None:
        return None
    return (Decimal(str(price)) * Decimal(str(request.get("duration_s", 0))) / 60).quantize(Decimal("0.0001"))


# ------------------------------------------------------------------ Deepgram

def deepgram_params(cfg: dict, language: str, dialect_hint: str | None, keyterms, rich: bool = True) -> list[tuple[str, str]]:
    """Query parameters for /v1/listen. Repeated `keyterm` entries carry the campaign's own words."""
    params = [("model", cfg.get("model", "nova-3")), ("language", resolve_language_code(language, dialect_hint)),
              ("punctuate", "true"), ("smart_format", "true")]
    if rich:
        params += [("diarize", "true"), ("utterances", "true")]
        params += [("keyterm", term) for term in cap_keyterms(keyterms, int(cfg.get("max_keyterms", DEFAULT_MAX_KEYTERMS)))]
    return params


def parse_deepgram(data: dict, language_code: str, model: str, provider: str) -> dict:
    """Words with confidence and speaker, and Deepgram's own utterances, from a /v1/listen response."""
    try:
        results = data["results"]
        raw_words = results["channels"][0]["alternatives"][0].get("words") or []
        words = [{"text": w.get("punctuated_word") or w["word"], "start": float(w["start"]), "end": float(w["end"]),
                  "confidence": w.get("confidence"), "speaker": None if w.get("speaker") is None else str(w["speaker"])}
                 for w in raw_words]
        utterances = [{"start": float(u["start"]), "end": float(u["end"]),
                       "speaker": None if u.get("speaker") is None else str(u["speaker"]), "text": u["transcript"]}
                      for u in results.get("utterances") or []]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise AdapterError("Deepgram returned a response in an unexpected shape") from None
    return {"words": words, "utterances": utterances, "provider": provider, "model": model, "language_code": language_code}


@register
class DeepgramTranscription(Adapter):
    stage, provider = "transcription", "deepgram"
    requires_env = ("DEEPGRAM_API_KEY",)
    URL = "https://api.deepgram.com/v1/listen"
    rich = True

    def cost(self, request: dict) -> Decimal | None:
        return _price_per_minute(self, request)

    def units(self, request: dict) -> Decimal:
        return (Decimal(str(request.get("duration_s", 0))) / 60).quantize(Decimal("0.01"))

    def _send(self, audio: bytes, params: list[tuple[str, str]]):
        try:
            with http_client(timeout=600) as client:
                return client.post(self.URL, params=params, content=audio, headers={
                    "Authorization": f"Token {config.env('DEEPGRAM_API_KEY', '')}", "Content-Type": "audio/wav"})
        except Exception as exc:  # transport errors can carry the request: never let them into a stored message
            raise AdapterError(f"Deepgram request failed: {type(exc).__name__}") from None

    def transcribe(self, audio_path: str, language: str, dialect_hint: str | None = None, duration_s: float = 0,
                   keyterms=()) -> dict:
        params = deepgram_params(self.cfg, language, dialect_hint, keyterms, self.rich)
        if not config.env("DEEPGRAM_API_KEY"):
            raise AdapterError("DEEPGRAM_API_KEY is not set")
        try:
            audio = Path(audio_path).read_bytes()
        except OSError:
            raise AdapterError("audio file could not be read") from None
        response = self._send(audio, params)
        if response.status_code == 400 and any(name == "keyterm" for name, _ in params):
            # Key terms are optional: if the language or model refuses them, transcribe without.
            params = [(name, value) for name, value in params if name != "keyterm"]
            response = self._send(audio, params)
        values = dict(params)
        result = parse_deepgram(check_response(response), values["language"], values["model"], self.provider)
        result["keyterms_sent"] = sum(1 for name, _ in params if name == "keyterm")
        return result


@register
class DeepgramBasicTranscription(DeepgramTranscription):
    """The first Deepgram call (language only, no speakers or key terms). Kept switchable until the rich call is proven."""
    provider = "deepgram_basic"
    rich = False


# ------------------------------------------------------------------ local faster-whisper

_DLL_DIRS_ADDED: set[str] = set()
_DLL_HANDLES: list = []     # os.add_dll_directory handles must stay alive
_MODELS: dict[tuple, object] = {}


def _cuda_library_dirs() -> list[Path]:
    """Folders of the nvidia-* pip wheels (cuBLAS, cuDNN) that hold the CUDA DLLs."""
    return [d for root in sys.path if root for d in sorted((Path(root) / "nvidia").glob("*/bin")) if d.is_dir()]


def prepare_cuda_dlls() -> int:
    """Make the pip-installed CUDA DLLs loadable before ctranslate2 starts (Windows needs them registered).
    Returns how many folders were registered."""
    if os.name != "nt":
        return 0
    for folder in _cuda_library_dirs():
        key = str(folder)
        if key not in _DLL_DIRS_ADDED:
            _DLL_HANDLES.append(os.add_dll_directory(key))
            os.environ["PATH"] = key + os.pathsep + os.environ.get("PATH", "")
            _DLL_DIRS_ADDED.add(key)
    return len(_DLL_DIRS_ADDED)


def cuda_available() -> bool:
    prepare_cuda_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # not installed, or the DLLs do not load: the CPU path covers it
        return False


def load_whisper_model(name: str, device: str, compute_type: str, cpu_threads: int = 0):
    """One loaded model per (name, device, compute type). Model files and caches live under <data dir>/cache (owner's rule)."""
    key = (name, device, compute_type)
    if key not in _MODELS:
        cache = config.data_dir() / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache / "hf")
        if device == "cuda":
            prepare_cuda_dlls()
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise AdapterError("faster-whisper is not installed") from None
        _MODELS[key] = WhisperModel(name, device=device, compute_type=compute_type, cpu_threads=cpu_threads,
                                    download_root=str(cache / "whisper"))
    return _MODELS[key]


def read_wav(path: str):
    """The 16 kHz mono wav that ffmpeg wrote, as the float array Whisper takes. Passing the array skips faster-whisper's
    own PyAV decoder (PyAV 19 breaks faster-whisper 1.2.1). Anything that is not such a wav goes in as a path."""
    try:
        with wave.open(path, "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                return path
            import numpy as np

            return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, EOFError):
        return path


@register
class FasterWhisperTranscription(Adapter):
    """Local Whisper: the declared fallback for Deepgram. Free, private, no speaker labels."""
    stage, provider = "transcription", "faster_whisper"

    def health(self) -> dict:
        ok = importlib.util.find_spec("faster_whisper") is not None
        return {"ok": ok, "provider": self.provider, "missing_env": [] if ok else ["faster-whisper (pip package)"]}

    def _devices(self) -> tuple[list[str], str | None]:
        """Devices to try in order, and why the GPU is not among them (None when the owner chose the CPU)."""
        wanted = self.cfg.get("device", "auto")
        if wanted == "cpu":
            return ["cpu"], None
        if cuda_available():
            return ["cuda", "cpu"], None
        return ["cpu"], "no usable GPU or CUDA libraries"

    def _run(self, device: str, audio_path: str, language: str, keyterms: list[str], duration_s: float = 0) -> dict:
        name = self.cfg.get("model", "large-v3-turbo")
        model = load_whisper_model(name, device, self.cfg.get("compute_type", "int8"), int(self.cfg.get("cpu_threads", 0)))
        prompt = ", ".join(keyterms)[:400] or None   # key terms bias the decoder toward the campaign's own words
        segments, _info = model.transcribe(read_wav(audio_path), language=language, word_timestamps=True, vad_filter=True,
                                           condition_on_previous_text=False, initial_prompt=prompt,
                                           beam_size=int(self.cfg.get("beam_size", 5)))
        words, utterances = [], []
        for segment in segments:   # a lazy generator: CUDA errors can surface here
            job_progress.report(float(segment.end), duration_s, "seconds of audio")   # real: how far into the audio Whisper has got
            utterances.append({"start": float(segment.start), "end": float(segment.end), "speaker": None, "text": segment.text.strip()})
            words += [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end),
                       "confidence": float(w.probability), "speaker": None}
                      for w in segment.words or [] if w.word.strip()]
        return {"words": words, "utterances": utterances, "provider": self.provider, "model": name,
                "language_code": language, "device": device}

    def transcribe(self, audio_path: str, language: str, dialect_hint: str | None = None, duration_s: float = 0,
                   keyterms=()) -> dict:
        resolve_language_code(language, None)   # rejects languages other than en and ar
        if not Path(audio_path).is_file():
            raise AdapterError("audio file not found")
        terms = cap_keyterms(keyterms, int(self.cfg.get("max_keyterms", 20)))
        devices, degraded = self._devices()
        for device in devices:
            try:
                result = self._run(device, str(audio_path), language, terms, duration_s)
            except AdapterError:
                raise
            except Exception as exc:
                if device == devices[-1]:
                    raise AdapterError(f"Whisper failed on the {device}: {type(exc).__name__}: {str(exc)[:160]}") from None
                degraded = f"the GPU failed ({type(exc).__name__}: {str(exc)[:120]})"
                continue
            if device == "cpu" and degraded:
                result["degraded"] = f"Whisper ran on the CPU because {degraded}"
            return result
        raise AdapterError("no device to run Whisper on")   # unreachable: devices always ends with the CPU


# ------------------------------------------------------------------ fake

FAKE_SENTENCES = {
    "en": [
        "Most people think success comes from talent.",
        "That is completely wrong.",
        "I lost fifty thousand dollars in my first year.",
        "Here is what nobody tells you about starting out.",
        "You only need three habits and nothing else.",
        "Why do we keep making the same mistake?",
        "The day I quit my job changed everything.",
        "If you ignore this you will pay for it later.",
        "Seven out of ten founders never learn this lesson.",
        "So I picked up the phone and made one call.",
        "Everyone told me the idea would fail.",
        "What would you do with one more hour a day?",
    ],
    "ar": [
        "معظم الناس يعتقدون أن النجاح يأتي من الموهبة.",
        "هذا غير صحيح تماماً.",
        "خسرت خمسين ألف دولار في سنتي الأولى.",
        "إليكم ما لا يخبركم به أحد عن البداية.",
        "تحتاج إلى ثلاث عادات فقط ولا شيء غيرها.",
        "لماذا نكرر الخطأ نفسه دائماً؟",
        "اليوم الذي تركت فيه وظيفتي غيّر كل شيء.",
        "إذا تجاهلت هذا فسوف تدفع الثمن لاحقاً.",
        "سبعة من كل عشرة مؤسسين لا يتعلمون هذا الدرس.",
        "فأمسكت الهاتف وأجريت مكالمة واحدة.",
        "قال لي الجميع إن الفكرة ستفشل.",
        "ماذا ستفعل لو كان لديك ساعة إضافية كل يوم؟",
    ],
}


def fake_words(language: str, duration_s: float) -> list[dict]:
    """A sentence every five seconds: 4.2 s of speech, 0.8 s of silence (matches the fixture's tone).
    Two speakers take turns; every ninth word has low confidence, so review queues have something to find."""
    words: list[dict] = []
    sentences = FAKE_SENTENCES[language]
    slot = 0
    while (slot + 1) * 5 <= duration_s + 0.01:
        tokens = sentences[slot % len(sentences)].split()
        step = 4.2 / len(tokens)
        for i, token in enumerate(tokens):
            start = slot * 5 + i * step
            words.append({"text": token, "start": round(start, 3), "end": round(start + step * 0.92, 3),
                          "confidence": 0.6 if len(words) % 9 == 8 else 0.98, "speaker": str(slot % 2)})
        slot += 1
    return words


FAKE_TRANSCRIPTION: dict = {"calls": []}   # every request the fake received: the Deepgram parameters it would have sent


@register
class FakeTranscription(Adapter):
    stage, provider = "transcription", "fake"

    def transcribe(self, audio_path: str, language: str, dialect_hint: str | None = None, duration_s: float = 0,
                   keyterms=()) -> dict:
        params = deepgram_params(self.cfg, language, dialect_hint, keyterms)
        FAKE_TRANSCRIPTION["calls"].append({"audio_path": audio_path, "language": language, "dialect_hint": dialect_hint,
                                            "duration_s": duration_s, "params": params})
        words = fake_words(language, duration_s)
        return {"words": words, "utterances": utterances_from_words(words), "provider": "fake", "model": "fake-1",
                "language_code": dict(params)["language"]}
