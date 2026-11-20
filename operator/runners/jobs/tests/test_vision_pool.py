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
