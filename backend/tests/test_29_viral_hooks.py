"""Viral hooks: the lexicon (data, expiring trends), the one allowed leading format tag, AI-sounding phrases, the rubric-scored
generator that spreads formats, and the owner's thumbs."""
from __future__ import annotations

import datetime

import pytest

from clipping import hook_lexicon, hook_rules, review
from clipping.agents import hooks as agent_b
from clipping.core import AppError

SEGMENT = "he lost forty thousand dollars in one hour and then he said never price by the hour again"


def test_a_leading_format_tag_keeps_its_colon_and_is_not_counted():
    assert hook_rules.check("POV: your boss reads the group chat") == ("POV: your boss reads the group chat", [])
    assert hook_rules.check("nobody: this guy after one win")[0] == "Nobody: this guy after one win"      # the lexicon's own spelling
    assert hook_rules.check("ما حدا: هو الساعة ثلاثة الفجر")[1] == []
    assert hook_rules.spoken_words("POV: your boss reads it") == ["your", "boss", "reads", "it"]
    assert any("too short" in p for p in hook_rules.problems("POV: wait now"))   # POV: + 2 words is too short: the tag is not a word


def test_only_the_leading_tag_may_have_a_colon():
    assert any("colon" in p for p in hook_rules.problems("Stop doing this: now please"))
    assert hook_rules.clean("POV: he said: no way") == "POV: he said no way"     # the second colon goes, the tag stays
    assert hook_rules.clean("Why: he left").startswith("Why he left")             # not a tag: cleaned as before


def test_ai_sounding_phrases_are_rejected_in_both_languages():
    assert any("AI" in p for p in hook_rules.problems("Unlock the secret of pricing"))
    assert any("AI" in p for p in hook_rules.problems("This is a game-changer for rent"))
    assert any("AI" in p for p in hook_rules.problems("اكتشف سر النجاح الان"))
    assert not hook_rules.problems("Stop pricing by the hour")
    assert not hook_rules.problems("Unlocked doors scare me")    # a different word: only the listed phrase is banned


def test_trend_formats_expire_and_the_lexicon_is_consistent():
    live = {f["id"] for f in hook_lexicon.formats("en", "gaming", today="2026-11-24")}
    assert {"pov", "nobody", "curiosity_gap"} <= live
    later = {f["id"] for f in hook_lexicon.formats("en", "gaming", today="2027-03-02")}
    assert "pov" not in later and "curiosity_gap" in later                          # a trend drops out, an evergreen stays
    assert hook_lexicon.leading_tag("POV: x", today="2027-03-02") is None           # and its tag is no longer allowed
    for f in hook_lexicon.load()["formats"]:
        assert f["type"] in agent_b.HOOK_TYPES and f["language"] in ("en", "ar") and f["examples"] and f["weight"] > 0
        for example in f["examples"]:   # every example in the data obeys the rules the code enforces
            assert not hook_rules.problems(example, language=f["language"]), (f["id"], example)
            assert not f.get("tag") or example.startswith(f["tag"].rstrip(":")), (f["id"], example)
    for lang in ("en", "ar"):
        for niche in ("business", "gaming", "podcast", "lifestyle"):
            assert len(hook_lexicon.fewshot(lang, niche)) >= 2
            assert len(hook_lexicon.formats(lang, niche)) >= 8        # enough different formats for 8 candidates
    assert hook_lexicon.load()["rubric_weights"].keys() == set(hook_lexicon.load()["rubric"])
    assert abs(sum(hook_lexicon.load()["rubric_weights"].values()) - 1) < 1e-9


def _item(text, fmt, hook_type="question", **marks):
    rubric = {"specific_to_clip": 7, "curiosity_gap": 7, "humour": 5, "trendiness": 5, "clarity": 8, "length": 8} | marks
    return {"text": text, "format": fmt, "type": hook_type, "rubric": rubric}


def test_validate_returns_top_three_with_different_formats_scored_by_the_rubric():
    raw = {"hooks": [
        _item("Stop pricing by the hour", "stop_doing", "stakes", specific_to_clip=9),
        _item("Stop charging by the day", "stop_doing", "stakes"),                       # same format as the best: dropped
        _item("He lost 40k in one hour", "numbered", "number", specific_to_clip=8),
        _item("POV: your client wants hourly rates", "pov", "story-open", humour=8),
        _item("Why he never prices hourly", "curiosity_gap", "question", specific_to_clip=3, humour=1, clarity=3),
        _item("Unlock the secret of pricing today", "stop_doing", "stakes"),             # AI sounding: rejected
    ]}
    rejected: list = []
    hooks = agent_b.validate(raw, SEGMENT, rejected=rejected, language="en")
    assert len(hooks) == 3 and [h["rank"] for h in hooks] == [1, 2, 3]
    assert len({h["pattern"] for h in hooks}) == 3
    assert hooks[0]["text"] == "Stop pricing by the hour" and hooks[0]["score"] >= hooks[1]["score"] >= hooks[2]["score"]
    assert hooks[1]["pattern"] in ("numbered", "pov") and not any("Unlock" in h["text"] for h in hooks)
    assert any("AI" in why for _, why in [(t, ", ".join(p)) for t, p in rejected])


def test_a_tag_format_without_its_tag_is_not_that_format():
    hooks = agent_b.validate({"hooks": [_item("Your boss reads the group chat", "pov", "story-open"), _item("He lost 40k in one hour", "numbered", "number")]},
                             SEGMENT, language="en")
    assert [h["text"] for h in hooks] == ["He lost 40k in one hour"]          # the line claimed POV but has no tag: dropped
    tagged = agent_b.validate({"hooks": [_item("Nobody: him after one price cut", "pov", "story-open")]}, SEGMENT, language="en")
    assert tagged[0]["pattern"] == "nobody"                                    # the tag decides the format, not the model's claim


def test_the_best_line_avoids_the_format_a_neighbouring_clip_opened_with():
    raw = {"hooks": [_item("Stop pricing by the hour", "stop_doing", "stakes", specific_to_clip=10),
                     _item("He lost 40k in one hour", "numbered", "number"), _item("Why he never prices hourly", "curiosity_gap", "question")]}
    hooks = agent_b.validate(raw, SEGMENT, language="en", avoid_formats=["stop_doing"])
    assert hooks[0]["pattern"] != "stop_doing" and {h["pattern"] for h in hooks} == {"stop_doing", "numbered", "curiosity_gap"}
    only = agent_b.validate({"hooks": [_item("Stop pricing by the hour", "stop_doing", "stakes"), _item("Why he never prices hourly", "curiosity_gap", "question")]},
                            SEGMENT, language="en", avoid_formats=["stop_doing", "curiosity_gap"])
    assert len(only) == 2                                                      # nothing else to offer: the avoided formats still appear, never an empty set


def test_the_old_hook_shape_still_works():
    hooks = agent_b.validate({"hooks": [{"text": "Stop doing this", "type": "stakes", "score": 0.9}, {"text": "What if rent is a trap", "type": "question", "score": 0.6}]},
                             SEGMENT, language="en")
    assert [h["hook_type"] for h in hooks] == ["stakes", "question"]


class _Registry:
    def __init__(self):
        self.payloads = []

    def call(self, conn, stage, method, payload, owner):
        self.payloads.append(payload["payload"])
        return {"hooks": [_item("Stop pricing by the hour", "stop_doing", "stakes"), _item("He lost 40k in one hour", "numbered", "number")]}, 0


def test_run_sends_formats_fewshot_niche_and_the_campaigns_hook_constraints():
    registry = _Registry()
    hooks, _ = agent_b.run(None, registry, SEGMENT, "en", 0.7, patterns=["numbered", "pov"], niche="business",
                           avoid_formats=["pov"], hook_constraints=["Must say #ad in the first line"])
    sent = registry.payloads[0]
    assert [c["id"] for c in sent["formats"]] == ["numbered", "pov"] and sent["formats"][1]["tag"] == "POV:"
    assert sent["constraints"] == ["Must say #ad in the first line"] and sent["avoid_formats"] == ["pov"] and sent["niche"] == "business"
    assert len(sent["good_bad"]) >= 2 and len(hooks) == 2


def test_thumbs_save_and_teach_the_library_once(produced):
    from clipping import hook_patterns

    with produced.db.session(produced.owner) as conn:
        clip = review.feed(conn, produced.owner.operator_id)[0]
        hook = review.get_clip(conn, clip["id"])["hooks"][0]
        assert review.rate_hook(conn, hook["id"], "up")["rating"] == 1
        assert review.rate_hook(conn, hook["id"], "down")["rating"] == -1       # a changed mind changes the thumb ...
        assert review.rate_hook(conn, hook["id"], "none")["rating"] is None
        stats = conn.execute("select sum(approvals) a, sum(rejections) r from hook_pattern_stats").fetchone()
        assert (stats["a"], stats["r"] or 0) == (1, 0)                          # ... but teaches the library only once
        assert review.get_clip(conn, clip["id"])["hooks"][0]["rating"] is None
        assert hook_patterns.library(conn, "en", "default", "business")        # the lexicon formats join the stored patterns
        with pytest.raises(AppError):
            review.rate_hook(conn, hook["id"], "sideways")
