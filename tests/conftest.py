"""Shared test fixtures: paths to the committed synthetic Takeout export."""

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
TESTDATA = PROJECT_ROOT / "testdata"
TAKEOUT_KEEP = TESTDATA / "Takeout" / "Keep"
TAKEOUT_ZIP = TESTDATA / "takeout-test.zip"


@pytest.fixture
def takeout_dir() -> Path:
    return TAKEOUT_KEEP


@pytest.fixture
def takeout_zip() -> Path:
    return TAKEOUT_ZIP
