"""Hook rules (owner notes 1 and 10): short, spoken, no dashes, readable in time; regenerate never repeats."""
from __future__ import annotations

import pytest

from clipping import hook_rules, pipeline, review
from clipping.adapters import media
from clipping.agents import hooks as agent_b


def test_clean_removes_dashes_colons_semicolons_and_quotes_but_keeps_words():
    assert hook_rules.clean('Nobody tells you this: "rent" is a trap — really') == "Nobody tells you this rent is a trap really"
    assert hook_rules.clean("Wait; what?? Don’t do it!!!") == "Wait, what? Don’t do it!"
    assert hook_rules.clean("هاد - غلط؛ كلياً") == "هاد غلط, كلياً"


@pytest.mark.parametrize("text,expected", [
    ("Stop doing this", []),
    ("Stop", ["too short"]),
    ("one two three four five six seven eight nine ten", ["too long"]),
    ("Wait — what", ["dash"]),
    ('He said "no"', ["quotation"]),
])
def test_problems_flags_each_rule(text, expected):
    found = " ".join(hook_rules.problems(text))
    assert all(word in found for word in expected) and bool(expected) == bool(found)


def test_reading_speed_rule_and_safe_area():
    assert hook_rules.show_seconds("Stop doing this") == 3.0          # 3 words: floor of 3 s
    assert hook_rules.show_seconds("one two three four five six seven eight nine") == 4.5
    assert hook_rules.problems("one two three four five six seven eight nine", available_s=3.0)
    assert not hook_rules.problems("one two three four five six seven eight nine", available_s=4.5)
    long_words = "extraordinarily unbelievable incomprehensible misunderstanding responsibilities"
    assert hook_rules.problems(long_words, frame_w=1080, title_size=69)    # would need more than two lines


def test_agent_validate_rewrites_rejects_and_never_repeats():
    raw = {"hooks": [{"text": "Stop doing this — now", "type": "stakes", "score": 0.9},
                     {"text": "This is a very long thesis about why everything you believe about money is wrong today", "type": "question", "score": 0.8},
                     {"text": "Same as before", "type": "question", "score": 0.7},
                     {"text": "You are doing it wrong", "type": "contrarian", "score": 0.6}]}
    rejected: list = []
    hooks = agent_b.validate(raw, "x", avoid=["same as before"], rejected=rejected)
    assert [h["text"] for h in hooks] == ["Stop doing this now", "You are doing it wrong"]
    assert rejected and "too long" in rejected[0][1][0]


def test_title_card_stays_long_enough_to_read():
    ass = media.build_ass(1080, 1920, 30, [], None, False, {}, "one two three four five six seven eight nine")
    line = next(x for x in ass.splitlines() if ",Title," in x)
    assert line.split(",")[2] == "0:00:05.10"       # 9 words: 4.5 s plus the fades


def test_regenerate_gives_new_hooks_each_time(produced):
    with produced.db.session(produced.owner) as conn:
        clip = review.feed(conn, produced.owner.operator_id)[0]
        before = {h["text"].lower() for h in review.get_clip(conn, clip["id"])["hooks"]}
        first = pipeline.regenerate_hooks(conn, clip["id"])
        second = pipeline.regenerate_hooks(conn, clip["id"])
    texts = [h["text"].lower() for h in second]
    assert len(texts) == len(set(texts)) and len(set(texts)) > len(before)
    assert not hook_rules.problems(second[-1]["text"]) and len(first) < len(second)
