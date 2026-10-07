"""The audit loop CONVERGES: a claim the loop was designed to weaken or drop can
never hold the package at the final gate.

Two defects, both live on a held client matter (2026-10-07, SUPPORTED=300 PARTIAL=2
at the gate):

* A claim REPAIRED IN THE LAST ROUND got a new key (the key hashes the text)
  and no verdict. The residual drop only drops claims carrying a failing
  verdict, so the just-repaired claim escaped it, the post-drop audit graded it
  PARTIAL, and the gate held on a claim the drop policy exists to remove.
* A claim whose citation sits on the line after its text could not be located
  by `claim + " " + cite`, so repair logged `SKIP: claim not located` every
  round and the drop skipped it too: never repaired, never dropped, held.

The fix converges after the round cap: audit, drop whatever is not SUPPORTED,
re-audit, until clean; every drop lands in `audit-dropped-claims.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_audit import (
    PROSE,
    SUPPORTED,
    UNSUPPORTED,
    Scripted,
    _claim_of,
    _exhibit_set,
    _is_control,
    _sr,
    _write_doc,
    text_msg,
    tool_msg,
)

from medchron.audit import claims as CL, coverage, repair
from medchron.audit.run import AuditPaths
from medchron.stages import audit_loop

PARTIAL = {
    "verdict": "PARTIAL",
    "unsupported_assertions": ["blood pressure value"],
    "contradictions": [],
    "note": "the page records a reading but not that one",
}
GOOD = "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)"


def _is_repair(p: dict) -> bool:
    return bool(p.get("system")) and "correct one sentence-group" in p["system"][0]["text"]


def test_a_claim_repaired_in_the_last_round_and_still_partial_is_dropped_not_held(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    n = {"repairs": 0}

    def reply(p, _):
        if _is_repair(p):
            # Every repair produces NEW text (a new key) that still overstates.
            n["repairs"] += 1
            return text_msg(f"Blood pressure was recorded as 120/80 at visit number {n['repairs']}. (Exhibit 1 - p. 2)")
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        return tool_msg(PARTIAL if "120/80" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(
        sr,
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n"
        "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)",
    )
    assert audit_loop.run(sr) == 0, "a PARTIAL the loop repaired in its last round must be dropped, not held"
    assert n["repairs"] == audit_loop.ROUNDS
    paths = AuditPaths(sr.slug_dir, "alpha")
    doc = paths.doc.read_text()
    assert "120/80" not in doc and "neck pain rated 6 of 10" in doc
    ok, summary = coverage.check(paths, lambda *_: None)
    assert ok and summary["live"] == 1
    dropped = json.loads((paths.out / "audit-dropped-claims.json").read_text())
    assert dropped["count"] == 1 and "120/80" in dropped["claims"][0]["claim"]
    assert dropped["claims"][0]["verdict"] == "PARTIAL"


def test_a_claim_whose_citation_is_on_the_next_line_is_repaired_and_never_holds(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    def reply(p, _):
        if _is_repair(p):
            return text_msg(
                "Blood pressure was recorded as 120/80 which the page still does not say. (Exhibit 1 - p. 2)"
            )
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        return tool_msg(PARTIAL if "120/80" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(
        sr,
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n"
        "Blood pressure was recorded as 120/80 at this visit.\n(Exhibit 1 - p. 2)",
    )
    assert audit_loop.run(sr) == 0, "an un-locatable claim must be located by its span, never left to hold"
    paths = AuditPaths(sr.slug_dir, "alpha")
    assert "120/80" not in paths.doc.read_text()
    edits = CL.read_rows(paths.out / "repair-edits.jsonl")
    assert not [e for e in edits if "not located" in str(e.get("result"))]


def test_a_clean_round_after_repairs_stops_the_loop(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """The round's verdict is read from the LIVE claims' latest rows. Before,
    every historical failing row counted, so one repaired flag made every later
    round report problems and the loop always ran to the cap."""
    state = {"repaired": False}
    log: list[str] = []

    def reply(p, _):
        if _is_repair(p):
            state["repaired"] = True
            return text_msg("Blood pressure was recorded at this visit. (Exhibit 1 - p. 2)")
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        return tool_msg(UNSUPPORTED if "120/80" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(
        sr,
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n"
        "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)",
    )
    assert audit_loop.run(sr) == 0
    assert any("round 2: audit clean" in line for line in log)
    assert not any("round cap reached" in line for line in log)


def test_the_final_drop_pass_drops_a_widened_citation_an_earlier_pass_fixes(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Nothing re-audits after the last drop pass, so a citation rewritten
    there would ship unverified: it is dropped instead. An earlier pass still
    rewrites it, so supported content is only lost when the passes run out."""

    def no_model(p, _):
        raise AssertionError("a drop pass never calls the model")

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(no_model))
    _exhibit_set(sr, [PROSE, PROSE, PROSE])
    widened = "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)"
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + widened)
    paths = AuditPaths(sr.slug_dir, "alpha")
    body = CL.body_of(paths.doc.read_text())
    keys = {c["claim"][:5]: c["key"] for c in CL.extract_claims(body, {1})}
    CL.append_row(paths.results, {"key": keys["The p"], "kind": "real", "verdict": "SUPPORTED"})
    CL.append_row(
        paths.results, {"key": keys["Blood"], "kind": "real", "verdict": "SUPPORTED_WIDENED", "widened": [1, 2, 3]}
    )
    original = paths.doc.read_text()
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, drop_residual=True, pause=0)
    assert "(Exhibit 1 - p. 1-3)" in paths.doc.read_text(), "an earlier pass rewrites the citation"
    paths.doc.write_text(original)
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, drop_residual=True, final=True, pause=0)
    doc = paths.doc.read_text()
    assert "120/80" not in doc and "neck pain rated 6 of 10" in doc
