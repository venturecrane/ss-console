"""The form catalog moved from code to the seat's config (ADR 0100 W4) without
changing what A&P resolves.

Until W4 ``form_letters.FORMS`` was a hardcoded catalog of A&P's toolbar forms,
offered on every Smokeball seat. It is now ``form_letters.forms`` in the seat's
customer.yaml. ``HARDCODED`` below is that dict, verbatim from the last commit
that carried it (origin/main 768dda627, form_letters.py), and is the golden the
config must reproduce, field for field.

The A&P edit itself is the lead's (Law 2): this suite never writes A&P's file.
It reads A&P's CURRENT customer.yaml, adds ``fixtures/form_catalog.yaml`` as
``form_letters.forms``, and proves the result resolves exactly ``HARDCODED``;
and that A&P's file today authors either no catalog (before the edit) or this
one (after it), never a different one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from form_catalog import CATALOG, PATH
from smokeball_connector import form_letters as fl

AP_YAML = Path(__file__).resolve().parents[3] / "customers" / "ashton-price" / "customer.yaml"

HARDCODED: dict[str, fl.FormSpec] = {
    "first_party_rep": fl.FormSpec(
        label="1st party rep letter",
        document_class="first_party_rep_letter",
        default_template="Form - 1st Party Rep Letter.docx",
        file_name="1st party letter.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="VIA FAX: ",
        email_label="Email: ",
        paragraph_fields=frozenset({"delivery_lines"}),
        signer_name_from="authored",
    ),
    "third_party_rep": fl.FormSpec(
        label="3rd party rep letter",
        document_class="third_party_rep_letter",
        default_template="Form - 3rd Party Rep Letter.docx",
        file_name="3rd Party Letter.docx",
        side="Defendants",
        client_side=False,
        fax_label="Via Fax: ",
        email_label="Via Email: ",
        paragraph_fields=frozenset({"delivery_lines", "carrier_address"}),
        signer_name_from="staff",
    ),
    "health_blue_shield": fl.FormSpec(
        label="health insurer notice (Blue Shield)",
        document_class="health_notice_blue_shield",
        default_template="Form - Med Ins Blue Shield.docx",
        file_name="Med Ins Req. Blue Shield.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="health",
    ),
    "dec_page_fax": fl.FormSpec(
        label="dec page request (fax cover)",
        document_class="dec_page_fax",
        default_template="Form - Fax Dec Page.docx",
        file_name="Fax Cover Sheet - Req Dec Page.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="fax",
    ),
    "med_pay_fax": fl.FormSpec(
        label="med pay request (fax cover)",
        document_class="med_pay_fax",
        default_template="Form - Fax Medpay.docx",
        file_name="Fax Cover Sheet Medpay.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset(),
        signer_name_from="preparer",
        facts_kind="fax",
    ),
    "wage_loss": fl.FormSpec(
        label="wage loss letter",
        document_class="wage_loss_letter",
        default_template="Form - Wage Loss Letter.docx",
        file_name="Wage Loss Letter.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset({"employer_address"}),
        signer_name_from="preparer",
        facts_kind="wage",
    ),
}


def _ap_with_catalog(tmp_path: Path) -> Path:
    doc = yaml.safe_load(AP_YAML.read_text(encoding="utf-8"))
    assert isinstance(doc.get("form_letters"), dict), "A&P authors a form_letters block (signers, firm, agencies)"
    doc["form_letters"]["forms"] = yaml.safe_load(PATH.read_text(encoding="utf-8"))["forms"]
    out = tmp_path / "customer.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return out


def test_ap_plus_the_catalog_resolves_the_hardcoded_forms(tmp_path: Path) -> None:
    resolved = fl.load_forms(str(_ap_with_catalog(tmp_path)))
    assert list(resolved) == list(HARDCODED)  # the order the refusal lists them in, too
    assert resolved == HARDCODED


def test_the_rest_of_aps_form_letters_block_is_untouched(tmp_path: Path) -> None:
    from smokeball_connector import form_letter_facts as facts

    before = facts.load_signers(str(AP_YAML))
    assert facts.load_signers(str(_ap_with_catalog(tmp_path))) == before


def test_aps_file_authors_no_catalog_yet_or_exactly_this_one() -> None:
    """Holds before the lead's A&P edit (no catalog) and after it (this one)."""
    assert fl.load_forms(str(AP_YAML)) in ({}, HARDCODED)


def test_the_suite_catalog_is_the_hardcoded_one() -> None:
    assert CATALOG == HARDCODED


def test_an_unauthored_catalog_offers_no_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("form_letters:\n  signers: {}\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    out = fl.render_firm_form_letter("m-1", "first_party_rep")
    assert out["status"] == "refused"
    assert out["reason"] == "form must be one of ['med_pay_ledger_email', 'police_report']"


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda e: e.pop("file_name"), "missing ['file_name']"),
        (lambda e: e.update(fax_lable=""), "unknown ['fax_lable']"),
        (lambda e: e.update(signer_name_from="anyone"), "signer_name_from must be one of"),
        (lambda e: e.update(facts_kind="police"), "facts_kind must be one of"),
        (lambda e: e.update(client_side="yes"), "client_side must be true or false"),
        (lambda e: e.update(paragraph_fields="delivery_lines"), "paragraph_fields must be a list"),
        (lambda e: e.update(label=" "), "label must be a non-empty string"),
    ],
)
def test_a_malformed_catalog_files_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: object, why: str
) -> None:
    forms = yaml.safe_load(PATH.read_text(encoding="utf-8"))["forms"]
    mutate(forms["first_party_rep"])  # type: ignore[operator]
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump({"form_letters": {"forms": forms}}), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    with pytest.raises(fl.CatalogError, match=why.replace("[", r"\[").replace("]", r"\]")):
        fl.load_forms()
    out = fl.render_firm_form_letter("m-1", "first_party_rep")
    assert out["status"] == "refused" and "form catalog could not be read" in out["reason"]
    assert out["fileId"] is None
