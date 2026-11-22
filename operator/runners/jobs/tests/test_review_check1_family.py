"""2026-11-22: the pre-delivery review's check 1 held delivered work for a
person to release, three times on one update and once on a full chronology.

* C1. An update never reached the check 1 decision: one model MUST-FIX on a
  family mention in the client's own message held the update. It is now
  decided on the document the update delivers, as on a full chronology.
* C2. A finding on a date with many entries that names no provider or page
  tied to no single entry and held. A phrase it quotes narrows it; when
  nothing narrows it, every candidate is decided, and a finding none of whose
  candidates is on another person's page is a NOTE the attorney reads.
* C3. A family member's health fact in the client's own record is the
  client's family history, a NOTE; a page headed with another patient's name
  and date of birth is still another person's (control).

Every name, date and page here is invented.
"""

from __future__ import annotations

import json

from jobs.proof import review as proof_review
from jobs.stages import manifest, review, review_evidence
from jobs.stages.base import StageRun
from jobs_testkit import make_pdf
from test_final_stages import DOC
from test_review_proof import FakeDoorway, _package, _settle_review, _verdict

P1 = "Prior history page.\nPatient: Alpha Example DOB: 01/01/1970\nChronic neck pain."
P2 = "Patient: Alpha Example DOB: 01/01/1970\nNeck pain after the collision. Cervical strain."
#: The client's own message, quoting a parent's lab report with its label.
OWN_MESSAGE = (
    "Portal message from Alpha Example DOB: 01/01/1970\n"
    "My mother sent me her results so you have her history.\n"
    "Patient: Gamma Stone\n"
    "Biopsy: benign.\n"
    "Continue therapy."
)
#: A page headed with another patient's name and date of birth.
OTHER_PATIENT = "Patient: Beta Roe DOB: 02/02/1980\nMy mother has diabetes.\nContinue therapy."
FAMILY_FINDING = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 entry reports lab results the client's mother sent, "
    "labeled for another patient | entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 |"
)
EXHIBIT = "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf"


def _pages(sr: StageRun, *rest: str) -> None:
    out = sr.slug_dir / "out" / "alpha"
    (out / EXHIBIT).write_bytes(make_pdf([P1, P2, *rest]))
    assert manifest.run(sr) == 0


def _as_update(sr: StageRun) -> None:
    """The run dir as an update leaves it for the review: the delivered
    chronology and the updated one, which adds the 03/30/2026 entry."""
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    added = "03/30/2026\nExample Clinic | Treatment Recommendations\n\nContinue therapy. (Exhibit 1 - p. 3)\n\n"
    assert added in DOC
    (ud / "base.md").write_text(DOC.replace(added, ""))
    (ud / "updated-chronology.md").write_text(DOC)


def _rec(sr: StageRun) -> dict:
    return proof_review.load(sr.slug_dir / "runs" / "alpha")


def _md(sr: StageRun) -> str:
    return (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


# ---- C1: the update lane -----------------------------------------------------------
def test_c1_an_update_with_a_family_mention_in_the_clients_own_message_is_a_note(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, OWN_MESSAGE)
    _as_update(sr)
    assert review.update_material(sr.slug_dir / "runs" / "alpha") is not None, "the review sees an update"
    assert _settle_review(sr, FakeDoorway(_verdict(FAMILY_FINDING))) == 0, log
    rec = _rec(sr)
    assert rec["must_fix"] == 0
    f = rec["findings"][0]
    assert f["grade"] == proof_review.NOTE and "the client's own record" in f["finding"]
    assert "Gamma" not in f["finding"]
    assert "Continue therapy." in (sr.slug_dir / "runs" / "alpha" / "update" / "updated-chronology.md").read_text()


def test_c1_an_update_on_a_page_headed_with_another_patient_still_holds(job_dir, firm_config_path, data_root):
    """The control on the update lane: the decision reads the same page the
    same way, and another person's page is never settled as a note. An
    update's entries leave only through its own assemble and proofs, so it
    holds rather than editing what those proofs read."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, OTHER_PATIENT)
    _as_update(sr)
    assert _settle_review(sr, FakeDoorway(_verdict(FAMILY_FINDING))) == review.EXIT_HELD, log
    rec = _rec(sr)
    assert rec["must_fix"] == 1 and rec["findings"][0]["grade"] == proof_review.MUST
    assert "Continue therapy." in (sr.slug_dir / "runs" / "alpha" / "update" / "updated-chronology.md").read_text()


# ---- C3: the full chronology -------------------------------------------------------
def test_c3_a_family_mention_in_the_clients_own_record_is_kept_as_a_note(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, OWN_MESSAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(FAMILY_FINDING))) == 0, log
    rec = _rec(sr)
    assert rec["must_fix"] == 0 and "the client's own record" in rec["findings"][0]["finding"]
    assert "Continue therapy." in _md(sr), "the entry is kept"
    assert not (sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).is_file()


def test_c3_control_a_page_headed_with_another_patient_is_left_out_by_rule(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, OTHER_PATIENT)
    assert _settle_review(sr, FakeDoorway(_verdict(FAMILY_FINDING))) == 0, log
    assert "Continue therapy." not in _md(sr)
    left = json.loads((sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).read_text())
    assert left["entries"][0]["date"] == "03/30/2026" and left["entries"][0]["pages"] == [3]


def test_c3_the_clients_page_with_another_patients_label_and_no_family_word_is_still_another_persons(
    job_dir, firm_config_path, data_root
):
    """Only a family attribution makes the client's page reporting another
    label the client's record; without one the rule is unchanged."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, OWN_MESSAGE.replace("My mother sent me her results so you have her history.", "Results attached."))
    row = (
        "| MUST-FIX | 1 another person's records | the 03/30/2026 entry is labeled for another patient "
        "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 |"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0, log
    assert "Continue therapy." not in _md(sr)


# ---- C2: many entries on one date --------------------------------------------------
_SAME_DAY = "".join(
    f"03/30/2026\nExample Clinic | Treatment Recommendations\n\n{t} (Exhibit 1 - p. {p})\n\n"
    for t, p in (
        ("Continue therapy.", 3),
        ("Brace fitted with device labels reviewed.", 4),
        ("Home exercise program given.", 5),
        ("Follow up in four weeks.", 6),
    )
)
SAME_DAY_DOC = DOC.replace(
    "03/30/2026\nExample Clinic | Treatment Recommendations\n\nContinue therapy. (Exhibit 1 - p. 3)\n\n", _SAME_DAY
)
CLIENT_PAGE = "Patient: Alpha Example DOB: 01/01/1970\nVisit note."
QUOTED = (
    '| MUST-FIX | 1 another person\'s records | an entry dated 03/30/2026 shows "device labels reviewed" '
    "for someone else | the 03/30/2026 entries |"
)
UNPLACED = (
    "| MUST-FIX | 1 another person's records | an entry dated 03/30/2026 may describe another patient "
    "| the 03/30/2026 entries |"
)


def test_c2_a_quoted_phrase_in_exactly_one_entry_names_it():
    assert len(review_evidence.candidates(SAME_DAY_DOC, UNPLACED)) == 4
    got = review_evidence.entry_for(SAME_DAY_DOC, QUOTED)
    assert got is not None and "Brace fitted" in got[2]


def test_c2_the_quoted_entry_on_another_patients_page_is_left_out(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, SAME_DAY_DOC)
    _pages(sr, CLIENT_PAGE, "Patient: Beta Roe DOB: 02/02/1980\nBrace fitted.", CLIENT_PAGE, CLIENT_PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(QUOTED))) == 0, log
    md = _md(sr)
    assert "Brace fitted" not in md and "Home exercise program given." in md and "Continue therapy." in md


def test_c2_no_narrowing_and_every_candidate_the_clients_is_a_disclosed_note(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, SAME_DAY_DOC)
    _pages(sr, CLIENT_PAGE, CLIENT_PAGE, CLIENT_PAGE, CLIENT_PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(UNPLACED))) == 0, log
    rec = _rec(sr)
    assert rec["must_fix"] == 0
    assert rec["findings"][0]["finding"].startswith("checked against the cited pages of all 4 entries")
    assert all(t in _md(sr) for t in ("Continue therapy.", "Brace fitted", "Home exercise", "Follow up"))
    names = [m["name"] for m in json.loads((sr.slug_dir / "out" / "alpha" / "upload_manifest.json").read_text())]
    notes = review.docx_text(sr.slug_dir / "out" / "alpha" / next(n for n in names if "Review Notes" in n))
    assert "all 4 entries" in notes, "the attorney reads it in the Review Notes"


def test_c2_no_narrowing_and_one_candidate_on_another_patients_page_holds(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, SAME_DAY_DOC)
    _pages(sr, CLIENT_PAGE, "Patient: Beta Roe DOB: 02/02/1980\nBrace fitted.", CLIENT_PAGE, CLIENT_PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(UNPLACED))) == review.EXIT_HELD, log
    assert _rec(sr)["must_fix"] == 1
    assert "Brace fitted" in _md(sr), "nothing is left out on a guess"
