"""The connector half of the logic-in-code switches (ADR 0104).

What these defend. A merge reaches a client seat without a person, so on a seat
that authors nothing:

* the model sees the tools it saw before: same required arguments, same words, and
  the only new arguments are optional (``test_the_listed_surface``). What a model
  reads can change its behaviour with no skill changing. A skills PR that changes
  a listing updates ``_LISTED``, so that change is one a reviewer sees (this is the
  PR that did, with the skills that use the words);
* an old-style call does what it did: a figure not on its source is still written,
  a typed post record is still recorded as typed, and a row says what would have
  been refused;
* nothing costs a model read that did not before.

And with the switch at ``enforce``, read from a real file, the same calls refuse.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import layout_sections, logic_in_code, medicals_layout
from smokeball_connector import letter_pages as lp
from smokeball_connector import outcome_ledger as ol
from smokeball_connector import post_journal as pj
from smokeball_connector import vendor_invoice as vi
from smokeball_connector import vendor_invoice_tools as vit
from smokeball_connector.server import server
from test_layout_tools import DESIGN, _empty_tab_tenant, _use
from test_layout_tools import lt as layout_tools
from test_medicals_tools import MATTER, _add, _filed, _Tenant, tenant  # noqa: F401 - tenant is a pytest fixture used by name

SHA = "a" * 64

#: The listing as the skills read it, from ``tool_surface()``: required arguments
#: and the sha256 of the description the model reads. Each tool's arguments may
#: grow only by the optional names in ``added``.
_LISTED: dict[str, dict[str, Any]] = {
    "add_medicals_row": {
        "required": ["charge", "matter_id", "provider_name", "service_end", "service_start", "source_file_id"],
        "description_sha256": "01b462e6af8a24a5bf5c305fcb27560a0319c7b033e609c67b5a50ee734bbd02",
        "added": [],
    },
    "add_negotiation_rows": {
        "required": ["matter_id", "rows"],
        "description_sha256": "2aae756a95937810c6dc128bf25a5ff395ff28b9a9fde654b28e909b3950b63d",
        "added": ["source_file_id"],
    },
    "file_attachment_pages_to_matter": {
        "required": [
            "download_url",
            "file_name",
            "first_page",
            "last_page",
            "matter_id",
            "matter_resolution",
            "sha256",
        ],
        "description_sha256": "f2ec49d1d948a6a8be120e2bc9cea07fb2ff9ba29a5c6ffdf8dd64202fd39fa6",
        "added": ["message_id"],
    },
    "get_expenses": {
        "required": ["matter_id"],
        "description_sha256": "54bb6805c3574e8c8ed4e1b4f5b6c22a93e518e43bbe45e931a0838c892b3cbb",
        "added": [],
    },
    "get_matter_balances": {
        "required": ["bank_account_id"],
        "description_sha256": "ae84c6c8936d834f6c55133438255fc332ed0dd3e6560585d963c1beca2dba69",
        "added": [],
    },
    "record_post_batch": {
        "required": ["file_name", "letters", "message_id", "page_count", "sha256"],
        "description_sha256": "3448858f5f0ac11d4b5ee01b5feb8c1973bda953f2e72e68faaaf4011acb1a48",
        "added": [],
    },
    "resolve_invoice_matter": {
        "required": [],
        "description_sha256": "4e7f17550f5039219e6b00abb78dad13267782b8ceeebf700efb439b13d847fc",
        "added": ["date_of_birth_as_printed", "first_page", "last_page", "message_id", "sha256"],
    },
    "stage_vendor_invoice": {
        "required": [
            "amount",
            "download_url",
            "file_name",
            "invoice_date",
            "invoice_number",
            "matter_id",
            "matter_resolution",
            "sha256",
            "vendor",
        ],
        "description_sha256": "e8805a0f175a63b3b1d8adb522352598fc53d9a9703058682eb6918fb13a9fc3",
        "added": ["message_id"],
    },
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SMD_CUSTOMER_YAML", raising=False)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    lp.FILED_PAGES._filed.clear()
    lp.FILED_DOCS._docs.clear()


def _seat_enforces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, switch: str) -> None:
    """The switch as a seat authors it: in the file, read fresh."""
    path = tmp_path / "customer.yaml"
    path.write_text(f"logic_in_code:\n  {switch}: enforce\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _firm_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "layouts.yaml"
    path.write_text(f"smokeball_layouts:\n  settlement_negotiations_design: {DESIGN}\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _surface() -> dict[str, Any]:
    return {t.name: t for t in server.tool_surface()}


# ---- what the model reads ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(_LISTED))
def test_the_listed_surface_is_what_the_skills_were_written_against(name: str) -> None:
    tool, listed = _surface()[name], _LISTED[name]
    schema = tool.input_schema
    assert sorted(schema.get("required") or []) == listed["required"], "a new argument stays optional"
    assert hashlib.sha256((tool.description or "").encode()).hexdigest() == listed["description_sha256"], (
        "the model reads this; the words that tell it to fill a new argument ship with the skills"
    )
    assert not set(listed["added"]) & set(schema.get("required") or [])
    assert set(listed["added"]) <= set(schema.get("properties") or {})


# ---- the value forms: nothing accepted today reads differently --------------


@pytest.mark.parametrize("raw", ["25000", "25000.00", "1250.5", "1250.50", "1", " 25000 "])
def test_a_figure_in_todays_form_reads_as_it_did(raw: str) -> None:
    today = Decimal(raw.strip())
    assert medicals_layout.parse_charge(raw) == today
    assert vi.parse_amount(raw) == today
    row, problem = layout_sections.parse_row({"demand_amount": raw})
    assert problem is None and row == {"demand_amount": raw.strip()}


def test_a_value_that_cannot_be_read_is_named_in_the_refusal() -> None:
    assert "'abc' does not read as one positive figure" in str(layout_sections.parse_row({"demand_amount": "abc"})[1])
    assert "'abc' does not read as one date" in str(layout_sections.parse_row({"demand_date": "abc"})[1])


# ---- printed_source_check ----------------------------------------------------


def test_observe_writes_a_charge_the_bill_does_not_print_and_says_so(tenant: _Tenant) -> None:
    _filed()
    out = _add(charge="9999.99")
    assert out["status"] == "written", out
    (row,) = ol.observations()
    assert row["switch"] == logic_in_code.PRINTED_SOURCE_CHECK and row["tool"] == ol.MEDICALS
    assert row["status"] == ol.WOULD_REFUSE and "9999.99" in row["reason"]
    assert row["matter_id"] == MATTER


def test_enforce_from_the_seats_file_refuses_the_same_charge(
    tenant: _Tenant,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seat_enforces(monkeypatch, tmp_path, logic_in_code.PRINTED_SOURCE_CHECK)
    _filed()
    out = _add(charge="9999.99")
    assert out["status"] == "refused" and "9999.99" in out["reason"]
    assert tenant.writes() == []
    assert ol.observations() == []


def test_observe_a_bill_it_cannot_read_is_its_own_status_and_pays_for_nothing(
    tenant: _Tenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smokeball_connector import cited_facts

    _filed(text=None)
    seen: list[bool] = []

    def document_text(_client: Any, _matter: str, _file: str, *, allow_vision: bool) -> tuple[str, str]:
        seen.append(allow_vision)
        return "", "bill.pdf"

    monkeypatch.setattr(cited_facts, "_document_text", document_text)
    assert _add()["status"] == "written"
    assert seen == [False], "a check that only observes never costs a model read"
    (row,) = ol.observations()
    assert row["status"] == ol.SOURCE_UNREADABLE


def test_enforce_may_pay_to_read_the_bill(
    tenant: _Tenant,
    monkeypatch: pytest.MonkeyPatch,
    logic_enforced: None,
) -> None:
    from smokeball_connector import cited_facts

    _filed(text=None)
    seen: list[bool] = []

    def document_text(_client: Any, _matter: str, _file: str, *, allow_vision: bool) -> tuple[str, str]:
        seen.append(allow_vision)
        return "", "bill.pdf"

    monkeypatch.setattr(cited_facts, "_document_text", document_text)
    assert _add()["status"] == "refused"
    assert seen == [True]


def test_observe_an_operator_offer_with_no_document_is_written_as_today(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _firm_layout(monkeypatch, tmp_path)
    _use(monkeypatch, _empty_tab_tenant())
    out = layout_tools.add_negotiation_rows(
        MATTER, [{"offer_amount": "18500.00", "offer_date": "2026-10-27"}], operator_tab=True
    )
    assert out["status"] == "written", out
    (row,) = ol.observations()
    assert row["tool"] == ol.NEGOTIATION and "source_file_id" in row["reason"]


def test_enforce_refuses_that_offer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logic_enforced: None) -> None:
    _firm_layout(monkeypatch, tmp_path)
    _use(monkeypatch, _empty_tab_tenant())
    out = layout_tools.add_negotiation_rows(
        MATTER, [{"offer_amount": "18500.00", "offer_date": "2026-10-27"}], operator_tab=True
    )
    assert out["status"] == "refused" and "source_file_id" in out["reason"]


@pytest.mark.parametrize(
    ("text", "status", "enforced"),
    [
        ("Invoice\nTotal due: $1,250.00\n", None, None),
        ("Invoice\nTotal due: $9,999.00\n", ol.WOULD_REFUSE, "is not printed"),
        (None, ol.SOURCE_UNREADABLE, "could not be read again"),
    ],
)
def test_the_vendor_amount_check(
    monkeypatch: pytest.MonkeyPatch, text: str | None, status: str | None, enforced: str | None
) -> None:
    monkeypatch.setattr(vi, "_source_text", lambda *_a: text)
    where = ("m-1", "msg-1")
    assert vi._amount_gate(b"", "x.pdf", SHA, (2, 3), Decimal("1250.00"), where) is None, "observe never refuses"
    rows = ol.observations()
    assert [r["status"] for r in rows] == ([status] if status else [])
    if status:
        assert (rows[0]["message_id"], rows[0]["matter_id"], rows[0]["first_page"]) == ("msg-1", "m-1", 2)
    monkeypatch.setattr(
        logic_in_code, "load_switch", lambda key, path=None: logic_in_code.Switch(key, logic_in_code.ENFORCE)
    )
    refusal = vi._amount_gate(b"", "x.pdf", SHA, (2, 3), Decimal("1250.00"), where)
    assert (refusal is None) if enforced is None else (enforced in str(refusal))


# ---- post_record_derivation --------------------------------------------------


def _typed(**over: Any) -> dict[str, Any]:
    letter = {"first_page": 1, "last_page": 2, "sender": "Allstate", "kind": "letter", "placed_on": "2026-PI-1"}
    letter.update(over)
    return letter


def _record(letters: list[dict[str, Any]]) -> dict[str, Any]:
    return pj.record_post_batch("msg-1", "post.pdf", SHA, 2, letters)


def _batch() -> dict[str, Any]:
    (path,) = (pj.journal_dir() / "batches").glob("*.json")
    return json.loads(path.read_text("utf-8"))


def _filed_row(number: str, message: str = "msg-1") -> None:
    ol.append(ol.Outcome(ol.FILE, SHA, message, 1, 2, "m-1", number, "filed"))


def test_observe_an_old_style_record_is_recorded_as_typed_with_todays_result() -> None:
    _filed_row("2026-PI-9")
    out = _record([_typed()])
    assert set(out) == {"status", "recorded", "letters", "to_list", "daily_list", "filing_unverified"}
    assert out["status"] == "recorded"
    assert _batch()["letters"][0]["placed_on"] == "2026-PI-1", "the firm's list says what it said yesterday"
    (row,) = ol.observations()
    assert row["status"] == ol.DIFFERS and row["switch"] == logic_in_code.POST_RECORD_DERIVATION
    assert "2026-PI-1" in row["reason"] and "2026-PI-9" in row["reason"]


def test_observe_says_nothing_when_the_typed_record_is_right() -> None:
    _filed_row("2026-PI-1")
    assert _record([_typed()])["status"] == "recorded"
    assert ol.observations() == []


def test_observe_reads_a_filing_that_carried_no_message_id() -> None:
    """A skill that still types the fields does not pass one to the filing."""
    _filed_row("2026-PI-1", message="")
    assert _record([_typed()])["status"] == "recorded"
    assert ol.observations() == []


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"placed_on": "", "issue": ""}, "a filed letter carries placed_on"),
        ({"placed_on": "X", "issue": "no_match"}, "a letter that was not placed has no placed_on"),
        ({"placed_on": "", "issue": "ambiguous", "candidates": ["A"]}, "ambiguous names every candidate"),
        ({"placed_on": "", "issue": "not_attempted"}, "issue is one of"),
        ({"placed_on": "X", "candidates": ["A"]}, "a filed letter carries no candidates"),
    ],
)
def test_observe_keeps_every_check_a_typed_letter_always_had(over: dict[str, Any], why: str) -> None:
    out = _record([_typed(**over)])
    assert out["status"] == "refused" and why in out["reason"]


def test_observe_a_straddling_filing_is_recorded_not_refused() -> None:
    ol.append(ol.Outcome(ol.FILE, SHA, "msg-1", 1, 3, "m-1", "2026-PI-1", "filed"))
    out = pj.record_post_batch("msg-1", "post.pdf", SHA, 3, [_typed(), _typed(first_page=3, last_page=3)])
    assert out["status"] == "recorded"
    assert any(r["status"] == ol.WOULD_REFUSE and "filed as one document" in r["reason"] for r in ol.observations())


def test_observe_an_untyped_letter_is_derived() -> None:
    _filed_row("2026-PI-9")
    out = _record([{"first_page": 1, "last_page": 2, "sender": "Allstate", "kind": "letter"}])
    assert out["letters_recorded"][0]["placed_on"] == "2026-PI-9"
    assert _batch()["letters"][0]["placed_on"] == "2026-PI-9"


def test_enforce_from_the_seats_file_refuses_the_typed_field_naming_what_it_derives_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seat_enforces(monkeypatch, tmp_path, logic_in_code.POST_RECORD_DERIVATION)
    _filed_row("2026-PI-9")
    out = _record([_typed()])
    assert out["status"] == "refused" and "2026-PI-9" in out["reason"]
    assert not (pj.journal_dir() / "batches").exists()


def test_one_switch_does_not_throw_the_other(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seat_enforces(monkeypatch, tmp_path, logic_in_code.PRINTED_SOURCE_CHECK)
    _filed_row("2026-PI-1")
    assert _record([_typed()])["status"] == "recorded"


def test_observe_a_partial_group_still_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vit, "_client", lambda: object())
    monkeypatch.setattr(vit, "_resolve", lambda *_a, **_k: {"verdict": "none", "candidates": [], "ignored": []})
    out = vit.resolve_invoice_matter(client_name="Dana", sha256=SHA)
    assert out["verdict"] == "none"
    (row,) = ol.observations()
    assert row["tool"] == ol.RESOLVE and row["status"] == ol.WOULD_REFUSE


def test_enforce_a_partial_group_searches_nothing(monkeypatch: pytest.MonkeyPatch, logic_enforced: None) -> None:
    monkeypatch.setattr(vit, "_resolve", lambda *_a, **_k: pytest.fail("searched"))
    assert vit.resolve_invoice_matter(client_name="Dana", sha256=SHA)["verdict"] == "refused"


def test_an_unwritable_observation_never_fails_the_act(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    monkeypatch.setenv(ol.JOURNAL_DIR_ENV, str(tmp_path / "file"))
    assert ol.observe("s", "t", ol.WOULD_REFUSE, "r") is False
