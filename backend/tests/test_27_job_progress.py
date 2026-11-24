"""Real job progress: measured share and time left, published from the running job and from ffmpeg's own progress lines."""
from __future__ import annotations

import pytest

from clipping import ffmpeg, job_progress
from clipping.db import Actor


def test_measure_gives_an_eta_only_from_real_work_done():
    assert job_progress.measure(0, 100, 30) == {"share": 0.0, "eta_s": None}      # nothing done yet: unknown
    assert job_progress.measure(1, 100, 30)["eta_s"] is None                      # 1 % is too little to extrapolate
    assert job_progress.measure(25, 100, 30) == {"share": 0.25, "eta_s": 90}      # 30 s for a quarter: 90 s left
    assert job_progress.measure(100, 100, 30) == {"share": 1.0, "eta_s": 0}
    assert job_progress.measure(5, 0, 30) == {"share": None, "eta_s": None}


def test_report_without_a_running_job_does_nothing():
    job_progress.report(5, 10, "seconds")   # must not raise


def test_report_writes_the_running_jobs_row(db, owner, monkeypatch):
    with db.session(Actor.worker()) as conn:
        job = conn.execute("""insert into jobs (type, owner_operator_id, input_hash, idempotency_key, status)
                              values ('transcribe', %s, 'h', 'k1', 'running') returning id""", (owner.operator_id,)).fetchone()
    monkeypatch.setattr(job_progress, "MIN_GAP_S", 0.0)
    with job_progress.bind(db, job["id"]):
        job_progress.report(30, 120, "seconds of audio")
    with db.session(Actor.worker()) as conn:
        row = conn.execute("select progress from jobs where id = %s", (job["id"],)).fetchone()
    assert row["progress"]["share"] == 0.25 and row["progress"]["unit"] == "seconds of audio"


def test_ffmpeg_reports_the_real_encode_position(db, owner, tmp_path, monkeypatch):
    with db.session(Actor.worker()) as conn:
        job = conn.execute("""insert into jobs (type, owner_operator_id, input_hash, idempotency_key, status)
                              values ('render', %s, 'h', 'k2', 'running') returning id""", (owner.operator_id,)).fetchone()
    monkeypatch.setattr(job_progress, "MIN_GAP_S", 0.0)
    out = tmp_path / "t.mp4"
    with job_progress.bind(db, job["id"]):
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "3", "-pix_fmt", "yuv420p", str(out)], progress_total_s=3)
    with db.session(Actor.worker()) as conn:
        row = conn.execute("select progress from jobs where id = %s", (job["id"],)).fetchone()
    assert out.exists() and row["progress"]["share"] == pytest.approx(1.0, abs=0.01) and row["progress"]["unit"] == "seconds of video"
