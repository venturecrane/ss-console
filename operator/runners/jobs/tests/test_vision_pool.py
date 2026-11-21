"""Page reading runs VISION_WORKERS pages at once when the stage is not
batched, and writes what one at a time wrote: the same transcripts and result
rows, the same part-file rows (in completion order, read back by page). All
text is invented."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path

import pytest

from jobs.stages import vision as vision_stage
from jobs_testkit import make_pdf
from test_paid_stages import Scripted, _msg, _seed_scan, _sr


class Slow(Scripted):
    def __init__(self) -> None:
        super().__init__(self._answer)
        self.live = self.peak = 0
        self.lock = threading.Lock()

    def _answer(self, params) -> object:
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        threading.Event().wait(0.02)
        with self.lock:
            self.live -= 1
        data = params["messages"][0]["content"][0]["source"]["data"]
        return _msg(f"HANDWRITING: no\nA typed page, image {hashlib.sha256(data.encode()).hexdigest()[:10]}.")


def _run(tmp: Path, job_dir: Path, firm: Path, data_root: Path, workers: int, mp: pytest.MonkeyPatch):
    mp.setattr(vision_stage, "VISION_WORKERS", workers)
    client = Slow()
    sr = _sr(job_dir, firm, data_root, client)
    _seed_scan(
        sr,
        {
            "s1": ("example fax.pdf", make_pdf([f"fax page {k}" for k in range(1, 6)])),
            "s2": ("example scan.pdf", make_pdf([f"scan page {k}" for k in range(1, 5)])),
        },
    )
    assert vision_stage.run(sr) == 0
    out = tmp / f"n{workers}"
    shutil.copytree(sr.slug_dir, out)
    shutil.rmtree(sr.slug_dir)
    return client, out


def test_eight_pages_at_once_write_what_one_at_a_time_wrote(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c1, one = _run(tmp_path, job_dir, firm_config_path, data_root, 1, monkeypatch)
    c8, eight = _run(tmp_path, job_dir, firm_config_path, data_root, 8, monkeypatch)
    assert c1.peak == 1 and c8.peak > 1
    assert len(c1.calls) == len(c8.calls) == 9
    assert sorted(json.dumps(c, sort_keys=True) for c in c1.calls) == sorted(
        json.dumps(c, sort_keys=True) for c in c8.calls
    ), "the requests changed"
    for fid in ("s1", "s2"):
        assert (one / "text" / f"{fid}.txt").read_bytes() == (eight / "text" / f"{fid}.txt").read_bytes()
        rows = [sorted((x / "partial" / f"{fid}.jsonl").read_text().splitlines()) for x in (one, eight)]
        assert rows[0] == rows[1]
    assert (one / "ocr_results.jsonl").read_bytes() == (eight / "ocr_results.jsonl").read_bytes()


def _twenty_pages(sr) -> None:
    _seed_scan(sr, {"s1": ("example fax.pdf", make_pdf([f"fax page {k}" for k in range(1, 21)]))})


class Gated(Slow):
    """The first `free` calls answer at once; every later call waits until
    `gate` is set, so nothing finishes between the event and its effect."""

    def __init__(self, free: int, gate: threading.Event) -> None:
        super().__init__()
        self.free, self.gate = free, gate
        self.after_gate = 0

    def _answer(self, params) -> object:
        with self.lock:
            n = len(self.calls)
            if self.gate.is_set():
                self.after_gate += 1
        if n > self.free:
            self.gate.wait(10)
        return super()._answer(params)


def test_a_failed_save_halts_the_pool_before_the_queued_pages_are_paid_for(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third save raises (a full disk). Before it, only the first wave and
    the two workers freed by saves 1 and 2 have started; after it, a worker
    may start at most the one page whose halt check it had already passed."""
    real = vision_stage.append_jsonl
    failed = threading.Event()
    saves = []

    def save(path: Path, row: dict) -> None:
        if path.parent.name == "partial":
            saves.append(row["page"])
            if len(saves) == 3:
                failed.set()
                raise OSError("no space left on device")
        real(path, row)

    monkeypatch.setattr(vision_stage, "append_jsonl", save)
    client = Gated(free=3, gate=failed)
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _twenty_pages(sr)
    with pytest.raises(OSError):
        vision_stage.run(sr)
    w = vision_stage.VISION_WORKERS
    assert client.after_gate <= w - 1, "a page started after the halt"
    assert len(client.calls) <= (w + 2) + (w - 1) < 20, "queued pages were paid for after the failure"


def test_no_page_starts_once_a_stop_is_requested(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop lands before the second call is even sent (the doorway's
    pre-call hook writes it), so each worker starts at most the one page whose
    stop check it had already passed, and none after."""
    from jobs import stop_request

    flag = tmp_path / stop_request.NAME
    monkeypatch.setattr(stop_request._Armed, "path", flag)
    asked = []

    def hook(stage: str) -> None:
        asked.append(stage)
        if len(asked) == 2:
            flag.write_text("stop")

    client = Slow()
    sr = _sr(job_dir, firm_config_path, data_root, client)
    sr.before_request = hook
    _twenty_pages(sr)
    with pytest.raises(stop_request.StopRequested):
        vision_stage.run(sr)
    assert len(client.calls) <= vision_stage.VISION_WORKERS < 20
