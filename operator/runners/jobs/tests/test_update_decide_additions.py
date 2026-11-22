"""SMD's correction of a held update can ADD a sentence read from a page of
the same record, and can name the finding it settles by anchor (2026-11-22).

A real update held on one true omission: the entry stopped at the neck of a
physical exam whose rest is on the next page of the same exhibit. The
finisher's sentence was reverted (the audit read a clinician's name the page
does not print), and SMD's correction could not be taken: the verb refused a
new citation, and it matched the finding to an entry by an anchor number the
review's duplicate fold had shifted. Every name and value here is synthetic.
"""

from __future__ import annotations

import json

import pytest

from jobs.medchron import update_decide as UD
from jobs.medchron.audit import coverage as audit_coverage
from jobs.medchron.update import review_repair as RR
from jobs.resume import MARKER_PREFIX
from test_update_decide import JOB, WRONG, _plan, _seat

ADDED = "Cardiovascular and pulmonary examinations were normal. (Exhibit 2 - p. 5)"


def _omission(anchor: str = "N1.c1") -> dict:
    return RR.Finding(
        "MUST", "2 omitted content", anchor, 2, 5, "the exam on p. 5 is left out", "add", "", ""
    ).as_dict()


def test_a_correction_may_add_a_cited_sentence_from_the_same_exhibit(tmp_path, job_dir, data_root) -> None:
    """Fails if the verb refuses an added paragraph citing a page of an
    exhibit the entry cites, or adds it without an SMD-attested audit row."""
    d, sd, paths = _seat(tmp_path, job_dir, data_root, open_=[_omission()])
    p = _plan(d, corrections=[{"old": WRONG, "new": WRONG + "\n\n" + ADDED, "covers": []}])
    assert [a["claim"] for a in p["attested"]] == [ADDED.split(" (Exhibit")[0]]
    UD.apply(d, p)
    assert ADDED in paths.doc.read_text()
    ok, summary = audit_coverage.check(paths, lambda _m: None)
    assert ok and summary["never"] == 0  # the new sentence is graded, by SMD
    assert json.loads((sd / "runs" / "alpha" / "update" / UD.RECORD).read_text())["status"] == "clean"


@pytest.mark.parametrize(
    ("new", "needle"),
    [
        (WRONG + "\n\n" + ADDED.replace("Exhibit 2", "Exhibit 3"), "an exhibit the entry does not cite"),
        (WRONG + "\n\nThe exams were normal.", "an added paragraph needs its own citation"),
        (ADDED, "changes a citation"),  # the old sentence and its citation are gone
    ],
)
def test_an_addition_is_still_refused_when_it_leaves_the_record(tmp_path, job_dir, data_root, new, needle) -> None:
    d, _sd, paths = _seat(tmp_path, job_dir, data_root, open_=[_omission()])
    before = paths.doc.read_text()
    with pytest.raises(UD.Refused, match=needle):
        _plan(d, corrections=[{"old": WRONG, "new": new, "covers": []}])
    assert paths.doc.read_text() == before and not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()


def test_covers_settles_a_finding_whose_anchor_the_fold_shifted(tmp_path, job_dir, data_root) -> None:
    """The record names N2 (before the review folded a duplicate) for what the
    document holds as N1. Fails if a correction on N1 that names N2.c1 in
    covers is refused, or if covers naming no open finding is accepted."""
    d, _sd, _paths = _seat(tmp_path, job_dir, data_root, open_=[_omission("N2.c1")])
    fix = {"old": WRONG, "new": WRONG + "\n\n" + ADDED}
    with pytest.raises(UD.Refused, match="no correction touches"):
        _plan(d, corrections=[{**fix, "covers": []}])
    with pytest.raises(UD.Refused, match="names 0 open findings"):
        _plan(d, corrections=[{**fix, "covers": ["N9.c1"]}])
    p = _plan(d, corrections=[{**fix, "covers": ["N2.c1"]}])
    assert len(p["attested"]) == 1


def test_covers_is_read_from_the_decisions_file(tmp_path) -> None:
    staged = tmp_path / "d.json"
    staged.write_text(json.dumps({"corrections": [{"old": "a", "new": "b", "covers": ["N2.c1"]}]}))
    assert UD.load_corrections(staged)[0]["covers"] == ["N2.c1"]
    staged.write_text(json.dumps({"corrections": [{"old": "a", "new": "b", "covers": "N2"}]}))
    with pytest.raises(UD.Refused, match="covers must be a list"):
        UD.load_corrections(staged)
