"""Phase D step 1: transcription upgrade. Deepgram Nova-3 request and response, local faster-whisper fallback,
versioned transcripts with speakers and confidence. No test calls a live service, downloads a model or needs a GPU:
Deepgram runs on an httpx MockTransport and Whisper on an injected fake model. One real GPU run is opt-in
(CLIPPING_REAL_WHISPER=1)."""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest

from clipping import alerts, campaigns, config, core, ffmpeg, pipeline, transcripts
from clipping.adapters import transcription
from clipping.adapters.base import AdapterError, activate
from clipping.core import AppError
from clipping.db import Actor
from conftest import approved_campaign

SECRET = "dg-SECRET-KEY-VALUE-0123456789"


# ------------------------------------------------------------------ helpers

def deepgram_response(words=None, utterances=None) -> dict:
    words = words if words is not None else [
        {"word": "hello", "punctuated_word": "Hello", "start": 0.1, "end": 0.4, "confidence": 0.99, "speaker": 0},
        {"word": "there", "punctuated_word": "there.", "start": 0.5, "end": 0.9, "confidence": 0.55, "speaker": 0},
        {"word": "hi", "punctuated_word": "Hi", "start": 1.2, "end": 1.4, "confidence": 0.97, "speaker": 1}]
    utterances = utterances if utterances is not None else [
        {"start": 0.1, "end": 0.9, "speaker": 0, "transcript": "Hello there."},
        {"start": 1.2, "end": 1.4, "speaker": 1, "transcript": "Hi"}]
    return {"results": {"channels": [{"alternatives": [{"words": words}]}], "utterances": utterances}}


def mock_deepgram(monkeypatch, handler) -> list[httpx.Request]:
    """Route every transcription HTTP call through `handler`; returns the list of requests it saw."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(transcription, "http_client", lambda timeout=60: httpx.Client(transport=httpx.MockTransport(respond)))
    return seen


def sent_params(request: httpx.Request) -> list[tuple[str, str]]:
    return list(request.url.params.multi_items())


class FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel: one segment with two words, or whatever `segments` says."""

    def __init__(self, segments=None, fail_with: Exception | None = None):
        self.calls: list[dict] = []
        self.fail_with = fail_with
        self.segments = segments if segments is not None else [SimpleNamespace(
            start=0.0, end=2.0, text=" Welcome to the show ",
            words=[SimpleNamespace(word=" Welcome", start=0.0, end=0.6, probability=0.91),
                   SimpleNamespace(word=" ", start=0.6, end=0.7, probability=0.5),
                   SimpleNamespace(word=" show", start=1.0, end=1.9, probability=0.42)])]

    def transcribe(self, audio_path, **kwargs):
        self.calls.append({"audio_path": audio_path, **kwargs})
        if self.fail_with:
            raise self.fail_with
        return iter(self.segments), SimpleNamespace(language=kwargs["language"])


def use_whisper(monkeypatch, model: FakeWhisperModel, cuda: bool = False) -> list[tuple]:
    """Make faster_whisper resolve to `model`; returns the (name, device, compute_type) of every load."""
    loads: list[tuple] = []

    def load(name, device, compute_type, cpu_threads=0):
        loads.append((name, device, compute_type))
        return model

    monkeypatch.setattr(transcription, "load_whisper_model", load)
    monkeypatch.setattr(transcription, "cuda_available", lambda: cuda)
    return loads


def make_source(db, owner, tmp_path, seconds=6, language="en", dialect=None, key_terms=None, name="Founders Table") -> dict:
    video = ffmpeg.make_fixture(tmp_path / f"clip{seconds}.mp4", seconds=seconds)
    campaign_id = approved_campaign(db, owner, name)
    with db.session(owner) as conn:
        if key_terms:
            conn.execute("update campaigns set key_terms = %s where id = %s", (key_terms, campaign_id))
        source = campaigns.register_source(conn, owner, campaign_id, str(video), language, dialect, permission_ref="P-T9")
    return {"campaign_id": campaign_id, "source": source}


def run_transcribe(db) -> None:
    assert pipeline.process_one(db)   # the transcribe job is the first one queued


def switch_to(db, provider: str) -> None:
    with db.session(Actor.system()) as conn:
        activate(conn, "transcription", provider)


def sample_source(file_hash="h" * 64) -> dict:
    return {"id": None, "owner_operator_id": None, "file_hash": file_hash, "language": "en", "dialect": None}


def words_of(*speakers, confidence=0.9) -> list[dict]:
    return [{"text": f"w{i}", "start": float(i), "end": i + 0.5, "confidence": confidence, "speaker": s}
            for i, s in enumerate(speakers)]


@pytest.fixture
def stored(db, owner):
    """A real transcript (version 1) for one source, created through transcripts.create."""
    with db.session(owner) as conn:
        campaign_id = approved_campaign(db, owner)
        src = conn.execute(
            """insert into source_assets (campaign_id, owner_operator_id, stored_path, file_hash, language, dialect, duration_s,
                   permission_ref) values (%s, %s, 'sources/x.mp4', %s, 'en', null, 20, 'P') returning *""",
            (campaign_id, owner.operator_id, "a" * 64)).fetchone()
        result = {"words": [{"text": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.99, "speaker": "0"},
                            {"text": "there", "start": 0.5, "end": 0.9, "confidence": 0.5, "speaker": "1"}],
                  "provider": "fake", "model": "fake-1", "language_code": "en"}
        transcript = transcripts.create(conn, src, result)
    return SimpleNamespace(db=db, owner=owner, source=src, transcript=transcript)


# ------------------------------------------------------------------ dialect code, key terms, utterances (unit)

@pytest.mark.parametrize("code", sorted(transcription.ARABIC_CODES))
def test_DW_1_1_every_nova3_arabic_code_is_sent_as_given(code):
    assert len(transcription.ARABIC_CODES) == 17
    assert transcription.resolve_language_code("ar", code) == code


def test_dialect_code_is_normalised_for_letter_case():
    assert transcription.resolve_language_code("ar", "AR-sa") == "ar-SA"
    assert transcription.resolve_language_code("en", " en-gb ") == "en-GB"


@pytest.mark.parametrize("language,dialect", [("ar", None), ("ar", ""), ("ar", "en-US"), ("ar", "ar-XX"), ("en", "ar-SA"), ("en", "fr")])
def test_a_dialect_that_does_not_fit_the_language_falls_back_to_the_plain_code(language, dialect):
    assert transcription.resolve_language_code(language, dialect) == language


def test_unsupported_language_is_refused():
    with pytest.raises(ValueError):
        transcription.resolve_language_code("fr", None)


def test_keyterms_over_the_limit_are_cut_to_exactly_n_keeping_the_first():
    terms = [f"term{i}" for i in range(80)]
    kept = transcription.cap_keyterms(terms, 50)
    assert len(kept) == 50 and kept == terms[:50]


def test_keyterm_cap_drops_blanks_duplicates_and_overlong_entries_before_counting():
    terms = ["  Acme   Corp ", "", "   ", "acme corp", "x" * 101, "Zed"]
    assert transcription.cap_keyterms(terms, 10) == ["Acme Corp", "Zed"]
    assert transcription.cap_keyterms(["a", "b", "c"], 2) == ["a", "b"]


def test_keyterm_cap_handles_zero_limit_and_none():
    assert transcription.cap_keyterms(["a"], 0) == [] and transcription.cap_keyterms(None, 5) == []


def test_utterances_split_on_speaker_change_pause_and_sentence_end():
    words = [{"text": "Hello", "start": 0, "end": 0.5, "speaker": "0"}, {"text": "there.", "start": 0.6, "end": 1.0, "speaker": "0"},
             {"text": "Next", "start": 1.1, "end": 1.4, "speaker": "0"}, {"text": "bit", "start": 1.5, "end": 1.8, "speaker": "0"},
             {"text": "reply", "start": 2.0, "end": 2.3, "speaker": "1"}, {"text": "after", "start": 5.0, "end": 5.3, "speaker": "1"}]
    got = transcription.utterances_from_words(words)
    assert [u["text"] for u in got] == ["Hello there.", "Next bit", "reply", "after"]
    assert [u["speaker"] for u in got] == ["0", "0", "1", "1"] and got[1]["start"] == 1.1 and got[1]["end"] == 1.8
    assert transcription.utterances_from_words([]) == []


def test_deepgram_params_rich_and_basic():
    rich = transcription.deepgram_params({"model": "nova-3", "max_keyterms": 2}, "ar", "ar-EG", ["a", "b", "c"])
    assert rich == [("model", "nova-3"), ("language", "ar-EG"), ("punctuate", "true"), ("smart_format", "true"),
                    ("diarize", "true"), ("utterances", "true"), ("keyterm", "a"), ("keyterm", "b")]
    basic = transcription.deepgram_params({}, "en", None, ["a"], rich=False)
    assert basic == [("model", "nova-3"), ("language", "en"), ("punctuate", "true"), ("smart_format", "true")]


def test_parse_deepgram_keeps_confidence_speakers_and_utterances():
    got = transcription.parse_deepgram(deepgram_response(), "en", "nova-3", "deepgram")
    assert [w["text"] for w in got["words"]] == ["Hello", "there.", "Hi"]
    assert got["words"][1]["confidence"] == 0.55 and [w["speaker"] for w in got["words"]] == ["0", "0", "1"]
    assert got["utterances"][1] == {"start": 1.2, "end": 1.4, "speaker": "1", "text": "Hi"}
    assert got["provider"] == "deepgram" and got["language_code"] == "en"


@pytest.mark.parametrize("bad", [{}, {"results": {}}, {"results": {"channels": []}}, [], {"results": {"channels": [{"alternatives": [{"words": [{"word": "x"}]}]}]}}])
def test_parse_deepgram_rejects_an_unexpected_shape(bad):
    with pytest.raises(AdapterError, match="unexpected shape"):
        transcription.parse_deepgram(bad, "en", "nova-3", "deepgram")


def test_parse_deepgram_accepts_no_words():
    assert transcription.parse_deepgram({"results": {"channels": [{"alternatives": [{}]}]}}, "en", "m", "p")["words"] == []


# ------------------------------------------------------------------ Deepgram adapter over a mock transport

def deepgram(price=0.0052, **cfg):
    return transcription.DeepgramTranscription({"config_json": {"model": "nova-3", "price_per_min_usd": price, **cfg}})


@pytest.fixture
def audio(tmp_path) -> str:
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFFfakewavdata")
    return str(path)


def test_DW_1_1_deepgram_adapter_sends_params_and_parses_confidence_and_speakers(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(200, json=deepgram_response()))
    result = deepgram().transcribe(audio, "ar", "ar-SA", 60, ["Acme", "Zed"])
    assert len(seen) == 1
    params = sent_params(seen[0])
    assert ("language", "ar-SA") in params and ("diarize", "true") in params and ("utterances", "true") in params
    assert ("model", "nova-3") in params and [v for k, v in params if k == "keyterm"] == ["Acme", "Zed"]
    assert seen[0].headers["authorization"] == f"Token {SECRET}" and seen[0].content == b"RIFFfakewavdata"
    assert result["language_code"] == "ar-SA" and result["keyterms_sent"] == 2
    assert result["words"][0]["speaker"] == "0" and result["words"][1]["confidence"] == 0.55 and len(result["utterances"]) == 2


def test_deepgram_retries_once_without_keyterms_when_deepgram_refuses_them(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(
        400 if any(k == "keyterm" for k, _ in sent_params(r)) else 200, json=deepgram_response()))
    result = deepgram().transcribe(audio, "en", None, 10, ["Acme"])
    assert len(seen) == 2 and not any(k == "keyterm" for k, _ in sent_params(seen[1])) and result["keyterms_sent"] == 0


def test_deepgram_keyterm_retry_is_not_used_for_other_errors(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(AdapterError, match="HTTP 500"):
        deepgram().transcribe(audio, "en", None, 10, ["Acme"])
    assert len(seen) == 1


def test_deepgram_error_carries_the_status_only_never_the_body(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    mock_deepgram(monkeypatch, lambda r: httpx.Response(401, text=f"bad token {SECRET}"))
    with pytest.raises(AdapterError) as err:
        deepgram().transcribe(audio, "en", None, 10)
    assert str(err.value) == "HTTP 401" and SECRET not in str(err.value)


def test_deepgram_transport_error_never_leaks_into_the_message(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.headers['authorization']}")

    mock_deepgram(monkeypatch, boom)
    with pytest.raises(AdapterError) as err:
        deepgram().transcribe(audio, "en", None, 10)
    assert SECRET not in str(err.value) and err.value.__cause__ is None and "ConnectError" in str(err.value)


def test_deepgram_without_a_key_fails_before_any_request(monkeypatch, audio):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(200, json=deepgram_response()))
    with pytest.raises(AdapterError, match="DEEPGRAM_API_KEY is not set"):
        deepgram().transcribe(audio, "en", None, 10)
    assert seen == []


def test_deepgram_unreadable_audio_is_an_adapter_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    with pytest.raises(AdapterError, match="audio file could not be read"):
        deepgram().transcribe(str(tmp_path / "missing.wav"), "en", None, 10)


def test_deepgram_bad_response_shape_is_an_adapter_error(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    mock_deepgram(monkeypatch, lambda r: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(AdapterError, match="unexpected shape"):
        deepgram().transcribe(audio, "en", None, 10)


def test_deepgram_basic_sends_no_diarize_utterances_or_keyterms(monkeypatch, audio):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(200, json=deepgram_response()))
    transcription.DeepgramBasicTranscription({"config_json": {}}).transcribe(audio, "en", None, 10, ["Acme"])
    assert {k for k, _ in sent_params(seen[0])} == {"model", "language", "punctuate", "smart_format"}


def test_deepgram_cost_units_and_unknown_price():
    assert deepgram().cost({"duration_s": 60}) == Decimal("0.0052") and deepgram().units({"duration_s": 90}) == Decimal("1.50")
    assert transcription.DeepgramTranscription({"config_json": {}}).cost({"duration_s": 60}) is None


def test_deepgram_health_reports_a_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert deepgram().health()["missing_env"] == ["DEEPGRAM_API_KEY"]
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    assert deepgram().health()["ok"] is True


# ------------------------------------------------------------------ faster-whisper adapter (fake model)

def whisper(**cfg):
    return transcription.FasterWhisperTranscription({"config_json": cfg})


def test_whisper_runs_on_cuda_int8_when_cuda_works(monkeypatch, audio):
    model = FakeWhisperModel()
    loads = use_whisper(monkeypatch, model, cuda=True)
    result = whisper().transcribe(audio, "en", None, 5, ["Acme", "Zed"])
    assert loads == [("large-v3-turbo", "cuda", "int8")] and result["device"] == "cuda" and "degraded" not in result
    assert [w["text"] for w in result["words"]] == ["Welcome", "show"]   # the blank word is dropped
    assert result["words"][0]["confidence"] == 0.91 and result["words"][0]["speaker"] is None
    assert result["utterances"] == [{"start": 0.0, "end": 2.0, "speaker": None, "text": "Welcome to the show"}]
    call = model.calls[0]
    assert call["language"] == "en" and call["word_timestamps"] is True and call["initial_prompt"] == "Acme, Zed"


def test_no_gpu_means_cpu_int8_with_a_degraded_note(monkeypatch, audio):
    loads = use_whisper(monkeypatch, FakeWhisperModel(), cuda=False)
    result = whisper().transcribe(audio, "ar", "ar-SA", 5)
    assert loads == [("large-v3-turbo", "cpu", "int8")] and result["device"] == "cpu"
    assert "CPU" in result["degraded"] and "no usable GPU" in result["degraded"]


def test_cuda_failing_while_decoding_falls_back_to_cpu(monkeypatch, audio):
    models = {"cuda": FakeWhisperModel(fail_with=RuntimeError("cublas64_12.dll not found")), "cpu": FakeWhisperModel()}
    loads: list[str] = []
    monkeypatch.setattr(transcription, "load_whisper_model", lambda name, device, ct, threads=0: loads.append(device) or models[device])
    monkeypatch.setattr(transcription, "cuda_available", lambda: True)
    result = whisper().transcribe(audio, "en")
    assert loads == ["cuda", "cpu"] and result["device"] == "cpu" and "the GPU failed" in result["degraded"]


def test_cpu_configured_by_the_owner_is_not_degraded(monkeypatch, audio):
    loads = use_whisper(monkeypatch, FakeWhisperModel(), cuda=True)
    result = whisper(device="cpu").transcribe(audio, "en")
    assert loads[0][1] == "cpu" and "degraded" not in result


def test_whisper_failure_on_the_last_device_is_an_adapter_error(monkeypatch, audio):
    use_whisper(monkeypatch, FakeWhisperModel(fail_with=RuntimeError("out of memory")), cuda=False)
    with pytest.raises(AdapterError, match="Whisper failed on the cpu: RuntimeError: out of memory"):
        whisper().transcribe(audio, "en")


def test_whisper_adapter_errors_pass_through_unchanged(monkeypatch, audio):
    def load(*a, **k):
        raise AdapterError("faster-whisper is not installed")

    monkeypatch.setattr(transcription, "load_whisper_model", load)
    monkeypatch.setattr(transcription, "cuda_available", lambda: True)
    with pytest.raises(AdapterError, match="not installed"):
        whisper().transcribe(audio, "en")


def test_whisper_refuses_other_languages_and_missing_audio(monkeypatch, audio, tmp_path):
    use_whisper(monkeypatch, FakeWhisperModel())
    with pytest.raises(ValueError):
        whisper().transcribe(audio, "fr")
    with pytest.raises(AdapterError, match="audio file not found"):
        whisper().transcribe(str(tmp_path / "none.wav"), "en")


def test_whisper_key_terms_are_capped_and_kept_short_in_the_prompt(monkeypatch, audio):
    model = FakeWhisperModel()
    use_whisper(monkeypatch, model)
    whisper(max_keyterms=3).transcribe(audio, "en", None, 5, [f"t{i}" * 30 for i in range(10)])
    prompt = model.calls[0]["initial_prompt"]
    assert prompt.count(",") == 2 and len(prompt) <= 400
    model2 = FakeWhisperModel()
    use_whisper(monkeypatch, model2)
    whisper().transcribe(audio, "en", None, 5, [])
    assert model2.calls[0]["initial_prompt"] is None


def test_whisper_health_follows_the_package(monkeypatch):
    monkeypatch.setattr(transcription.importlib.util, "find_spec", lambda name: None)
    assert whisper().health() == {"ok": False, "provider": "faster_whisper", "missing_env": ["faster-whisper (pip package)"]}
    monkeypatch.setattr(transcription.importlib.util, "find_spec", lambda name: object())
    assert whisper().health()["ok"] is True


def test_load_whisper_model_without_the_package_is_an_adapter_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)   # makes `import faster_whisper` raise ImportError
    monkeypatch.setattr(transcription, "_MODELS", {})
    with pytest.raises(AdapterError, match="not installed"):
        transcription.load_whisper_model("tiny", "cpu", "int8")


def test_load_whisper_model_keeps_files_under_the_data_cache_and_loads_once(monkeypatch):
    created = []

    class Model:
        def __init__(self, name, **kwargs):
            created.append((name, kwargs))

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(transcription, "_MODELS", {})
    first = transcription.load_whisper_model("tiny", "cpu", "int8")
    assert transcription.load_whisper_model("tiny", "cpu", "int8") is first and len(created) == 1
    cache = config.data_dir() / "cache"
    assert created[0][1]["download_root"] == str(cache / "whisper") and created[0][1]["compute_type"] == "int8"
    assert os.environ["HF_HOME"] == str(cache / "hf")


def test_cuda_available_is_false_when_ctranslate2_cannot_load(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert transcription.cuda_available() is False


def test_cuda_available_follows_the_device_count(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    assert transcription.cuda_available() is True
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 0))
    assert transcription.cuda_available() is False


def test_cuda_dll_folders_come_from_the_nvidia_wheels(monkeypatch, tmp_path):
    (tmp_path / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (tmp_path / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    (tmp_path / "nvidia" / "other").mkdir()
    monkeypatch.setattr(sys, "path", [str(tmp_path), ""])
    assert [p.parent.name for p in transcription._cuda_library_dirs()] == ["cublas", "cudnn"]


@pytest.mark.skipif(sys.platform != "win32", reason="DLL folders are a Windows-only mechanism; faking os.name breaks pathlib elsewhere")
def test_prepare_cuda_dlls_registers_each_folder_once(monkeypatch, tmp_path):
    folder = tmp_path / "nvidia" / "cublas" / "bin"
    folder.mkdir(parents=True)
    registered = []
    monkeypatch.setattr(transcription.os, "name", "nt")
    monkeypatch.setattr(transcription.os, "add_dll_directory", lambda p: registered.append(p) or p, raising=False)
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    monkeypatch.setattr(transcription, "_DLL_DIRS_ADDED", set())
    monkeypatch.setattr(transcription, "_DLL_HANDLES", [])
    monkeypatch.setenv("PATH", "base")
    assert transcription.prepare_cuda_dlls() == 1 and transcription.prepare_cuda_dlls() == 1
    assert registered == [str(folder)] and os.environ["PATH"].startswith(str(folder))


def test_prepare_cuda_dlls_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(transcription.os, "name", "posix")
    assert transcription.prepare_cuda_dlls() == 0


# ------------------------------------------------------------------ the fake provider

def test_fake_words_have_speakers_and_some_low_confidence():
    words = transcription.fake_words("en", 20)
    assert {w["speaker"] for w in words} == {"0", "1"} and min(w["confidence"] for w in words) < 0.8
    assert transcription.fake_words("en", 3) == [] and transcription.fake_words("ar", 10)[0]["text"] == "معظم"


def test_fake_provider_records_the_request_it_would_have_sent():
    result = transcription.FakeTranscription({"config_json": {}}).transcribe("a.wav", "ar", "ar-EG", 10, ["Acme"])
    call = transcription.FAKE_TRANSCRIPTION["calls"][0]
    assert call["language"] == "ar" and ("language", "ar-EG") in call["params"] and ("keyterm", "Acme") in call["params"]
    assert result["language_code"] == "ar-EG" and result["provider"] == "fake" and result["utterances"]


# ------------------------------------------------------------------ transcripts: validation helpers

def test_clean_words_drops_malformed_words_and_clamps_confidence():
    got = transcripts.clean_words([
        {"text": " ok ", "start": "1", "end": 0.5, "confidence": 1.7, "speaker": 3},
        {"text": "", "start": 0, "end": 1}, {"text": "nan", "start": float("nan"), "end": 1}, {"text": "neg", "start": -1, "end": 1},
        {"text": "noend", "start": 1}, {"text": "low", "start": 2, "end": 3, "confidence": -4}, {"text": "blank", "start": 3, "end": 4, "speaker": "  "}])
    assert got == [{"text": "ok", "start": 1.0, "end": 1.0, "confidence": 1.0, "speaker": "3"},
                   {"text": "low", "start": 2.0, "end": 3.0, "confidence": 0.0, "speaker": None},
                   {"text": "blank", "start": 3.0, "end": 4.0, "confidence": None, "speaker": None}]


@pytest.mark.parametrize("bad", ["words", {"a": 1}, None, ["str"], [1]])
def test_clean_words_refuses_the_wrong_container_or_item_type(bad):
    with pytest.raises(AppError) as err:
        transcripts.clean_words(bad)
    assert err.value.code == "bad_words"


def test_speaker_map_names_speakers_in_order_of_appearance():
    words = words_of("1", "0", "1", None)
    assert transcripts.speaker_map(words) == {"1": "Speaker 1", "0": "Speaker 2"}
    assert transcripts.speaker_map(words, {"0": " Host ", "1": ""}) == {"1": "Speaker 1", "0": "Host"}
    assert transcripts.speaker_map(words, {"0": "x" * 200})["0"] == "x" * transcripts.MAX_SPEAKER_NAME
    assert transcripts.speaker_map(words_of(None, None)) == {}
    with pytest.raises(AppError) as err:
        transcripts.speaker_map(words, ["a"])
    assert err.value.code == "bad_speakers"


def test_key_terms_come_owner_first_then_name_then_required_tags():
    campaign = {"name": "Founders Table", "key_terms": ["Acme Cola", "Zed"]}
    version = {"rules_json": {"content_rules": [
        {"text": "Use #FoundersTable and @acmecola in every post", "requirement": "required"},
        {"text": "Avoid #competitor", "requirement": "forbidden"},
        {"text": "Add #ad", "requirement": "required"}]}}
    assert transcripts.key_terms_for(campaign, version) == ["Acme Cola", "Zed", "Founders Table", "FoundersTable", "acmecola"]
    assert transcripts.key_terms_for({"name": "N", "key_terms": None}, {"rules_json": None}) == ["N"]


def test_srt_has_seven_words_per_cue_with_millisecond_times():
    srt = transcripts.to_srt([{"text": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(8)])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:03,400\nw0 w1 w2 w3 w4 w5 w6\n") and "2\n00:00:03,500 --> 00:00:03,900\nw7" in srt
    assert transcripts.to_srt([]) == ""


# ------------------------------------------------------------------ transcripts: storage and versions

def test_DW_1_3_transcript_keeps_speakers_and_confidence(stored):
    with stored.db.session(stored.owner) as conn:
        got = transcripts.load(conn, stored.source["id"])
    assert got["version"] == 1 and got["provider"] == "fake" and got["dialect"] == "en"
    assert got["words"][1] == {"text": "there", "start": 0.5, "end": 0.9, "confidence": 0.5, "speaker": "1"}
    assert got["speakers"] == {"0": "Speaker 1", "1": "Speaker 2"} and [u["speaker"] for u in got["utterances"]] == ["0", "1"]
    assert got["id"] == str(stored.transcript["id"])


def test_DW_1_3_load_returns_the_latest_version(stored):
    edited = [{"text": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.99, "speaker": "0"},
              {"text": "everyone", "start": 0.5, "end": 1.1, "confidence": 1.0, "speaker": "1"}]
    with stored.db.session(stored.owner) as conn:
        assert transcripts.save_version(conn, stored.owner, stored.transcript["id"], edited, {"1": "Guest"}, 1) == 2
        assert transcripts.save_version(conn, stored.owner, stored.transcript["id"], edited[:1], None, 2) == 3
    with stored.db.session(stored.owner) as conn:
        got = transcripts.load(conn, stored.source["id"])
        files = conn.execute("select version_no, word_json_path from transcript_versions order by version_no").fetchall()
    assert got["version"] == 3 and [w["text"] for w in got["words"]] == ["Hello"]
    assert [f["version_no"] for f in files] == [1, 2, 3] and len({f["word_json_path"] for f in files}) == 3


def test_save_version_keeps_old_versions_and_records_speaker_names_and_the_edit(stored):
    edited = [{"text": "Hello", "start": 0.0, "end": 0.4, "speaker": "0"}, {"text": "all", "start": 0.5, "end": 0.9, "speaker": "1"}]
    with stored.db.session(stored.owner) as conn:
        transcripts.save_version(conn, stored.owner, stored.transcript["id"], edited, {"1": "Guest"}, 1)
        v1 = conn.execute("select * from transcript_versions where version_no = 1").fetchone()
        v2 = conn.execute("select * from transcript_versions where version_no = 2").fetchone()
        current = conn.execute("select current_version from transcripts").fetchone()["current_version"]
        logged = conn.execute("select * from audit_log where action = 'transcript.edit'").fetchone()
        old_words = json.loads(alerts.registry.get(conn, "storage").path(v1["word_json_path"]).read_text(encoding="utf-8"))
        loaded = transcripts.load(conn, stored.source["id"])
    assert current == 2 and old_words[1]["text"] == "there" and v1["edited_by"] is None
    assert v2["speakers"] == {"0": "Speaker 1", "1": "Guest"} and str(v2["edited_by"]) == str(stored.owner.operator_id)
    assert logged["detail"] == {"version": 2, "words": 2} and loaded["speakers"]["1"] == "Guest"


def test_save_version_on_a_stale_base_is_refused_and_names_the_current_version(stored):
    words = words_of("0")
    with stored.db.session(stored.owner) as conn:
        transcripts.save_version(conn, stored.owner, stored.transcript["id"], words, None, 1)
        with pytest.raises(transcripts.StaleTranscript) as err:
            transcripts.save_version(conn, stored.owner, stored.transcript["id"], words, None, 1)
    assert err.value.code == "stale" and err.value.version == 2


def test_save_version_refuses_an_empty_or_invalid_edit_and_an_unknown_transcript(stored):
    with stored.db.session(stored.owner) as conn:
        with pytest.raises(AppError) as empty:
            transcripts.save_version(conn, stored.owner, stored.transcript["id"], [{"text": "", "start": 0, "end": 1}], None, 1)
        with pytest.raises(AppError) as bad:
            transcripts.save_version(conn, stored.owner, stored.transcript["id"], "nope", None, 1)
        with pytest.raises(AppError) as missing:
            transcripts.save_version(conn, stored.owner, "00000000-0000-0000-0000-000000000000", words_of("0"), None, 1)
    assert (empty.value.code, bad.value.code, missing.value.code) == ("empty_transcript", "bad_words", "not_found")


def test_load_without_a_transcript_says_so(db, owner):
    with db.session(owner) as conn:
        with pytest.raises(AppError) as err:
            transcripts.load(conn, "00000000-0000-0000-0000-000000000000")
    assert err.value.code == "no_transcript"


def test_create_refuses_an_empty_result_and_stores_nothing(db, owner):
    with db.session(owner) as conn:
        with pytest.raises(AppError) as err:
            transcripts.create(conn, sample_source(), {"words": [{"text": " ", "start": 0, "end": 1}], "provider": "fake"})
        assert err.value.code == "empty_transcript"
        assert conn.execute("select count(*) as n from transcripts").fetchone()["n"] == 0


def test_load_of_an_older_file_without_confidence_or_stored_utterances_derives_them(stored):
    with stored.db.session(stored.owner) as conn:
        key = conn.execute("select word_json_path from transcript_versions").fetchone()["word_json_path"]
        old = [{"text": "Hello", "start": 0, "end": 0.4}, {"text": "there.", "start": 0.5, "end": 0.9}]
        alerts.registry.get(conn, "storage").path(key).write_text(json.dumps(old), encoding="utf-8")
        conn.execute("update transcript_versions set utterances = '[]', speakers = '{}'")
        got = transcripts.load(conn, stored.source["id"])
    assert got["words"][0]["confidence"] is None and got["words"][0]["speaker"] is None
    assert got["utterances"] == [{"start": 0, "end": 0.9, "speaker": None, "text": "Hello there."}] and got["speakers"] == {}


def test_find_matches_on_file_language_and_dialect(stored):
    with stored.db.session(stored.owner) as conn:
        same = {"file_hash": "a" * 64, "language": "en", "dialect": None}
        assert transcripts.find(conn, same)["id"] == stored.transcript["id"]
        assert transcripts.find(conn, {**same, "dialect": "en-GB"}) is None and transcripts.find(conn, {**same, "language": "ar"}) is None


def test_transcript_versions_table_has_row_level_security(db):
    with db.session(Actor.system()) as conn:
        row = conn.execute("select relrowsecurity as on_, relforcerowsecurity as forced from pg_class where relname = 'transcript_versions'").fetchone()
        policies = conn.execute("select count(*) as n from pg_policies where tablename = 'transcript_versions'").fetchone()["n"]
    assert row["on_"] and row["forced"] and policies == 5


def test_migration_seeds_whisper_as_deepgrams_fallback_and_keeps_the_basic_call(db):
    with db.session(Actor.system()) as conn:
        rows = {r["provider"]: r for r in conn.execute("select * from adapters where stage = 'transcription'").fetchall()}
    assert rows["deepgram"]["fallback_provider"] == "faster_whisper" and rows["deepgram"]["config_json"]["max_keyterms"] == 50
    assert rows["faster_whisper"]["config_json"]["compute_type"] == "int8" and rows["deepgram_basic"]["fallback_provider"] == "faster_whisper"
    assert not rows["faster_whisper"]["active"] and not rows["deepgram_basic"]["active"]


# ------------------------------------------------------------------ stage_transcribe (integration)

def test_DW_1_1_fake_records_the_same_request_the_deepgram_adapter_sends(db, owner, tmp_path):
    owner_terms = [f"Owner term {i}" for i in range(60)]
    made = make_source(db, owner, tmp_path, language="ar", dialect="ar-SA", key_terms=owner_terms)
    run_transcribe(db)
    call = transcription.FAKE_TRANSCRIPTION["calls"][0]
    terms = [v for k, v in call["params"] if k == "keyterm"]
    assert ("language", "ar-SA") in call["params"] and ("diarize", "true") in call["params"] and ("utterances", "true") in call["params"]
    assert len(terms) == 50 and terms == owner_terms[:50]   # the owner's terms come first and the list is cut to exactly N
    with db.session(owner) as conn:
        got = transcripts.load(conn, made["source"]["id"])
        row = conn.execute("select * from transcripts").fetchone()
    assert row["language_code"] == "ar-SA" and got["dialect"] == "ar-SA" and got["speakers"]


def test_campaign_name_and_required_tags_reach_the_request_after_the_owners_terms(db, owner, tmp_path):
    make_source(db, owner, tmp_path, key_terms=["Acme Cola"])
    run_transcribe(db)
    terms = [v for k, v in transcription.FAKE_TRANSCRIPTION["calls"][0]["params"] if k == "keyterm"]
    assert terms[:2] == ["Acme Cola", "Founders Table"]


def test_DW_1_2_three_deepgram_failures_fall_back_to_faster_whisper(db, owner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    seen = mock_deepgram(monkeypatch, lambda r: httpx.Response(500, text="server error"))
    model = FakeWhisperModel()
    use_whisper(monkeypatch, model, cuda=True)
    switch_to(db, "deepgram")
    made = make_source(db, owner, tmp_path)
    run_transcribe(db)
    with db.session(owner) as conn:
        row = conn.execute("select * from transcripts").fetchone()
        got = transcripts.load(conn, made["source"]["id"])
        failed_alert = conn.execute("select * from alerts where type = 'adapter_failed'").fetchone()
        job = conn.execute("select status from jobs where type = 'transcribe'").fetchone()
    assert len(seen) == 3 and len(model.calls) == 1   # three Deepgram attempts, then the local model
    assert row["provider"] == "faster_whisper" and [w["text"] for w in got["words"]] == ["Welcome", "show"]
    assert job["status"] == "done" and "faster_whisper" in failed_alert["message"]
    assert model.calls[0]["initial_prompt"].startswith("Founders Table")


def test_DW_1_2_cap_pause_uses_local_whisper_and_alerts(db, owner, tmp_path, monkeypatch):
    use_whisper(monkeypatch, FakeWhisperModel(), cuda=False)
    switch_to(db, "deepgram")
    with db.session(Actor.system()) as conn:
        conn.execute("update adapters set paused_reason = 'monthly cap of $10 reached' where provider = 'deepgram'")
    make_source(db, owner, tmp_path)
    run_transcribe(db)
    with db.session(owner) as conn:
        provider = conn.execute("select provider from transcripts").fetchone()["provider"]
        types = {a["type"] for a in conn.execute("select type from alerts").fetchall()}
    assert provider == "faster_whisper" and {"transcription_paused", "transcription_degraded"} <= types


def test_stage_raises_no_degraded_alert_when_the_gpu_ran(db, owner, tmp_path, monkeypatch):
    use_whisper(monkeypatch, FakeWhisperModel(), cuda=True)
    switch_to(db, "faster_whisper")
    make_source(db, owner, tmp_path)
    run_transcribe(db)
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from alerts where type = 'transcription_degraded'").fetchone()["n"] == 0
        assert conn.execute("select provider from transcripts").fetchone()["provider"] == "faster_whisper"


def test_empty_audio_fails_the_job_with_empty_transcript_and_stores_nothing(db, owner, tmp_path):
    make_source(db, owner, tmp_path, seconds=3)   # the fake finds no speech in 3 s
    run_transcribe(db)
    with db.session(owner) as conn:
        job = conn.execute("select status, error from jobs where type = 'transcribe'").fetchone()
        assert conn.execute("select count(*) as n from transcripts").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from jobs where type = 'moments'").fetchone()["n"] == 0
    assert job["status"] == "failed" and "transcript is empty" in job["error"]


def test_stage_transcribe_leaves_no_audio_file_behind(db, owner, tmp_path):
    make_source(db, owner, tmp_path)
    run_transcribe(db)
    assert list((config.data_dir() / "storage").rglob("*.wav")) == [] and list((config.data_dir() / "tmp").rglob("audio.wav")) == []


def test_cached_transcript_skips_the_provider_for_a_second_campaign(db, owner, tmp_path):
    video = ffmpeg.make_fixture(tmp_path / "same.mp4", seconds=6)
    for name in ("One", "Two"):
        campaign_id = approved_campaign(db, owner, name)
        with db.session(owner) as conn:
            campaigns.register_source(conn, owner, campaign_id, str(video), "en", permission_ref=f"P-{name}")
        pipeline.run_until_idle(db)
    assert len(transcription.FAKE_TRANSCRIPTION["calls"]) == 1
    with db.session(owner) as conn:
        assert conn.execute("select count(*) as n from transcripts").fetchone()["n"] == 1
        second = conn.execute("select id from source_assets order by created_at desc limit 1").fetchone()["id"]
        assert transcripts.load(conn, second)["version"] == 1   # found by file and language


# ------------------------------------------------------------------ DW-1.4: no secret anywhere after a failed Deepgram call

def _everything_stored(db) -> str:
    """All rows of every table in the database as text (superuser connection, so row-level security hides nothing)."""
    with psycopg.connect(db.dsn) as conn:
        tables = [r[0] for r in conn.execute("select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE'")]
        return "\n".join(str(row[0]) for table in tables for row in conn.execute(f'select t::text from "{table}" t'))


@pytest.mark.parametrize("failure", ["status_body_echoes_key", "transport_error_echoes_key"])
def test_DW_1_4_failed_deepgram_call_leaves_no_key_anywhere(db, owner, tmp_path, monkeypatch, failure):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)

    def handler(request):
        assert SECRET in request.headers["authorization"]   # the key really was on the wire, so the scan is meaningful
        if failure == "transport_error_echoes_key":
            raise httpx.ConnectError(f"cannot reach host with {request.headers['authorization']}")
        return httpx.Response(401, text=f"invalid credentials {SECRET}")

    seen = mock_deepgram(monkeypatch, handler)
    use_whisper(monkeypatch, FakeWhisperModel(fail_with=RuntimeError(f"also broken {SECRET}")), cuda=False)   # both providers fail
    switch_to(db, "deepgram")
    make_source(db, owner, tmp_path)
    run_transcribe(db)
    with db.session(owner) as conn:
        job = conn.execute("select status, error from jobs where type = 'transcribe'").fetchone()
    # the failure path really ran: three Deepgram attempts, then the local model failed too and the job stopped
    assert len(seen) == 3 and job["status"] == "failed" and "faster_whisper failed" in job["error"]
    assert SECRET not in _everything_stored(db)


def test_DW_1_4_key_never_appears_in_the_job_error_of_a_basic_failure(db, owner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", SECRET)
    mock_deepgram(monkeypatch, lambda r: httpx.Response(403, text=SECRET))
    use_whisper(monkeypatch, FakeWhisperModel(), cuda=True)
    switch_to(db, "deepgram")
    make_source(db, owner, tmp_path)
    run_transcribe(db)
    assert SECRET not in _everything_stored(db)


# ------------------------------------------------------------------ the pipeline readers still work on the new store

def test_pipeline_words_reads_the_latest_version(stored):
    edited = [{"text": "Edited", "start": 0.0, "end": 0.5, "confidence": 1.0, "speaker": "0"}]
    with stored.db.session(stored.owner) as conn:
        transcripts.save_version(conn, stored.owner, stored.transcript["id"], edited, None, 1)
        assert [w["text"] for w in pipeline._words(conn, stored.source)] == ["Edited"]


# ------------------------------------------------------------------ opt-in: the real model on this laptop's GPU

@pytest.mark.skipif(os.environ.get("CLIPPING_REAL_WHISPER") != "1", reason="set CLIPPING_REAL_WHISPER=1 to run the real GPU model")
def test_DW_1_2_real_whisper_runs_on_the_60s_fixture(footage, tmp_path, monkeypatch):
    real_data = Path(__file__).resolve().parents[2] / ".data"
    monkeypatch.setenv("CLIPPING_DATA_DIR", str(real_data))   # the model files live under L:\Projects\Clipping\.data\cache
    wav = ffmpeg.extract_audio(Path(footage), tmp_path / "a.wav")
    result = whisper().transcribe(str(wav), "en", None, 60, ["Acme"])
    assert result["device"] in ("cuda", "cpu") and result["provider"] == "faster_whisper"   # a test tone: 0 words is fine
    assert (real_data / "cache" / "whisper").exists()
    print("REAL_WHISPER", result["device"], result.get("degraded"), len(result["words"]))


def test_read_wav_gives_whisper_a_float_array_for_16k_mono_and_a_path_for_anything_else(tmp_path):
    import wave

    def write(name, rate, channels, frames=1600):
        path = tmp_path / name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(channels), out.setsampwidth(2), out.setframerate(rate)
            out.writeframes((b"\x00\x40" * channels) * frames)
        return str(path)

    good = transcription.read_wav(write("ok.wav", 16000, 1))
    assert len(good) == 1600 and good.dtype.name == "float32" and abs(float(good[0]) - 0.5) < 1e-6
    for odd in (write("stereo.wav", 16000, 2), write("rate.wav", 44100, 1)):
        assert transcription.read_wav(odd) == odd
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav")
    assert transcription.read_wav(str(junk)) == str(junk)
