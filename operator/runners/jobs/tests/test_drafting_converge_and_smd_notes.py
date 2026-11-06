"""Two defects seen on one discovery job (2026-11-05), as falsifiers.

1. The audit's full read of a set is a model reading, and two reads of
   unchanged text do not return the same findings. The convergence rule
   compared COUNTS ("a full read after repairs finds no fewer than the last
   one"), so a new finding on a request no repair had touched, the same size
   as the last read's, failed a set whose documents were sound. The rule now
   compares findings by identity (section, request number, verdict): a set
   fails only when a full read reports again a finding the last full read
   reported (``converge``), so a defect that survives its repair still fails.

2. SMD's hand edits to notes-final.md (a section headed "Scope of this draft
   and SMD's edits", or "Before you serve these") were removed by the notes
   rewrite that follows a review finding. Code now carries every SMD-authored
   section through a rewrite byte for byte (``notes.carry_smd``).

Every name, number and sentence is invented (Example / Exampletown).
"""

from __future__ import annotations

import json
import re

import pytest

from jobs.drafting import converge, notes as notes_mod
from test_drafting_proof_findings import AuditClient, _drafts, pricing  # noqa: F401  (pytest fixture)
from test_drafting_review import KINDS, _run

RFA = "Requests for Admission"
_LABEL = re.compile(r"REQUEST FOR ADMISSION NO\. (\d+)\n")


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    from test_drafting_review import CHECKER

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


CLEAN = "- claim | SUPPORTED | cite\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0"


def _rows(nums: list[int]) -> str:
    rows = [f"- `RFA {n}: the request` | DRIFTS | assumes a fact the file does not bear out" for n in nums]
    return "\n".join(rows) + f"\nSUPPORTED=0 DRIFTS={len(rows)} INVENTED=0 ARITHMETIC=0"


def _full_reads(flag_on_read):
    """An auditor of the admissions set. A FULL read (all three requests in
    view) reports the requests ``flag_on_read(k)`` names on the k-th full
    read; a delta read (only the requests a repair changed) reports nothing."""
    reads = {"full": 0}

    def answer(params):
        body = params["messages"][-1]["content"]
        nums = {int(n) for n in _LABEL.findall(body)}
        if nums != {1, 2, 3}:
            return CLEAN
        reads["full"] += 1
        flagged = flag_on_read(reads["full"])
        return _rows(flagged) if flagged else CLEAN

    return answer, reads


def _edit(n: int) -> tuple[str, str]:
    """A repair that rewords request ``n`` (a real change to its text)."""
    old = f"REQUEST FOR ADMISSION NO. {n}\n\n{KINDS[RFA][2]}"
    return old, f"REQUEST FOR ADMISSION NO. {n}\n\nAdmit that YOU owned the PREMISES on the date of the INCIDENT."


def test_a_later_full_read_that_finds_a_new_defect_on_an_untouched_request_never_fails_the_set(tmp_path, pricing):
    """Read 1 flags No. 1, read 2 (after No. 1 is repaired) flags No. 2, whose
    text nothing changed, read 3 flags No. 3: each read finds as many as the
    last, and none finds again what the last one found. Each is repaired and
    the set passes; a count comparison failed it on the third read."""
    answer, reads = _full_reads(lambda k: [k] if k <= 3 else [])
    client = AuditClient(drafts=_drafts(), audit_fn=answer, fixes=[_edit(1), _edit(2), _edit(3)])
    r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert reads["full"] == 4
    assert client.count("REPAIR-PROMPT") >= 3


def test_a_finding_that_survives_its_repair_on_the_next_full_read_still_fails_the_set(tmp_path, pricing):
    """No. 1 is flagged on every full read; its repair rewords it, the delta
    read of the reworded request passes, and the next full read flags it
    again: a real defect the repair did not clear. The set fails on that read,
    after one repair, and nothing is paid past it."""
    answer, reads = _full_reads(lambda _k: [1])
    client = AuditClient(drafts=_drafts(), audit_fn=answer, fixes=[_edit(1), _edit(1)])
    _r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("audit_unrepaired: "), (v.stage, v.reason)
    assert "the last full read also found" in v.reason
    assert reads["full"] == 2
    assert client.count("REPAIR-PROMPT") == 1
    assert seat.created == [] and seat.sent == []
    assert client.count("INDEPENDENT PRE-DELIVERY REVIEW") == 0


def test_a_finding_is_known_by_its_section_request_and_verdict():
    audit = (
        "# Audit v2\n\nTallies: {}\n\n## MECHANICAL: quotations not found verbatim in the corpus\n- a quote\n\n"
        "## AUDIT: REQUESTS FOR ADMISSION\n\n- `RFA 4: x` | DRIFTS | y\n- `RFA 4: x` | COMPOUND | z\n"
        "- claim | SUPPORTED | cite\n- `the set as a whole` | UNDEFINED | w\n"
    )
    assert converge.finding_ids(audit) == {
        ("REQUESTS FOR ADMISSION", 4, "DRIFTS"),
        ("REQUESTS FOR ADMISSION", 4, "COMPOUND"),
        ("REQUESTS FOR ADMISSION", None, "UNDEFINED"),
        ("MECHANICAL", None, "a quote"),
    }
    # The same request flagged in other words is the same finding; another verdict is not.
    again = audit.replace("`RFA 4: x` | DRIFTS | y", "`RFA 4: another wording` | DRIFTS | other words")
    assert converge.persisting(converge.finding_ids(audit), converge.finding_ids(again)) == 4


# ---- 2. SMD's sections in the notes survive every rewrite -------------------------------------

SMD_SCOPE = (
    "## Scope of this draft and SMD's edits\n\n"
    "- SMD removed request No. 7 by hand: it asked about a lease the file does not hold.\n"
    "- The definitions were left as drafted.  \n\n"  # trailing spaces kept: byte for byte
)
SMD_SERVE = "## Before you serve these\n\n1. Confirm the set number with the client.\n2. Sign page 6.\n\n"


def _code_notes(tables: str = "### x\n\n| Request | Basis in the file |\n") -> str:
    return (
        "# Attorney notes: Discovery\n\n## Items for the attorney\n\n- None.\n\n## For review\n\n- a line\n\n"
        f"## Request basis, record gaps and your decisions\n\n{tables}\n"
    )


def test_a_rewrite_keeps_every_smd_section_byte_for_byte_where_smd_put_it():
    old = _code_notes().replace("## Items for the attorney", SMD_SERVE + "## Items for the attorney")
    old = old.rstrip("\n") + "\n\n" + SMD_SCOPE
    new = _code_notes("### x\n\n| Request | Basis in the file |\n| No. 1 | a rewritten row |\n")
    out = notes_mod.carry_smd(old, new)
    assert SMD_SCOPE in out and SMD_SERVE in out
    assert out.index("# Attorney notes") < out.index(SMD_SERVE) < out.index("## Items for the attorney")
    assert out.index("a rewritten row") < out.index(SMD_SCOPE)
    assert notes_mod.carry_smd(old, out) == out  # idempotent: a second rewrite adds nothing twice
    assert notes_mod.carry_smd(None, new) == new
    assert notes_mod.carry_smd(_code_notes(), new) == new  # code's own sections are not SMD's


def test_an_smd_subsection_inside_a_code_section_is_kept():
    sub = "### SMD's note on No. 3\n\nThe attorney asked for this wording on 11/4.\n\n"
    old = _code_notes().replace("## For review\n\n", "## For review\n\n" + sub)
    out = notes_mod.carry_smd(old, _code_notes())
    assert sub in out


def test_the_notes_rewrite_after_a_review_finding_keeps_smds_hand_edits(tmp_path, pricing):
    """The job delivered; SMD edited notes-final.md by hand; the job resumes and
    the review holds on the notes, so the tables are written again (``_notes``
    with the review's findings). SMD's sections come through byte for byte."""
    client = AuditClient(drafts=_drafts(), audit_fn=lambda _p: CLEAN)
    r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    path = r.data / "notes-final.md"
    edited = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + SMD_SCOPE + SMD_SERVE
    path.write_text(edited, encoding="utf-8")
    out = r._notes(r._digest_text(), ["the review found in the notes: a cite (x)"])
    assert SMD_SCOPE in out and SMD_SERVE in out
    assert path.read_text(encoding="utf-8") == out
    # and a dropped basis row never reaches into SMD's section
    assert json.dumps(out).count("Scope of this draft") == 1
