"""A rehearsed job's copied usage is never counted as the job's spend.

``jobs rehearse`` (``medchron/rehearsal.py``) byte-copies the job's slug dir,
its per-unit ``runs/<unit>/usage-ledger.jsonl`` included, into
``<job_dir>/rehearsal/<stamp>/data/``. The spend a lane records is what the
job's own runner budgets (``medchron/driver.py``: the per-unit ledgers of the
job's units plus the orphan ledger), so a job rehearsed and then resumed
records exactly what it spent, not that plus every rehearsal's copy. The
figure is debited to the firm's month, so a copy counted twice is a firm
charged twice.

Each case runs the medchron lane's REAL class, the REAL ``rehearsal.prepare``
and the lane's own resume marker, with a child that appends one priced usage
row ($5.00) to the unit's ledger on each run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jobs import budget, resume, smd_stop, spend, stop
from jobs.medchron import rehearsal
from jobs_testkit import PRICING

ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "read"}

# Run 1 crashes with no verdict after spending $5.00; run 2 (the resume) spends
# $5.00 more and delivers, reporting the $10.00 its budget counted.
RUNNER = """
import json, pathlib, sys
jd = pathlib.Path(sys.argv[1])
ledger = jd / 'data' / 'example' / 'runs' / 'alpha' / 'usage-ledger.jsonl'
ledger.parent.mkdir(parents=True, exist_ok=True)
with ledger.open('a') as f:
    f.write(json.dumps(%s) + '\\n')
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n == 1:
    sys.exit(1)
print(json.dumps([{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 10.0,
                   "pages": 300, "documents": 40, "folder_id": "folder-9",
                   "files": [{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}],
                   "proof": {"readback": "confirmed", "files": [{"name": "x.docx", "file_id": "f1", "size": 10, "sha256": "a" * 64}], "review": "passed", "review_cents": 0}}]))
""" % repr(ROW)


@pytest.fixture(autouse=True)
def _pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(p))
    monkeypatch.setattr(resume, "STOP_FLAG_GRACE_SECONDS", 0.2)


def _rehearsed_after_one_run(tmp_path: Path):
    import test_upload_daemon as t

    d, broker = t._daemon(tmp_path, script=RUNNER)
    t._submit(d, broker, "01A")
    assert d.tick() == "failed"  # run 1: $5.00 spent, no verdict
    jd = d.jobs / "01A"
    d.heartbeat(running=None)  # the daemon's next poll: the seat is parked, as rehearse requires
    copy = rehearsal.prepare(jd)
    assert (copy / "data" / "example" / "runs" / "alpha" / "usage-ledger.jsonl").is_file()
    return d, broker, jd


def test_a_rehearsed_then_resumed_job_records_exactly_what_it_spent(tmp_path):
    """MUTATION: price every usage-ledger.jsonl under the job dir (an rglob)
    and the rehearsal's copy is counted too: 1500 cents, not 1000."""
    d, broker, _ = _rehearsed_after_one_run(tmp_path)
    assert broker.records[-1][2]["cents"] == 500
    (d.queue / ".resume-01A.json").write_text(json.dumps({"job_id": "01A", "reason": "rehearsed", "redo": []}))
    assert d.tick() == "delivered"
    assert broker.records[-1][1] == "delivered"
    assert broker.records[-1][2]["cents"] == 1000


def test_the_stop_paths_price_only_the_jobs_own_ledgers(tmp_path):
    """``jobs stop`` and SMD's stop record and print the job's spend through
    the same function: the rehearsal's copy is not the job's."""
    _, _, jd = _rehearsed_after_one_run(tmp_path)
    assert spend.priced_cents(jd, "medchron") == 500
    assert spend.priced_cents(jd, "medchron") == smd_stop.spend.priced_cents(jd, "medchron")
    assert stop.spend is spend


def test_every_lane_names_its_ledgers_and_never_globs(tmp_path):
    """Each lane prices only the ledger its runner budgets; a copy anywhere
    else under the job dir (a rehearsal, a seed) is never priced."""
    for lane in [*stop.LANES, "note"]:
        jd = tmp_path / lane
        (jd / "rehearsal" / "x" / "data").mkdir(parents=True)
        (jd / "rehearsal" / "x" / "data" / spend.LEDGER_NAME).write_text(json.dumps(ROW) + "\n")
        assert spend.priced_cents(jd, lane) == 0, lane
