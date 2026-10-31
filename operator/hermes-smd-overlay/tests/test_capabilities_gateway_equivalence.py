"""Equivalence gate for ADR 0100 Wave 1: what each REAL seat's gateway loads.

Every seat under ``operator/customers/`` is registered against its own authored
customer.yaml and compared with what it loaded before tools were gated on
authorship. Before, every seat loaded everything; so "today" is computed, not
hard-coded, as the full set each plugin defines. The seats that author
everything must see no change at all; the seats that author nothing must see
none of the gated surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import webhook_gate as gate
from bootstrap.translate import _send_posture_soul_section
from shared import capabilities
from tests.conftest import FakePluginContext, load_plugin

CUSTOMERS = Path(__file__).resolve().parents[2] / "customers"
SEATS = sorted(
    p.parent.name for p in CUSTOMERS.glob("*/customer.yaml") if not p.parent.name.startswith("_")
)
CASEWORK = {"casework_finish", "casework_brief", "reply_verdicts", "casework_step_done"}
NO_LANE_SEATS = {"captain-mark", "scott", "brokery", "ashton-price-marketing"}


def _medchron_today() -> set[str]:
    """Every tool the plugin defines: what register() offered every seat before."""
    m = load_plugin("hermes-smd-medchron")
    return set(
        {
            **m.TOOLS,
            **m.demand.TOOLS,
            **m.drafting.TOOLS,
            **m.litigation.TOOLS,
            **m.negotiation.TOOLS,
            **m.note.TOOLS,
            **m.casefile.TOOLS,
        }
    )


def _registered(plugin_name: str) -> set[str]:
    ctx = FakePluginContext()
    load_plugin(plugin_name).register(ctx)
    return set(ctx.tools)


def _destructive_line(doc: dict, *, as_today: bool) -> bool:
    """Whether any persona's SOUL renders the destructive act line. ``as_today``
    renders as before the gate: as if the seat authored Smokeball."""
    customer = dict(doc)
    if as_today:
        customer["connectors"] = {"smokeball": {"backend": "mcp:smokeball"}}
    return any(
        "mcp_smokeball_delete_events" in _send_posture_soul_section(p, customer)
        for p in doc.get("personas") or []
        if isinstance(p, dict)
    )


@pytest.fixture
def seat(monkeypatch):
    def use(name: str) -> dict:
        path = CUSTOMERS / name / "customer.yaml"
        monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
        doc = capabilities.read(path)
        assert doc is not None, name
        return doc

    return use


def test_the_real_seats_are_present():
    assert {"ashton-price", "pilot-smokeball"} | NO_LANE_SEATS <= set(SEATS), SEATS


@pytest.mark.parametrize("name", SEATS)
def test_each_seat_loads_exactly_its_authored_surface(seat, name):
    doc = seat(name)
    lanes = capabilities.authored_lanes(doc)
    smokeball = capabilities.authors_connector(doc, "smokeball")
    medchron = load_plugin("hermes-smd-medchron")
    expected = set()
    for lane in lanes:
        expected |= set(medchron.LANE_TOOLS[lane])
    assert _registered("hermes-smd-medchron") == expected
    escalation = _registered("hermes-smd-escalation")
    assert (CASEWORK <= escalation) is smokeball
    assert not (CASEWORK & escalation) or smokeball
    # The drafting lane's exit stays registered on every seat: it authorizes
    # writes to non-Smokeball seams too (local_file, session_output).
    assert _registered("hermes-smd-drafting") == {"smd_deliver_draft"}
    assert gate._route_served("smokeball") is smokeball
    assert _destructive_line(doc, as_today=False) is (
        _destructive_line(doc, as_today=True) and smokeball
    )


def test_ashton_price_loads_exactly_what_it_loads_today(seat):
    doc = seat("ashton-price")
    assert capabilities.authored_lanes(doc) == list(capabilities.JOB_LANES)
    assert _registered("hermes-smd-medchron") == _medchron_today()
    assert _registered("hermes-smd-escalation") == set(load_plugin("hermes-smd-escalation").TOOLS)
    assert gate._route_served("smokeball") is True
    assert _destructive_line(doc, as_today=False) == _destructive_line(doc, as_today=True)


def test_pilot_smokeball_drops_only_its_unauthored_lanes(seat):
    doc = seat("pilot-smokeball")
    medchron = load_plugin("hermes-smd-medchron")
    dropped = _medchron_today() - _registered("hermes-smd-medchron")
    unauthored = set(capabilities.JOB_LANES) - set(capabilities.authored_lanes(doc))
    assert unauthored == {"drafting", "litigation", "negotiation"}
    assert dropped == set().union(*(set(medchron.LANE_TOOLS[lane]) for lane in unauthored))
    assert _registered("hermes-smd-escalation") == set(load_plugin("hermes-smd-escalation").TOOLS)
    assert gate._route_served("smokeball") is True
    # pilot authors destructive: confirm and Smokeball: the line is unchanged.
    assert _destructive_line(doc, as_today=False) is True


@pytest.mark.parametrize("name", sorted(NO_LANE_SEATS))
def test_seats_that_author_nothing_load_none_of_the_gated_surface(seat, name):
    doc = seat(name)
    assert capabilities.authored_lanes(doc) == []
    assert _registered("hermes-smd-medchron") == set()
    assert not (CASEWORK & _registered("hermes-smd-escalation"))
    assert gate._route_served("smokeball") is False
    assert _destructive_line(doc, as_today=False) is False
