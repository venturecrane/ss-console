"""No repair loses a value the audit did not name (2026-10-13; the laptop
pipeline's repair_value_check and drop_losses, after a home-medication list
lost every dosage to a repair on 2026-10-09). Driven through `repair.run`
with a scripted repair tier, the way the audit loop calls it."""

from __future__ import annotations

import json
from pathlib import Path

from test_audit import PROSE, Scripted, _exhibit_set, _sr, _write_doc, text_msg

from medchron.audit import claims as CL, repair
from medchron.audit.run import AuditPaths

MEDS = "Lisinopril 10 mg daily and metoprolol 25 mg twice daily were continued at this visit. (Exhibit 1 - p. 1)"
FINDING = {"verdict": "PARTIAL", "unsupported_assertions": ["twice daily"], "contradictions": [], "note": "once daily"}


def _scene(job_dir: Path, firm: Path, data_root: Path, answer: str):
    sr = _sr(job_dir, firm, data_root, Scripted(lambda _p, _n: text_msg(answer)))
    _exhibit_set(sr, [PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + MEDS)
    paths = AuditPaths(sr.slug_dir, "alpha")
    key = CL.extract_claims(CL.body_of(paths.doc.read_text()), {1})[0]["key"]
    CL.append_row(paths.results, {"key": key, "kind": "real", "exhibit": 1, "page_spec": "1", **FINDING})
    return sr, paths


def _edits(paths: AuditPaths) -> list[dict]:
    return [json.loads(x) for x in (paths.out / "repair-edits.jsonl").read_text().splitlines()]


def test_a_repair_that_drops_the_dosages_is_refused_and_the_claim_kept(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, paths = _scene(job_dir, firm_config_path, data_root, "Lisinopril and metoprolol were continued at this visit.")
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, pause=0)
    assert "metoprolol 25 mg" in paths.doc.read_text()  # the old repair applied the rewrite
    row = _edits(paths)[-1]
    assert row["result"].startswith("REJECT") and row["lost_values"] == ["10", "25"]


def test_a_repair_that_removes_only_what_the_audit_named_is_applied(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, paths = _scene(
        job_dir,
        firm_config_path,
        data_root,
        "Lisinopril 10 mg daily and metoprolol 25 mg were continued at this visit.",
    )
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, pause=0)
    assert "twice daily" not in paths.doc.read_text() and "metoprolol 25 mg" in paths.doc.read_text()


def test_a_repair_round_drop_is_refused_and_the_round_cap_drop_names_what_it_took(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, paths = _scene(job_dir, firm_config_path, data_root, "DROP")
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, pause=0)
    assert "metoprolol 25 mg" in paths.doc.read_text()
    assert _edits(paths)[-1]["result"] == "REJECT: drop would lose values the audit did not name"
    # Past the cap the claim must still go (the gate admits no unsupported
    # claim), and its row names every value it took: a named loss, never silent.
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, drop_residual=True, final=True, pause=0)
    assert "metoprolol" not in paths.doc.read_text()
    assert _edits(paths)[-1]["lost_values"] == ["10", "25"]
