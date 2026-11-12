"""The job child the host matrix runs (``host_matrix_kit.py``).

It IS the runner: ``python host_matrix_child.py <subcommand> <job_dir> ...``
calls the real ``jobs.__main__.main`` with the lane's own subcommand and
arguments, so the real CLI arms the stop checkpoints, catches what it catches,
writes ``verdict.json`` through ``verdict.write`` and exits with the code the
real CLI maps. Only the lane's RUN class is swapped, for one that returns the
verdict the matrix cell names, built by the lane's REAL verdict class
(``<lane>/outcome.py``, the chronology driver's ``Outcome``). Nothing paid runs.

The cell comes in on ``HOST_MATRIX_ENDING`` (added by the matrix after it
recorded the env the lane built, so it never appears in a golden).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

ENDING = os.environ.get("HOST_MATRIX_ENDING", "delivered")
PROOF_FILES = [{"name": "x.docx", "file_id": "f1", "size": 10, "sha256": "a" * 64}]
PROOF = {"readback": "confirmed", "files": PROOF_FILES, "review": "passed", "review_cents": 0}


def _usage(job_dir: Path, lane: str) -> None:
    """One paid call's receipt, through the real usage ledger the lane's spend reads."""
    from jobs import ledger, spend

    paths = spend.ledgers(job_dir, lane)
    path = paths[0] if paths else job_dir / "data" / spend.LEDGER_NAME
    ledger.Ledger(path).record(
        "read", "claude-opus-5", {"input_tokens": 1_000_000, "output_tokens": 0}, effort=None, cache=False, batch=False
    )


def _wait_for_stop(job_dir: Path) -> None:
    """A run still working when the Machine stops: the real checkpoint raises
    in an armed runner; an unarmed one is terminated by the lane's drain."""
    from jobs import stop_request

    # The matrix stops the Machine only once the run is under way (its
    # ``stopping`` reads this mark), so a slow interpreter start under load
    # cannot turn a checkpoint into a terminate.
    (job_dir / ".host-matrix-running").write_text("1")
    end = time.monotonic() + 30
    while time.monotonic() < end:
        stop_request.check("host matrix")
        time.sleep(0.05)
    raise SystemExit(9)


def _smd_stop_now(job_dir: Path) -> None:
    """SMD stops the job while it runs (``jobs stop``'s own writer), then the
    run keeps going until the lane's grace ends it."""
    from jobs import smd_stop

    smd_stop.request(job_dir, by="smd@example.test", why="host matrix stop", at=1_700_000_000.0)
    end = time.monotonic() + 30
    while time.monotonic() < end:
        time.sleep(0.05)
    raise SystemExit(9)


def _before(job_dir: Path, lane: str) -> None:
    """The endings that never reach a verdict."""
    if ENDING == "no_verdict_usage":
        _usage(job_dir, lane)
        sys.stdout.flush()
        os._exit(1)
    if ENDING == "machine_stop":
        _wait_for_stop(job_dir)
    if ENDING == "stopped_by_smd_running":
        _smd_stop_now(job_dir)
    if ENDING == "broker_down_mid_run":
        # The broker goes away while the job runs: the lane cannot record its ending.
        sock = Path(os.environ["HOST_MATRIX_SOCKET"])
        sock.replace(sock.with_name(sock.name + ".down"))


def _state(ending: str) -> str:
    return {"delivered": "delivered", "held": "held", "broker_down_mid_run": "delivered"}.get(ending, "failed")


# -- the verdicts, by each lane's real verdict class ---------------------------------


def _medchron(job_dir: Path) -> list[Any]:
    from jobs.medchron.driver import Outcome

    s = _state(ENDING)
    if s == "delivered":
        return [
            Outcome(
                unit="alpha", outcome="delivered", reason=None, stage=None, dollars=5.0, pages=30, documents=4,
                folder_id="folder-9", files=[{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}],
                proof=dict(PROOF),
            )
        ]  # fmt: skip
    stage = "classify" if s == "held" else "extract"
    reason = "decision: two files could not be placed" if s == "held" else "extract: a stage failed"
    return [Outcome(unit="alpha", outcome=s, reason=reason, stage=stage, dollars=5.0, pages=30, documents=4)]


def _verdict_class(module: str) -> Any:
    """The lane's own verdict class, from a fixed table."""
    if module == "demand":
        from jobs.demand.outcome import Verdict
    elif module == "drafting":
        from jobs.drafting.outcome import Verdict
    elif module == "litigation":
        from jobs.litigation.outcome import Verdict
    elif module == "casefile":
        from jobs.casefile.outcome import Verdict
    else:
        raise ValueError(module)
    return Verdict


def _demand_like(module: str, job_dir: Path, **extra: Any) -> Any:
    Verdict = _verdict_class(module)
    s = _state(ENDING)
    if s == "delivered":
        files = [{"name": "Out.docx", "size": 41000, "role": module}]
        return Verdict(
            "delivered", stage="file", dollars=5.0, folder_id="folder-7", files=files, proof=dict(PROOF), **extra
        )
    if s == "held":
        return Verdict("held", stage="render", reason="format: the layout did not hold", dollars=5.0, **extra)
    return Verdict("failed", stage="compose", reason="unexpected: a stage failed", dollars=5.0, **extra)


def _one_object(module: str, **delivered: Any) -> Any:
    Verdict = _verdict_class(module)
    s = _state(ENDING)
    if s == "delivered":
        files = [{"name": "Out.xlsx", "size": 900, "role": "report", "sha256": "b" * 64}]
        return Verdict(
            "delivered", stage="report", cents=500, folder_id="folder-7", files=files, proof=dict(PROOF), **delivered
        )
    if s == "held":
        return Verdict("held", stage="read", cents=500, reason="limit_held: the per-job cap was reached")
    return Verdict("failed", stage="read", cents=500, reason="unexpected: a stage failed")


def _negotiation() -> dict[str, Any]:
    s = _state(ENDING)
    if s == "delivered":
        return {
            "verdict": "delivered", "stage": "report", "cents": 500, "matters_total": 9, "matters_seeded": 0,
            "docs_read": 2, "docs_failed": 0, "notices": [],
            "proof": {"readback": "confirmed", "rows": 1, "review": "code_only", "review_cents": 0},
        }  # fmt: skip
    return {"verdict": "failed", "stage": "read", "cents": 500, "reason": "unexpected: a stage failed"}


def _records(job_dir: Path) -> Any:
    """The records verdict, and the reply facts its run leaves in ``data/``
    (``records/run.py`` writes ``facts.json`` and the cuts; here in the shape
    ``test_records_lane.py``'s runner writes them, since the reply composer the
    lane runs reads them)."""
    import hashlib
    import json

    from jobs.records.outcome import Verdict

    s = _state(ENDING)
    data = job_dir / "data"
    (data / "cuts").mkdir(parents=True, exist_ok=True)
    blob = b"%PDF-1.4 matrix cut " + b"x" * 5000
    sha = hashlib.sha256(blob).hexdigest()
    (data / "cuts" / "1-1.pdf").write_bytes(blob)
    cut = {"n": 1, "k": 1, "file_id": "00000001-aaaa-bbbb-cccc-dddddddddddd", "pages": "1-4", "page_count": 4,
           "sha256": sha, "size": len(blob)}  # fmt: skip
    (data / "cuts.json").write_text(json.dumps([{**cut, "name": "Clinic records pp. 1-4.pdf", "path": "cuts/1-1.pdf"}]))
    held = s == "held"
    facts = {
        "text": "Attached:\n- a", "html": "<p>Attached:</p>", "items": [],
        "hold_reason": "per_job_cap_usd: x" if held else None,
        "reply_text": "Attached:\n- a\n\nSMD is finishing this request." if held else "Here is what we found.\n\nAttached:\n- a",
        "reply_html": "<p>x</p>",
    }  # fmt: skip
    (data / "facts.json").write_text(json.dumps(facts))
    items = [{"n": 1, "outcome": "attached", "files": 1}]
    if s == "delivered":
        proof = {"readback": "confirmed", "files": [{"name": "1-1.pdf", "file_id": cut["file_id"], "size": len(blob),
                 "sha256": sha}], "review": "code_only", "review_cents": 0, "review_model": ""}  # fmt: skip
        return Verdict("delivered", stage="verdict", cents=500, items=items, cuts=[cut], proof=proof)
    if held:
        reason = "per_job_cap_usd: the run's spend reached the job's cost cap during locate; the run stopped"
        return Verdict("held", stage="verdict", cents=500, reason=reason, items=items)
    return Verdict("failed", stage="cut", cents=500, reason="unexpected: a stage failed")


class _Run:
    """Stands in for one lane's run class; its ``run()`` returns the cell's verdict."""

    lane = ""
    make: Any = None

    def __init__(self, job_dir: Any = None, *a: Any, **kw: Any) -> None:
        self.job_dir = Path(job_dir) if job_dir is not None else Path(sys.argv[2])

    def reopen(self, stages: Any) -> None:
        return None

    def _state(self) -> dict[str, Any]:
        return {}

    def _put(self, *a: Any) -> None:
        return None

    def run(self) -> Any:
        _before(self.job_dir, self.lane)
        return type(self).make(self.job_dir)


def _patch(sub: str) -> None:
    def run_class(lane: str, make: Any) -> type:
        return type(f"Matrix{lane.title()}Run", (_Run,), {"lane": lane, "make": staticmethod(make)})

    if sub == "run":
        from jobs.medchron import driver

        driver.Driver = run_class("medchron", _medchron)
    elif sub == "demand":
        from jobs.demand import run

        run.DemandRun = run_class("demand", lambda jd: _demand_like("demand", jd))
    elif sub == "draft":
        from jobs.drafting import run
        from jobs.drafting.revise import run as revise

        run.DraftingRun = run_class("drafting", lambda jd: _demand_like("drafting", jd, document_class="memo"))
        revise.ReviseRun = run.DraftingRun
    elif sub == "litigate":
        from jobs.litigation import run

        run.LitigationRun = run_class("litigation", lambda jd: _one_object("litigation", matters_total=9))
    elif sub == "casefile":
        from jobs.casefile import run

        run.CasefileRun = run_class("casefile", lambda jd: _one_object("casefile", matters_total=9))
    elif sub == "negotiate":
        from jobs.negotiation import run

        run.NegotiationRun = run_class("negotiation", lambda jd: _negotiation())
    elif sub == "library-setup":
        from jobs.library_setup import job as setup_job

        def run_dir(jd: Path, inputs_dir: Any = None) -> dict[str, Any]:
            _before(Path(jd), "drafting")
            s = _state(ENDING)
            if s == "delivered":
                return {"outcome": "delivered", "setup_outcome": "proposed", "counts": {"found": 2}}
            if s == "held":
                return {"outcome": "held", "hold": "no_candidates", "reason": "no_candidates: nothing to set up from"}
            raise RuntimeError("a stage failed")

        setup_job.run_dir = run_dir
    elif sub == "records":
        from jobs.records import run

        run.RecordsRun = run_class("records", _records)


def main() -> int:
    argv = sys.argv[1:]
    _patch(argv[0] if argv else "")
    from jobs.__main__ import main as jobs_main

    return int(jobs_main(argv))


if __name__ == "__main__":
    sys.exit(main())
