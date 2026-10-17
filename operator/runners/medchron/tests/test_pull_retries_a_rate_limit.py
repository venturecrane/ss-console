"""A file not fetched for a reason time fixes is fetched again within the run.

2026-10-14 (A&P litigation proof): ~400 files fetched, two answered HTTP 429
after the connector's own attempts, and the whole run failed
``fetch_unfinished`` for a person to resume. ``fetch_all`` now makes a second
pass (and a third) over the rows that failed for a retryable reason, after a
pause. A 403 or 404 is final and is not tried again: those are findings on
the matter (litigation/fetch.py), not transport.

FALSIFIER: set RETRY_PASSES to 0 and the first test fails; retry 404s and the
second test's mint count rises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from medchron.demand import pull


class _Seat:
    """Mint fails with the given error text the first ``fails`` times per id."""

    def __init__(self, fails: dict[str, tuple[int, str]]) -> None:
        self.fails = dict(fails)
        self.mints: list[list[str]] = []

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        self.mints.append(list(file_ids))
        out = []
        for fid in file_ids:
            n, err = self.fails.get(fid, (0, ""))
            if n > 0:
                self.fails[fid] = (n - 1, err)
                out.append({"id": fid, "error": err})
            else:
                out.append({"id": fid, "url": f"https://fake.invalid/{fid}", "size": 3})
        return out

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(url[-3:].encode())
        return 3


def _targets(*ids: str) -> list[dict[str, Any]]:
    return [{"id": i, "name": f"{i}.pdf", "ext": ".pdf", "folder": "f", "size": 3} for i in ids]


def test_a_429_is_fetched_on_the_second_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(pull.time, "sleep", lambda s: sleeps.append(s))
    seat = _Seat({"b": (1, "Smokeball GET /x/download -> HTTP 429: Too Many Requests")})
    rows = pull.fetch_all(seat, "m", _targets("a", "b", "c"), tmp_path, lambda _m: None)
    assert [r["ok"] for r in rows] == [True, True, True]
    assert seat.mints == [["a", "b", "c"], ["b"]]
    assert sleeps == [pull.RETRY_PAUSE_SECONDS]
    # The log on disk ends with the good row, so a resume sees it as done.
    last = [r for r in pull.read_jsonl(tmp_path / "pulled.jsonl") if r["id"] == "b"][-1]
    assert last["ok"] is True


def test_a_404_or_403_is_final_and_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull.time, "sleep", lambda _s: None)
    seat = _Seat(
        {
            "gone": (9, "Smokeball GET /x/download -> HTTP 404: not found"),
            "shut": (9, "Smokeball GET /x/download -> HTTP 403: forbidden"),
        }
    )
    rows = pull.fetch_all(seat, "m", _targets("a", "gone", "shut"), tmp_path, lambda _m: None)
    assert [r["ok"] for r in rows] == [True, False, False]
    assert seat.mints == [["a", "gone", "shut"]]


def test_a_limit_that_outlasts_every_pass_is_still_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull.time, "sleep", lambda _s: None)
    seat = _Seat({"b": (9, "Smokeball GET /x/download -> HTTP 429: Too Many Requests")})
    rows = pull.fetch_all(seat, "m", _targets("b"), tmp_path, lambda _m: None)
    assert rows[0]["ok"] is False
    assert len(seat.mints) == 1 + pull.RETRY_PASSES


class _ListingSeat(_Seat):
    """A seat whose fetch answers HTTP 403 for the ``shut`` file (the status on
    the exception's response, as the connector raises it)."""

    def list_files(self, matter_id: str) -> list[dict[str, Any]]:
        return _targets("a", "gone", "shut")

    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]:
        return []

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        if url.endswith("/shut"):
            exc = RuntimeError("Smokeball GET /x/download -> HTTP 403: forbidden")
            exc.response = type("R", (), {"status_code": 403})()  # type: ignore[attr-defined]
            raise exc
        return super().fetch(url, dest, expected_size)


def test_the_pull_records_a_403_and_a_404_and_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER (review 2026-10-17 B2): ``run`` raised PullError for every
    failed row but a 404 status, so one file Smokeball will not serve (403)
    failed the demand and drafting pulls forever, though ``fetch_all`` already
    treated it as final and did not retry it."""
    monkeypatch.setattr(pull.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pull, "select", lambda docs, paths, firm: docs)
    seat = _ListingSeat({"gone": (9, "Smokeball GET /x/download -> HTTP 404: not found")})
    report = pull.run(seat, "m", None, set(), tmp_path, lambda _m: None)  # type: ignore[arg-type]
    assert report["absent_from_storage"] == ["gone.pdf"]
    assert report["refused_by_smokeball"] == ["shut.pdf"]


def test_the_pull_still_fails_on_a_file_a_retry_could_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pull, "select", lambda docs, paths, firm: docs)
    seat = _ListingSeat({"a": (9, "Smokeball GET /x/download -> HTTP 429: Too Many Requests")})
    with pytest.raises(pull.PullError):
        pull.run(seat, "m", None, set(), tmp_path, lambda _m: None)  # type: ignore[arg-type]
