"""Общие фикстуры. Реальные вызовы внешних API в тестах запрещены."""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from trade_agent.config import load_settings          # noqa: E402
from trade_agent.db import Database                   # noqa: E402


@pytest.fixture
def settings(tmp_path):
    cfg = load_settings(PROJECT_DIR)
    cfg.db_path = tmp_path / "test.db"
    cfg.digest_dir = tmp_path / "digest"
    cfg.log_dir = tmp_path / "logs"
    cfg.brain_dir = tmp_path / "brain"
    (cfg.brain_dir / "companies").mkdir(parents=True, exist_ok=True)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def db(settings):
    database = Database(settings.db_path)
    yield database
    database.close()
