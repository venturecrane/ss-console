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
from smokeball_connector import cited_facts, extract, form_letters as fl, sr1_form, vision
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
