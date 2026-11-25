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


def test_owner_sets_the_hook_time():
    ass = media.build_ass(1080, 1920, 30, [], None, False, {}, "one two three", None, 8.0)
    line = next(x for x in ass.splitlines() if ",Title," in x)
    assert line.split(",")[2] == "0:00:08.60"       # the owner's 8 s plus the fades, not the automatic 3 s


def test_regenerate_gives_new_hooks_each_time(produced):
    with produced.db.session(produced.owner) as conn:
        clip = review.feed(conn, produced.owner.operator_id)[0]
        before = {h["text"].lower() for h in review.get_clip(conn, clip["id"])["hooks"]}
        first = pipeline.regenerate_hooks(conn, clip["id"])
        second = pipeline.regenerate_hooks(conn, clip["id"])
    texts = [h["text"].lower() for h in second]
    assert len(texts) == len(set(texts)) and len(set(texts)) > len(before)
    assert not hook_rules.problems(second[-1]["text"]) and len(first) < len(second)


# ------------------------------------------------------------------ the owner's own hook for one clip

def test_hook_position_and_start_time_reach_the_subtitle_script():
    ass = media.build_ass(1080, 1920, 30, [], None, False, {}, "one two three", None, 4.0, 2.5, "middle")
    line = next(x for x in ass.splitlines() if ",Title," in x)
    assert line.split(",")[1] == "0:00:02.50" and line.split(",")[2] == "0:00:07.10"      # starts at 2.5 s, stays 4 s plus fades
    style = next(x for x in ass.splitlines() if x.startswith("Style: Title"))
    assert style.split(",")[18] == "5"                                                     # alignment 5 = middle
    top = next(x for x in media.build_ass(1080, 1920, 30, [], None, False, {}, "one two three").splitlines() if x.startswith("Style: Title"))
    assert top.split(",")[18] == "8"                                                       # unchanged default: top


def test_hook_start_never_runs_past_the_clip():
    ass = media.build_ass(1080, 1920, 10, [], None, False, {}, "one two three", None, 5.0, 9.9, None)
    line = next(x for x in ass.splitlines() if ",Title," in x)
    assert line.split(",")[1] == "0:00:09.50" and line.split(",")[2] == "0:00:10.00"


def test_owner_hook_is_a_new_version_with_its_own_time_and_overrides_the_global_setting(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]
        v2 = review.edit(conn, produced.owner, target["id"], hook={"text": "Nobody told me this about rent", "from_s": 1, "for_s": 6, "position": "bottom"})
        meta = v2["render_meta"]
        assert v2["status"] == "rendering" and v2["version_no"] == 2
        assert meta["hook_text"] == "Nobody told me this about rent" and meta["hook_custom"] is True and "hook_id" not in meta
        assert (meta["hook_from_s"], meta["hook_for_s"], meta["hook_position"]) == (1.0, 6.0, "bottom")
        _, source, _, _ = pipeline._clip_ctx(conn, v2["id"])
        request = pipeline._render_request(conn, v2, source, None, meta["hook_text"])
        assert (request["hook_show_s"], request["hook_from_s"], request["hook_position"]) == (6.0, 1.0, "bottom")
        plain = pipeline._render_request(conn, dict(v2, render_meta={}), source, None, None)
        assert plain["hook_from_s"] is None and plain["hook_position"] is None      # a clip with no choices keeps the automatic time
    pipeline.run_until_idle(produced.db)
    with produced.db.session(produced.owner) as conn:
        done = review.get_clip(conn, v2["id"])
        assert done["status"] == "ready_for_review" and done["hook_text"] == "Nobody told me this about rent"
        assert done["render_meta"]["hook_for_s"] == 6.0 and done["render_meta"]["overlay_burned"]    # rendered once, choices kept


@pytest.mark.parametrize("hook, code", [({"text": "   "}, "bad_hook"), ({"text": "x" * 200}, "bad_hook"), ({"for_s": 99}, "bad_hook_timing"),
                                         ({"from_s": -1}, "bad_hook_timing"), ({"from_s": "soon"}, "bad_hook_timing"), ({"position": "left"}, "bad_hook_position")])
def test_owner_hook_refuses_bad_input_in_plain_words(produced, hook, code):
    from clipping.core import AppError

    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]
        with pytest.raises(AppError) as err:
            review.edit(conn, produced.owner, target["id"], hook=hook)
        assert err.value.code == code and err.value.args[0].endswith(".")
