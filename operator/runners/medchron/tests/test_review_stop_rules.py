"""2026-10-24 stop audit, rows 11 to 14: the pre-delivery review decides on
the cited page instead of holding for SMD.

* Row 11. A system-vocabulary hit ("the auditor") printed verbatim on the
  page its sentence cites is the record's own wording: a NOTE, not a hold.
* Row 12. A model check 1 ("another person's records") is decided by the
  cited page's patient identifiers: the client's page, a NOTE; another
  person's page, the entry left out by rule and disclosed without naming
  that person. A model check 3 quoting words the page prints is a NOTE.
* Row 13. An entry dated after today (a misread year) goes to the repair
  round, where the finisher re-reads the date on its page; the review holds
  only when the date is still after today.
* Row 14. The policy's counts are written so a replay can measure the
  residual check 2/4 after the repair.
"""

from __future__ import annotations

import json
from datetime import date

from medchron.proof import review as proof_review
from medchron.stages import manifest, review, review_evidence, review_policy
from medchron.stages.base import StageRun
from medchron_testkit import make_pdf
from test_final_stages import DOC
from test_review_proof import (
    CLEAN,
    PLAN,
    PLAN_DOC,
    TODAY,
    FakeDoorway,
    _manifest_names,
    _package,
    _settle_review,
    _verdict,
)

P1 = "Prior history page. Patient: Alpha Example DOB: 01/01/1970. Chronic neck pain."
P2 = "Patient: Alpha Example DOB: 01/01/1970. Neck pain after the collision. Cervical strain."


def _pages(sr: StageRun, p3: str, p2: str = P2) -> None:
    """Exhibit 1 with three real pages, re-manifested (the review reads the
    cited page's text from the exhibit)."""
    out = sr.slug_dir / "out" / "alpha"
    (out / "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf").write_bytes(make_pdf([P1, p2, p3]))
    assert manifest.run(sr) == 0


def _md(sr: StageRun) -> str:
    return (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def _notes(sr: StageRun) -> str:
    names = _manifest_names(sr)
    return review.docx_text(sr.slug_dir / "out" / "alpha" / names[1]) if len(names) > 1 else ""


# ---- row 11 ----------------------------------------------------------------------
AUDITOR = "Continue therapy; the auditor noted a coding error on the claim form."


def test_system_words_the_cited_page_prints_verbatim_are_a_note_not_a_hold(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, DOC.replace("Continue therapy.", AUDITOR))
    _pages(sr, "Patient: Alpha Example. Plan: continue therapy. The auditor noted a coding error on the claim form.")
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    assert any("record's own wording" in f["finding"] and "the auditor" in f["finding"] for f in rec["findings"])
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["system_words_on_record"] == 1


def test_system_words_no_cited_page_prints_still_hold(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, DOC.replace("Continue therapy.", AUDITOR))
    _pages(sr, "Patient: Alpha Example. Plan: continue therapy.")
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 2
    assert log[-1].startswith("review_held:") and "the auditor" in log[-1]


# ---- row 12 ----------------------------------------------------------------------
CHECK1 = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is the co-plaintiff Beta Roe's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 |"
)


def test_a_check1_on_the_clients_own_page_is_a_note(job_dir, firm_config_path, data_root):
    """A family-history line on the client's page: the page labels the
    client, so the finding is checked and noted, never a hold."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Alpha Example DOB: 01/01/1970. Mother has diabetes. Continue therapy.")
    d = FakeDoorway(_verdict(CHECK1))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1, "nothing is repaired"
    assert "Continue therapy." in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"].startswith("checked against the cited page")


def test_a_check1_on_another_persons_page_leaves_the_entry_out_and_discloses_it(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Beta Roe DOB: 02/02/1980. Continue therapy.")
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    md = _md(sr)
    assert "03/30/2026\nExample Clinic" not in md and "Continue therapy." not in md and "Cervical strain." in md
    left = json.loads((sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).read_text())
    assert left["entries"][0]["date"] == "03/30/2026" and left["entries"][0]["pages"] == [3]
    assert left["entries"][0]["reason"] == review_evidence.LEFT_OUT_REASON
    notes = _notes(sr)
    assert "left out before delivery by rule" in notes and "03/30/2026" in notes
    assert "Beta" not in notes and "co-plaintiff" not in notes, "the other person is never named"
    chron = sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"
    assert "Continue therapy." not in review.docx_text(chron), "the delivered chronology was re-rendered"


def test_the_left_out_disclosure_is_in_the_rendered_chronologys_limitations(job_dir, firm_config_path, data_root):
    """Limitations is written by build_doc before the review; the review
    refreshes it (``limitations.refresh``, #3464) before re-rendering, so the
    delivered Word file itself says which entry was left out and why."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Beta Roe DOB: 02/02/1980. Continue therapy.")
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    md = _md(sr)
    lim = md[md.index("## Records Reviewed and Limitations") :]
    assert "The entry dated 03/30/2026 (Example Clinic, Exhibit 1" in lim, lim
    chron = sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"
    text = review.docx_text(chron)
    assert "The entry dated 03/30/2026" in text and review_evidence.LEFT_OUT_REASON in text
    assert "Beta" not in text


def test_leaving_an_entry_out_re_derives_the_summary_tables_from_the_entries_left(job_dir, firm_config_path, data_root):
    """Decision (4), 2026-10-24: the Treatment Timeline counted the removed
    visit (2 visits, 01/20-03/30) and the Diagnostic Highlights kept a code
    only the removed entry carried. Both are computed again with build_doc's
    own functions over the entries that remain."""
    doc = DOC.replace(
        "| S13.4XXA | Sprain of ligaments of cervical spine | 01/20/2026 | Exhibit 1 - p. 2 |",
        "| S13.4XXA | Sprain of ligaments of cervical spine | 01/20/2026 | Exhibit 1 - p. 2 |\n"
        "| M54.2 | Cervicalgia | 03/30/2026 | Exhibit 1 - p. 3 |\n"
        "| Z99.9 | Not in the index | 03/30/2026 | Exhibit 1 - p. 3 |",
    )
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    (sr.slug_dir / "runs" / "alpha" / "map-1.md").write_text(
        "## INDEX\n2026-01-20 | Example Clinic | S13.4XXA | clinic.pdf\n2026-03-30 | Example Clinic | M54.2 | clinic.pdf\n"
    )
    _pages(sr, "Patient: Beta Roe DOB: 02/02/1980. Continue therapy.")
    assert "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 |" in _md(sr)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    md = _md(sr)
    assert "| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |" in md, md
    assert "03/30/2026 | 2" not in md
    assert "M54.2" not in md, "only the removed entry carried it"
    assert "| S13.4XXA | Sprain of ligaments of cervical spine | 01/20/2026 | Exhibit 1 - p. 2 |" in md
    assert "Z99.9" in md, "a code the index cannot derive is kept as it stands"
    assert any("re-derived" in ln for ln in log)


def test_a_check1_on_a_page_that_names_no_one_keeps_the_entry_as_a_note(job_dir, firm_config_path, data_root):
    """The independent review's B1 (2026-10-24): a page with no patient name
    and no date of birth gives no evidence the entry is anyone else's. The
    map read it as the client's record; the review's suspicion alone never
    drops it (the 08-27 rule). The finding is a NOTE and nothing is left out."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Plan: continue therapy.")
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    md = _md(sr)
    assert "03/30/2026\nExample Clinic" in md and "Continue therapy." in md, "the entry is kept"
    assert not (sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT).exists(), "no left-out row for it"
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    assert rec["findings"][0]["finding"].startswith("checked against the cited page, which names no other person")
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["identity_left_out"] == 0 and pol["identity_unnamed"] == 1
    assert "left out" not in _notes(sr)


def test_a_model_check3_quoting_words_the_page_prints_is_a_note(job_dir, firm_config_path, data_root):
    doc = DOC.replace("Continue therapy.", "Continue therapy in the referral pipeline at the spine center.")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    _pages(sr, "Patient: Alpha Example. Continue therapy in the referral pipeline at the spine center.")
    row = '| MUST-FIX | 3 system language | "referral pipeline" reads as the system | entry 03/30/2026 Example Clinic |'
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0, log
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0


# ---- row 13 ----------------------------------------------------------------------
REDATE = (
    '{"edits": [{"finding": "F1", "anchor": "N2", "action": "redate", "corrected": "01/30/2026", '
    '"exhibit": 1, "page": 3, "page_says": "Visit date 01/30/2026", '
    '"reason": "the page dates the visit 01/30/2026; 03/30 was a misread"}], "complete": true}'
)


def _review_on(sr: StageRun, doorway: FakeDoorway, today: date) -> int:
    sr._doorway = doorway  # type: ignore[assignment]
    return review.run(sr, today=today, verify=lambda _b, _a: [], images=lambda _x, _p: None, audit=lambda _s: 0)


def test_an_entry_dated_after_today_is_redated_from_its_page_and_files(job_dir, firm_config_path, data_root):
    """The DOC's 03/30/2026 entry, reviewed as of 2026-02-01, is a misread:
    its page dates the visit 01/30/2026."""
    sr, log = _package(job_dir, firm_config_path, data_root, PLAN_DOC)
    _pages(sr, f"Patient: Alpha Example. Visit date 01/30/2026. {PLAN}")
    d = FakeDoorway(CLEAN, REDATE)
    assert _review_on(sr, d, date(2026, 2, 1)) == 0, log
    md = _md(sr)
    assert "01/30/2026" in md and "03/30/2026\n" not in md
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and any(f["finding"].startswith("corrected before delivery") for f in rec["findings"])
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["repair_pending_by_check"] == {"5": 1} and pol["repair_settled"] == 1


def test_an_entry_still_dated_after_today_holds(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, PLAN_DOC)
    _pages(sr, f"Patient: Alpha Example. {PLAN}")
    decline = (
        '{"edits": [{"finding": "F1", "anchor": "N2", "action": "decline", "reason": "no date"}], "complete": true}'
    )
    assert _review_on(sr, FakeDoorway(CLEAN, decline), date(2026, 2, 1)) == 2
    assert log[-1].startswith("review_held:") and "03/30/2026" in log[-1]
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["repair_residual_by_check"] == {"5": 1}


def test_the_policy_record_is_written_on_a_clean_review(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["must_fix_left"] == 0 and pol["repair_residual_by_check"] == {}
    assert TODAY.year == 2026
