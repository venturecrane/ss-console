"""SMD keeps a sentence the update's entry read disputes (``medchron/update_settle.py``),
and three resume edges of the update's audit: a job cut mid-upload, a redo
naming the removed update review, and the projection after a hand edit.
Through the real stage, daemon, driver and planner. Every value is invented."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import test_upload_daemon as ud
from jobs.medchron import update_settle as US
from jobs.medchron.gates import claim_audit
from jobs.resume import MARKER_PREFIX
from jobs.stages import entry_audit as EA, entry_audit_io as IO
from test_entry_audit import VALUE, YES, Client
from test_update_entry_read import DOC, E1, NOT_SUPPORTED, _on, _record, scene  # noqa: F401 - scene is a pytest fixture the tests request by name

JOB = "01M5J9RGY9G9FFXYBZ3CH59XBY"
HOLD = "audit coverage: an entry was not read against its pages, a line found wrong was not corrected, or the edit tripwire held"
LINE = "The pulse was 72 at triage during the visit. (Exhibit 1 - p. 2)"


def _seat(tmp_path: Path, job_dir: Path, reason: str = HOLD) -> Any:
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "held", "reason": reason}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": "held"}))
    return d


def test_smd_keeps_a_disputed_line_and_it_is_never_held_or_edited_again(scene, tmp_path: Path, job_dir: Path) -> None:
    sr, doc, log = scene(Client(_on("N1", VALUE), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD
    d = _seat(tmp_path, job_dir)
    (held,) = US.held_lines(sr.slug_dir, "alpha")
    assert held["line"] == LINE and held["class"] == "value"
    p = US.plan(d, JOB, shas=[held["sha"]], by="smd-operator", source="vfy_TEST")
    US.apply(d, p, at="2026-11-20T00:00:00Z")
    rec = json.loads(US.path(sr.slug_dir, "alpha").read_text())
    assert rec["lines"][held["sha"]] == {"by": "smd-operator", "source": "vfy_TEST", "at": "2026-11-20T00:00:00Z"}
    assert json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())["intervention"] == "decide_update"
    # The resume: a later read finds it wrong again, with a correction a second reader supports.
    again = Client(_on("N1", VALUE), YES)
    sr2, _d, log2 = scene(again, fresh=False)
    assert EA.run(sr2) == 0, log2
    assert doc.read_text(encoding="utf-8") == DOC and LINE in DOC  # never edited by the reader
    r = _record(sr2)
    assert r["recorded_by_reason"] == {"smd_settled": 1} and r["unresolved"] == 0  # still read, still recorded
    assert again.calls.count("verify") == 0


def test_falsifier_without_smds_mark_the_resume_holds_again(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", VALUE), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD
    sr2, _d, _l = scene(Client(_on("N1", VALUE), NOT_SUPPORTED), fresh=False)
    assert EA.run(sr2) == EA.EXIT_HELD


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"shas": ["0" * 64]}, "not lines job"),
        ({"shas": []}, "name the held lines"),
        ({"by": ""}, "--by"),
        ({"reason": "identity: an emailed attachment names a different person"}, "not an audit hold"),
    ],
)
def test_the_command_refuses_what_it_cannot_settle(scene, tmp_path: Path, job_dir: Path, kw, why) -> None:
    sr, doc, log = scene(Client(_on("N1", VALUE), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD
    d = _seat(tmp_path, job_dir, kw.get("reason", HOLD))
    (held,) = US.held_lines(sr.slug_dir, "alpha")
    args = {"shas": kw.get("shas", [held["sha"]]), "by": kw.get("by", "smd"), "source": "vfy_x"}
    with pytest.raises(US.Refused, match=why):
        US.plan(d, JOB, **args)
    assert not US.path(sr.slug_dir, "alpha").exists()


# ---- item 10: a job cut mid-upload is filed in part ---------------------------------
def test_an_old_audit_job_cut_mid_upload_is_not_read_again(tmp_path: Path) -> None:
    from jobs.medchron.state import RunState

    st = RunState.load_or_new(tmp_path / "state.json", slug="example-matter", unit="alpha")
    st.finish("audit", status="done", exit_code=0, dollars=0.0, pages=0)
    (tmp_path / "runs" / "alpha").mkdir(parents=True)
    assert claim_audit.stale_update_audit(tmp_path, "alpha", st)
    rec = {"folder_id": "f1", "files": [{"name": "A.docx", "sha256": "ab", "bytes": 1, "sent": True}]}
    (tmp_path / "runs" / "alpha" / "delivery.json").write_text(json.dumps(rec))
    assert not claim_audit.stale_update_audit(tmp_path, "alpha", st)


# ---- item 12: the projection after a hand edit -----------------------------------
def test_the_projection_counts_an_entry_read_before_a_hand_edit_as_still_to_read(scene) -> None:
    from jobs.stages.read_inputs import client_line

    sr, doc, log = scene(
        Client(
            lambda _t: json.dumps(
                {
                    "lines": {"D": "ok", "P": "ok", **{f"S{i}": "ok" for i in range(1, 60)}},
                    "findings": [],
                    "complete": True,
                }
            )
        )
    )
    assert EA.run(sr) == 0, log
    n, done, _tin, _tout = IO.tokens(sr.slug_dir, "alpha", sr.cfg, True, client_line(sr))
    assert done == n  # every answer is for the request as it stands
    doc.write_text(DOC.replace(E1, E1.replace("72", "74")), encoding="utf-8")  # SMD's hand edit of N1
    n2, done2, _t2, _o2 = IO.tokens(sr.slug_dir, "alpha", sr.cfg, True, client_line(sr))
    assert n2 == n and done2 == n - 1
