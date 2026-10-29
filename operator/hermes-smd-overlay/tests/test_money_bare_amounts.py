"""A figure the firm's record stores as a bare number may be quoted in a reply.

WHY (2026-10-29, pilot-smokeball rehearsal). The fabrication gate lets a dollar
figure reach a reply only when it was READ this session from the firm's own
record (ss-console#2258). The register learned money only in ``$`` form, and
Smokeball never writes a ``$``: an expense row carries ``"price": 96.25`` and a
Medicals line ``"InitialInvoiceAmount": "1875.40"``. So vendor-invoice-intake
staged an expense, re-read it with ``get_expenses`` exactly as its skill says,
and the reply "staged as an expense of $96.25" was refused as untraceable.

Both directions are pinned, through the trust plugin's real post-hook:

* a bare amount under a money-named field in a TENANT-SOURCE read seeds;
* a figure absent from every read, a figure only in a WRITE tool's return, and
  a figure only in ``read_attachment_text`` (the vendor's PDF) still block.
"""

from __future__ import annotations

import json

import pytest

from shared import provenance
from shared.outbound_gate import evaluate
from tests.conftest import load_plugin

_LAW = "law-firm"

# What get_expenses returns after the stage: the HATEOAS envelope with the
# row the connector staged (price x quantity 1), the matter reference attached.
_EXPENSES = {
    "value": [
        {
            "id": "exp-1",
            "matterNumber": "2026-PI-102",
            "subject": "Valley Records invoice 4471",
            "expenseDate": "2026-10-20",
            "quantity": 1,
            "price": 96.25,
            "finalized": False,
        }
    ],
    "deletedExcluded": 0,
}

# get_matter_layouts(section="Medicals"): Smokeball returns values as strings.
_MEDICALS = {
    "status": "ok",
    "items": [
        {
            "section": "Medicals",
            "fields": {
                "Providers[0]/Provider/DisplayName": "Valley Imaging Center",
                "Providers[0]/Invoices[0]/InitialInvoiceAmount": "1875.40",
                "Providers[0]/InvoiceBalance": "1875.40",
                "Providers[0]/ServiceStartDate": "2026-08-14",
            },
        }
    ],
}


@pytest.fixture
def trust():
    plugin = load_plugin("hermes-smd-trust")
    provenance._reset_for_tests()
    yield plugin
    provenance._reset_for_tests()


def _read(plugin, session: str, tool: str, result: str) -> None:
    plugin.on_post_tool_call(tool_name=tool, result=result, session_id=session, tool_call_id="t")


def _gate(session: str, body: str):
    return evaluate(body, None, _LAW, allowed_money=provenance.register_for(session).money())


def _seed_unrelated(plugin, session: str) -> None:
    """One unrelated tenant read, so no test rides an empty register."""
    _read(
        plugin,
        session,
        "mcp_smokeball_list_matters",
        json.dumps({"value": [{"matterNumber": "2026-PI-101"}]}),
    )


# --------------------------------------------------------------------------- #
# Direction one: the firm's record, read back, licenses its own figure
# --------------------------------------------------------------------------- #


def test_a_staged_expense_read_back_licenses_its_amount(trust) -> None:
    """The refused 19:08:15 reply, end to end."""
    session = "s-bare-expense"
    _read(trust, session, "mcp_smokeball_get_expenses", json.dumps(_EXPENSES))
    decision = _gate(
        session,
        "Staged: Valley Records invoice 4471, $96.25 per the entry staged on matter 2026-PI-102.",
    )
    assert decision.allowed, decision.reason


def test_the_dispatcher_envelope_seen_on_seats_is_peeled(trust) -> None:
    """On a seat the post-hook result is ``{"result": "<the connector's JSON>"}``
    (live-caught 2026-08-11). A walker that parsed only the outer object would
    find nothing and the fix would pass here and fail on the pilot."""
    session = "s-bare-envelope"
    _read(
        trust, session, "mcp_smokeball_get_expenses", json.dumps({"result": json.dumps(_EXPENSES)})
    )
    assert _gate(session, "Staged as an expense of $96.25, unfinalized.").allowed


def test_a_medicals_row_read_back_licenses_its_charge(trust) -> None:
    session = "s-bare-medicals"
    _read(trust, session, "mcp_smokeball_get_matter_layouts", json.dumps(_MEDICALS))
    decision = _gate(session, "Medicals tab row added, $1,875.40 for service 2026-08-14.")
    assert decision.allowed, decision.reason


# --------------------------------------------------------------------------- #
# Direction two: everything that is not a read of the firm's record still blocks
# --------------------------------------------------------------------------- #


def test_a_figure_in_no_read_still_blocks_and_is_named(trust) -> None:
    session = "s-bare-invented"
    _read(trust, session, "mcp_smokeball_get_expenses", json.dumps(_EXPENSES))
    decision = _gate(session, "Staged as an expense of $145.00, unfinalized.")
    assert not decision.allowed
    assert "$145.00" in decision.reason


def test_a_figure_only_in_a_write_result_still_blocks(trust) -> None:
    """The write's own return never seeds: it is the Operator's act described
    back to it, not the firm's record read."""
    session = "s-bare-write"
    _seed_unrelated(trust, session)
    _read(
        trust,
        session,
        "mcp_smokeball_stage_vendor_invoice",
        json.dumps({"status": "staged", "expense_id": "exp-9", "amount": "145.00", "price": 145.0}),
    )
    _read(
        trust,
        session,
        "mcp_smokeball_add_medicals_row",
        json.dumps({"status": "written", "charge": "1875.40"}),
    )
    decision = _gate(session, "Staged as an expense of $145.00. Medicals tab row added, $1,875.40.")
    assert not decision.allowed
    assert "$145.00" in decision.reason and "$1,875.40" in decision.reason


def test_a_figure_only_in_the_vendors_pdf_still_blocks(trust) -> None:
    """``read_attachment_text`` reads the vendor's invoice, which is not the
    firm's record, even when the text is JSON carrying a money-named field."""
    session = "s-bare-pdf"
    _seed_unrelated(trust, session)
    _read(
        trust,
        session,
        "mcp_smokeball_read_attachment_text",
        json.dumps(
            {"text": "INVOICE 4471  Amount due 96.25", "amount": "96.25", "total_price": 96.25}
        ),
    )
    decision = _gate(session, "Staged as an expense of $96.25, unfinalized.")
    assert not decision.allowed
    assert "$96.25" in decision.reason


# --------------------------------------------------------------------------- #
# The field rule itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"quantity": 96.25},  # not a money-named key
        {"id": 9625, "count": 96.25, "page": 96},
        {"price": "96.255"},  # not a plain amount
        {"price": "about 96.25"},
        {"price": True},
        {"price": "-96.25"},
    ],
)
def test_only_plain_amounts_under_money_named_keys_register(payload) -> None:
    assert provenance.money_field_figures(json.dumps(payload)) == []


def test_prose_registers_nothing_new() -> None:
    assert provenance.money_field_figures("price 96.25 on the invoice") == []
