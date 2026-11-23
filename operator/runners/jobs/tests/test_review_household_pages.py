"""2026-11-22: entries citing a household member's pages (the client's
surname, another given name) reached two delivered chronologies.

* A registration placeholder date of birth (01/01/1900, 00/00/0000, an
  impossible date) is nobody's: ``foreign_identity.findings`` never reads it
  as "a date of birth that is not the client's" (it withheld a client's own
  pages). A real child's birthday (1/1/11) still is one.
* The entry reader's ``another_person`` findings (``runs/<unit>/entry_read.json``,
  recorded only until now) are settled before delivery on the cited page by
  ``review_evidence.whose``: another person's (a real labelled date of birth
  that is not the client's), the entry is left out and disclosed; a page
  naming only the client's surname is kept and counted.
* A model check 1 that names pages lands only on an entry citing one of them
  (``review_evidence.entry_for``), so an entry the reader left out never
  passes its finding to the other entry on that date.

Every name and date here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.medchron.gates import foreign_identity as FI
from jobs.proof import review as proof_review
from jobs.stages import review_evidence, review_policy
from jobs.stages.entry_audit_apply import doc_sha
from test_final_stages import DOC
from test_review_proof import CLEAN, FakeDoorway, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _md, _notes, _pages

CLIENT_DOB = "07/02/1964"


# ---- placeholder dates of birth -----------------------------------------------------
@pytest.mark.parametrize(
    "dob",
    ["01/01/1900", "1/1/1900", "00/00/0000", "01/01/0000", "00/15/1980", "05/00/1980", "02/31/1980", "13/01/1980"],
)
def test_a_placeholder_date_of_birth_is_nobodys(dob: str) -> None:
    assert FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB}) == []


@pytest.mark.parametrize(
    ("dob", "norm"),
    [("03/14/1961", "03/14/1961"), ("1/1/11", "01/01/2011"), ("2/2/22", "02/02/2022"), ("11/11/11", "11/11/2011")],
)
def test_a_real_date_of_birth_that_is_not_the_clients_still_flags(dob: str, norm: str) -> None:
    found = FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB})
    assert found == [f"a date of birth {norm} that is not the client's"]


def test_a_four_digit_year_is_never_read_as_two_digits() -> None:
    assert FI._norm_dob("01", "01", "0000") is None
    assert FI._norm_dob("01", "01", "0011") == "01/01/0011"
    assert FI.placeholder_dob("01", "01", "0011")


# ---- the entry reader's another_person findings ------------------------------------
#: Another person's page: the client's surname, another given name, a real
#: labelled date of birth that is not the client's.
HOUSEHOLD = "Patient: Gamma Example DOB: 03/14/1961\nContinue therapy."
#: The same name with no date of birth: nothing on it says it is not hers.
SAME_SURNAME = "Patient: Gamma Example | Visit 03/30/2026\nContinue therapy."


def _reader(sr, *, sha: str | None = None, page: int = 3, rows: object = None) -> None:
    """The entry reader's record: one another_person finding on the 03/30/2026
    entry (N2), recorded only."""
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    if rows is None:
        found = {"class": "another_person", "line": "S1", "exhibit": 1, "page": page, "reason": "record_only_class"}
        rows = [
            {"key": "N1", "status": "read", "findings": 0, "applied": 0, "recorded": []},
            {"key": "N2", "status": "read", "findings": 1, "applied": 0, "recorded": [found]},
        ]
    rec = {"complete": True, "doc_sha": doc_sha(doc.read_text(encoding="utf-8")) if sha is None else sha, "rows": rows}
    (sr.slug_dir / "runs" / "alpha" / "entry_read.json").write_text(json.dumps(rec))


def _policy(sr) -> dict:
    return json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())


@pytest.mark.parametrize("trusted", [True, False])
def test_the_readers_finding_on_another_persons_page_leaves_the_entry_out(
    job_dir, firm_config_path, data_root, trusted
):
    """Placed by its anchor while the chronology is the one the reader read,
    else by the one entry citing the page."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, HOUSEHOLD)
    _reader(sr, sha=None if trusted else "not-this-document")
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    md = _md(sr)
    assert "03/30/2026\nExample Clinic" not in md and "Continue therapy." not in md and "Cervical strain." in md
    assert "| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |" in md, "the tables are re-derived"
    left = json.loads((sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).read_text())
    assert [(e["date"], e["pages"], e["reason"]) for e in left["entries"]] == [
        ("03/30/2026", [3], review_evidence.LEFT_OUT_REASON)
    ]
    notes = _notes(sr)
    assert "left out before delivery by rule" in notes
    assert "Gamma" not in notes and "03/14/1961" not in notes, "the other person is never named"
    pol = _policy(sr)
    assert pol["identity_left_out"] == 1 and pol["identity_reader_unplaced"] == 0


def test_the_readers_finding_on_a_same_surname_page_with_no_date_of_birth_is_kept(job_dir, firm_config_path, data_root):
    """The surname is the client's and nothing else labels the patient: the
    reader's suspicion alone never drops the entry."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, SAME_SURNAME)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    assert "Continue therapy." in _md(sr)
    assert not (sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).exists()
    pol = _policy(sr)
    assert pol["identity_reader_kept"] == 1 and pol["identity_left_out"] == 0


def test_a_reader_finding_its_entry_does_not_cite_is_unplaced(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, HOUSEHOLD)
    _reader(sr, page=1)  # N2 cites page 3 only
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    assert "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_reader_unplaced"] == 1


@pytest.mark.parametrize("rows", ["not a list", [{"key": "N2", "recorded": "not a list"}, "not a row", {"key": "N1"}]])
def test_a_malformed_reader_record_is_skipped(job_dir, firm_config_path, data_root, rows):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, HOUSEHOLD)
    _reader(sr, rows=rows)
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    assert "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_left_out"] == 0


def test_the_reader_and_the_review_on_one_entry_leave_it_out_once(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, HOUSEHOLD)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    left = json.loads((sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).read_text())
    assert len(left["entries"]) == 1
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"].startswith("left out before delivery by rule")
    assert _policy(sr)["identity_left_out"] == 1


# ---- a finding never lands on the date's other entry ---------------------------------
#: Entry B: the same date as A (N2, citing p. 3), citing only the client's p. 2.
ENTRY_B = "03/30/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain, improving. (Exhibit 1 - p. 2)\n\n"
TWO_ON_ONE_DATE = DOC.replace("## Exhibit List", ENTRY_B + "## Exhibit List")
#: A check 1 naming A's page and a page no remaining entry cites.
CHECK1_TWO_PAGES = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 and Exhibit 1 p. 1 |"
)


def test_entry_for_needs_a_named_page_even_when_one_entry_has_the_date() -> None:
    md = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 1 - p. 2)")
    assert review_evidence.entry_for(md, CHECK1_TWO_PAGES) is None
    assert review_evidence.entry_for(DOC, CHECK1_TWO_PAGES) is not None, "DOC's entry cites p. 3"


def test_after_the_reader_leaves_entry_a_out_the_review_never_lands_on_entry_b(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_DATE)
    _pages(sr, HOUSEHOLD)
    _reader(sr)
    _settle_review(sr, FakeDoorway(_verdict(CHECK1_TWO_PAGES)))
    md = _md(sr)
    assert "Continue therapy." not in md, "entry A is left out by the reader settle"
    assert "Cervical strain, improving." in md, "entry B cites neither named page and is kept"
    left = json.loads((sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).read_text())
    assert [e["pages"] for e in left["entries"]] == [[3]]
