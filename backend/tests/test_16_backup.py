"""Automatic and manual database backups: dump file, 14-file retention, restore into a scratch database,
and a failed backup that alerts without stopping housekeeping."""
from __future__ import annotations

import time

import psycopg
from psycopg.conninfo import make_conninfo

from clipping import backup, ops
from clipping.db import Actor


def _drop(dsn: str, name: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{name}" with (force)')


def test_backup_file_is_created_and_not_empty(db, tmp_path):
    ops.ensure_owner(db)
    path = backup.create(db.dsn, tmp_path)
    assert path.exists()
    assert path.name.startswith("clipping-") and path.name.endswith(".dump")
    assert path.stat().st_size > 0
    assert not list(tmp_path.glob("*.partial"))


def test_retention_keeps_the_newest_14(tmp_path):
    for day in range(1, 17):
        (tmp_path / f"clipping-202611{day:02d}-0900.dump").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_text("keep me")
    removed = backup.prune(tmp_path)
    kept = sorted(p.name for p in tmp_path.glob("clipping-*.dump"))
    assert len(removed) == 2 and len(kept) == 14
    assert kept[0] == "clipping-20261103-0900.dump" and kept[-1] == "clipping-20261116-0900.dump"
    assert (tmp_path / "unrelated.txt").exists()


def test_restore_into_scratch_database_reads_a_row_back(db, tmp_path):
    ops.ensure_owner(db)
    with psycopg.connect(db.dsn, autocommit=True) as conn:
        live_count = conn.execute("select count(*) from operators").fetchone()[0]
    path = backup.create(db.dsn, tmp_path)
    name = backup.restore(path, db.dsn)
    try:
        assert name != db.name and name.startswith("clipping_restore_")
        with psycopg.connect(make_conninfo(db.dsn, dbname=name)) as conn:
            assert conn.execute("select count(*) from operators").fetchone()[0] == live_count >= 1
            assert conn.execute("select name from operators order by created_at limit 1").fetchone() is not None
    finally:
        _drop(db.dsn, name)


def test_failed_backup_raises_alert_and_housekeeping_continues(db, monkeypatch):
    ops.ensure_owner(db)

    def boom(dsn, directory=None):
        raise RuntimeError("pg_dump exploded")

    monkeypatch.setattr(backup, "create", boom)
    monkeypatch.setattr(backup, "due", lambda: True)   # a dump made by a parallel test must not make this one "not due"
    monkeypatch.setattr(ops, "_audience_checked", {"at": time.time()})   # skip the network-backed audience refresh
    continued = []
    from clipping import campaigns

    monkeypatch.setattr(campaigns, "purge_expired_sources", lambda conn: continued.append(True))

    ops.housekeeping(db)   # must not raise

    assert continued == [True]
    with db.session(Actor.worker()) as conn:
        rows = conn.execute("select type, severity, message from alerts where type = 'backup_failed'").fetchall()
    assert len(rows) == 1
    assert rows[0]["severity"] == "warn"
    assert "pg_dump exploded" in rows[0]["message"]


def test_a_second_copy_is_made_only_when_the_setting_is_on_and_holds_only_the_dump(db, tmp_path, monkeypatch):
    ops.ensure_owner(db)
    main, second = tmp_path / "main", tmp_path / "second"
    path = backup.create(db.dsn, main)
    monkeypatch.delenv("CLIPPING_BACKUP_COPY_DIR", raising=False)
    assert backup.copy_to_second(path) is None and not second.exists()          # default: off
    monkeypatch.setenv("CLIPPING_BACKUP_COPY_DIR", str(second))
    copy = backup.copy_to_second(path)
    assert copy == second / path.name and copy.read_bytes() == path.read_bytes()
    assert [p.name for p in second.iterdir()] == [path.name]                     # no .env, no key, no partial files


def test_worker_errors_are_logged_and_the_third_in_a_row_raises_one_alert(db, caplog):
    ops.ensure_owner(db)
    streak: dict = {}
    for _ in range(4):
        ops._note_failure(db, "jobs", RuntimeError("disk gone"), streak)
    assert sum("worker jobs failed" in r.getMessage() and "RuntimeError" in r.getMessage() for r in caplog.records) == 4
    with db.session(Actor.worker()) as conn:
        rows = conn.execute("select severity, message from alerts where type = 'worker_jobs'").fetchall()
    assert len(rows) == 1 and rows[0]["severity"] == "warn" and "disk gone" in rows[0]["message"]


def test_the_worker_loop_reports_a_failing_job_pass_instead_of_swallowing_it(db, monkeypatch, caplog):
    import threading

    from clipping import pipeline

    ops.ensure_owner(db)
    stop = threading.Event()
    calls = []

    def boom(_db):
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        raise RuntimeError("claim failed")

    monkeypatch.setattr(pipeline, "process_one", boom)
    monkeypatch.setattr(ops, "housekeeping", lambda _db: None)
    monkeypatch.setattr(ops, "keepalive", lambda _db: True)
    monkeypatch.setattr(ops, "keep_awake", lambda on: True)
    ops.worker_loop(db, stop, poll_s=0.01)
    assert len(calls) == 3 and any("claim failed" in r.getMessage() for r in caplog.records)
    with db.session(Actor.worker()) as conn:
        assert conn.execute("select count(*) as n from alerts where type = 'worker_jobs'").fetchone()["n"] == 1
