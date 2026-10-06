"""The health-insurer notice and the DMV SR1 (2026-10-06), and the two pieces
they stand on: reading a PHOTO of a document, and accepting a value read off
that photo only beside its label.

What these defend, each written so removing the line it defends fails it:

* a photo (JPEG/PNG) goes to one vision call as an ``image`` block, and a HEIC
  photo or one over the API's 5 MB is refused by name before any call;
* a cited value is accepted only on the line of one of ITS labels (or right
  under it); a value elsewhere on the card, an altered value, a short value or
  an uncited field is never accepted, and prints the marker;
* the health notice is the firm's form: the client's own title and last name
  in the first sentence, the assisting staff member's name, email and the
  authored preparer title on the signature, the member ID only from the card;
* the SR1 fills what the record holds, ticks "Injured" and "Driver", leaves
  the certification (date, printed name, signature) untouched, and its result
  never carries a value: no date of birth or license number in the turn that
  writes the reply.

All facts are synthetic; no client value is committed.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import cited_facts, extract, form_letters as fl, sr1_form, sr19_form, vision
from smokeball_connector.form_docx import document_paragraphs, placeholders_in
from smokeball_connector.library import NotResolved, ResolvedTemplate

FORMS_DIR = Path(__file__).parent / "fixtures" / "forms"
HEALTH = (FORMS_DIR / "Form - Med Ins Blue Shield.docx").read_bytes()
SR1 = (FORMS_DIR / "Form - DMV SR1.pdf").read_bytes()

MATTER = "m-9"
CLIENT = "c-9"
OTHER = "c-other"
INS_1P = "c-ins1"
INS_3P = "c-ins3"
STAFF = "s-resp"
ASSIST = "s-assist"
CARD = "f-card"
LICENSE = "f-dl"

CARD_TEXT = """[p.1]
blue CALIFORNIA                 trio HMO
Subscriber: ID# XQZ900111222
DANA EXAMPLE
HILL PHYS SACRAMENTO PRIMED INC
Group #
W1234567
Effective: 01/01/2026
Primary Care: $30
"""
LICENSE_TEXT = """[p.1]
CALIFORNIA DRIVER LICENSE
DL Z7654321
EXP 01/01/2030
LN EXAMPLE
FN DANA
"""


class _Record:
    def __init__(self, *, title: str | None = "Ms", texts: dict[str, str] | None = None) -> None:
        self.title = title
        self.texts = {CARD: CARD_TEXT, LICENSE: LICENSE_TEXT} if texts is None else texts
        self.uploads: list[tuple[str, str, bytes]] = []

    def get(self, path: str, **_params: Any) -> Any:
        if path == f"/matters/{MATTER}":
            return {
                "id": MATTER,
                "clientIds": [CLIENT],
                "personResponsibleStaffId": STAFF,
                "personAssistingStaffId": ASSIST,
            }
        if path == f"/matters/{MATTER}/roles":
            return {
                "roles": [
                    {
                        "name": "Plaintiff",
                        "isClient": True,
                        "contactId": CLIENT,
                        "relationships": [{"id": "rel-i1", "name": "Insurer", "contactId": INS_1P}],
                    },
                    {
                        "name": "Defendant",
                        "isOtherSide": True,
                        "contactId": OTHER,
                        "relationships": [{"id": "rel-i3", "name": "Insurer", "contactId": INS_3P}],
                    },
                ]
            }
        if path == f"/matters/{MATTER}/layouts":
            return {"value": [{"id": "lay"}]}
        if path == f"/matters/{MATTER}/layouts/lay":
            values = {
                "Matter/CaseDetails/AccidentDetails/AccidentDate": "2026-03-04",
                "Matter/CaseDetails/AccidentDetails/AccidentLocation": "Main St and 5th, Exampleton",
                "Matter/Plaintiffs/InsurancePolicy/PolicyNumber": "PP-111",
                "Matter/Defendants/InsurancePolicy/PolicyNumber": "DP-333",
            }
            return {"values": [{"key": k, "value": v} for k, v in values.items()]}
        if path == f"/contacts/{CLIENT}":
            person = {
                "firstName": "Dana",
                "lastName": "Example",
                "birthDate": "1990-02-03T00:00:00",
                "cell": {"areaCode": "916", "number": "555-0199"},
                "residentialAddress": {
                    "addressLine1": "1 Test Way",
                    "city": "Exampleton",
                    "state": "CA",
                    "zipCode": "95000",
                },
            }
            if self.title is not None:
                person["title"] = self.title
            return {"id": CLIENT, "person": person}
        if path == f"/contacts/{OTHER}":
            return {
                "id": OTHER,
                "person": {
                    "firstName": "Robin",
                    "lastName": "Other",
                    "residentialAddress": {
                        "addressLine1": "9 Far Rd",
                        "city": "Elsewhere",
                        "state": "CA",
                        "zipCode": "95999",
                    },
                },
            }
        if path == f"/contacts/{INS_1P}":
            return {"id": INS_1P, "company": {"name": "Own Side Insurance"}}
        if path == f"/contacts/{INS_3P}":
            return {"id": INS_3P, "company": {"name": "Other Side Mutual"}}
        if path == f"/staff/{ASSIST}":
            return {"id": ASSIST, "firstName": "Alex", "lastName": "Barnes", "email": "alex@firm.example"}
        if path == f"/staff/{STAFF}":
            return {"id": STAFF, "firstName": "Sam", "lastName": "Signer"}
        if path == f"/matters/{MATTER}/documents/files":
            return {"value": []}
        if path.startswith(f"/matters/{MATTER}/documents/files/"):
            return {"id": path.rsplit("/", 1)[1]}
        raise AssertionError(f"unscripted GET {path}")

    def add_file(self, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None) -> dict[str, Any]:
        self.uploads.append((matter_id, file_name, data))
        return {"fileId": "f-new"}


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text("form_letters:\n  preparer_title: 'Legal Assistant'\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return path


@pytest.fixture
def documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cited documents' transcriptions, as read_document would return them."""

    def _text(client: _Record, _matter: str, file_id: str) -> tuple[str, str]:
        if file_id not in client.texts:
            raise RuntimeError("no such file")
        return client.texts[file_id], {CARD: "blue shield front", LICENSE: "CDL"}.get(file_id, file_id)

    monkeypatch.setattr(cited_facts, "_document_text", _text)


# ---- photos of documents ----------------------------------------------------


def test_a_photo_is_one_image_block_with_the_label_rule() -> None:
    body = vision.build_request("AAAA", "image/jpeg")
    block, text = body["messages"][0]["content"]
    assert block == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    assert "Label: value" in text["text"] and "photograph" in text["text"]
    # A scanned page is unchanged: a document block and the page instruction.
    page = vision.build_request("AAAA")["messages"][0]["content"]
    assert page[0]["type"] == "document" and page[1]["text"] == vision.INSTRUCTION


def test_heic_and_oversize_photos_are_refused_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(vision, "_transcribe_page", lambda *_a, **_k: pytest.fail("no call may be made"))
    assert vision.transcribe_image(b"x", media_type="image/heic").reason == vision.REASON_INCOMPLETE
    big = b"\xff\xd8\xff" + b"0" * (vision.IMAGE_MAX_BYTES + 1)
    assert vision.transcribe_image(big, media_type="image/jpeg").reason == vision.REASON_OVER_BYTE_CAP


def test_a_photo_is_read_once_and_composed_like_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    calls: list[str] = []

    def _one(blob: bytes, abort: Any = None, *, media_type: str = "application/pdf") -> tuple[str, str, None]:
        calls.append(media_type)
        return "ID# XQZ900111222", "end_turn", None

    monkeypatch.setattr(vision, "_transcribe_page", _one)
    out = vision.transcribe_image(b"\xff\xd8\xffdata", media_type="image/jpeg")
    assert calls == ["image/jpeg"] and out.reason is None
    assert out.text.startswith("[p.1]") and "XQZ900111222" in out.text


def test_extract_routes_a_jpeg_to_vision_and_refuses_heic_by_name() -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    # A JPEG is binary: before this it fell to "no text-extraction path".
    result = extract.extract_text_ex(jpeg, file_name="card.jpg", file_extension=".jpg", allow_vision=False)
    assert result.method == extract.METHOD_NONE_SCANNED and result.text == ""
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    with pytest.raises(extract.UnsupportedDocumentError, match="HEIC"):
        extract.extract_text_ex(heic, file_name="card.heic", file_extension=".heic", allow_vision=True)


# ---- a cited value is accepted only beside its label ------------------------


@pytest.mark.parametrize(
    ("value", "field", "ok"),
    [
        ("XQZ900111222", "member_id", True),  # on the ID# line
        ("XQZ 900 111 222", "member_id", True),  # spacing is not a different value
        ("W1234567", "group_number", True),  # on the line right under "Group #"
        ("XQZ900111223", "member_id", False),  # one digit off
        ("W1234567", "member_id", False),  # a real value, under the wrong label
        ("01/01/2026", "group_number", False),  # on the card, not beside Group
        ("CA", "group_number", False),  # too short to prove anything
    ],
)
def test_value_beside_label(value: str, field: str, ok: bool) -> None:
    assert cited_facts.value_beside_label(CARD_TEXT, value, cited_facts.LABELS[field]) is ok


def test_confirm_marks_what_it_could_not_prove(documents: None) -> None:
    got = cited_facts.confirm(
        _Record(),
        MATTER,
        {
            "member_id": {"value": "XQZ900111222", "file_id": CARD},
            "group_number": {"value": "W1234567"},  # no file cited
            "driver_license_number": {"value": "Z0000000", "file_id": LICENSE},  # not what the license says
            "vehicle_vin": {"value": "1HGCM82633A004352", "file_id": "f-missing"},  # unreadable
        },
        {"member_id", "group_number", "driver_license_number", "vehicle_vin", "vehicle_plate"},
    )
    assert got.facts["member_id"].value == "XQZ900111222" and got.shown == {"member_id": "XQZ900111222"}
    for field in ("group_number", "driver_license_number", "vehicle_vin", "vehicle_plate"):
        assert got.facts[field].value is None, field
    assert set(got.refused) == {"group_number", "driver_license_number", "vehicle_vin"}


def test_a_license_number_is_accepted_but_never_shown(documents: None) -> None:
    got = cited_facts.confirm(
        _Record(),
        MATTER,
        {"driver_license_number": {"value": "Z7654321", "file_id": LICENSE}},
        {"driver_license_number"},
    )
    assert got.facts["driver_license_number"].value == "Z7654321" and got.shown == {}


# ---- the health-insurer notice ----------------------------------------------


def test_the_health_form_carries_its_fields_and_the_firms_fixed_addressee() -> None:
    assert placeholders_in(HEALTH) == [
        "date",
        "client_name",
        "date_of_loss",
        "member_id",
        "client_salutation",
        "preparer_email",
        "signer_name",
        "signer_title",
    ]
    text = "\n".join(document_paragraphs(HEALTH))
    # The committed fixture names the recovery vendor generically (vendor names
    # are scrubbed from this public repo); the firm's library copy names it.
    assert "Recovery Vendor" in text and "La Grange, KY 40031-0589" in text and "Blue Shield of California" in text
    assert "mailto:" not in str(HEALTH)  # the source letter's link to its own preparer is gone


def _file_health(record: _Record, monkeypatch: pytest.MonkeyPatch, cited: Any) -> tuple[dict[str, Any], str]:
    monkeypatch.setattr(fl, "_client", lambda: record)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    hit = ResolvedTemplate(
        bytes=HEALTH, name="Form - Med Ins Blue Shield", file_id="t", matter_id="lib", folder_id="fold"
    )
    monkeypatch.setattr(fl, "_resolve_form", lambda _c, _s: hit)
    out = fl.render_firm_form_letter(MATTER, "health_blue_shield", "2026-10-06", cited)
    text = "\n".join(document_paragraphs(record.uploads[-1][2])) if record.uploads else ""
    return out, text


def test_the_health_notice_reads_as_the_firms_letter(
    seat: Path, documents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _Record()
    out, text = _file_health(record, monkeypatch, {"member_id": {"value": "XQZ900111222", "file_id": CARD}})
    assert out["status"] == "filed" and out["unfilled"] == []
    assert record.uploads[0][1] == "Med Ins Req. Blue Shield.docx"
    for want in (
        "October 6, 2026",
        "Patient/Client\t\t: Dana Example",
        "03/04/2026",
        "Member ID #\t\t: XQZ900111222",
        "retained by Ms. Example to present a claim",
        "please send the copy to alex@firm.example",
        "Alex Barnes",
        "Legal Assistant",
    ):
        assert want in text, want
    assert "Sam Signer" not in text  # the notice is the legal assistant's, not the attorney's
    assert out["card_values_to_check"] == {"member ID": "XQZ900111222"}


def test_a_member_id_not_on_the_card_prints_the_marker(
    seat: Path, documents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _Record()
    out, text = _file_health(record, monkeypatch, {"member_id": {"value": "XQZ999999999", "file_id": CARD}})
    assert "Member ID #\t\t: [Not in the file: member ID (could not be confirmed on the cited document)]" in text
    assert out["card_values_to_check"] == {} and "member ID" in out["cited_refused"]


def test_no_title_on_the_contact_is_a_marker_never_a_guess(
    seat: Path, documents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _Record(title=None)
    out, text = _file_health(record, monkeypatch, None)
    assert "retained by [Not in the file: client's title (Mr./Ms.) for Example]" in text
    assert "Member ID #\t\t: [Not in the file: member ID]" in text
    assert any("title" in u for u in out["unfilled"])


def test_an_unset_preparer_title_prints_the_marker(
    documents: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("form_letters: {}\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    _out, text = _file_health(_Record(), monkeypatch, None)
    assert "[Not in the file: how Alex Barnes signs: title]" in text


# ---- the SR1 ------------------------------------------------------------------


def _fields(blob: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    return {k: v.get("/V") for k, v in (PdfReader(io.BytesIO(blob)).get_fields() or {}).items()}


def test_the_sr1_fills_the_record_and_leaves_the_certification(seat: Path, documents: None) -> None:
    values, _sources, _confirmed = sr1_form.gather(
        _Record(), MATTER, {"driver_license_number": {"value": "Z7654321", "file_id": LICENSE}}
    )
    blob = sr1_form.fill_sr1(SR1, values, injured_driver=True)
    got = _fields(blob)
    assert got["DRIVERS NAME.0"] == "Dana Example"
    assert got["DATE OF BIRTH-MONTH.0"] == "02/03/1990"
    assert got["DATE OF ACCIDENT-MONTH"] == "03/04/2026"
    assert got["DRIVER LICENSE NUMBER.0"] == "Z7654321"
    assert got["DRIVERS STREET ADDRESS.0"] == "1 Test Way" and got["ZIP CODE.0"] == "95000"
    assert got["HOME PREFIX.0"] == "916" and got["HOME PHONE NUMBER.0"] == "555-0199"
    assert got["DRIVERS NAME.1"] == "Robin Other" and got["INSURANCE CO. NAME.221"] == "Other Side Mutual"
    assert got["POLICY NUMBER.0"] == "PP-111" and got["POLICY NUMBER.1"] == "DP-333"
    assert got["INJURED.0"] == "/INJURED" and got["DRIVER1"] == "/DRIVER"
    for untouched in ("DATE", "PRINTED NAME", "# of vehicles", "Title", "Date"):
        assert got.get(untouched) in (None, ""), untouched


def test_render_sr1_files_it_and_its_result_carries_no_value(
    seat: Path, documents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _Record()
    monkeypatch.setattr(sr1_form, "_client", lambda: record)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    hit = ResolvedTemplate(bytes=SR1, name="Form - DMV SR1", file_id="t", matter_id="lib", folder_id="fold")
    monkeypatch.setattr(sr1_form, "resolve_template", lambda *_a: hit)
    out = sr1_form.render_sr1(MATTER, {"driver_license_number": {"value": "Z7654321", "file_id": LICENSE}})
    assert out["status"] == "filed" and record.uploads[0][1] == "DMV SR1 - for client signature.pdf"
    dumped = json.dumps(out)
    for value in ("02/03/1990", "1990", "Z7654321", "1 Test Way", "555-0199", "PP-111"):
        assert value not in dumped, value
    assert "her date of birth" in out["filled"] and "her driver license number" in out["filled"]
    assert "the certification: date, printed name and signature" in out["left_for_client"]


def test_an_sr1_that_does_not_resolve_files_nothing(seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    monkeypatch.setattr(sr1_form, "_client", lambda: record)
    monkeypatch.setattr(sr1_form, "resolve_template", lambda *_a: NotResolved("no file named 'Form - DMV SR1.pdf'"))
    out = sr1_form.render_sr1(MATTER)
    assert out["status"] == "refused" and record.uploads == []


def test_a_pdf_template_name_keeps_its_extension() -> None:
    from smokeball_connector.library import LibraryConfig

    cfg = LibraryConfig(authored=True, templates={"sr1_form": "Form - DMV SR1.pdf"})
    assert cfg.template_name("sr1_form") == "Form - DMV SR1.pdf"


def test_the_letter_date_default_is_unchanged_for_rep_letters() -> None:
    assert fl.FORMS["first_party_rep"].facts_kind == "rep" and date(2026, 10, 6)


def test_a_company_client_is_never_the_driver(seat: Path, documents: None) -> None:
    """Rehearsal 2026-10-06: on a matter whose client is a company, the SR1
    printed the company as "her name". A driver is a person."""

    class _Company(_Record):
        def get(self, path: str, **params: Any) -> Any:
            if path == f"/contacts/{CLIENT}":
                return {"id": CLIENT, "company": {"name": "Example Holdings LLC"}}
            return super().get(path, **params)

    values, _s, _c = sr1_form.gather(_Company(), MATTER, None)
    assert "DRIVERS NAME.0" not in values and "NAME &  ADDRESS OF INJURED OR DECEASED.0" not in values
    assert values["DRIVERS NAME.1"] == "Robin Other"  # the other side is still read


def test_the_client_completes_list_survives_the_reply_checks() -> None:
    import re

    for line in sr1_form.CLIENT_COMPLETES:
        assert "$" not in line and not re.search(r"\b[A-Z]{2,}\b", line), line


# ---- the fax cover sheets and the SR 19C (2026-10-06) -------------------------

DEC = (FORMS_DIR / "Form - Fax Dec Page.docx").read_bytes()
MEDPAY = (FORMS_DIR / "Form - Fax Medpay.docx").read_bytes()
SR19 = (FORMS_DIR / "Form - DMV SR19.pdf").read_bytes()
ADJ = "c-adj1"


class _Faxed(_Record):
    """The 1st party insurer carries no fax; its adjuster does (the shape of
    the live A&P records), and the Plaintiffs layout names both plus a claim."""

    def get(self, path: str, **params: Any) -> Any:
        if path == f"/matters/{MATTER}/roles":
            out = super().get(path, **params)
            out["roles"][0]["relationships"].append({"id": "rel-a1", "name": "Adjuster", "contactId": ADJ})
            return out
        if path == f"/matters/{MATTER}/layouts/lay":
            out = super().get(path, **params)
            out["values"].append({"key": "Matter/Plaintiffs/InsurancePolicy/Claims/Number", "value": "CL-0001"})
            return out
        if path == f"/contacts/{ADJ}":
            return {"id": ADJ, "company": {"name": "Pat Adjuster", "fax": {"areaCode": "800", "number": "555-0101"}}}
        return super().get(path, **params)


@pytest.fixture
def firm_seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text(
        "form_letters:\n  preparer_title: 'Legal Assistant'\n"
        "  firm:\n    name: 'Example & Firm, LLP'\n    address: ['1 Firm Way', 'Lawtown, CA 90000']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return path


def _file_fax(record: _Record, form: str, blob: bytes, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], str]:
    monkeypatch.setattr(fl, "_client", lambda: record)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    hit = ResolvedTemplate(bytes=blob, name=form, file_id="t", matter_id="lib", folder_id="fold")
    monkeypatch.setattr(fl, "_resolve_form", lambda _c, _s: hit)
    out = fl.render_firm_form_letter(MATTER, form, "2026-10-06")
    return out, "\n".join(document_paragraphs(record.uploads[-1][2])) if record.uploads else ""


def test_the_fax_forms_carry_their_fields_and_no_preparer_link() -> None:
    assert set(placeholders_in(DEC)) == {
        "preparer_email",
        "carrier_name",
        "signer_name",
        "carrier_fax",
        "date_numeric",
        "claim_number",
    }
    assert set(placeholders_in(MEDPAY)) == {
        "preparer_email",
        "carrier_name",
        "signer_name",
        "carrier_fax",
        "page_count",
        "date_numeric",
        "claim_number",
        "client_name",
        "signer_title",
    }
    for blob in (DEC, MEDPAY):
        assert b"mailto" not in blob


def test_the_dec_page_cover_reads_as_the_firms(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Faxed()
    out, text = _file_fax(record, "dec_page_fax", DEC, monkeypatch)
    assert out["status"] == "filed" and record.uploads[0][1] == "Fax Cover Sheet - Req Dec Page.docx"
    for want in (
        "Own Side Insurance",
        "(800) 555-0101",
        "10/06/2026",
        "Claim # CL-0001",
        "Alex Barnes",
        "Please forward a copy of the dec page as soon as possible. My email is alex@firm.example.",
    ):
        assert want in text, want
    assert out["unfilled"] == []
    assert "adjuster contact" in out["facts_used"]["carrier_fax"]  # the reply names where the fax came from


def test_the_med_pay_cover_leaves_the_page_count_for_the_sender(
    firm_seat: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, text = _file_fax(_Faxed(), "med_pay_fax", MEDPAY, monkeypatch)
    assert "applied for Dana Example’s claim" in text and "PAYMENT DIRECTLY TO THE PROVIDERS" in text
    assert "[Not in the file: page count (attach the bills, then count the pages)] (including cover)" in text
    assert "Legal Assistant" in text and out["unfilled"] == [
        "[Not in the file: page count (attach the bills, then count the pages)]"
    ]


def test_no_fax_on_record_prints_the_marker(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _out, text = _file_fax(_Record(), "dec_page_fax", DEC, monkeypatch)
    assert "[Not in the file: 1st party insurer fax number]" in text


# ---- the SR 19C -----------------------------------------------------------------


def test_the_sr19_reads_as_the_firms_request(firm_seat: Path, documents: None) -> None:
    from datetime import date

    values, _c = sr19_form.gather(
        _Record(), MATTER, {"driver_license_number": {"value": "Z7654321", "file_id": LICENSE}}, date(2026, 10, 6)
    )
    got = _fields(sr19_form.fill_acroform(SR19, values, sr19_form.CHECKS))
    assert got["Name/Address1"] == "\nExample & Firm, LLP\n1 Firm Way\nLawtown, CA 90000"  # name line blank
    assert got["Date of Request1"] == "10/06/2026" and got["AccidentvDate2"] == "03/04/2026"
    assert got["Client1"] == got["Driver1"] == "Dana Example"
    assert got["BDay 1"] == "02/03/1990" and got["DLNo 1"] == "Z7654321"
    assert got["Address1"] == "1 Test Way, Exampleton, CA 95000"
    assert got["Subject of Inquiry"] == "Robin Other" and got["Address2"] == "9 Far Rd, Elsewhere, CA 95999"
    for box in ("Insurance1", "Check Box1.0.0", "Check Box1.3.0", "Check Box5"):
        assert got[box] == "/Yes", box
    for untouched in ("Uninsured", "Photocopy1", "Check Box6", "date of certification", "printed name", "DLNo 2"):
        assert got.get(untouched) in (None, "", "/Off"), untouched


def test_an_entity_client_is_never_the_driver(firm_seat: Path, documents: None) -> None:
    from datetime import date

    class _Company(_Record):
        def get(self, path: str, **params: Any) -> Any:
            if path == f"/contacts/{CLIENT}":
                return {"id": CLIENT, "company": {"name": "Example Holdings LLC"}}
            return super().get(path, **params)

    values, _c = sr19_form.gather(_Company(), MATTER, None, date(2026, 10, 6))
    assert not {"Client1", "Driver1", "BDay 1", "Address1", "DLNo 1"} & set(values)


def test_render_sr19_files_it_and_carries_no_value(
    firm_seat: Path, documents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _Record()
    monkeypatch.setattr(sr19_form, "_client", lambda: record)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    monkeypatch.setattr(
        sr19_form,
        "resolve_template",
        lambda *_a: ResolvedTemplate(bytes=SR19, name="Form - DMV SR19", file_id="t", matter_id="lib", folder_id="f"),
    )
    out = sr19_form.render_sr19(MATTER, {"driver_license_number": {"value": "Z7654321", "file_id": LICENSE}})
    assert out["status"] == "filed" and record.uploads[0][1] == "DMV SR19 - for signature.pdf"
    dumped = json.dumps(out)
    for value in ("Z7654321", "02/03/1990", "1 Test Way", "Robin Other"):
        assert value not in dumped, value
    assert "the certification: date, printed name and signature" in out["left_for_signer"]


def test_an_sr19_that_does_not_resolve_files_nothing(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    monkeypatch.setattr(sr19_form, "_client", lambda: record)
    monkeypatch.setattr(sr19_form, "resolve_template", lambda *_a: NotResolved("no file"))
    assert sr19_form.render_sr19(MATTER)["status"] == "refused" and record.uploads == []


def test_the_signer_list_survives_the_reply_checks() -> None:
    import re

    for line in sr19_form.CLIENT_COMPLETES:
        assert "$" not in line and not re.search(r"\b[A-Z]{2,}\b", line), line


# ---- the wage loss letter and the med pay ledger email (2026-10-06) ------------

WAGE = (FORMS_DIR / "Form - Wage Loss Letter.docx").read_bytes()
EMP = "c-emp"


class _Employed(_Faxed):
    """The client's role names an Employer with a mailing address, and the
    1st party adjuster contact carries the claims email."""

    def get(self, path: str, **params: Any) -> Any:
        if path == f"/matters/{MATTER}/roles":
            out = super().get(path, **params)
            out["roles"][0]["relationships"].append({"id": "rel-e1", "name": "Employer", "contactId": EMP})
            return out
        if path == f"/contacts/{EMP}":
            address = {"addressLine1": "100 Dock Rd", "city": "Portville", "state": "CA", "zipCode": "95001"}
            return {"id": EMP, "company": {"name": "Acme Freight Co", "businessAddress": address}}
        if path == f"/contacts/{ADJ}":
            return {"id": ADJ, "company": {"name": "Pat Adjuster", "email": "claims@carrier.example"}}
        return super().get(path, **params)


def _wage(record: _Record, monkeypatch: pytest.MonkeyPatch, employer: Any = None) -> tuple[dict[str, Any], str]:
    monkeypatch.setattr(fl, "_client", lambda: record)
    monkeypatch.setattr(fl, "SLEEP", lambda _s: None)
    hit = ResolvedTemplate(bytes=WAGE, name="wage", file_id="t", matter_id="lib", folder_id="fold")
    monkeypatch.setattr(fl, "_resolve_form", lambda _c, _s: hit)
    out = fl.render_firm_form_letter(MATTER, "wage_loss", "2026-10-06", employer=employer)
    return out, "\n".join(document_paragraphs(record.uploads[-1][2])) if record.uploads else ""


def test_the_wage_form_carries_its_fields_and_no_preparer_link() -> None:
    assert set(placeholders_in(WAGE)) == {
        "date",
        "employer_address",
        "client_name",
        "date_of_loss",
        "accident_date_long",
        "client_salutation",
        "signer_name",
        "signer_title",
        "client_dob",
        "employer_name",
    }
    assert b"mailto" not in WAGE


def test_the_wage_letter_reads_as_the_firms(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Employed()
    out, text = _wage(record, monkeypatch)
    assert out["status"] == "filed" and record.uploads[0][1] == "Wage Loss Letter.docx"
    paras = [p for p in text.split("\n") if p.strip()]
    i = paras.index("Attn: Human Resources")
    assert paras[i + 1 : i + 4] == ["Acme Freight Co", "100 Dock Rd", "Portville, CA 95001"]
    for want in (
        "October 6, 2026",
        "RE:\tClient/Employee\t:\tDana Example",
        "Date of Injury\t\t:\t03/04/2026",
        "in an accident that occurred on March 4, 2026.  As a result of the injuries sustained, Ms. Example suffered",
        "applies to Ms. Example and sign",
        "Alex Barnes",
        "Legal Assistant",
        "Employee Name: \tDana Example",
        "D.O.B.:\t02/03/1990",
        "verify that Dana Example is/was employed by Acme Freight Co. As a result of an accident, "
        "Ms. Example was unable to perform the necessary duties",
    ):
        assert want in text, want
    assert " her " not in text and out["unfilled"] == []
    assert out["facts_used"]["employer_name"] == "the client role's Employer relationship"


def test_with_no_employer_nothing_is_filed(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Faxed()
    out, _text = _wage(record, monkeypatch)
    assert out["status"] == "needs_employer" and record.uploads == []


def test_the_employer_as_the_sender_wrote_it(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out, text = _wage(_Faxed(), monkeypatch, {"name": "Beta Bakery", "address": "5 Oven Ln, Crumbton, CA 95002"})
    assert "Beta Bakery\n5 Oven Ln\nCrumbton, CA 95002" in text
    assert "as the sender wrote it" in out["facts_used"]["employer_name"]
    out, text = _wage(_Faxed(), monkeypatch, {"name": "Beta Bakery"})
    assert "Beta Bakery\n[Not in the file: employer's mailing address]" in text
    assert out["unfilled"] == ["[Not in the file: employer's mailing address]"]


def test_the_file_beats_the_email_and_a_nameless_employer_is_refused(
    firm_seat: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _out, text = _wage(_Employed(), monkeypatch, {"name": "Beta Bakery"})
    assert "Acme Freight Co" in text and "Beta Bakery" not in text
    record = _Faxed()
    out, _t = _wage(record, monkeypatch, {"address": "no name"})
    assert out["status"] == "refused" and record.uploads == []


def _ledger(record: _Record, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(fl, "_client", lambda: record)
    return fl.render_firm_form_letter(MATTER, "med_pay_ledger_email")


def test_the_ledger_email_is_drafted_and_nothing_is_filed(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Employed()
    out = _ledger(record, monkeypatch)
    assert out["status"] == "drafted" and record.uploads == [] and out["fileId"] is None
    assert out["email"]["to"] == "claims@carrier.example"
    assert out["email"]["subject"] == "Claim CL-0001 | Medical Payment Ledger"
    body = out["email"]["body"]
    assert "regarding our client, Dana Example." in body and "your insured" not in body
    assert body.endswith("Kind regards,") and "Alex Barnes" not in body  # her own signature applies
    assert "adjuster contact Pat Adjuster" in out["facts_used"]["carrier_email"]


def test_a_ledger_email_missing_its_facts_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _ledger(_Record(), monkeypatch)
    assert out["status"] == "incomplete"
    assert out["email"]["to"] == "[Not in the file: 1st party insurer email]"
    assert out["unfilled"] == [
        "[Not in the file: 1st party insurer email]",
        "[Not in the file: 1st party claim number]",
    ]


def test_an_unusable_employer_on_file_never_reads_as_none(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Nameless(_Employed):
        def get(self, path: str, **params: Any) -> Any:
            if path == f"/contacts/{EMP}":
                return {"id": EMP, "company": {}}
            return super().get(path, **params)

    class _Two(_Employed):
        def get(self, path: str, **params: Any) -> Any:
            if path == f"/matters/{MATTER}/roles":
                out = super().get(path, **params)
                out["roles"][0]["relationships"].append({"id": "rel-e2", "name": "Employer", "contactId": "c-emp2"})
                return out
            if path == "/contacts/c-emp2":
                return {"id": "c-emp2", "company": {"name": "Second Job LLC"}}
            return super().get(path, **params)

    for record, why in ((_Nameless(), "no name"), (_Two(), "2 Employers (Acme Freight Co, Second Job LLC)")):
        out, _t = _wage(record, monkeypatch, {"name": "Beta Bakery"})  # the sender's never replaces it
        assert out["status"] == "employer_unclear" and why in out["reason"] and record.uploads == []


def test_the_result_shows_whom_the_letter_is_addressed_to(firm_seat: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out, _t = _wage(_Employed(), monkeypatch)
    assert out["employer_used"] == "Acme Freight Co\n100 Dock Rd\nPortville, CA 95001"


def test_the_reply_block_is_line_for_line_and_never_ready_when_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live 2026-10-06: a model-laid block arrived as one run-on paragraph headed
    # "Ready to send" with markers in it. The block is the tool's, and every
    # line is its own paragraph (the mail renderer joins adjacent lines).
    ready = _ledger(_Employed(), monkeypatch)["reply_block"]
    paras = ready.split("\n\n")
    assert paras[0] == "Ready to send from your email (the address is on the adjuster contact Pat Adjuster):"
    assert paras[1:4] == ["To: claims@carrier.example", "Subject: Claim CL-0001 | Medical Payment Ledger", "Hello,"]
    assert paras[-1] == "Kind regards," and all("\n" not in p for p in paras)
    waiting = _ledger(_Record(), monkeypatch)["reply_block"]
    assert "Ready to send" not in waiting
    assert waiting.startswith(
        "The med pay ledger email is waiting on the file: [Not in the file: 1st party insurer email]"
    )
