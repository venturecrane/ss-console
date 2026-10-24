"""A control a person found GENUINE can be retired; nothing else can.

WHY (2026-10-22). The Curtis audit ran 612 controls; two came back SUPPORTED
against a diagnosis index page that really does state every diagnosis, and the
whole audit went INVALID with nothing able to record that the control, not the
verifier, was wrong.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import test_upload_daemon as ud
from medchron import audit_decide
from medchron.audit import coverage as audit_coverage, retired
from medchron.audit.run import AuditPaths
from medchron.resume import MARKER_PREFIX

JOB = "01M5J9RGY9G9FFXYBZ3CH59XBY"
BAD = "40f8d510a95aebc2-ctl1"
INVALID = "audit INVALID: a control was wrongly SUPPORTED, no exhibit bytes, or a repair did not reconcile"


def _rows(data_root: Path) -> AuditPaths:
    paths = AuditPaths(data_root / "example-matter", "alpha")
    paths.out.mkdir(parents=True, exist_ok=True)
    rows = [
        {"key": BAD, "kind": "control(vs Ex1)", "verdict": "SUPPORTED"},
        {"key": "aaaa-ctl2", "kind": "control(vs Ex2)", "verdict": "UNSUPPORTED"},
        {"key": "bbbb", "kind": "claim", "verdict": "SUPPORTED"},
    ]
    paths.results.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return paths


def _seat(tmp_path: Path, job_dir: Path, *, reason: str = INVALID, state: str = "failed"):
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "failed", "reason": reason}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": state}))
    return d


def test_a_retired_control_no_longer_invalidates_the_audit(tmp_path: Path, job_dir: Path, data_root: Path) -> None:
    paths = _rows(data_root)
    d = _seat(tmp_path, job_dir)
    p = audit_decide.plan(d, JOB, keys=[BAD], reason="diagnosis index page", by="smd", source="vfy_x")
    audit_decide.apply(d, p)
    assert retired.keys(paths.out) == {BAD}
    assert json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())["job_id"] == JOB


def test_control_the_final_gate_counts_a_supported_control_until_it_is_retired(data_root: Path) -> None:
    paths = _rows(data_root)
    paths.doc.parent.mkdir(parents=True, exist_ok=True)
    from medchron.audit import claims as CL

    paths.doc.write_text(f"# Chronology\n{CL.BODY_START}\n{CL.BODY_END}\n")
    # Three SUPPORTED controls reach the INVALID threshold (``audit/controls``,
    # 2026-10-24: max(3, ceil(1%))); retiring one brings the round under it.
    for k in ("cccc-ctl2", "dddd-ctl2"):
        CL.append_row(paths.results, {"key": k, "kind": "control(vs Ex2)", "verdict": "SUPPORTED"})
    log: list[str] = []
    _ok, summary = audit_coverage.check(paths, log.append)
    assert summary["controls_bad"] == 3
    retired.path(paths.out).write_text(json.dumps({"retired": [{"key": BAD, "reason": "index page"}]}))
    _ok, summary = audit_coverage.check(paths, log.append)
    assert summary["controls_bad"] == 0


@pytest.mark.parametrize(
    ("kw", "keys", "needle"),
    [
        ({"reason": "coverage gate: x"}, [BAD], "not an invalid audit"),
        ({"state": "running"}, [BAD], "not holding"),
        ({}, ["aaaa-ctl2"], "not SUPPORTED control rows"),
        ({}, ["bbbb"], "not SUPPORTED control rows"),
    ],
)
def test_the_command_refuses_what_it_cannot_retire(
    tmp_path: Path, job_dir: Path, data_root: Path, kw: dict, keys: list[str], needle: str
) -> None:
    _rows(data_root)
    d = _seat(tmp_path, job_dir, **kw)
    with pytest.raises(audit_decide.Refused, match=needle):
        audit_decide.plan(d, JOB, keys=keys, reason="r", by="smd", source="s")
    assert not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()
