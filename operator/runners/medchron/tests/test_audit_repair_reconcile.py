"""Parity N3: a repair never leaves text the audit cannot count.

On a real offline run (2026-10-14) the audit's repair round failed to
reconcile: 241 claims before, 237 after, 238 expected. An entry's provider
line ("Name | Heading") had been cited as a paragraph, so the audit graded it;
the repair cut it to the bare name, 25 characters with no value, which the
audit no longer reads as a claim. The text stayed in the document ungraded
and the count was one short. Now a rewrite that leaves no gradable claim is a
drop (counted, and refused when it loses a value the audit did not name), a
rewrite that splits a claim in two is refused, and collect never cites an
entry's header. Every value is invented; each test fails on the branch head
before N3.
"""

from __future__ import annotations

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

from medchron.audit import claims as CL
from medchron.audit.run import AuditPaths
from medchron.stages import audit_loop
from medchron.update import uncited as U

GOOD = "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)"
HEADER = "Example Chiropractic, D.C. | Patient Complaints & Limitations (Exhibit 1 - p. 2)"
SPLIT = "Neck tenderness was recorded on palpation at the visit. (Exhibit 1 - p. 2)"


def _is_repair(p: dict) -> bool:
    return bool(p.get("system")) and "correct one sentence-group" in p["system"][0]["text"]


def _run(
    job_dir: Path, firm_config_path: Path, data_root: Path, claim: str, rewrite: str
) -> tuple[int, str, list[str]]:
    log: list[str] = []

    def reply(p, _):
        if _is_repair(p):
            return text_msg(rewrite)
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        return tool_msg(SUPPORTED if "neck pain rated" in _claim_of(p) else UNSUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\n" + GOOD + "\n\n" + claim)
    rc = audit_loop.run(sr)
    return rc, AuditPaths(sr.slug_dir, "alpha").doc.read_text(), log


def test_a_rewrite_under_the_floor_is_a_counted_drop_never_ungraded_text(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    rc, doc, log = _run(job_dir, firm_config_path, data_root, HEADER, "Example Chiropractic, D.C.")
    assert not any("MISMATCH" in ln for ln in log), [ln for ln in log if "claims before" in ln]
    assert rc == 0
    assert "Example Chiropractic, D.C. (Exhibit" not in doc  # no ungraded cited text is left behind
    body = CL.body_of(doc)
    assert all(CL.graded(seg) for seg in [body[: m.start()].rsplit("\n\n", 1)[-1] for m in CL.CITE.finditer(body)])


def test_a_rewrite_that_splits_a_claim_in_two_is_refused(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    two = "Neck tenderness was recorded. (Exhibit 1 - p. 2)\n\nPalpation was performed at the visit by the provider."
    rc, doc, log = _run(job_dir, firm_config_path, data_root, SPLIT, two)
    assert not any("MISMATCH" in ln for ln in log), [ln for ln in log if "claims before" in ln]
    assert rc == 0 and "Palpation was performed" not in doc


def test_collect_never_cites_an_entry_header_read_as_a_paragraph() -> None:
    entry = (
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
        "Medical Diagnoses\nNeck sprain was diagnosed at the visit. (Exhibit 1 - p. 1)\n\n"
        "07/18 (log date as written; year not stated in the record)\n"
        "Example Chiropractic | Patient Complaints & Limitations\n\n"
        "Example Chiropractic, D.C. | Patient Complaints & Limitations\n\n"
        "Patient Complaints & Limitations\nNeck stiffness was reported on the log row. (Exhibit 1 - p. 2)"
    )
    got, rows = U.cite_entry(entry, {"Medical Diagnoses", "Patient Complaints & Limitations"})
    assert rows == [], rows
    assert "record)\nExample Chiropractic | Patient Complaints & Limitations\n\n" in got
    assert "Example Chiropractic, D.C. | Patient Complaints & Limitations\n\n" in got
