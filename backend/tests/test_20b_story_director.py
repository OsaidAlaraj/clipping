"""The Story Director: the plan (what to do and when to leave a clip alone) and its execution in the one encode."""
from __future__ import annotations

from pathlib import Path

import pytest

from clipping import polish, story_director
from test_20_polish import CFG, PLAIN, beep_source, beep_times, clip_for, flash_times, render, w  # noqa: F401


def talk(sentences: list[str], each: float = 4.0, gap: float = 0.3) -> list[dict]:
    """Words of consecutive sentences, `each` seconds long, with a pause between."""
    out, t = [], 0.0
    for s in sentences:
        tokens = s.split()
        step = (each - gap) / len(tokens)
        out += [{"text": tok, "start": round(t + i * step, 3), "end": round(t + i * step + step * 0.9, 3)} for i, tok in enumerate(tokens)]
        t += each
    return out


SLOW = ["So yeah well it was a thing that happened.", "And then we sort of kind of went along with it.", "It was just a normal day for everybody there.",
        "Nobody told us the truth, we lost 40 million dollars!", "That was the biggest mistake of my life.", "Anyway.", "Thanks for watching."]


def test_a_slow_opening_gets_a_cold_open_from_a_strong_later_line_within_the_length_limit():
    words = talk(SLOW, each=4.0)
    plan = story_director.plan(words, 0.0, 28.0, "en", "podcast", 60.0)
    cold = plan["cold_open"]
    assert cold and "40 million" in cold["text"] and "cold_open" in plan["techniques"]
    assert cold["clip"][1] - cold["clip"][0] <= 4.0 and plan["why"] and plan["why_ar"]
    assert plan["ending_trim_s"] > 0 and "ending_trim" in plan["techniques"]          # "Thanks for watching." is trimmed


def test_no_cold_open_when_the_clip_already_opens_strongly_or_would_break_the_length_limit_or_for_gaming():
    strong = ["Nobody told us the truth, we lost 40 million dollars!"] + SLOW[:4] + ["It ended there."] * 2
    assert story_director.plan(talk(strong), 0.0, 28.0, "en", "podcast", 60.0)["cold_open"] is None
    assert story_director.plan(talk(SLOW), 0.0, 28.0, "en", "podcast", 28.5)["cold_open"] is None      # 28 s + teaser > 28.5 s
    assert story_director.plan(talk(SLOW), 0.0, 28.0, "en", "gaming", 60.0)["cold_open"] is None


def test_the_plan_never_invents_words_the_teaser_is_the_clips_own_sentence():
    words = talk(SLOW)
    cold = story_director.plan(words, 0.0, 28.0, "en", "podcast", 60.0)["cold_open"]
    said = " ".join(x["text"] for x in words)
    assert cold["text"] in said


def test_plan_maps_words_with_a_cold_open_first_then_the_clip_shifted_by_its_length():
    words = [w("a.", 0.0, 1.0), w("b.", 2.0, 3.0), w("c.", 4.0, 5.0), w("d.", 6.0, 7.0)]
    plan = polish.with_story(None, 8.0, cold=(4.0, 5.0), trim=1.0)
    shown = plan.words(words, 0.0, 8.0)
    assert [x["text"] for x in shown] == ["c.", "a.", "b.", "c.", "d."] and shown[0]["start"] == 0.0
    assert plan.new_duration == pytest.approx(8.0 - 1.0 + 1.0) and shown[1]["start"] == pytest.approx(1.0)
    assert plan.keeps[0] == (4.0, 5.0) and plan.keeps[-1][1] == 7.0


def test_cold_open_render_plays_the_teaser_first_then_the_whole_clip(tmp_path):
    src = beep_source(tmp_path)                                    # a flash and a beep together at 0.9, 2.9, 4.9, 6.9 s
    story = {"cold_open": {"clip": [4.6, 5.6]}, "ending_trim_s": 0.0}
    words = [w("one", 0.9, 1.3), w("two", 2.9, 3.3), w("three", 4.9, 5.3), w("four", 6.9, 7.3)]
    result = render(tmp_path, src, clip_for(8.0, story=story), words, {**PLAIN, "tighten": False})
    assert result["duration_s"] == pytest.approx(9.0, abs=0.15)
    out = Path(result["path"])
    flashes, beeps = flash_times(out), beep_times(out)
    assert len(flashes) == len(beeps) == 5 and flashes[0] == pytest.approx(0.3, abs=0.2) and flashes[1] - flashes[0] == pytest.approx(1.6, abs=0.15)       # the teaser's beep (4.9 - 4.6)
    assert max(abs(f - b) for f, b in zip(flashes, beeps)) < 0.06                              # picture and sound stay together


def test_ending_trim_removes_the_last_seconds(tmp_path):
    src = beep_source(tmp_path)
    story = {"cold_open": None, "ending_trim_s": 1.5}
    result = render(tmp_path, src, clip_for(8.0, story=story), [w("one", 0.9, 1.3)], {**PLAIN, "tighten": False})
    assert result["duration_s"] == pytest.approx(6.5, abs=0.15)
