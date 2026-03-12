"""Tests that main.py registers all blueprints and starts all workers/DBs."""

import importlib
import sys
from contextlib import ExitStack
from unittest.mock import patch

EXPECTED_BLUEPRINTS = {"seerr", "transcode", "import_scan", "test_media", "managarr_cleanup"}

_PATCHES = [
    "src.transcode.queue.init_db",
    "src.transcode.worker.start",
    "src.test_media.queue.init_db",
    "src.test_media.worker.start",
    "src.managarr.worker.init_db",
    "src.managarr.worker.start",
    "shutil.copy",
]


def _import_main(monkeypatch, tmp_path):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.ini"))
    sys.modules.pop("main", None)
    mocks = {}
    with ExitStack() as stack:
        for p in _PATCHES:
            mocks[p] = stack.enter_context(patch(p))
        mod = importlib.import_module("main")
    return mod, mocks


def test_all_blueprints_registered(monkeypatch, tmp_path):
    main, _ = _import_main(monkeypatch, tmp_path)
    assert set(main.app.blueprints) == EXPECTED_BLUEPRINTS


def test_all_workers_started(monkeypatch, tmp_path):
    _, mocks = _import_main(monkeypatch, tmp_path)
    mocks["src.transcode.worker.start"].assert_called_once()
    mocks["src.test_media.worker.start"].assert_called_once()
    mocks["src.managarr.worker.start"].assert_called_once()


def test_all_dbs_initialized(monkeypatch, tmp_path):
    _, mocks = _import_main(monkeypatch, tmp_path)
    mocks["src.transcode.queue.init_db"].assert_called_once()
    mocks["src.test_media.queue.init_db"].assert_called_once()
    mocks["src.managarr.worker.init_db"].assert_called_once()
