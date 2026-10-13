"""Shared fixtures: repo-root import path and per-test isolated temp database."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db


@pytest.fixture(autouse=True)
def isolated_temp_db():
    """Every test runs against a fresh throwaway database.

    The real database is never touched: db.temp_db() swaps NOA_DB_PATH to a
    temp file for the test and restores the environment afterwards.
    """
    with db.temp_db() as path:
        assert db.is_test_mode()
        assert db.get_db_path() != db.DEFAULT_DB_PATH
        assert str(db.get_db_path()).startswith(str(path.parent))
        yield path
