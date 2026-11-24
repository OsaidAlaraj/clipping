"""The scout engine: the LLM names moments by sentence number; code owns the times, the length range and the edges."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from clipping import moments_scout
from clipping.adapters import fake_llm
from clipping.db import Actor
from test_12_moments import find, make_source, speech


def _scout_route(db):
    with db.session(Actor.system()) as conn:
        conn.execute("update content_profiles set config = config || %s where name = 'podcast'", (Jsonb({"route": ["own", "scout"]}),))


def _calls():
    return [p for k, p in fake_llm.FAKE_MOMENT_LLM["calls"] if k == "scout"]


def test_scout_picks_are_cut_on_sentence_ends_inside_the_length_range(db, owner):
    fake_llm.FAKE_MOMENT_LLM.update(rate=None, compare=None, scout=None, bias=None, calls=[])
    _scout_route(db)
    result = find(db, make_source(db, owner, speech(10)))
    scouted = [c for c in result["kept"] + [f["candidate"] for f in result["filtered"]] if c["source"] == "scout"]
    assert _calls() and "scout" in result["route"] and scouted
    assert all(30 - 1 <= c["end_s"] - c["start_s"] <= 60 + 1 for c in scouted)
    units = _calls()[0]["units"]
    assert all(len(u) == 6 and isinstance(u[0], int) for u in units)           # ids, never times, are what the model names


def test_scout_answer_with_ids_that_were_not_sent_is_ignored(db, owner):
    fake_llm.FAKE_MOMENT_LLM.update(rate=None, compare=None, bias=None, calls=[],
                                    scout={"moments": [{"first": 9999, "last": 10001, "score": 0.9, "reason": "made up"},
                                                       {"first": 3, "last": 1, "score": 0.9, "reason": "backwards"},
                                                       {"first": 2, "last": 14, "score": 2, "reason": "score out of range"}]})
    _scout_route(db)
    result = find(db, make_source(db, owner, speech(10)))
    assert not [c for c in result["kept"] if c["source"] == "scout"]            # nothing invented survives; the other engines still answer
    assert result["kept"]


def test_scout_failure_never_loses_the_other_engines(db, owner):
    fake_llm.FAKE_MOMENT_LLM.update(rate=None, compare=None, bias=None, calls=[], scout={"unexpected": 1})
    _scout_route(db)
    result = find(db, make_source(db, owner, speech(10)))
    assert result["kept"] and any("scout" in n for n in result["notes"])


def test_parse_rejects_text_that_is_not_a_plain_pick():
    ok = moments_scout.parse({"moments": [{"first": 1, "last": 4, "score": 0.7, "reason": "  a\x00 clear   point "}]}, {1, 2, 3, 4})
    assert ok == [{"first": 1, "last": 4, "score": 0.7, "reason": "a clear point"}]
