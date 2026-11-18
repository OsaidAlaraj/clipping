"""Phase 7: the small endpoints and merged calls that keep the screens quick."""
from __future__ import annotations

from fastapi.testclient import TestClient

from clipping import api, review
from conftest import add_accounts
from test_7_discovery import refresh


def test_counts_endpoint_returns_only_the_two_badge_numbers(produced):
    api._owner = produced.owner
    body = TestClient(api.app).get("/api/counts").json()
    assert set(body) == {"to_review", "alerts"}
    assert body["to_review"] == 3 and 0 <= body["alerts"] <= 5


def test_counts_endpoint_is_far_smaller_than_home(produced):
    api._owner = produced.owner
    client = TestClient(api.app)
    assert len(client.get("/api/counts").content) * 10 < len(client.get("/api/home").content)


def test_counts_drop_when_a_clip_is_decided(produced):
    api._owner = produced.owner
    client = TestClient(api.app)
    clip = client.get("/api/feed").json()["clips"][0]
    client.post(f"/api/clips/{clip['id']}/accept", json={})
    assert client.get("/api/counts").json()["to_review"] == 2


def test_a_refused_decision_leaves_the_clip_in_the_feed(produced):
    """The web rolls an optimistic action back when this refusal comes; the clip must still be there to roll back to."""
    api._owner = produced.owner
    client = TestClient(api.app)
    clip = client.get("/api/feed").json()["clips"][0]
    refused = client.post(f"/api/clips/{clip['id']}/reject", json={"reason_code": ""})
    assert refused.status_code >= 400 and refused.json()["error"]
    assert clip["id"] in [c["id"] for c in client.get("/api/feed").json()["clips"]]


def test_a_very_long_feed_still_answers_with_every_clip(produced):
    """Long feeds: the response grows with the clips, so the web must window it; the API must not drop or reorder any."""
    api._owner = produced.owner
    with produced.db.session(produced.owner) as conn:
        clips = review.feed(conn, produced.owner.operator_id)
    body = TestClient(api.app).get("/api/feed").json()["clips"]
    assert [c["id"] for c in body] == [str(c["id"]) for c in clips]


def test_listing_detail_carries_the_categories_so_the_page_needs_one_call(db, owner):
    api._owner = owner
    add_accounts(db, owner)
    refresh(db, owner)
    client = TestClient(api.app)
    first = client.get("/api/listings").json()["listings"][0]
    detail = client.get(f"/api/listings/{first['id']}").json()
    assert "podcast" in detail["categories"] and detail["categories"] == sorted(detail["categories"])


def test_dismiss_then_undo_restores_the_listing(db, owner):
    api._owner = owner
    add_accounts(db, owner)
    refresh(db, owner)
    client = TestClient(api.app)
    target = client.get("/api/listings").json()["listings"][0]["id"]
    client.post(f"/api/listings/{target}/dismiss")
    assert target not in [r["id"] for r in client.get("/api/listings").json()["listings"]]
    client.post(f"/api/listings/{target}/dismiss?undo=true")
    assert target in [r["id"] for r in client.get("/api/listings").json()["listings"]]
