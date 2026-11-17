"""Database access. Every transaction runs as an identified actor so row-level security and the
gate triggers apply (migrations 002 and 003)."""
from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb  # noqa: F401  (re-exported)

from . import config


@dataclass(frozen=True)
class Actor:
    """Who is acting. kind 'operator' is a human; 'worker' is the pipeline and can pass no human gate."""
    operator_id: str | None
    role: str | None
    kind: str = "operator"

    @staticmethod
    def worker() -> "Actor":
        return Actor(None, None, "worker")

    @staticmethod
    def system() -> "Actor":
        return Actor(None, None, "system")


class Database:
    def __init__(self, dsn: str, pool_size: int = 4):
        self.dsn = dsn
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=pool_size)
        self._lock = threading.Lock()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False)

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        try:
            conn = self._pool.get_nowait()
            if conn.closed or conn.broken:
                conn = self._connect()
        except queue.Empty:
            conn = self._connect()
        try:
            yield conn
        finally:
            try:
                if not conn.closed and not conn.broken:
                    conn.rollback()
                    self._pool.put_nowait(conn)
                else:
                    conn.close()
            except queue.Full:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def session(self, actor: Actor) -> Iterator[psycopg.Connection]:
        """One transaction as the given actor. Commits on success, rolls back on error."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "select set_config('app.operator_id', %s, true), set_config('app.role', %s, true)",
                    (actor.operator_id or "", actor.role or ""),
                )
                if actor.kind == "operator":
                    conn.execute("set local role app_user")
                elif actor.kind == "worker":
                    conn.execute("set local role app_worker")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def checkpoint(conn: psycopg.Connection) -> None:
        """Commit what is done so far inside a long session and carry on as the same actor (a discovery pass takes
        minutes; its new campaigns should not wait for the whole pass, nor hold one transaction open)."""
        row = conn.execute("select current_setting('app.operator_id', true) as op, current_setting('app.role', true) as role, "
                           "current_user as who").fetchone()
        conn.commit()
        conn.execute("select set_config('app.operator_id', %s, true), set_config('app.role', %s, true)",
                     (row["op"] or "", row["role"] or ""))
        if row["who"] in ("app_user", "app_worker"):
            conn.execute(f"set local role {row['who']}")

    def close(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
            except queue.Empty:
                break

    # ------------------------------------------------------------ migrations
    def migrate(self, directory: Path | None = None) -> list[str]:
        directory = directory or config.MIGRATIONS
        applied: list[str] = []
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("create table if not exists public.schema_migrations (name text primary key, applied_at timestamptz default now())")
            done = {r[0] for r in conn.execute("select name from public.schema_migrations")}
            for path in sorted(directory.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                    conn.execute("insert into public.schema_migrations (name) values (%s)", (path.name,))
                applied.append(path.name)
        return applied


_db: Database | None = None
_local_server = None


def start_local_postgres(pgdata: Path | None = None) -> str:
    """Start (or attach to) the embedded Postgres used for local runs. Returns its URI."""
    global _local_server
    import pixeltable_pgserver as pgserver

    pgdata = pgdata or config.data_dir() / "pg"
    pgdata.mkdir(parents=True, exist_ok=True)
    _local_server = pgserver.get_server(pgdata, cleanup_mode="stop")   # stops with this process
    return _local_server.get_uri()


def get_db() -> Database:
    global _db
    if _db is None:
        dsn = config.env("DATABASE_URL") or start_local_postgres()
        _db = Database(dsn)
    return _db


def set_db(db: Database | None) -> None:
    global _db
    _db = db
