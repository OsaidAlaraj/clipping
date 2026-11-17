"""Sample-campaign demo (spec 15.6): one campaign goes from intake to approval, produces packaged clips
from fixture footage, is accepted in the feed, posted through the checklist, and has views and a payout
logged. Every external service is a fake; FFmpeg is real. Nothing is posted anywhere: the "post link"
is a placeholder the demo types in where the owner would paste a real one."""
from __future__ import annotations

import datetime as dt

from . import alerts, campaigns, config, ffmpeg, ledger, ops, pipeline, publish, review
from .core import kill_switch_on, set_kill_switch
from .db import Actor, Database

DEMO_NAME = "The Founders Table (sample)"


def fixture_video() -> str:
    path = config.data_dir() / "fixtures" / "sample_footage.mp4"
    if not path.exists():
        ffmpeg.make_fixture(path, seconds=60)
    return str(path)


def run(db: Database, log=print, finish_all: bool = True, render_size: tuple[int, int] | None = (540, 960)) -> dict:
    owner = ops.ensure_owner(db)
    out: dict = {"steps": []}

    def step(text: str) -> None:
        out["steps"].append(text)
        log(f"  - {text}")

    with db.session(owner) as conn:
        if conn.execute("select 1 from campaigns where name = %s", (DEMO_NAME,)).fetchone():
            log("Demo campaign already exists. Nothing to do.")
            return out
        if render_size:   # a smaller render keeps the demo quick; a configuration change, not a code change
            conn.execute("update adapters set config_json = config_json || %s where stage = 'render' and provider = 'ffmpeg'",
                         (alerts.Jsonb({"width": render_size[0], "height": render_size[1], "preset": "ultrafast"}),))
        for platform, handle in (("tiktok", "sample_owner_tt"), ("instagram_reels", "sample_owner_ig"), ("youtube_shorts", "sample_owner_yt")):
            conn.execute("insert into accounts (platform, handle, owner_operator_id, verified) values (%s, %s, %s, true) on conflict do nothing",
                         (platform, handle, owner.operator_id))

    log("1. Intake and approval")
    brief = (config.FIXTURES / "briefs" / "01_podcast_clear.txt").read_text(encoding="utf-8")
    with db.session(owner) as conn:
        result = campaigns.intake(conn, owner, "content_rewards", DEMO_NAME, brief, category="podcast")
        campaign_id = result["campaign"]["id"]
        step(f"Brief stored as version 1 (snapshot {result['version']['snapshot_hash'][:12]}), status {result['campaign']['status']}, "
             f"rules status {result['version']['compliance_status']}")
        blocked = campaigns.intake(conn, owner, "content_rewards", "SpinKing (sample, blocked)",
                                   (config.FIXTURES / "briefs" / "02_gaming_casino.txt").read_text(encoding="utf-8"), category="gaming")
        step(f"A gambling brief was hard-blocked on intake: status {blocked['campaign']['status']}")
        campaigns.confirm_rules(conn, owner, campaign_id)
        campaigns.approve(conn, owner, campaign_id)
        step("Owner confirmed the extracted rules and approved the campaign (gate 1)")

    log("2. Production")
    with db.session(owner) as conn:
        source = campaigns.register_source(conn, owner, campaign_id, fixture_video(), "en", permission_ref="SAMPLE-PERMISSION-001")
        review.timer_start(conn, owner, "cutting", campaign_id)
        step(f"Fixture footage registered ({source['duration_s']} s, hash {source['file_hash'][:12]})")
    jobs = pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        conn.execute("update time_entries set ended_at = now(), minutes = 20 where task_type = 'cutting' and ended_at is null")
        clips = review.feed(conn, owner.operator_id)
        step(f"{jobs} pipeline jobs ran; {len(clips)} packaged clips are in the feed")
        for clip in clips:
            step(f"Clip {clip['duration_s']} s, {clip['platform']}, compliance {clip['compliance_status']}: {clip['priority_reason']}")
    if not clips:
        raise RuntimeError("the demo produced no clips")

    log("3. Review")
    with db.session(owner) as conn:
        session = review.start_session(conn, owner, campaign_id)
        first = clips[0]
        accepted = review.accept(conn, owner, first["id"], session["id"])
        step(f"Accepted the top clip (gate 2); hook recorded: “{first['hook_text']}”")
        if len(clips) > 1:
            rejected = review.reject(conn, owner, clips[1]["id"], "weak_hook", session_id=session["id"])
            review.undo(conn, owner, rejected["id"])
            step("Rejected the second clip with reason “weak hook”, then used Undo: it is back in the feed")
        if len(clips) > 2:
            review.park(conn, owner, clips[2]["id"], "Check the cut", session["id"])
            step("Moved the third clip to Review Later")
        summary = review.stop_session(conn, owner, session["id"], {"campaign_id": str(campaign_id)})
        conn.execute("update time_entries set minutes = 10 where session_id = %s", (session["id"],))
        step(f"Session summary: {summary['clips_reviewed']} reviewed, {summary['accepted']} accepted, {summary['rejected']} rejected, "
             f"{summary['posts_due']} posts due")
    out["accepted_review_id"] = accepted["id"]

    if not finish_all:
        return out

    log("4. Posting")
    with db.session(owner) as conn:
        campaigns.recheck_brief(conn, owner, campaign_id, current_text=brief)
        step("Brief re-checked before the batch: unchanged")
        publish.approve_publish(conn, owner, first["id"], {name: True for name in publish.OWNER_TICKS})
        step("Publish checklist complete and approved (gate 3)")
        post = publish.mark_posted(conn, owner, first["id"], "https://example.com/sample-post-not-real", True, True)
        conn.execute("update time_entries set ended_at = now(), minutes = 0 where ended_at is null")
        conn.execute("insert into time_entries (operator_id, owner_operator_id, task_type, campaign_id, ended_at, minutes) "
                     "values (%s, %s, 'posting', %s, now(), 6)", (owner.operator_id, owner.operator_id, campaign_id))
        step("Owner recorded the post link (placeholder link; nothing was posted)")

    log("5. Tracking and money")
    with db.session(owner) as conn:
        ledger.record_views(conn, owner, post["id"], "h48", 21000)
        ledger.record_views(conn, owner, post["id"], "d7", 64000)
        payout = conn.execute("select * from payouts where clip_id = %s", (first["id"],)).fetchone()
        for status in ("approved", "held", "paid"):
            payout = ledger.set_payout_status(conn, owner, payout["id"], status)
        step(f"Views logged at 48 h (21,000) and 7 d (64,000). Payout: gross ${payout['gross_usd']}, fee ${payout['fee_usd']}, "
             f"net ${payout['net_usd']}, status {payout['status']}")
        stats = ledger.net_per_hour(conn)
        weekly = ledger.weekly_median_net_per_hour(conn)
        step(f"Net pay per hour: ${stats['overall']['paid_per_hour']} over {stats['overall']['hours']} h "
             f"(confidence {stats['confidence']}); weekly median ${weekly['median_net_per_hour']}")
        out["net_per_hour"] = stats["overall"]["paid_per_hour"]
        out["median"] = weekly["median_net_per_hour"]

    log("6. Kill switch and audit log")
    with db.session(owner) as conn:
        started = dt.datetime.now()
        set_kill_switch(conn, True, "demo check")
    blocked_jobs = pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        assert kill_switch_on(conn)
        hidden = publish.queue(conn)
        step(f"Kill switch on in {(dt.datetime.now() - started).total_seconds():.2f} s: {blocked_jobs} jobs ran, "
             f"ready-to-post packages shown: {sum(1 for i in hidden['items'] if i['status'] != 'accepted')}")
        set_kill_switch(conn, False, "demo check finished")
        trail = conn.execute(
            """select action, count(*) as n from audit_log
               where action like 'gate.%%' or action like '%%status:%%' or action in ('brief.recheck', 'post.recorded', 'rules.confirmed')
               group by action order by min(at)"""
        ).fetchall()
        total = conn.execute("select count(*) as n from audit_log").fetchone()["n"]
        step(f"Audit log holds {total} rows. Gate and state-change rows:")
        for row in trail:
            step(f"    {row['action']} × {row['n']}")
    return out
