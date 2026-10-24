"""2026-10-23: the audit decides two things by rule that used to wait on SMD.

* A control that comes back SUPPORTED where the control page's OWN TEXT
  carries the claim's dates and values was badly chosen (Curtis, matter
  200530: a diagnosis index): retired by rule, honoured by every consumer. A
  SUPPORTED control whose page does not carry them is still a rubber stamp,
  and the round is INVALID.
* A live claim still not SUPPORTED at the final gate is dropped and listed in
  the Review Notes, never silently and never as a hold.
"""

from __future__ import annotations

import json
from pathlib import Path

from medchron.audit import claims as CL, coverage, retired
from medchron.audit.run import AuditPaths, Round
from medchron.stages import audit_loop, review_policy
from medchron.stages.base import StageRun
from medchron.update import proofs as P
from medchron_testkit import make_pdf
from test_audit import PROSE, SUPPORTED, Scripted, _exhibit_set, _sr, _write_doc, tool_msg

CLAIM = "Seen 01/02/2026 with blood pressure 120/80 and ibuprofen 400 mg."


def _eight_claims(sr: StageRun) -> None:
    _write_doc(
        sr,
        "\n\n".join(
            f"01/0{i}/2026\nExample Clinic | Medical Diagnoses\n\n{CLAIM} Visit {i} of eight. (Exhibit 1 - p. 1)"
            for i in range(1, 9)
        ),
    )


def _round(sr: StageRun, control_page: str, log: list[str]) -> tuple[int, AuditPaths]:
    out = _exhibit_set(sr, [PROSE, PROSE])
    (out / "Exhibit 2 - Riverside Imaging - 01-09-2026 (Medical Records).pdf").write_bytes(make_pdf([control_page]))
    _eight_claims(sr)
    paths = AuditPaths(sr.slug_dir, "alpha")
    return Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=1).execute(), paths


def test_a_supported_control_whose_page_carries_the_claims_values_is_retired_not_invalid(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)), log=log)
    rc, paths = _round(sr, PROSE, log)
    assert rc == 0, log
    ctl = [r for r in CL.read_rows(paths.results) if r["kind"].startswith("control")]
    assert len(ctl) == 1 and ctl[0]["verdict"] == "SUPPORTED" and ctl[0]["retired"] == "rule"
    assert retired.keys(paths.out) == {ctl[0]["key"]}
    row = json.loads(retired.path(paths.out).read_text())["retired"][0]
    assert row["by"] == "rule" and "date:2026-01-02" in row["reason"]
    # The final gate and the update's proof 5 honour it too.
    ok, summary = coverage.check(paths, log.append)
    assert ok and summary["controls_bad"] == 0
    r = P.Result()
    x = P.Inputs([], [], "", "", [], [], CL.read_rows(paths.results), [], [], set())
    x.retired_controls = retired.keys(paths.out)
    P._audit(x, r)
    assert not any(f.startswith("5") for f in r.fails), r.fails


def test_a_supported_control_whose_page_does_not_carry_the_claim_is_still_a_rubber_stamp(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)))
    rc, paths = _round(sr, "Dental cleaning performed on 05/14/2024. No complaints. Return in six months.", [])
    assert rc == 2
    assert retired.keys(paths.out) == set()
    assert not coverage.check(paths, lambda *_: None)[0]


def test_carried_needs_every_strong_value_or_two_names() -> None:
    page = "Visit 01/02/2026. BP 120/80. Ibuprofen 400 mg. Riverside Imaging."
    assert retired.carried("On 01/02/2026 blood pressure was 120/80.", page)
    assert not retired.carried("On 01/03/2026 blood pressure was 120/80.", page)  # one date absent
    assert not retired.carried("On 01/02/2026 blood pressure was 120/80.", "")  # a scan: no text layer
    assert not retired.carried("the neck hurt", page)  # nothing checkable


def _gate_doc(sr: StageRun) -> AuditPaths:
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(
        sr,
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
        "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)\n\n"
        "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)",
    )
    paths = AuditPaths(sr.slug_dir, "alpha")
    body = CL.body_of(paths.doc.read_text())
    keys = {c["claim"][:20]: c["key"] for c in CL.extract_claims(body, {1})}
    CL.append_row(paths.results, {"key": keys["The patient reports "], "kind": "real", "verdict": "SUPPORTED"})
    CL.append_row(
        paths.results, {"key": keys["Blood pressure was r"], "kind": "real", "verdict": "PARTIAL", "note": "no vitals"}
    )
    return paths


def test_a_claim_not_supported_at_the_gate_is_dropped_and_listed_for_the_notes(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)))
    paths = _gate_doc(sr)
    assert not coverage.check(paths, lambda *_: None)[0]
    assert audit_loop._drop_at_gate(sr, paths)
    doc = paths.doc.read_text()
    assert "120/80" not in doc and "neck pain rated 6 of 10" in doc
    assert coverage.check(paths, lambda *_: None)[0]
    audit_loop._record_drops(sr, paths)
    rec = json.loads((paths.out / audit_loop.DROPPED_RECORD).read_text())
    assert rec["count"] == 1 and rec["claims"][0]["how"] == audit_loop.GATE_HOW
    assert rec["claims"][0]["verdict"] == "PARTIAL" and "120/80" in rec["claims"][0]["claim"]
    assert audit_loop.GATE_HOW == review_policy.GATE_DROP
    assert any("120/80" in ln for ln in review_policy.gate_drops(paths.out))
