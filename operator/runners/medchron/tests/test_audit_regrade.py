"""A record-true fact is never deleted from a stale verdict (2026-10-07).

Three claims left a delivered package at the round cap. Their citations parsed
(each had a key and an exhibit/page on its audit row); the dropped-claims record
showed `exhibit: None` only because its drop rows were written by a build that
did not record them. Each claim was graded PARTIAL on a non-defect: a printed
page label differing from the cited page position, an omission ("also listed
but not mentioned (not an issue)"), a point the auditor itself called "actually
supported". The repair tier, shown those findings, returned the claim UNCHANGED
every round. The key hashes the text, so it never moved; every later round
resumed the cached PARTIAL; the cap deleted the claim.

Two fixes, tested here: an unchanged repair sends the claim back to be graded
again (and the first drop pass defers a claim still waiting for that), and the
auditor is told what is not a defect. A claim the audit keeps grading PARTIAL is
still dropped, per policy, and the record marks it contested.

The three live shapes are re-created with synthetic text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
from test_audit_convergence import GOOD, _is_repair

from medchron.audit import claims as CL
from medchron.audit.run import AuditPaths
from medchron.stages import audit_loop

HEADED = (
    "Renal and cardiovascular: Chronic kidney disease stage 3 and mixed hyperlipidemia were "
    "assessed on 02/17/2021, with a last GFR of 67. (Exhibit 1 - p. 2)"
)
SHORT = "The patient reported slight improvement in neck, upper back, and low back pain. (Exhibit 1 - p. 2)"
BULLETS = (
    "The Example Ambulance Call Report recorded:\n"
    "- Age 79, male, MVA, no LOC, no headstrike, (L) shoulder pain 5/10.\n"
    "- BP 170/75, pulse 85, respirations 18, O2 sat 100.\n"
    '- Handwritten notations read "driver seatbelt" and "No speed."\n'
    "(Exhibit 1 - p. 2)"
)
NON_DEFECT = {
    "verdict": "PARTIAL",
    "unsupported_assertions": [
        "Citation is to p.2 but the page prints 0001",
        "the page also lists another condition not mentioned in the claim (not an issue)",
    ],
    "contradictions": [],
    "note": "the page lists these conditions, consistent with the claim",
}


def test_the_three_live_shapes_extract_with_their_citation() -> None:
    """The premise to rule out first: were these claims un-parseable? No. A
    heading-prefixed claim, a short sentence, and a bullet block whose citation
    sits on its own line after the last bullet each extract as ONE claim
    carrying exhibit 1, p. 2, and the full text."""
    body = "\n\n".join(["01/02/2026\nExample Clinic | Medical Diagnoses", HEADED, SHORT, BULLETS]) + "\n"
    got = CL.extract_claims(body, {1})
    assert [(c["exhibit"], c["page_spec"]) for c in got] == [(1, "2")] * 3
    assert got[0]["claim"].startswith("Renal and cardiovascular:")
    assert got[2]["claim"].startswith("The Example Ambulance Call Report recorded:\n- Age 79")
    assert got[2]["claim"].endswith('"No speed."')
    spans = CL.claim_spans(body, {1})
    assert all(spans[c["key"]] in body for c in got)


def _cited_audit(p: dict) -> bool:
    """The claim's own audit call (not its widen retry, not a control)."""
    return "cited to Exhibit 1 p.2)" in p["messages"][0]["content"][-1]["text"]


def _run_shape(job_dir: Path, firm_config_path: Path, data_root: Path, claim_text: str, partial_for: int):
    """The auditor grades `claim_text` NON_DEFECT for its first `partial_for`
    own audits and SUPPORTED after; the repair tier always returns it unchanged."""
    seen = {"audits": 0, "repairs": 0}
    marker = claim_text.split()[1]

    def reply(p, _):
        if _is_repair(p):
            seen["repairs"] += 1
            return text_msg(p["messages"][0]["content"].split("CLAIM:\n", 1)[1].split("\n\nASSERTIONS", 1)[0])
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        if marker in _claim_of(p):
            if _cited_audit(p):
                seen["audits"] += 1
            return tool_msg(NON_DEFECT if seen["audits"] <= partial_for else SUPPORTED)
        return tool_msg(SUPPORTED)

    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + claim_text)
    rc = audit_loop.run(sr)
    return rc, AuditPaths(sr.slug_dir, "alpha"), seen, log


def _dropped(paths: AuditPaths) -> dict:
    return json.loads((paths.out / audit_loop.DROPPED_RECORD).read_text())


@pytest.mark.parametrize("shape", [HEADED, SHORT, BULLETS], ids=["heading-prefixed", "short", "bullets"])
def test_a_claim_the_repair_leaves_unchanged_is_graded_again_not_dropped(
    job_dir: Path, firm_config_path: Path, data_root: Path, shape: str
) -> None:
    """Graded PARTIAL once, returned unchanged by the repair, graded SUPPORTED
    on the second reading: the fact stays. Before the fix the second reading
    never happened (the cached PARTIAL was resumed) and the cap dropped it."""
    rc, paths, seen, _log = _run_shape(job_dir, firm_config_path, data_root, shape, partial_for=1)
    assert rc == 0
    assert seen["audits"] == 2, "the unchanged claim must be audited again, not resumed from its stale verdict"
    assert shape.split("(Exhibit")[0].strip() in paths.doc.read_text()
    assert _dropped(paths)["count"] == 0
    edits = CL.read_rows(paths.out / "repair-edits.jsonl")
    assert [e for e in edits if str(e.get("result", "")).startswith("NOOP")]


def test_an_unchanged_claim_from_the_last_round_is_regraded_before_any_drop(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The repair's no-op lands in the LAST round, so the next thing to run is
    the first drop pass. That pass defers the stale verdict; the post-drop
    audit grades it, and a SUPPORTED there keeps the claim."""
    rc, paths, seen, _log = _run_shape(job_dir, firm_config_path, data_root, SHORT, partial_for=audit_loop.ROUNDS)
    assert rc == 0
    assert seen["audits"] == audit_loop.ROUNDS + 1
    assert "slight improvement in neck" in paths.doc.read_text()
    assert _dropped(paths)["count"] == 0
    edits = CL.read_rows(paths.out / "repair-edits.jsonl")
    assert [e for e in edits if e.get("result") == "DEFER: re-audit pending"]


def test_a_claim_still_partial_on_every_reading_is_dropped_and_marked_contested(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The policy holds where the audit keeps grading the claim unsupported:
    it is dropped at the cap. The audit and the repair disagreed, so the record
    says so, carries the auditor's findings, and keeps the WHOLE claim (the live
    record cut the bullet block off mid-word at 300 characters)."""
    rc, paths, _seen, log = _run_shape(job_dir, firm_config_path, data_root, BULLETS, partial_for=10**6)
    assert rc == 0
    assert "Ambulance Call Report" not in paths.doc.read_text()
    dropped = _dropped(paths)
    assert dropped["count"] == 1
    d = dropped["claims"][0]
    assert d["contested"] is True and (d["exhibit"], d["page_spec"]) == (1, "2")
    assert d["claim"].endswith('"No speed."')
    assert any("prints 0001" in a for a in d["assertions"])
    assert any("CONTESTED" in line for line in log)


def test_a_marker_makes_a_key_undone_until_a_new_verdict_lands() -> None:
    rows = [
        {"key": "a", "kind": "real", "verdict": "PARTIAL"},
        {"key": "a", "kind": CL.REAUDIT},
        {"key": "b", "kind": "real", "verdict": "PARTIAL"},
        {"key": "b", "kind": CL.REAUDIT},
        {"key": "b", "kind": "real", "verdict": "SUPPORTED"},
        {"key": "c-ctl2", "kind": "control(vs Ex2)", "verdict": "UNSUPPORTED"},
    ]
    assert CL.pending_reaudit(rows) == {"a"}
    assert CL.done_keys(rows) == {"b", "c-ctl2"}


def test_the_auditor_is_told_what_is_not_a_defect() -> None:
    """The upstream half: each live PARTIAL listed only non-defects. The image
    prompt and the text-mode system prompt carry the same rules."""
    from medchron.audit import verify as VF

    for prompt in (VF.PROMPT.format(cite="Exhibit 1 p.2", claim="x"), VF.SYSTEM_TEXT):
        flat = " ".join(prompt.split())
        assert "Something the page says that the claim leaves out" in flat
        assert "may print a different number" in flat and "Never report that difference" in flat
        assert "at the same strength, is supported" in flat
        assert "do not list it, not even with a remark that it is supported" in flat
