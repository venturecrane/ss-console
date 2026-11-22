"""A household member who shares the client's surname is not the client.

WHY (2026-11-22). Twice in one day a delivered chronology cited another
person's pages as the client's: a household member treated by the same
providers, her records arriving through the matter's email folders. Each
check that could have caught it was blind to her in its own way:

* the date-of-birth test read only a label and a numeric date on one line,
  so a value on the next line, after fill underscores, with a word month,
  or in a label column below its labels was never read;
* the patient-name test called any name carrying the client's surname the
  client's, so "Patient: <household member> <surname>" was invisible;
* a line naming a relative anywhere was skipped whole, patient label too;
* the identity stage checked only folded attachments with a sender, and on a
  one-person matter compared nothing else ("nothing to check");
* the review's ``whose`` counted the shared surname as naming the client;
* the entry reader's ``another_person`` findings were recorded and read by
  nothing.

Every name, date and number here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.gates import foreign_identity as FI
from jobs.proof import review as proof_review
from jobs.stages import identity as identity_stage, review_evidence, review_policy
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, seed_raw_manifest
from test_review_proof import CLEAN, FakeDoorway, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _md, _pages

# The job's one client is "Alpha Example", dob 01/01/1970 (jobs_testkit).
SUR, DOBS, FIRSTS = {"Example"}, {"01/01/1970"}, {"Alpha"}
REGISTRATION = (
    "Alder Creek Clinic    Patient Registration\n"
    "Patient Name: Gamma Example\n"
    "Date of Birth:\n"
    "08/02/2008\n"
    "Phone: 555-0100\n"
)
FAX_COVER = "FAX COVER SHEET\nTo: Records Department\nPages: 3\nPlease call with any questions.\n"
#: The client's own record naming a household member as a relative, in each
#: way a form prints one. None of it is another person's record.
CLIENT_OWN = (
    "Patient Name: Alpha Example    DOB: 01/01/1970\n"
    "Emergency Contact: Gamma Example    DOB: 08/02/2008\n"
    "Mother: Delta Example\n"
    "Name: Delta Example   DOB: 05/05/1950   Relationship: Mother\n"
    "Guarantor Name:\n"
    "Guarantor DOB:\n"
    "Delta Example\n"
    "05/05/1950\n"
    "Emergency Contact: Gamma Example\n"
    "Date of Birth:\n"
    "08/02/2008\n"
    "Patient: EXAMPLE, ALPHA J\n"
)


def _found(text: str) -> list[str]:
    return FI.findings(text, SUR, DOBS, FIRSTS)


# ---- the rule's two tests ------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Intake\nDate of Birth:\n08/02/2008\nPhone: 555-0100\n", id="value-on-the-next-line"),
        pytest.param("Intake\nDate of Birth: ________ 08/02/2008\n", id="fill-underscores"),
        pytest.param("Visit\nDOB: 02-Aug-2008\n", id="word-month"),
        pytest.param("Visit\nBirth Date: Aug 2, 2008\n", id="word-month-first"),
        pytest.param("Registration\nName:\nDate of Birth:\nPhone:\nGamma Q\n08/02/2008\n555-0100\n", id="label-column"),
    ],
)
def test_a_date_of_birth_printed_off_its_labels_line_is_read(text: str) -> None:
    """FALSIFIER: the one-line numeric DOB_LABEL of 126724860 returns [] for each."""
    assert _found(text) == ["a date of birth 08/02/2008 that is not the client's"]


def test_a_label_column_reads_each_value_in_its_labels_order_and_no_further() -> None:
    # The date under "Visit Date" is not the date of birth: the column pairs
    # by position, and a label column with too few values is not read.
    assert _found("Date of Birth:\nVisit Date:\n01/01/1970\n05/01/2023\n") == []
    assert _found("Date of Birth:\nPhone:\nVisit Date: 05/01/2023\n") == []


def test_a_patient_with_the_clients_surname_and_another_first_name_is_another_person() -> None:
    """FALSIFIER: ``_conflicts`` returned False for any name carrying the surname."""
    want = ["a patient name with the client's surname and another first name"]
    assert _found("Patient: Gamma Example\n") == want
    assert _found("Patient Name: EXAMPLE, GAMMA\n") == want
    # The client by her first name, her initial, or written short is the client.
    assert _found("Patient: Alpha Example\nPatient Name: EXAMPLE, ALPHA J\nPatient: A Example\n") == []
    assert FI.findings("Patient: Chris Example\n", SUR, DOBS, {"Christopher"}) == []


def test_a_patient_label_is_read_though_its_line_names_a_relative() -> None:
    """FALSIFIER: the whole line was skipped because it said "Emergency"."""
    line = "Patient Name: Gamma Example    DOB: 08/02/2008    Emergency Contact: Alpha Example\n"
    assert _found(line) == [
        "a date of birth 08/02/2008 that is not the client's",
        "a patient name with the client's surname and another first name",
    ]


def test_the_clients_own_record_naming_a_household_member_as_a_relative_is_not_flagged() -> None:
    """The negative control: every relative's name and date of birth on the
    client's own page stays the relative's."""
    assert _found(CLIENT_OWN) == []


def test_the_file_is_the_unit_a_fax_cover_goes_with_its_file() -> None:
    assert _found(FAX_COVER) == []
    assert _found(FAX_COVER + REGISTRATION)


# ---- the identity stage ----------------------------------------------------------
def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat(),
    )


def _scene(data_root: Path, rows: list[tuple[dict, str]], units: list[str]) -> Path:
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True, exist_ok=True)
    (sd / "units").mkdir(exist_ok=True)
    for r, text in rows:
        (sd / "text" / f"{r['id']}.txt").write_text(text)
    seed_raw_manifest(data_root, [{"ext": ".pdf", "ok": True, **r} for r, _t in rows])
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": i} for i in units]))
    return sd


def test_a_one_person_build_checks_every_file_its_unit_holds(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: the stage checked only rows with ``from_email`` and logged
    "single-unit matter; nothing to check"; the household member's file in
    the client's folder stayed in the unit."""
    sd = _scene(
        data_root,
        [
            ({"id": "c1", "name": "clinic visit", "folder": "/Medical"}, "[p.1]\n" + CLIENT_OWN),
            ({"id": "h1", "name": "registration", "folder": "/Medical"}, f"[p.1]\n{FAX_COVER}[p.2]\n{REGISTRATION}"),
        ],
        ["c1", "h1"],
    )
    log: list[str] = []
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, log)) == 0, log
    assert FI.withheld(sd) == {"h1"}, "the whole file, its fax cover with it; the client's own file stays"
    assert [r["id"] for r in json.loads((sd / "units" / "alpha.json").read_text())] == ["c1"]
    assert not any("nothing to check" in ln for ln in log)
    assert not any("Gamma" in ln or "2008" in ln for ln in log), "the other person is never in a log line"


def test_the_clients_own_file_with_one_misfiled_page_is_kept(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A provider's production of the client's records with another patient's
    page filed in it is the client's file: the sample matter's deliberate
    misfiled page (customers/pilot-smokeball/seed/records) withheld the whole
    orthopedic production and failed its coverage gate until this. FALSIFIER:
    drop ``names_client`` from the identity stage and c2 is withheld."""
    misfiled = "Patient: Alpha Example  DOB: 01/01/1970\nOffice visit.\n"
    sd = _scene(
        data_root,
        [({"id": "c2", "name": "ortho records", "folder": "/Medical"}, f"[p.1]\n{misfiled}[p.2]\n{REGISTRATION}")],
        ["c2"],
    )
    assert FI.names_client(misfiled, SUR, DOBS, FIRSTS) and not FI.names_client(REGISTRATION, SUR, DOBS, FIRSTS)
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, [])) == 0
    assert FI.withheld(sd) == set()


def test_a_pdf_filed_in_the_email_folder_without_a_sender_is_checked(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: a row without ``from_email`` was skipped. It is checked here
    though no unit holds it (the one-person unit sweep cannot be what finds it)."""
    row = {"id": "e1", "name": "scan from the office", "folder": "/Example_Alpha/EMAILS"}
    assert identity_stage.in_email(row) and not identity_stage.in_email({"folder": "/Medical"})
    sd = _scene(data_root, [(row, "[p.1]\n" + REGISTRATION)], [])
    assert identity_stage.run(_sr(job_dir, firm_config_path, data_root, [])) == 0
    assert FI.withheld(sd) == {"e1"}


# ---- the review's check 1 ------------------------------------------------------------
def test_whose_reads_a_household_members_page_as_other_and_the_clients_own_as_client(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: the shared surname counted as naming the client: "client"."""
    sr, _log = _package(job_dir, firm_config_path, data_root)
    assert review_evidence.whose(sr, ["Patient: Gamma Example\nOffice visit."]) == "other"
    assert review_evidence.whose(sr, [CLIENT_OWN]) == "client"


def test_a_check1_on_a_household_members_page_leaves_the_entry_out(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: on 126724860 the finding became a NOTE "which is the client's"."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Gamma Example\nContinue therapy.")
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    assert "Continue therapy." not in _md(sr) and "Cervical strain." in _md(sr)
    pol = json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())
    assert pol["identity_left_out"] == 1 and pol["identity_client"] == 0


def _trace_page_3_to_a_file(sr: StageRun, file_pages: list[str]) -> None:
    """Exhibit 1's pages 1-2 from the client's file, page 3 from page 1 of
    file "x2", whose transcript is `file_pages` (its other pages are not in
    the exhibit)."""
    out = sr.slug_dir / "out" / "alpha"
    files = [
        {"file": "clinic.pdf", "id": "x1", "start_page": 1, "pages": 2},
        {"file": "x2.pdf", "id": "x2", "start_page": 3, "pages": 1},
    ]
    (out / "page_map.json").write_text(json.dumps([{"exhibit": 1, "total_pages": 3, "files": files}]))
    (sr.slug_dir / "units").mkdir(exist_ok=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps([{"id": "x1", "name": "clinic.pdf"}, {"id": "x2", "name": "x2.pdf"}])
    )
    (sr.slug_dir / "text").mkdir(exist_ok=True)
    body = "".join(f"[p.{n}]\n{t}\n" for n, t in enumerate(file_pages, 1))
    (sr.slug_dir / "text" / "x2.txt").write_text(body)


def test_a_check1_on_a_fax_cover_goes_with_its_file(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """The cited page names no one; its FILE is another person's. FALSIFIER:
    ``whose`` of the page alone is "unnamed" and the entry was kept as a NOTE."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, FAX_COVER)
    _trace_page_3_to_a_file(sr, [FAX_COVER, REGISTRATION])
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    assert "Continue therapy." not in _md(sr)


def test_a_check1_on_a_fax_cover_of_the_clients_own_file_keeps_the_entry(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, FAX_COVER)
    _trace_page_3_to_a_file(sr, [FAX_COVER, CLIENT_OWN])
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1))) == 0, log
    assert "Continue therapy." in _md(sr)


# ---- the entry reader's another_person finding ---------------------------------------
def _reader_record(sr: StageRun, exhibit: int, page: int) -> None:
    rec = {"rows": [{"key": "N2", "recorded": [{"class": "another_person", "line": "S1", "exhibit": exhibit,
                                                 "page": page, "reason": "record_only_class"}]}]}  # fmt: skip
    (sr.slug_dir / "runs" / "alpha" / "entry_read.json").write_text(json.dumps(rec))


def test_an_entry_readers_another_person_finding_is_settled_by_the_reviews_check1(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: ``record_only_class`` was read by nothing; the review passed
    CLEAN and the entry was delivered."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Gamma Example\nContinue therapy.")
    _reader_record(sr, 1, 3)
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    assert "Continue therapy." not in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    assert any(f["finding"].startswith("left out before delivery by rule") for f in rec["findings"])


def test_an_entry_readers_finding_on_the_clients_own_page_is_a_note_and_the_entry_stays(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, "Patient: Alpha Example DOB: 01/01/1970\nMother: Gamma Example\nContinue therapy.")
    _reader_record(sr, 1, 3)
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0, log
    assert "Continue therapy." in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    notes = [f["finding"] for f in rec["findings"]]
    assert notes == [
        "checked against the cited page, which is the client's: the page cited for the entry dated 03/30/2026 "
        "(Example Clinic) was read as possibly not the client's"
    ]
