"""2026-11-22: entries citing a household member's pages (the client's
surname, another given name) reached two delivered chronologies.

* A registration placeholder date of birth (01/01/1900, 00/00/0000, an
  impossible date) is nobody's: ``foreign_identity.findings`` never reads it
  as "a date of birth that is not the client's" (it withheld a client's own
  pages).
* ``foreign_identity.household``: a "Patient:" label with the client's
  surname and another given name, on a page that labels no one as the client,
  is another person; the review's cited-page check (``review_evidence.whose``)
  reads it so.
* The entry reader's ``another_person`` findings (``runs/<unit>/entry_read.json``,
  recorded only until now) are settled before delivery on the cited page:
  another person's, the entry is left out and disclosed; the client's, kept.

Every name and date here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.medchron.gates import foreign_identity as FI
from jobs.proof import review as proof_review
from jobs.stages import review_evidence, review_policy
from jobs.stages.entry_audit_apply import doc_sha
from test_review_proof import CLEAN, FakeDoorway, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _md, _notes, _pages

CLIENT_DOB = "07/02/1964"


# ---- placeholder dates of birth -----------------------------------------------------
@pytest.mark.parametrize("dob", ["01/01/1900", "00/00/0000", "1/1/1900", "02/31/1980", "13/01/1980", "11/11/1111"])
def test_a_placeholder_date_of_birth_is_nobodys(dob: str) -> None:
    assert FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB}) == []


def test_a_real_date_of_birth_that_is_not_the_clients_still_flags() -> None:
    found = FI.findings("Patient: Elena Reyes DOB: 03/14/1961", {"Reyes"}, {CLIENT_DOB})
    assert found == ["a date of birth 03/14/1961 that is not the client's"]


# ---- household --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "other"),
    [
        ("Patient: Marco Reyes DOB 03/14/1961", True),
        ("Patient: Marco Reyes | MRN 4411", True),
        ("Patient: REYES, MARCO", True),
        ("Patient: E. Reyes", False),
        ("Patient: REYES, ELENA", False),
        ("Patient Name: Elena-Rose Reyes", False),
        ("Guarantor: REYES, MARCO", False),
        ("Patient: Marco Reyes\nDOB: 07/02/1964", False),
        ("Patient: Marco Reyes\nPatient: Elena Reyes", False),
        ("Patient: Marco Smith", False),  # no shared surname: findings() decides that one
        ("Patient: denies pain", False),
    ],
)
def test_household_reads_a_same_surname_patient_label(text: str, other: bool) -> None:
    assert FI.household(text, "Reyes", "Elena Maria", CLIENT_DOB) is other


def test_household_does_not_change_the_withholding_gates_reading() -> None:
    """findings() (the gates that withhold whole files) still reads a
    same-surname name as the client's."""
    assert FI.findings("Patient: Marco Reyes", {"Reyes"}, {CLIENT_DOB}, {"Elena"}) == []


# ---- the review -------------------------------------------------------------------
HOUSEHOLD = "Patient: Gamma Example | Visit 03/30/2026\nContinue therapy."
CLIENTS = "Patient: Alpha Example | Visit 03/30/2026\nContinue therapy."


def _reader(sr, *, sha: str | None = None, page: int = 3) -> None:
    """The entry reader's record: one another_person finding on the 03/30/2026
    entry (N2), recorded only."""
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    rec = {
        "complete": True,
        "doc_sha": sha if sha is not None else doc_sha(doc.read_text(encoding="utf-8")),
        "rows": [
            {"key": "N1", "status": "read", "findings": 0, "applied": 0, "recorded": []},
            {
                "key": "N2",
                "status": "read",
                "findings": 1,
                "applied": 0,
                "recorded": [
                    {"class": "another_person", "line": "S1", "exhibit": 1, "page": page, "reason": "record_only_class"}
                ],
            },
        ],
    }
    (sr.slug_dir / "runs" / "alpha" / "entry_read.json").write_text(json.dumps(rec))


def _policy(sr) -> dict:
    return json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())


def test_whose_reads_a_household_page_as_another_persons(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    assert review_evidence.whose(sr, [HOUSEHOLD]) == "other"
    assert review_evidence.whose(sr, [CLIENTS]) == "client"


@pytest.mark.parametrize("trusted", [True, False])
def test_the_readers_another_person_finding_on_a_household_page_leaves_the_entry_out(
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
    assert "Gamma" not in _notes(sr), "the other person is never named"
    pol = _policy(sr)
    assert pol["identity_left_out"] == 1 and pol["identity_reader_unplaced"] == 0


def test_the_readers_finding_on_the_clients_own_page_keeps_the_entry(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, CLIENTS)
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


def test_the_reviews_check1_on_a_household_page_leaves_the_entry_out(job_dir, firm_config_path, data_root):
    """No reader record (an older job): the review's own check 1 decides, and
    a same-surname page is no longer read as the client's."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, HOUSEHOLD)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    assert "Continue therapy." not in _md(sr)
    assert _policy(sr)["identity_left_out"] == 1
