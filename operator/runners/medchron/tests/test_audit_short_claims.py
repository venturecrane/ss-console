"""Parity N2: a short claim that carries a value is graded, in both lanes.

The audit skipped every claim under 30 characters, so a cited line such as
"Magnesium was 1.7 mg/dL." was never checked: on a real update 12 such lines
(lab values, visit durations, a dated eGFR) were cited and ungraded, and a
wrong value among them would have reached the firm. The floor now holds only
for value-free fragments (``claims.graded``). Every value is invented; the
first two tests fail on the code before N2.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_audit import (
    PROSE,
    SUPPORTED,
    Scripted,
    _claim_of,
    _exhibit_set,
    _is_control,
    _sr,
    _write_doc,
    text_msg,
    tool_msg,
)

from medchron.audit import claims as CL, coverage
from medchron.audit.run import AuditPaths
from medchron.stages import audit_loop

GOOD = "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)"
WRONG_MG = "Magnesium was 9.9 mg/dL. (Exhibit 1 - p. 2)"
SHORT_OK = "Phosphorus was 2.6 mg/dL. (Exhibit 1 - p. 1)"
CONTRADICTED = {
    "verdict": "CONTRADICTED",
    "unsupported_assertions": ["magnesium 9.9"],
    "contradictions": ["the page records magnesium 1.9 mg/dL"],
    "note": "the value differs from the page",
}


def _is_repair(p: dict) -> bool:
    return bool(p.get("system")) and "correct one sentence-group" in p["system"][0]["text"]


def test_a_short_claim_with_a_value_is_graded_and_a_value_free_fragment_is_not() -> None:
    body = (
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
        f"{WRONG_MG}\n\nTotal time was 45 minutes. (Exhibit 1 - p. 2)\n\n"
        "Seen on 01/02/26. (Exhibit 1 - p. 3)\n\n"
        "Allergies were unchanged. (Exhibit 1 - p. 2)\n\n1. Unchanged. (Exhibit 1 - p. 2)\n"
    )
    got = [c["claim"] for c in CL.extract_claims(body, {1})]
    assert "Magnesium was 9.9 mg/dL." in got
    assert "Total time was 45 minutes." in got and "Seen on 01/02/26." in got
    # A fragment with no value keeps the floor; an enumerator is not a value.
    assert not any("Allergies" in c or "Unchanged" in c for c in got)


def test_full_lane_a_short_wrong_magnesium_value_is_caught_and_the_loop_converges(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    graded: list[str] = []

    def reply(p, _):
        if _is_repair(p):
            return text_msg("Magnesium was measured on the panel. (Exhibit 1 - p. 2)")
        if _is_control(p):
            return tool_msg(CONTRADICTED)
        graded.append(_claim_of(p))
        return tool_msg(CONTRADICTED if "9.9" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + SHORT_OK + "\n\n" + WRONG_MG)
    assert audit_loop.run(sr) == 0
    paths = AuditPaths(sr.slug_dir, "alpha")
    doc = paths.doc.read_text()
    assert "9.9" not in doc, "a short claim with a wrong value must be caught, never passed through"
    assert SHORT_OK in doc  # a short supported value is graded and kept
    assert any("Phosphorus was 2.6" in c for c in graded) and any("9.9" in c for c in graded)
    ok, summary = coverage.check(paths, lambda *_: None)
    assert ok and summary["live"] == 3


def test_full_lane_converges_when_a_short_wrong_value_cannot_be_repaired(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Repair returns the claim unchanged every round: the round cap drops it,
    the drop names the value it took, and nothing holds."""

    def reply(p, _):
        if _is_repair(p):
            return text_msg(WRONG_MG)
        if _is_control(p):
            return tool_msg(CONTRADICTED)
        return tool_msg(CONTRADICTED if "9.9" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + WRONG_MG)
    assert audit_loop.run(sr) == 0
    paths = AuditPaths(sr.slug_dir, "alpha")
    assert "9.9" not in paths.doc.read_text() and "neck pain rated 6 of 10" in paths.doc.read_text()
    dropped = json.loads((paths.out / "audit-dropped-claims.json").read_text())
    assert dropped["count"] == 1 and "9.9" in dropped["claims"][0]["claim"]
