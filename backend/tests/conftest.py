"""Test setup. One embedded Postgres for the run; every test gets its own database cloned from a
migrated template. All vendors are fakes; FFmpeg is real. No test calls a live service."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import psycopg
import pytest

# Everything the tests write stays on the project drive (owner's rule: keep the work on L:).
_TMP_ROOT = Path(__file__).resolve().parents[2] / ".data" / "tmp"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP_ROOT)
os.environ["TMP"] = os.environ["TEMP"] = str(_TMP_ROOT)
_TMP = Path(tempfile.mkdtemp(prefix="clip_test_"))
os.environ["CLIPPING_DATA_DIR"] = str(_TMP / "data")
os.environ["CLIPPING_USE_FAKES"] = "1"
os.environ["AUTH_MODE"] = "local"
os.environ["TOKEN_VAULT_KEY"] = "dGVzdC12YXVsdC1rZXktMzItYnl0ZXMtbG9uZy0hISE="   # tests only (32 bytes, base64)
for _name in ("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DEEPGRAM_API_KEY", "VIZARD_API_KEY"):
    os.environ.pop(_name, None)

from clipping import campaigns, config, ffmpeg, ops, pipeline  # noqa: E402
from clipping.adapters import vendors  # noqa: E402
from clipping.adapters.base import activate  # noqa: E402
from clipping.db import Actor, Database, set_db  # noqa: E402

BRIEFS = config.FIXTURES / "briefs"


def brief(name: str) -> str:
    return next(BRIEFS.glob(f"{name}*.txt")).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def pg():
    import pixeltable_pgserver as pgserver

    server = pgserver.get_server(_TMP / "pg", cleanup_mode="stop")
    yield server.get_uri()
    server.cleanup()
    shutil.rmtree(_TMP, ignore_errors=True)


def _dsn(admin: str, name: str) -> str:
    # The admin URI is TCP on Windows but a Unix socket (host=/path) on Linux; rebuild it rather than cut strings.
    return psycopg.conninfo.make_conninfo(admin, dbname=name)


def _admin(admin: str, sql: str) -> None:
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session")
def base_template(pg):
    _admin(pg, "create database tpl_base")
    db = Database(_dsn(pg, "tpl_base"))
    db.migrate()
    with db.session(Actor.system()) as conn:
        conn.execute("""update adapters set config_json = config_json || '{"width":270,"height":480,"preset":"ultrafast","crf":30,"encoder":"x264"}'
                        where stage = 'render'""")
        conn.execute("""update adapters set config_json = config_json || '{"preset":"ultrafast","crf":30}' where stage = 'captions'""")
        activate(conn, "alerts", "fake")
        activate(conn, "discovery", "fake")
        activate(conn, "audience", "fake")
    db.close()
    return "tpl_base"


def _clone(pg: str, template: str) -> Database:
    name = "t_" + uuid.uuid4().hex[:12]
    _admin(pg, f'create database "{name}" template "{template}"')
    db = Database(_dsn(pg, name))
    db.name = name
    return db


def _reset_fakes() -> None:
    vendors.FAKE_ALERTS.update(sent=[], fail=False)
    vendors.FAKE_SCHEDULER.update(posts={}, channels=set(), fail=False)
    vendors.FAKE_SOURCE.update(campaigns=[], briefs={})
    from clipping.adapters import discovery as _discovery

    _discovery.FAKE_DISCOVERY.update(campaigns=None, fail=False, calls=[], briefs={})
    from clipping.adapters import transcription as _transcription

    _transcription.FAKE_TRANSCRIPTION.update(calls=[])
    from clipping.adapters import audience as _audience

    _audience.FAKE_AUDIENCE.update(countries={"US": 40, "GB": 10, "SA": 30, "EG": 20}, fail=False, calls=[])
    from clipping.adapters import oauth as _oauth

    _oauth.reset_fake()
    from clipping.adapters import publishing as _publishing

    _publishing.reset_fake()


@pytest.fixture
def db(pg, base_template):
    database = _clone(pg, base_template)
    set_db(database)
    _reset_fakes()
    yield database
    database.close()
    set_db(None)
    _admin(pg, f'drop database if exists "{database.name}" with (force)')


@pytest.fixture
def owner(db) -> Actor:
    return ops.ensure_owner(db)


@pytest.fixture(scope="session")
def footage() -> str:
    path = Path(os.environ["CLIPPING_DATA_DIR"]) / "fixtures" / "footage60.mp4"
    if not path.exists():
        ffmpeg.make_fixture(path, seconds=60)
    return str(path)


def add_accounts(db: Database, actor: Actor) -> None:
    with db.session(actor) as conn:
        for platform, handle in (("tiktok", "own_tt"), ("instagram_reels", "own_ig"), ("youtube_shorts", "own_yt")):
            conn.execute("insert into accounts (platform, handle, owner_operator_id, verified) values (%s, %s, %s, true)",
                         (platform, handle, actor.operator_id))


def approved_campaign(db: Database, actor: Actor, name: str = "Founders Table", brief_name: str = "01") -> str:
    with db.session(actor) as conn:
        result = campaigns.intake(conn, actor, "content_rewards", name, brief(brief_name))
        campaigns.approve(conn, actor, result["campaign"]["id"])
        return str(result["campaign"]["id"])


_PRODUCED: dict = {}
_CLIP_FILES: dict[Path, bytes] = {}   # the template's rendered clips as they were, so one test re-rendering them cannot spoil the next


def _storage_clips() -> Path:
    return Path(os.environ["CLIPPING_DATA_DIR"]) / "storage" / "clips"


def _restore_clip_files() -> None:
    for path, data in _CLIP_FILES.items():
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)


@pytest.fixture(scope="session")
def produced_template(pg, base_template, footage):
    """A database where one approved campaign has been through the whole pipeline (three packaged clips)."""
    database = _clone(pg, base_template)
    set_db(database)
    actor = ops.ensure_owner(database)
    add_accounts(database, actor)
    campaign_id = approved_campaign(database, actor)
    with database.session(actor) as conn:
        source = campaigns.register_source(conn, actor, campaign_id, footage, "en", permission_ref="TEST-PERMISSION-1")
    _PRODUCED.update(actor=actor, campaign_id=campaign_id, source_id=str(source["id"]), jobs=pipeline.run_until_idle(database))
    _CLIP_FILES.update({p: p.read_bytes() for p in _storage_clips().rglob("*") if p.is_file()})
    database.close()
    set_db(None)
    return database.name


class Produced:
    def __init__(self, db: Database):
        self.db = db
        self.owner: Actor = _PRODUCED["actor"]
        self.campaign_id: str = _PRODUCED["campaign_id"]
        self.source_id: str = _PRODUCED["source_id"]
        self.jobs: int = _PRODUCED["jobs"]


@pytest.fixture
def produced(pg, produced_template) -> Produced:
    database = _clone(pg, produced_template)
    set_db(database)
    _reset_fakes()
    yield Produced(database)
    _restore_clip_files()
    database.close()
    set_db(None)
    _admin(pg, f'drop database if exists "{database.name}" with (force)')
