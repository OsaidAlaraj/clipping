"""Database backups: a custom-format pg_dump per run, the newest 14 kept, and restore into a scratch database.

    python run.py backup                 dump now (starts the embedded Postgres if it is not running)
    python -m clipping restore <file>    restore a dump into a new scratch database, never the live one
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from . import config

KEEP = 14
INTERVAL_S = 24 * 3600
NAME = re.compile(r"^clipping-\d{8}-\d{4}\.dump$")


def backups_dir() -> Path:
    path = config.data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tool(name: str) -> Path:
    """pg_dump / pg_restore from the binaries that ship inside pixeltable-pgserver."""
    import pixeltable_pgserver

    path = Path(pixeltable_pgserver.__file__).parent / "pginstall" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if not path.exists():
        raise FileNotFoundError(f"{name} is missing from the embedded Postgres install ({path.parent})")
    return path


def _dumps(directory: Path) -> list[Path]:
    # names carry a fixed-width timestamp, so name order is time order
    return sorted(p for p in directory.iterdir() if NAME.match(p.name))


def create(dsn: str, directory: Path | None = None) -> Path:
    """Write one consistent dump of the database. pg_dump takes a single snapshot, so the file is consistent.
    The dump is written under a .partial name and renamed only when complete."""
    directory = directory or backups_dir()
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f"clipping-{dt.datetime.now():%Y%m%d-%H%M}.dump"
    partial = final.with_suffix(".partial")
    result = subprocess.run(
        [str(_tool("pg_dump")), "--format=custom", "--no-owner", "--no-privileges", f"--file={partial}", f"--dbname={dsn}"],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {config.redact(result.stderr)[-300:]}")
    os.replace(partial, final)
    return final


def prune(directory: Path | None = None, keep: int = KEEP) -> list[Path]:
    """Delete all but the newest `keep` dumps. Returns the deleted files."""
    dumps = _dumps(directory or backups_dir())
    doomed = dumps[:-keep] if keep > 0 else dumps
    for path in doomed:
        path.unlink()
    return doomed


def due(directory: Path | None = None) -> bool:
    dumps = _dumps(directory or backups_dir())
    return not dumps or time.time() - dumps[-1].stat().st_mtime >= INTERVAL_S


def run_if_due(dsn: str) -> Path | None:
    """The automatic backup: one dump per 24 hours, then retention. Called from housekeeping."""
    if not due():
        return None
    path = create(dsn)
    prune()
    return path


def restore(dump: Path, dsn: str) -> str:
    """Restore a dump into a NEW database on the same server. Returns the scratch database name.
    The live database is never touched. A failed restore keeps the scratch database for inspection."""
    if not dump.exists():
        raise FileNotFoundError(f"no such dump: {dump}")
    name = f"clipping_restore_{dt.datetime.now():%Y%m%d_%H%M%S}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    target = make_conninfo(dsn, dbname=name)
    result = subprocess.run(
        [str(_tool("pg_restore")), "--no-owner", "--no-privileges", f"--dbname={target}", str(dump)],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore reported errors in scratch database {name}: {config.redact(result.stderr)[-300:]}")
    return name


@contextmanager
def _server_dsn() -> Iterator[str]:
    """DATABASE_URL, or the embedded Postgres started the way the app starts it, stopped again afterwards
    if this command was the one that started it."""
    if config.env("DATABASE_URL"):
        yield config.env("DATABASE_URL")   # type: ignore[misc]
        return
    import pixeltable_pgserver as pgserver

    pgdata = config.data_dir() / "pg"
    pgdata.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(pgdata, cleanup_mode=None, start=False)
    started = server.get_pid() is None
    if started:
        server.start()
    try:
        yield server.get_uri()
    finally:
        if started:
            server.stop()


def cli(argv: list[str]) -> int:
    """`backup` and `restore <file>` for python -m clipping."""
    if argv[1] == "backup":
        with _server_dsn() as dsn:
            path = create(dsn)
            removed = prune()
        print(f"Backup written: {path}")
        if removed:
            print(f"Removed {len(removed)} old backup(s); keeping the newest {KEEP}.")
        return 0
    if len(argv) < 3:
        print("usage: python -m clipping restore <file.dump>")
        return 2
    with _server_dsn() as dsn:
        name = restore(Path(argv[2]), dsn)
    print(f"Restored into scratch database: {name}")
    print("Check it (the live database is untouched):")
    print(f"  psql -d {name} -c \"select count(*) from operators;\"")
    print(f"  psql -d {name} -c \"select count(*) from campaigns;\"")
    print("Drop it when you are done: drop database " + name + " with (force);")
    return 0
