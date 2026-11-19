"""Review feed: a decided clip never returns; a skipped clip returns after about five others, at most twice, then goes to
Review Later. The order is kept on the server, so it survives a reload."""
from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from clipping import api, core, review

T0 = dt.datetime(2026, 11, 19, 12, 0, tzinfo=dt.timezone.utc)


def row(name, **kw):
    return {"id": name, "skip_count": 0, "skip_anchor": None, "skipped_at": None, **kw}


def ids(rows):
    return [r["id"] for r in rows]


# ------------------------------------------------------------------ placement (pure)

def test_a_skipped_clip_sits_after_the_clip_it_was_skipped_behind():
    ranked = [row("a", skip_count=1, skip_anchor="c", skipped_at=T0), row("b"), row("c"), row("d")]
    assert ids(review.place_skipped(ranked)) == ["b", "c", "a", "d"]


def test_without_a_usable_anchor_a_skipped_clip_returns_after_five_clips():
    ranked = [row("a", skip_count=1, skipped_at=T0)] + [row(n) for n in "bcdefgh"]
    assert ids(review.place_skipped(ranked)) == ["b", "c", "d", "e", "f", "a", "g", "h"]
    short = [row("a", skip_count=1, skip_anchor="gone", skipped_at=T0), row("b"), row("c")]
    assert ids(review.place_skipped(short)) == ["b", "c", "a"]          # fewer than five others: the end


def test_clips_skipped_earlier_are_placed_first_so_a_later_skip_can_follow_them():
    ranked = [row("a", skip_count=1, skip_anchor="c", skipped_at=T0), row("b", skip_count=1, skip_anchor="a", skipped_at=T0 + dt.timedelta(seconds=5)),
              row("c"), row("d")]
    assert ids(review.place_skipped(ranked)) == ["c", "a", "b", "d"]


def test_unskipped_clips_keep_their_ranked_order():
    assert ids(review.place_skipped([row("x"), row("y"), row("z")])) == ["x", "y", "z"]


# ------------------------------------------------------------------ on the database

def _feed(produced):
    with produced.db.session(produced.owner) as conn:
        core.set_setting(conn, "quality_min_p_clear", 0, produced.owner.operator_id)
        return [str(c["id"]) for c in review.feed(conn, produced.owner.operator_id)]


def test_a_skipped_clip_moves_back_and_stays_there_after_a_reload(produced):
    first = _feed(produced)
    with produced.db.session(produced.owner) as conn:
        result = review.skip(conn, produced.owner, first[0], first[-1])
        assert result == {"clip_id": first[0], "skipped": True, "parked": False, "skip_count": 1}
    assert _feed(produced) == [first[1], first[2], first[0]]
    assert _feed(produced) == [first[1], first[2], first[0]]             # a second load gives the same order


def test_a_decided_clip_never_returns_and_skipping_it_does_nothing(produced):
    first = _feed(produced)
    with produced.db.session(produced.owner) as conn:
        review.accept(conn, produced.owner, first[0])
        review.reject(conn, produced.owner, first[1], "weak_hook")
        review.park(conn, produced.owner, first[2])
        for clip_id in first:
            assert review.skip(conn, produced.owner, clip_id)["skipped"] is False
    assert _feed(produced) == []


def test_the_third_skip_moves_the_clip_to_review_later(produced):
    first = _feed(produced)
    target = first[0]
    with produced.db.session(produced.owner) as conn:
        assert review.skip(conn, produced.owner, target, first[-1])["parked"] is False
        assert review.skip(conn, produced.owner, target, first[-1])["skip_count"] == 2          # returned twice
        third = review.skip(conn, produced.owner, target, first[-1])
        assert third["parked"] is True
        assert conn.execute("select status, park_reason from clips where id = %s", (target,)).fetchone() == {"status": "review_later", "park_reason": "Skipped three times"}
        assert target in [str(c["id"]) for f in review.review_later(conn) for c in f["clips"]]
    assert target not in _feed(produced)


def test_a_bad_anchor_is_ignored_and_a_clip_cannot_anchor_to_itself(produced):
    first = _feed(produced)
    with produced.db.session(produced.owner) as conn:
        review.skip(conn, produced.owner, first[0], "not-a-uuid")
        review.skip(conn, produced.owner, first[1], first[1])
        stored = conn.execute("select skip_anchor from clips where id in (%s, %s)", (first[0], first[1])).fetchall()
        assert [r["skip_anchor"] for r in stored] == [None, None]
    assert sorted(_feed(produced)) == sorted(first)                       # nothing lost


def test_the_undo_of_a_decision_is_not_affected_by_skips(produced):
    first = _feed(produced)
    with produced.db.session(produced.owner) as conn:
        review.skip(conn, produced.owner, first[0], first[-1])
        decision = review.reject(conn, produced.owner, first[1], "weak_hook")
        review.undo(conn, produced.owner, decision["id"])
    assert set(_feed(produced)) == set(first)


def test_the_skip_endpoint_works_over_the_api(produced):
    first = _feed(produced)
    api._owner = produced.owner
    client = TestClient(api.app)
    reply = client.post(f"/api/clips/{first[0]}/skip", json={"anchor_id": first[-1]})
    assert reply.status_code == 200 and reply.json()["skip_count"] == 1
    assert [c["id"] for c in client.get("/api/feed").json()["clips"]] == [first[1], first[2], first[0]]
    assert client.get("/api/feed").json()["clips"][-1]["skip_count"] == 1
