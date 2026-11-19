"""The model half of the merge runs its batches several at once and still
joins them in batch order, however the replies arrive."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from jobs.medchron import config as config_mod, job as job_mod
from jobs.stages import merge_model as mm
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm_config_path)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: None,
    )


def _blocks(n: int) -> list[str]:
    # each block alone fills a batch, so n blocks are n batches
    return [f"##### CLUSTER {i}\n" + "x" * mm.BATCH_CHARS + "\n" for i in range(1, n + 1)]


def _wire(monkeypatch: pytest.MonkeyPatch, run) -> None:
    monkeypatch.setattr(mm, "_run_batch", run)
    monkeypatch.setattr(mm.mf, "check", lambda *_a, **_k: (0, []))


def test_batches_run_at_once_and_join_in_batch_order(
    monkeypatch: pytest.MonkeyPatch, job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path
) -> None:
    live, peak, lock = 0, 0, threading.Lock()

    def run(sr, d, model, system, text, label, hd, depth=0):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02 * (7 - int(label)))  # the first batch answers last
        with lock:
            live -= 1
        return [f"merged {label}"]

    _wire(monkeypatch, run)
    log: list[str] = []
    out = mm.merge_blocks(_sr(job_dir, firm_config_path, data_root, log), tmp_path, _blocks(6), hd=None)  # type: ignore[arg-type]
    assert out == "\n\n".join(f"merged {i}" for i in range(1, 7))
    assert peak > 1, "the batches ran one after another"
    assert peak <= mm.MERGE_WORKERS


def test_one_failed_batch_fails_the_merge_and_names_it(
    monkeypatch: pytest.MonkeyPatch, job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path
) -> None:
    def run(sr, d, model, system, text, label, hd, depth=0):
        return None if label == "2" else [f"merged {label}"]

    _wire(monkeypatch, run)
    log: list[str] = []
    out = mm.merge_blocks(_sr(job_dir, firm_config_path, data_root, log), tmp_path, _blocks(3), hd=None)  # type: ignore[arg-type]
    assert out is None
    assert any("merge batch 2 FAILED" in line for line in log)
