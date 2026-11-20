"""`python run.py` serves a production web build and rebuilds it only when the web sources are newer than the build."""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

RUN = Path(__file__).resolve().parents[2] / "run.py"


@pytest.fixture
def run_module(monkeypatch):
    for key in ("TMP", "TEMP", "PIP_CACHE_DIR", "npm_config_cache", "HF_HOME", "XDG_CACHE_HOME"):   # run.py sets these at import
        monkeypatch.setenv(key, os.environ.get(key, ""))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    spec = importlib.util.spec_from_file_location("run_script", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (when, when))


def test_stale_when_there_is_no_build_and_fresh_when_the_build_is_newest(run_module, tmp_path):
    touch(tmp_path / "app" / "page.tsx", 1000)
    touch(tmp_path / "package.json", 1000)
    assert run_module.web_build_is_stale(tmp_path)                      # no build yet
    touch(tmp_path / ".next" / "BUILD_ID", 2000)
    assert not run_module.web_build_is_stale(tmp_path)


@pytest.mark.parametrize("source", ["app/page.tsx", "components/Feed.tsx", "lib/i18n.tsx", "public/icon.png", "package.json", "next.config.mjs"])
def test_a_newer_web_source_triggers_a_rebuild(run_module, tmp_path, source):
    touch(tmp_path / ".next" / "BUILD_ID", 2000)
    touch(tmp_path / "app" / "page.tsx", 1000)
    assert not run_module.web_build_is_stale(tmp_path)
    touch(tmp_path / source, 3000)
    assert run_module.web_build_is_stale(tmp_path)


def test_tests_and_node_modules_do_not_trigger_a_rebuild(run_module, tmp_path):
    touch(tmp_path / ".next" / "BUILD_ID", 2000)
    touch(tmp_path / "tests" / "feed.test.tsx", 3000)
    touch(tmp_path / "node_modules" / "x" / "index.js", 3000)
    assert not run_module.web_build_is_stale(tmp_path)


def test_the_dev_flag_keeps_next_dev_and_the_default_runs_next_start():
    text = RUN.read_text(encoding="utf-8")
    assert '"dev" if dev_server else "start"' in text and 'if not dev_server and not ensure_web_build()' in text
