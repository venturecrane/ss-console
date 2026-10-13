"""Suite-wide: the write read-back (readback.py) polls with real pauses of up
to 13 s between reads. No test here is testing the clock, so every test runs
the poll without sleeping; a test that wants to count the pauses patches
``readback.SLEEP`` itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from smokeball_connector import readback

# operator/pytest.ini runs --import-mode=importlib, which puts no test dir on
# sys.path; the shared fake document store (fake_files.py) is imported by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _readback_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readback, "SLEEP", lambda _seconds: None)
