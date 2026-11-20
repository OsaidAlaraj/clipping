"""Focused behaviour tests for small helper modules without external services."""
from __future__ import annotations

import pytest

from clipping.language_check import empty_transcript_message, mismatch_message, words_per_minute
from clipping.router import allocate, floored


def test_router_shares_are_normalized_and_respect_the_floor():
    shares = floored({"strong": 9.0, "weak": 0.1, "new": 0.1}, 0.1)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert min(shares.values()) >= 0.1
    assert allocate(shares, slots=24)["weak"] >= 1


def test_language_check_ignores_short_or_sufficient_transcripts():
    assert words_per_minute([{"text": "a"}] * 30, 60) == 30
    assert empty_transcript_message([], 119, "en") is None
    assert empty_transcript_message([{"text": "a"}] * 40, 120, "en") is None
    message = empty_transcript_message([{"text": "a"}] * 10, 600, "ar")
    assert message and "10 words" in message and "'ar'" in message


def test_language_mismatch_is_reported_only_for_supported_different_languages():
    assert mismatch_message("en", "ar") and mismatch_message("en", "ar").startswith("This video sounds like")
    assert mismatch_message("en", "en") is None
    assert mismatch_message("en", "fr") is None
