"""Real progress of the job that is running now (owner note 9).

A handler calls `report(done, total, unit)` while it works: seconds of audio transcribed, seconds of video encoded. The
numbers go to `jobs.progress` through their own short transaction (the handler's transaction is still open and invisible),
at most once a second. The time left is measured: elapsed time divided by the share done, never a guess. A job that cannot
measure its work reports nothing, and the screen then shows only the stage name."""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time

from psycopg.types.json import Jsonb

log = logging.getLogger("clipping")
_current: contextvars.ContextVar = contextvars.ContextVar("job_progress", default=None)
MIN_GAP_S = 1.0
MIN_SHARE_FOR_ETA = 0.03       # too little done to extrapolate a time left


class _State:
    def __init__(self, db, job_id):
        self.db, self.job_id = db, job_id
        self.started = time.monotonic()
        self.last_write = 0.0
        self.last_share = -1.0


@contextlib.contextmanager
def bind(db, job_id):
    token = _current.set(_State(db, job_id))
    try:
        yield
    finally:
        _current.reset(token)


def measure(done: float, total: float, elapsed_s: float) -> dict:
    """{"share", "eta_s"} for `done` of `total` after `elapsed_s` seconds; eta is None until enough is done to measure."""
    if not total or total <= 0:
        return {"share": None, "eta_s": None}
    share = min(max(done / total, 0.0), 1.0)
    eta = round(elapsed_s * (1 - share) / share) if share >= MIN_SHARE_FOR_ETA and elapsed_s >= 1 else None
    return {"share": round(share, 3), "eta_s": eta}


def report(done: float, total: float, unit: str = "") -> None:
    """Publish how far the running job is. Never raises: progress must not break the work it describes."""
    state = _current.get()
    if state is None or not total or total <= 0:
        return
    now = time.monotonic()
    share = min(max(done / total, 0.0), 1.0)
    if share < 1.0 and (now - state.last_write < MIN_GAP_S or abs(share - state.last_share) < 0.005):
        return
    state.last_write, state.last_share = now, share
    value = {**measure(done, total, now - state.started), "done": round(done, 1), "total": round(total, 1), "unit": unit}
    try:
        from .db import Actor

        with state.db.session(Actor.worker()) as conn:
            conn.execute("update jobs set progress = %s where id = %s and status = 'running'", (Jsonb(value), state.job_id))
    except Exception as exc:   # the cause goes to the server log only
        log.warning("progress not saved: %r", exc)
