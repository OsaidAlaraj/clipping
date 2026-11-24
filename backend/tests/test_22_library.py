"""The accepted-clips library (owner notes 13): filters, sorts and the campaign picker, and the query runs on an empty database."""
from __future__ import annotations

import datetime as dt

from clipping import library

T = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.timezone.utc)


def row(name, campaign="c1", campaign_name="Founders Table", hook="A hook", minutes=0, views=None, net=None):
    return {"id": name, "campaign_id": campaign, "campaign_name": campaign_name, "hook_text": hook,
            "updated_at": T + dt.timedelta(minutes=minutes), "views_h48": views, "net_usd": net}


def ids(rows):
    return [r["id"] for r in rows]


ROWS = [
    row("a", campaign="c1", campaign_name="Founders Table", hook="Why people quit", minutes=1, views=500, net=12.5),
    row("b", campaign="c2", campaign_name="Gym Mornings", hook="Five rules", minutes=3, views=None, net=None),
    row("c", campaign="c1", campaign_name="Founders Table", hook="Parachutes", minutes=2, views=9000, net=40),
]


def test_newest_first_by_default():
    assert ids(library.select_clips(ROWS)) == ["b", "c", "a"]


def test_sorts_by_views_earned_and_campaign():
    assert ids(library.select_clips(ROWS, sort="views")) == ["c", "a", "b"]          # a clip without numbers goes last
    assert ids(library.select_clips(ROWS, sort="earned")) == ["c", "a", "b"]
    assert ids(library.select_clips(ROWS, sort="campaign")) == ["c", "a", "b"]       # Founders Table first, newest of it first
    assert ids(library.select_clips(ROWS, sort="nonsense")) == ["b", "c", "a"]      # unknown sort falls back to newest


def test_filters_by_selected_campaigns():
    assert ids(library.select_clips(ROWS, campaign_ids={"c2"})) == ["b"]
    assert ids(library.select_clips(ROWS, campaign_ids={"c1", "c2"})) == ["b", "c", "a"]
    assert ids(library.select_clips(ROWS, campaign_ids=set())) == ["b", "c", "a"]   # no selection means every campaign


def test_search_matches_campaign_name_or_hook_without_case():
    assert ids(library.select_clips(ROWS, q="gym")) == ["b"]
    assert ids(library.select_clips(ROWS, q="PARACHUTES")) == ["c"]
    assert ids(library.select_clips(ROWS, q="   ")) == ["b", "c", "a"]
    assert ids(library.select_clips(ROWS, q="nothing matches this")) == []


def test_the_picker_lists_each_campaign_once_with_its_count_and_is_not_filtered():
    picker = library.campaign_list(ROWS)
    assert picker == [{"id": "c1", "name": "Founders Table", "count": 2}, {"id": "c2", "name": "Gym Mornings", "count": 1}]


def test_the_library_runs_on_an_empty_database(db, owner):

    with db.session(owner) as conn:
        result = library.listing(conn, ["whatever"], "x", "views")
    assert result == {"clips": [], "campaigns": [], "total": 0, "sort": "views"}
