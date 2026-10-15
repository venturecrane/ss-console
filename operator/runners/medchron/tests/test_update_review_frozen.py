"""The update review's "freeze, then shrink" loop (``stages/update_review.py``,
``update/frozen.py``, ``update/duplicates.py``).

On two real matters (2026-10-14) the review loop never converged: each fresh
review raised mostly NEW objections, and a duplicate of another ADDED entry
that carried values of its own was refused round after round. Now round 1's
must-fix list is frozen, duplicates are decided in code, one narrow re-check
confirms or settles what is still open, and the stage ends after at most one
more finish. Every value here is invented; each test fails on the code
before the change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron.update import body as B, duplicates as DU, review_repair as RR
from medchron.update.values import content_numbers
from test_update_finisher import LATER, N1, _edits, _finding, _record, _review, _review_rounds, _run

MRI = "Lumbar MRI showed a disc protrusion of 4 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
DOSE = "Ibuprofen 600 mg was prescribed three times daily at the imaging visit. (Exhibit 2 - p. 2)"
BP = "Blood pressure 128/82 was recorded at the imaging visit by the technologist. (Exhibit 2 - p. 3)"
HEAD = "08/27/2026\n{} | Medical Diagnoses\n\nMedical Diagnoses\n{}\n\nAll Other Information\n{}"
A = HEAD.format("Sample Imaging Center", MRI, DOSE)  # carries 600
B_ = HEAD.format("Sample Imaging", MRI.replace("showed", "recorded"), BP)  # carries 128/82


def _body_numbers(entry: str) -> set[str]:
    return content_numbers(entry.split("\n", 2)[2])


def _decline(*fids: str) -> str:
    return _edits(unresolved=[{"finding": f, "why": "the page is too faint to settle it"} for f in fids])


# (a) ----------------------------------------------------------------------------
def test_a_must_fix_first_raised_after_round_one_is_advisory_and_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 raises one must-fix the finisher declines; the re-check does
    not raise it again but raises a NEW must-fix on the same entry. Before,
    the new one went to the finisher and, unsettled, held the update."""
    r1 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    r2 = _review(_finding("N1", "2 omitted content", "a plan on p. 3 is left out", page=3))
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r2], [_decline("F1")])
    assert rc == 0, _record(sr)["open"]
    rec = _record(sr)
    (late,) = rec["rounds"][1]["findings"]
    assert late["severity"] == "MUST" and late["outcome"] == "advisory: not on the round-1 list"
    assert rec["status"] == "clean" and rec["open"] == []
    assert sum(kw.get("system") == "update-finish-system" for kw in sr.doorway.seen) == 1  # never finished


# (b) + (c) -----------------------------------------------------------------------
def test_an_added_duplicate_with_values_of_its_own_is_folded_into_the_entry_its_problem_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2 repeats N1's visit, and each carries a value the other lacks.
    Before, the drop was refused ("the survivor N1 does not carry 128/82") and
    the update held. Now N2's paragraph carrying 128/82 moves into N1, cited,
    and N2 goes: no value is lost and no model is asked."""
    dup = _review(_finding("N2", "4 duplicate", "N2 composes the same visit as N1"))
    rc, sr, md = _run(tmp_path, monkeypatch, [A, B_, LATER], [dup], [_decline("F1")])
    assert rc == 0, _record(sr)["open"]
    ents = B.entries_of(md)[1]
    assert len(ents) == 2 and ents[0].startswith(A) and ents[0].endswith(BP) and ents[1] == LATER
    assert _body_numbers(ents[0]) >= _body_numbers(A) | _body_numbers(B_)
    (f,) = _record(sr)["rounds"][0]["findings"]
    assert f["outcome"] == "applied" and f["survivor"] == "N1"  # (c) taken from the problem text
    assert not any(kw.get("system") == "update-finish-system" for kw in sr.doorway.seen)


def test_the_survivor_is_read_from_the_problem_only_when_it_names_exactly_one_other_entry() -> None:
    def dup(problem: str, survivor: str = "") -> RR.Finding:
        f = RR.Finding("MUST", "4 duplicate", "N3", 2, 1, problem, "none", "", "")
        f.survivor = survivor
        return f

    assert DU.survivor_of(dup("N3 repeats the visit N1.c2 records")) == "N1"
    assert DU.survivor_of(dup("N3 repeats delivered D12")) == "D12"
    assert DU.survivor_of(dup("N3 repeats N1 and N2")) == ""  # two named: the finisher decides
    assert DU.survivor_of(dup("N3 repeats another visit")) == ""
    assert DU.survivor_of(dup("N3 repeats N1", survivor="N2")) == "N2"  # the review's own field wins


# (d) ----------------------------------------------------------------------------
def test_two_entries_named_as_duplicates_of_each_other_fold_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    both = _review(
        _finding("N1", "4 duplicate", "N1 repeats N2"),
        _finding("N2", "4 duplicate", "N2 repeats N1"),
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [A, B_, LATER], [both], [_decline("F1", "F2")])
    assert rc == 0, _record(sr)["open"]
    ents = B.entries_of(md)[1]
    assert len(ents) == 2 and ents[1] == LATER  # one of the pair is left, never neither
    assert _body_numbers(ents[0]) >= _body_numbers(A) | _body_numbers(B_)
    assert md.count(DOSE.split(" (Exhibit")[0]) == 1 and md.count(BP.split(" (Exhibit")[0]) == 1
    outcomes = sorted(f["outcome"] for f in _record(sr)["rounds"][0]["findings"])
    assert outcomes[0] == "applied" and outcomes[1].startswith("settled: the pair was folded once")


# (e) ----------------------------------------------------------------------------
def test_the_loop_ends_after_one_recheck_holding_only_the_frozen_findings_it_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two must-fix in round 1, both declined. The re-check raises one again
    (confirmed), not the other (settled), plus a new one (advisory). The one
    more finish declines again: held with exactly the confirmed one, and no
    third review."""
    r1 = _review(
        _finding("N1.c1", "1 wrong value", "size misread"),
        _finding("N2.c1", "3 truncated", "cut off", exhibit=3),
    )
    r2 = _review(
        _finding("N1.c1", "1 wrong value", "size still misread"),
        _finding("N2", "1 wrong provider", "the provider line names another clinic", exhibit=3),
    )
    rc, sr, _md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r2, _review()], [_decline("F1", "F2"), _decline("F1")])
    assert rc == 2
    assert _review_rounds(sr) == {"r1", "r2"}
    rec = _record(sr)
    assert [(f["anchor"], f["klass"]) for f in rec["open"]] == [("N1.c1", "1 wrong value")]
    frozen = {r["finding"]["anchor"]: r["finding"]["outcome"] for r in rec["frozen"]}
    assert frozen["N1.c1"].startswith("confirmed on re-check; declined by the finisher")
    assert frozen["N2.c1"] == "settled: not raised again on re-check"
    hold = sr.logged[-1]
    assert hold.startswith("update_held:") and "1 must-fix" in hold and "size still misread" in hold


# (f) ----------------------------------------------------------------------------
def test_a_frozen_finding_the_recheck_does_not_raise_again_is_settled_and_the_confirmed_one_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = MRI.replace("4 mm", "5 mm")
    r1 = _review(
        _finding("N1.c1", "1 wrong value", "size misread"),
        _finding("N2.c1", "3 truncated", "cut off", exhibit=3),
    )
    r2 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = _edits({"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": good})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r2], [_decline("F1", "F2"), fix])
    assert rc == 0, _record(sr)["open"]
    assert good in md
    rec = _record(sr)
    frozen = {r["finding"]["anchor"]: r["finding"]["outcome"] for r in rec["frozen"]}
    assert frozen == {"N1.c1": "applied", "N2.c1": "settled: not raised again on re-check"}
    targets = {a for kw in sr.doorway.review_calls if "-r2-" in kw["custom_id"] for a in _anchors(kw)}
    assert targets == {"N1", "N2"}  # only the entries open frozen findings sit on


def _anchors(kw: dict[str, Any]) -> list[str]:
    import update_testkit as K

    return K.part_anchors(kw["messages"][0]["content"])


def test_a_round_one_fix_that_applies_is_delivered_without_a_second_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = MRI.replace("4 mm", "5 mm")
    r1 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = _edits({"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": good})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, json.dumps({"findings": [], "complete": True})], [fix])
    assert rc == 0 and good in md
    assert _review_rounds(sr) == {"r1"}


def test_a_duplicate_dated_otherwise_is_never_folded_in_code_and_a_same_date_one_is() -> None:
    """The code fold had the drop's blind spot: it compared bodies only, so an
    entry dated 08/28 folded into an 08/27 visit would lose its date."""
    keep, heads = {2, 3}, {"Medical Diagnoses", "All Other Information"}
    doc = (
        "X - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n{}\n\n## Exhibit List\n"
    )

    def dup(anchor: str, problem: str) -> RR.Finding:
        return RR.Finding("MUST", "4 duplicate", anchor, 2, 1, problem, "none", "", "")

    later = B_.replace("08/27/2026", "08/28/2026", 1)
    md, edits, moved = DU.resolve(doc.format(A + "\n\n" + later), [dup("N2", "N2 repeats N1")], keep, heads, [])
    assert moved == {} and later in md and "dated 08/28/2026" in edits[0].outcome
    md, edits, moved = DU.resolve(doc.format(A + "\n\n" + B_), [dup("N2", "N2 repeats N1")], keep, heads, [])
    assert moved == {"N2": "N1"} and edits[0].outcome == "applied" and BP in md
    day_before = "08/26/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n" + MRI
    md, edits, moved = DU.resolve(doc.format(A), [dup("N1", "N1 repeats delivered D1")], keep, heads, [day_before])
    assert moved == {} and A in md and "dated 08/27/2026" in edits[0].outcome


def test_an_entry_folded_under_one_delivered_visit_is_rehomed_under_the_same_date_visit_named_next() -> None:
    """A real re-check named delivered D133 for an entry round 1 had folded
    under D132 (same date); both findings were skipped and the update held.
    The fold now moves under the visit named, carrying only what it lacks, or
    goes when that visit carries it all; another date is still refused."""
    from medchron.update import entry_edits as EE

    keep, heads = {2, 3}, {"Medical Diagnoses", "All Other Information"}
    doc = (
        "X - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n{}\n\n## Exhibit List\n"
    )
    d1 = "08/27/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n" + MRI
    d2 = "08/27/2026\nSample Imaging Annex | Medical Diagnoses\n\nMedical Diagnoses\n" + MRI.replace("showed", "noted")
    d2_all = d2 + "\n\nAll Other Information\n" + DOSE.replace("(Exhibit 2", "(Exhibit 1")
    d3 = d2.replace("08/27/2026", "08/28/2026", 1)

    def dup(problem: str) -> RR.Finding:
        return RR.Finding("MUST", "4 duplicate", "N1", 2, 1, problem, "none", "", "")

    # in one pass: folded under D1, then named a duplicate of D2
    md, edits, moved = DU.resolve(doc.format(A), [dup("repeats D1"), dup("repeats D2")], keep, heads, [d1, d2])
    (ent,) = B.entries_of(md)[1]
    assert moved == {"N1": "D2"} and [e.outcome for e in edits] == ["applied", "applied"]
    assert EE.is_fold(ent) and ent.startswith(d2.split("\n\n")[0]) and ent.endswith(DOSE)  # 600 kept, under D2
    # across passes: a fold under D1 (round 1) named a duplicate of D2, which carries all of it
    fold = EE.fold_text(d1, DOSE, heads)
    md, edits, moved = DU.resolve(doc.format(fold), [dup("repeats D2")], keep, heads, [d1, d2_all])
    assert moved == {"N1": "D2"} and edits[0].outcome == "applied" and B.entries_of(md)[1] == []
    # another date: refused, the fold stays
    md, edits, moved = DU.resolve(doc.format(fold), [dup("repeats D2")], keep, heads, [d1, d3])
    assert moved == {} and fold in md and "dated 08/27/2026" in edits[0].outcome
