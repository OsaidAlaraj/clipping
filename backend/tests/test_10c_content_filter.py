"""Azure's safety filter refusing a moment sets that moment aside instead of failing the job."""
from __future__ import annotations

import httpx
import pytest

from clipping import alerts, pipeline
from clipping.adapters import vendors
from clipping.db import Actor


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://example.invalid"))


def test_a_content_filter_400_becomes_a_named_refusal_with_its_categories():
    body = {"error": {"code": "content_filter", "innererror": {"content_filter_result": {
        "violence": {"filtered": True, "severity": "high"}, "hate": {"filtered": False, "severity": "safe"}}}}}
    with pytest.raises(vendors.ContentFiltered) as err:
        vendors._raise_if_content_filtered(_response(400, body))
    assert err.value.code == "content_filtered" and err.value.categories == ["violence"]


def test_other_errors_are_left_to_the_normal_check():
    vendors._raise_if_content_filtered(_response(400, {"error": {"code": "BadRequest"}}))
    vendors._raise_if_content_filtered(_response(500, {"error": {"code": "content_filter"}}))
    vendors._raise_if_content_filtered(httpx.Response(400, content=b"not json", request=httpx.Request("POST", "https://x.invalid")))


def test_a_filtered_moment_is_set_aside_and_the_job_does_not_fail(produced, monkeypatch):
    db, owner = produced.db, produced.owner
    with db.session(Actor.worker()) as conn:
        candidate = conn.execute("select * from candidates order by created_at limit 1").fetchone()

    def refuse(*a, **k):
        raise vendors.ContentFiltered(["violence"])

    monkeypatch.setattr(pipeline.agent_b, "run", refuse)
    with db.session(Actor.worker()) as conn:
        cost = pipeline.stage_hooks(conn, {"payload": {"candidate_id": str(candidate["id"])}})
        row = conn.execute("select status, reason from candidates where id = %s", (candidate["id"],)).fetchone()
        alert = conn.execute("select type, severity from alerts where type = 'moment_filtered'").fetchone()
    assert cost == 0 and row["status"] == "discarded" and "safety filter" in row["reason"]
    assert alert["severity"] == "info"
    assert alerts.registry is not None
