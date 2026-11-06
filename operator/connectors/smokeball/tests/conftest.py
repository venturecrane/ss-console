"""Suite-wide: the write read-back (readback.py) polls with real pauses of up
to 13 s between reads. No test here is testing the clock, so every test runs
the poll without sleeping; a test that wants to count the pauses patches
``readback.SLEEP`` itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from smokeball_connector import logic_in_code, memo_confirm, readback

# operator/pytest.ini runs --import-mode=importlib, which puts no test dir on
# sys.path; the shared fake document store (fake_files.py) is imported by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _outcome_rows_stay_in_the_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every filing, stage, resolve and keying call writes an outcome row
    (outcome_ledger.py) under ``$SMD_POST_JOURNAL_DIR``; a test's rows land in
    its own directory, never in the seat's default path."""
    monkeypatch.setenv("SMD_POST_JOURNAL_DIR", str(tmp_path / "post-journal"))


@pytest.fixture
def logic_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every ``logic_in_code`` switch at ``enforce``. Absent, a seat observes, so
    a test of what a check refuses asks for this."""
    monkeypatch.setattr(logic_in_code, "load_switch", lambda key, path=None: logic_in_code.Switch(key, "enforce"))


@pytest.fixture(autouse=True)
def _readback_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readback, "SLEEP", lambda _seconds: None)
    # The memo read-back (memo_confirm.py) waits its own vendor-lag window.
    monkeypatch.setattr(memo_confirm, "SLEEP", lambda _seconds: None)
