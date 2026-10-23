"""The fetch stops before it downloads when the files still to fetch cannot fit
on the volume (2026-10-23: a list over 230 matters had 4.5 GB of a 10 GB
volume fetched, 87 matters to go, 1.2 GB free, the seat's own state on the
same disk)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from medchron.litigation import fetch
from medchron.litigation.outcome import REASON, LitigationFailed
from medchron.litigation.run import LitigationRun

GB = 1 << 30


def _data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "files").mkdir(parents=True)
    files = [{"id": "a", "size": 3 * GB}, {"id": "b", "size": 2 * GB}, {"id": "c"}]
    (data / "files" / "M1.json").write_text(json.dumps(files), encoding="utf-8")
    return data


def _run(data: Path, lines: list[str]) -> SimpleNamespace:
    plan = {"M1": {"candidates": ["a", "b", "c"], "read_groups": ["case"], "audit": "changed"}}
    return SimpleNamespace(
        data=data,
        log=lines.append,
        _active=lambda: ["M1"],
        _plan=lambda: plan,
        _fetch_ids=LitigationRun._fetch_ids,
    )


def test_pending_counts_only_files_not_yet_fetched(tmp_path):
    data = _data(tmp_path)
    files = json.loads((data / "files" / "M1.json").read_text())
    assert fetch.pending_bytes(data, "M1", files, ["a", "b", "c"]) == (5 * GB, 3)
    (fetch.matter_dir(data, "M1") / "pulled.jsonl").write_text(json.dumps({"id": "a", "ok": True}) + "\n")
    assert fetch.pending_bytes(data, "M1", files, ["a", "b", "c"]) == (2 * GB, 2)


def test_a_run_that_cannot_fit_fails_before_it_downloads(tmp_path, monkeypatch):
    data, lines = _data(tmp_path), []
    monkeypatch.setattr(fetch, "free_bytes", lambda _p: int(1.2 * GB))
    with pytest.raises(LitigationFailed) as exc:
        LitigationRun._disk_preflight(_run(data, lines))
    assert REASON.match(str(exc.value)) and str(exc.value).startswith("disk_short: 3 files")
    assert "1.2 GB free" in str(exc.value)
    assert not (data / "m" / "M1" / "raw").exists()  # nothing was fetched


def test_a_run_that_fits_proceeds_and_says_what_it_needs(tmp_path, monkeypatch):
    data, lines = _data(tmp_path), []
    monkeypatch.setattr(fetch, "free_bytes", lambda _p: 16 * GB)
    LitigationRun._disk_preflight(_run(data, lines))
    assert lines == ["[fetch] 3 files to fetch, 5.0 GB listed; 16.0 GB free"]


def test_the_reserve_alone_must_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "free_bytes", lambda _p: fetch.DISK_RESERVE_BYTES - 1)
    assert fetch.disk_check(tmp_path, 0)[0] is False
    monkeypatch.setattr(fetch, "free_bytes", lambda _p: fetch.DISK_RESERVE_BYTES)
    assert fetch.disk_check(tmp_path, 0)[0] is True
