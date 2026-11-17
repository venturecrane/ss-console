"""An update's billing refresh is this client's alone, on a two-client matter (2026-11-17).

WHY. The first build planned every billing-named file on the matter. The $0
rehearsal on a real two-client matter, for an update for one client (a
one-unit job, so not ``job.joint`` and no patient check), planned the other
client's 18 files, a fire department's "No Record Statement" and one of the
other client's delivered exhibits; their dates would have reached this
client's worksheet. Every name, date and amount here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jobs.medchron import billing_plan as BP, job as job_mod, update_retire as URT
from jobs.stages import billing_update
from jobs.stages.review import docx_text
from jobs.medchron.update import review_list as RL
from jobs_testkit import doc_row, make_pdf
from test_update_billing_refresh import BILL, _scene as _billing_scene
from test_update_e2e import MEDICAL, _driver, _Model, _text, _upload_pause, scene  # noqa: F401 - scene is a pytest fixture the tests request by name

OTHER_DIR, SHARED_DIR, OTHER_CHRON = "fold-gamma", "fold-shared", "fold-gamma-chron"
OTHER = {"client_name": "Gamma Other", "surname": "Other", "folder_prefix": "/Gamma Other"}
SHARED_BILL = {**BILL, "patient": "Gamma Other", "provider": "Shared Clinic", "line_items": [
    {"date": "04/04/2026", "description": "office visit", "charge": "$90.00", "page": 1}
]}  # fmt: skip
BIG = 30 * 1024 * 1024


class _TwoClients(_Model):
    def create(self, **kw):
        text = json.dumps(kw.get("messages"), default=str)
        if not kw.get("tool_choice") and "doc_type" in text:
            self.calls.append({"billing": True, "shared": "Shared Clinic" in text})
            return _text(json.dumps(SHARED_BILL if "Shared Clinic" in text else BILL))
        return super().create(**kw)


def _two_client_scene(scene, firm: Path):
    job_dir, seat = _billing_scene(scene, firm, recorded={})
    seat.folders += [
        {"id": OTHER_DIR, "name": "Gamma Other", "parentId": None, "path": "/Gamma Other"},
        {"id": SHARED_DIR, "name": "SHARED", "parentId": None, "path": "/SHARED"},
        {"id": OTHER_CHRON, "name": "MEDICAL CHRONOLOGY - Gamma Other", "parentId": None, "path": "/MEDICAL CHRONOLOGY - Gamma Other"},
    ]  # fmt: skip
    other, shared, exhibit = make_pdf(["Ledger"]), make_pdf(["Shared bill"]), make_pdf(["Exhibit"])
    seat.docs += [
        doc_row("og", "Other Clinic Ledger.pdf", OTHER_DIR, len(other)),
        doc_row("sh", "Shared Clinic Bill.pdf", SHARED_DIR, len(shared)),
        doc_row("gc", "Gamma Other - Medical Chronology 08-01-26.docx", OTHER_CHRON, 10),
        doc_row("ex", "Exhibit 3 - Gamma Clinic Bills (Medical Records).pdf", OTHER_CHRON, len(exhibit)),
        doc_row("hg", "Huge Clinic Billing.pdf", MEDICAL, BIG),
    ]
    seat.blobs.update({"og": other, "sh": shared, "ex": exhibit})
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["matter_clients"] = [OTHER, {"client_name": "Alpha Example", "surname": "Example"}]
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    return job_dir, seat


def test_a_one_client_update_on_a_two_client_matter_reads_only_this_clients_billing(
    scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    job_dir, seat = _two_client_scene(scene, firm_config_path)
    _upload_pause(monkeypatch)
    model = _TwoClients()
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "delivered", o
    sd = data_root / "example-matter"
    minted = {i for batch in seat.mints for i in batch}
    # Never asked for: the other client's folder, a delivered exhibit, a file over the read limit.
    assert not {"og", "ex", "hg"} & minted
    aside = {x["id"]: x["why"] for x in json.loads((sd / "billing" / "set_aside.json").read_text())}
    assert aside == {"og": BP.OTHER_CLIENT, "ex": BP.IN_CHRONOLOGY}
    ws = next((sd / "out" / "alpha").glob("Alpha Example - Medical Billing Worksheet *.docx"))
    text = docx_text(ws)
    # The shared file's page names another patient: set aside on the worksheet, its date nowhere.
    assert "Excluded: Documents Naming Another Patient" in text and "Gamma Other" in text
    assert "04/04/2026" not in text and "03/03/2026" in text
    # The file over the read limit is named, never a silent gap.
    assert "Billing Records Not Read" in text and "Huge Clinic Billing.pdf" in text
    line = billing_update.reply_line(sd / "runs" / "alpha" / "update")
    assert line is not None and billing_update.TOO_LARGE in line and "Huge Clinic Billing.pdf" in line
    rep = json.loads((sd / "runs" / "alpha" / "dos_report.json").read_text())
    assert [r["date"] for r in rep["billed_no_record"]] == ["2026-03-03"]
    review = docx_text(next((sd / "out" / "alpha").glob("Alpha Example - Records Review *.docx")))
    assert RL.BILLING_ROW in review


def _unit(**kw) -> job_mod.Unit:
    base = {
        "unit": "alpha",
        "client_name": "Alpha Example",
        "name_token": "Example",
        "surname": "Example",
        "dob": "01/01/1970",
    }
    return job_mod.Unit(**{**base, **kw})


@pytest.mark.parametrize(
    ("folder", "name", "want"),
    [
        ("/Alpha Example/Billing", "Clinic Ledger", BP.OWN),
        ("/Gamma Other/Billing", "Clinic Ledger", BP.OTHER_CLIENT),
        ("/SHARED", "Clinic Statement", BP.SHARED),
        ("/SHARED", "Other Clinic Ledger", BP.OTHER_CLIENT),  # named for the other client
        ("/SHARED", "Example Clinic Ledger", BP.OWN),  # named for this client
    ],
)
def test_billing_files_route_as_a_joint_matters_files_do(folder: str, name: str, want: str) -> None:
    listing = [{"id": "f1", "name": name, "ext": ".pdf", "folderId": "d1", "size": 10}]
    read, aside = BP.decide(
        listing,
        {"d1": folder},
        [r"(?i)ledger|statement"],
        _unit(folder_prefix="/Alpha Example"),
        [OTHER],
        "{CLIENT} - Medical Chronology {MM-DD-YY}.docx",
    )
    got = (read or aside)[0]["why"]
    assert got == want


def test_a_file_in_any_delivered_chronology_folder_is_never_a_billing_source() -> None:
    listing = [
        {"id": "c", "name": "Gamma Other - Medical Chronology 08-01-26", "ext": ".docx", "folderId": "cf"},
        {"id": "e", "name": "Exhibit 3 - Clinic Bills (Medical Records)", "ext": ".pdf", "folderId": "cf", "size": 9},
    ]
    read, aside = BP.decide(
        listing,
        {"cf": "/MEDICAL CHRONOLOGY - Gamma Other"},
        [r"(?i)\bbill"],
        _unit(),
        [OTHER],
        "{CLIENT} - Medical Chronology {MM-DD-YY}.docx",
    )
    assert read == [] and [(x["id"], x["why"]) for x in aside] == [("e", BP.IN_CHRONOLOGY)]


def test_a_billing_named_file_that_is_not_a_bill_yields_no_billed_date() -> None:
    """A no-record statement is read like any billing-named file; the read types
    it, and its row says it holds no charges, never a billed date."""
    assert RL._finding_of([{"doc_type": "RECORDS_ONLY", "patient": None}], "alpha example") == RL.NO_CHARGES_ROW
    assert RL._finding_of([{"doc_type": "VENDOR_INVOICE"}], "alpha example") == RL.VENDOR_ROW
    assert RL._finding_of([{"doc_type": "LEDGER", "patient": "Gamma Other"}], "alpha example") == RL.OTHER_PATIENT_ROW
    assert RL._finding_of([{"doc_type": "LEDGER", "patient": "Alpha Example"}], "alpha example") == RL.BILLING_ROW


def test_the_plan_command_prints_the_scoped_plan_from_the_saved_listing(
    scene, data_root, firm_config_path, pricing_path, monkeypatch, capsys
) -> None:
    job_dir, seat = _two_client_scene(scene, firm_config_path)
    _upload_pause(monkeypatch)
    monkeypatch.setenv("JOBS_FIRM_CONFIG", str(firm_config_path))
    assert _driver(job_dir, firm_config_path, pricing_path, seat, _TwoClients()).run()[0].outcome == "delivered"
    assert BP.main([str(job_dir), "--firm-config", str(firm_config_path)]) == 0
    out = capsys.readouterr().out
    assert "refreshes: True" in out
    assert "ASIDE  Other Clinic Ledger.pdf" in out and "ASIDE  Exhibit 3 - Gamma Clinic Bills" in out
    assert "READ   Huge Clinic Billing.pdf" in out and billing_update.TOO_LARGE in out
    assert "READ   Example Clinic Ledger.pdf" in out and "1 pp" in out


def test_smd_retires_a_left_companion_in_one_recorded_act(
    scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    job_dir, seat = _billing_scene(scene, firm_config_path, recorded={})  # no hash on record: the worksheet is left
    _upload_pause(monkeypatch)
    assert _driver(job_dir, firm_config_path, pricing_path, seat, _TwoClients()).run()[0].outcome == "delivered"
    assert "w1" not in seat.deleted
    with pytest.raises(URT.Refused, match="not a file this update left"):
        URT.plan(job_dir, "c-unknown")
    job, path, rec, row = URT.plan(job_dir, "w1")
    URT.apply(seat, job, path, rec, row, "scott@smd.services", "the firm's copy is unchanged")
    assert "w1" in seat.deleted
    after = json.loads(path.read_text())
    assert not any(x.get("id") == "w1" for x in after["left"])
    assert after["retired"][-1]["by"] == "scott@smd.services"
