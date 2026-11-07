"""SMD's hand corrections are never rewritten by the audit's repair.

The strip applies SMD's ``drops.json`` citation_edits before the audit runs.
On a client job (2026-10-23) the audit graded those sentences like the
model's and its repair rewrote 21 of 133 of them, one into a misattributed
sentence that reached the firm. Now each claim SMD wrote carries an
SMD-attested SUPPORTED row before round 1. Every value is invented; the first
test fails on the branch head before the fix.
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

from jobs.medchron.audit import claims as CL
from jobs.medchron.audit.run import AuditPaths
from jobs.stages import audit_loop

GOOD = "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)"
SMD = "Dr. Example Second recorded lumbar tenderness at the follow-up visit. (Exhibit 1 - p. 2)"
REWRITE = "Dr. Example First recorded lumbar tenderness at the follow-up visit. (Exhibit 1 - p. 2)"


def _is_repair(p: dict) -> bool:
    return bool(p.get("system")) and "correct one sentence-group" in p["system"][0]["text"]


def _run(
    job_dir: Path, firm_config_path: Path, data_root: Path, edits: list[dict]
) -> tuple[int, str, list[dict], AuditPaths]:
    calls: list[dict] = []

    def reply(p, _):
        calls.append(p)
        if _is_repair(p):
            return text_msg(REWRITE)
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        # The stub auditor cannot support SMD's sentence: the model's reading
        # is the one SMD's edit corrected.
        return tool_msg(SUPPORTED if "neck pain rated" in _claim_of(p) else UNSUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=[])
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + SMD)
    (sr.slug_dir / "drops.json").write_text(json.dumps({"manual_drops": {}, "citation_edits": edits}))
    rc = audit_loop.run(sr)
    paths = AuditPaths(sr.slug_dir, "alpha")
    return rc, paths.doc.read_text(), calls, paths


def test_an_smd_written_claim_with_an_unsupported_verdict_stays_unchanged(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    edits = [{"find": "Dr. Example First recorded", "replace": SMD}]
    rc, doc, calls, paths = _run(job_dir, firm_config_path, data_root, edits)
    assert SMD in doc and REWRITE not in doc
    assert not any(_is_repair(p) for p in calls)
    assert not any("lumbar tenderness" in _claim_of(p) for p in calls if not _is_repair(p))
    assert rc == 0
    smd = [r for r in CL.read_rows(paths.results) if r.get("mode") == "smd"]
    assert [(r["verdict"], r["exhibit"]) for r in smd] == [("SUPPORTED", 1)]


def test_without_an_smd_edit_the_same_claim_is_still_repaired(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    _rc, doc, calls, _paths = _run(job_dir, firm_config_path, data_root, [])
    assert any(_is_repair(p) for p in calls)
    assert SMD not in doc  # rewritten, then dropped once the stub fails the rewrite too


def test_a_correction_inside_a_sentence_with_no_citation_stays_unchanged(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    # SMD corrected only the provider's name; the model's citation stayed.
    edits = [{"find": "Dr. Example First recorded lumbar", "replace": "Dr. Example Second recorded lumbar"}]
    rc, doc, calls, paths = _run(job_dir, firm_config_path, data_root, edits)
    assert SMD in doc and REWRITE not in doc
    assert not any(_is_repair(p) for p in calls)
    assert rc == 0
    assert [r["verdict"] for r in CL.read_rows(paths.results) if r.get("mode") == "smd"] == ["SUPPORTED"]


def test_a_short_replacement_does_not_attest_a_sentence_that_only_ends_the_same() -> None:
    from jobs.medchron.audit import authored

    texts = authored.authored_texts([{"find": "x", "replace": "BP 120/80 (Exhibit 1 - p. 3)"}])
    assert authored.is_authored({"exhibit": 1, "claim": "BP 120/80"}, texts)
    assert not authored.is_authored({"exhibit": 1, "claim": "At the other visit, BP 120/80"}, texts)
    assert not authored.is_authored({"exhibit": 2, "claim": "BP 120/80"}, texts)
    long = authored.authored_texts([{"find": "x", "replace": SMD[: SMD.index(" (Exhibit")] + " (Exhibit 1 - p. 9)"}])
    assert authored.is_authored({"exhibit": 1, "claim": "On 01/02/2026, " + SMD[: SMD.index(" (Exhibit")]}, long)
    inline = authored.inline_texts(
        [{"find": "x", "replace": "Second"}, {"find": "y", "replace": "recorded lumbar tenderness at"}]
    )
    assert inline == set()  # a short or one-word correction never attests a whole sentence
    inline = authored.inline_texts([{"find": "x", "replace": "Dr. Example Second recorded lumbar"}])
    assert authored.is_authored({"exhibit": 1, "claim": "other"}, set(), SMD, inline)
    assert not authored.is_authored({"exhibit": 1, "claim": "other"}, set(), GOOD, inline)
