"""Laughter and applause per second. Two taggers behind one small interface:

  * SherpaTagger: a local sherpa-onnx audio-tagging model (CED, AudioSet labels), used when the package and the
    model files under .data/cache/models/audio-tagging are present.
  * BurstTagger: the fallback. Laughter and applause are sudden, noisy rises in loudness, so it scores a second by how
    far its loudness jumps above the recent median, weighted by how noise-like the sound is (zero-crossing rate).

A tagger is fed one second of 16 kHz mono int16 samples at a time (`observe`) and, once the loudness series of the
whole file is known, returns one score in 0..1 per second (`finish`). `pick_tagger()` chooses; callers never
import sherpa-onnx themselves."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .. import config

SAMPLE_RATE = 16000
WINDOW_S = 3                 # the model reads three seconds at a time (a model call per second would be too slow)
MODEL_DIR = "models/audio-tagging"
LAUGH_APPLAUSE = ("laughter", "giggle", "chuckle", "belly laugh", "applause", "clapping", "cheering", "crowd")
BURST_RISE_DB = 5.0          # a rise this far above the recent median starts to count
BURST_FULL_DB = 14.0         # ... and counts fully at this rise
MEDIAN_WINDOW_S = 15
NOISE_ZCR = 0.08             # zero-crossing rate above this is noise-like (voiced speech sits well below)
SILENT_LUFS = -50.0


def model_dir() -> Path:
    return config.data_dir() / "cache" / MODEL_DIR


class BurstTagger:
    name = "loudness_burst"

    def __init__(self):
        self.zcr: list[float] = []

    def observe(self, samples) -> None:
        import numpy as np

        signs = np.signbit(samples)
        self.zcr.append(float(np.count_nonzero(signs[1:] != signs[:-1])) / max(len(samples) - 1, 1))

    def finish(self, loudness: list[float]) -> list[float]:
        import numpy as np

        scores = []
        for i, level in enumerate(loudness):
            recent = loudness[max(0, i - MEDIAN_WINDOW_S):i]
            if level <= SILENT_LUFS or not recent:
                scores.append(0.0)
                continue
            rise = level - float(np.median(recent))
            noisy = (self.zcr[i] if i < len(self.zcr) else 0.0) >= NOISE_ZCR
            strength = (rise - BURST_RISE_DB) / (BURST_FULL_DB - BURST_RISE_DB)
            scores.append(round(min(max(strength, 0.0), 1.0), 3) if noisy else 0.0)
        return scores


class SherpaTagger:
    name = "sherpa_onnx"

    def __init__(self, model: Path, labels: Path):
        import sherpa_onnx

        self._lib = sherpa_onnx
        self._tagger = sherpa_onnx.AudioTagging(sherpa_onnx.AudioTaggingConfig(
            model=sherpa_onnx.AudioTaggingModelConfig(ced=str(model), num_threads=2), labels=str(labels), top_k=10))
        self._buffer: list = []
        self.windows: list[float] = []

    def _flush(self) -> None:
        import numpy as np

        if not self._buffer:
            return
        audio = np.concatenate(self._buffer).astype(np.float32) / 32768.0
        seconds = len(self._buffer)
        self._buffer = []
        stream = self._tagger.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        events = self._tagger.compute(stream)
        score = max((float(e.prob) for e in events if str(e.name).lower() in LAUGH_APPLAUSE), default=0.0)
        self.windows += [score] * seconds

    def observe(self, samples) -> None:
        self._buffer.append(samples)
        if len(self._buffer) >= WINDOW_S:
            self._flush()

    def finish(self, loudness: list[float]) -> list[float]:
        self._flush()
        scores = [round(min(max(s, 0.0), 1.0), 3) for s in self.windows[:len(loudness)]]
        return scores + [0.0] * (len(loudness) - len(scores))


def pick_tagger():
    """The model-backed tagger when the package and model files exist, else the loudness-burst fallback."""
    folder = model_dir()
    model, labels = folder / "model.int8.onnx", folder / "class_labels_indices.csv"
    if importlib.util.find_spec("sherpa_onnx") and model.is_file() and labels.is_file():
        try:
            return SherpaTagger(model, labels)
        except Exception:   # a broken install or model file must not stop the signals job
            pass
    return BurstTagger()
