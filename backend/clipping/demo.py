"""Sample-campaign demo (spec 15.6): one campaign goes from intake to approval, produces packaged clips
from fixture footage, is accepted in the feed, posted through the checklist, and has views and a payout
logged. Every external service is a fake; FFmpeg is real. Nothing is posted anywhere: the "post link"
is a placeholder the demo types in where the owner would paste a real one."""
from __future__ import annotations

import datetime as dt

from . import alerts, brand_checks, campaigns, config, ffmpeg, ledger, moments, ops, pipeline, publish, review, signals
from .core import AppError, KillSwitchOn, kill_switch_on, set_kill_switch
from .db import Actor, Database

DEMO_NAME = "The Founders Table (sample)"


def fixture_video() -> str:
    path = config.data_dir() / "fixtures" / "sample_footage.mp4"
    if not path.exists():
        ffmpeg.make_fixture(path, seconds=60)
    return str(path)


def stage_lines(conn, source_id) -> list[str]:
    """One line per production stage, from what was stored: the provider that really ran, the numbers it left behind.
    This is what shows that a no-keys run went through the declared fallbacks and not through a vendor."""
    lines = []
    transcript = conn.execute("select provider, model, current_version from transcripts where source_id = %s order by created_at limit 1",
                              (source_id,)).fetchone()
    lines.append(f"Transcription: {transcript['provider']}{' ' + transcript['model'] if transcript['model'] else ''}, "
                 f"version {transcript['current_version']}" if transcript else "Transcription: no transcript was stored")
    measured = signals.load(conn, source_id)
    lines.append(f"Signals: {len(measured['series']['loudness'])} seconds measured, {len(measured['topics'])} topic(s)"
                 if measured else "Signals: not measured (moments were found without them)")
    kept = conn.execute("select count(*) filter (where status <> 'filtered') as kept, count(*) filter (where status = 'filtered') as aside, "
                        "string_agg(distinct engine, ', ') as engines, string_agg(distinct profile, ', ') as profile "
                        "from candidates where source_id = %s", (source_id,)).fetchone()
    lines.append(f"Moments: {kept['kept']} kept, {kept['aside']} set aside, engines {kept['engines'] or 'none'}, profile {kept['profile'] or 'none'}")
    renders = conn.execute("select render_meta from clips cl join candidates ca on ca.id = cl.candidate_id where ca.source_id = %s "
                           "and cl.file_path is not null", (source_id,)).fetchall()
    encoders = sorted({str((r["render_meta"] or {}).get("encoder")) for r in renders})
    once = all((r["render_meta"] or {}).get("encodes") == 1 for r in renders)
    lines.append(f"Render: {len(renders)} clip(s), one encode each: {'yes' if once and renders else 'no'}, encoder {', '.join(encoders) or 'none'}")
    return lines


def signals_only_check(conn, source_id) -> str:
    """The declared fallback for ranking: with the AI switched off, the moment engine still ranks on the audio and transcript
    signals. Run on a copy of the work that is rolled back, so the demo's own moments and clips are not touched."""
    with conn.transaction(force_rollback=True):
        conn.execute("update adapters set active = false where stage = 'llm'")
        try:
            found = moments.find(conn, source_id, None)
        except AppError as exc:
            return f"Signals-only ranking: not possible here ({exc.code})"
        return (f"Signals-only ranking (AI switched off for this check, then restored): {len(found['kept'])} moment(s) kept, "
                f"AI state {found['llm']}")


def _failed_jobs(conn) -> str:
    rows = conn.execute("select type, error from jobs where status = 'failed' order by created_at limit 3").fetchall()
    return "; ".join(f"{r['type']}: {config.redact(str(r['error'] or ''))[:160]}" for r in rows)


def run(db: Database, log=print, finish_all: bool = True, render_size: tuple[int, int] | None = (540, 960)) -> dict:
    """Runs the sample campaign end to end. If the kill switch is on (before the run, or switched on during it) the demo stops
    cleanly where it is: out["stopped"] says so and nothing is half-written."""
    owner = ops.ensure_owner(db)
    out: dict = {"steps": []}

    def step(text: str) -> None:
        out["steps"].append(text)
        log(f"  - {text}")

    with db.session(owner) as conn:
        if conn.execute("select 1 from campaigns where name = %s", (DEMO_NAME,)).fetchone():
            log("Demo campaign already exists. Nothing to do.")
            return out
        if kill_switch_on(conn):
            out["stopped"] = "kill_switch"
            step("The kill switch is on, so the demo did not start. Switch it off in Settings and run it again.")
            return out
    try:
        return _run(db, owner, out, step, log, finish_all, render_size)
    except KillSwitchOn:
        out["stopped"] = "kill_switch"
        step("The kill switch was switched on, so the demo stopped here and left the sample campaign as it is. Switch it off in Settings to go on.")
        return out


def _run(db: Database, owner, out: dict, step, log, finish_all: bool, render_size) -> dict:
    with db.session(owner) as conn:
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
        for line in stage_lines(conn, source["id"]):
            step(line)
        step(signals_only_check(conn, source["id"]))
        for clip in clips:
            step(f"Clip {clip['duration_s']} s, {clip['platform']}, compliance {clip['compliance_status']}: {clip['priority_reason']}. "
                 f"Quality: {clip['quality']['label']}")
        if not clips:
            if kill_switch_on(conn):
                raise KillSwitchOn()
            raise RuntimeError(f"the demo produced no clips ({_failed_jobs(conn) or 'no job failed'})")

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
        if len(clips) > 1:
            taught = brand_checks.record_rejection(conn, owner, clips[1]["id"], "Rejected: missing #sampleadtag in the caption")
            step(f"A marketplace rejection reason was recorded for the brand: it became {len(taught['checks'])} check(s) "
                 f"({', '.join(c['rule'] for c in taught['checks'])}) for the brand's next clips")
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
