"""Search egress (ADR 0099): what may leave the seat in a web query.

Layer 1 (structural): on a client-data seat, a session that read client
material cannot search. Layer 2 (screen, every seat): a query carrying an
identifier shape, a read token, a matter number, a party email or a client-side
party name is refused. The refusal names kinds only; the audit row carries the
query's length and the verdict, never the text.

Driven through the REAL trust gate (``evaluate_tool_call``) and the trust
plugin's real ``on_post_tool_call`` seeding, with only the seat's connectors
stubbed.
"""

from __future__ import annotations

import json

import pytest

from shared import matter_binding, provenance, search_egress
from shared.inbound import SESSION_TAINT
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce

SID = "sess-egress"
CLIENT_ID = "c-client-1"
INSURER_ID = "c-insurer-1"


def _contact(cid: str, first: str, last: str, roles: list[str]) -> dict:
    return {
        "id": cid,
        "person": {"firstName": first, "lastName": last},
        "tags": [{"type": "Role", "name": r} for r in roles],
    }


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    search_egress._reset_for_tests()
    provenance._reset_for_tests()
    matter_binding._reset_for_tests()
    SESSION_TAINT._tainted.clear()
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: "agent")
    monkeypatch.setattr(enforce, "_resolve_persona_exposure", lambda slug="": {})
    monkeypatch.setattr(enforce, "_resolve_vertical_floors", lambda: {})
    yield
    search_egress._reset_for_tests()
    provenance._reset_for_tests()
    matter_binding._reset_for_tests()


@pytest.fixture
def client_seat(monkeypatch):
    monkeypatch.setattr(search_egress, "seat_has_client_data_connector", lambda: True)


@pytest.fixture
def plain_seat(monkeypatch):
    monkeypatch.setattr(search_egress, "seat_has_client_data_connector", lambda: False)


def _search(tool: str = "web_search", call_id: str = "tc-1", **args) -> dict | None:
    if not args:
        args = {"query": "personal injury marketing trends 2026"}
    return enforce.evaluate_tool_call(tool, args, "seat", session_id=SID, tool_call_id=call_id)


def _read(tool: str, result: object, args: dict | None = None) -> None:
    trust.on_post_tool_call(
        tool_name=tool, args=args or {}, result=json.dumps(result), session_id=SID
    )


# ---------------------------------------------------------------------------
# Layer 1
# ---------------------------------------------------------------------------


def test_a_clean_session_searches(client_seat):
    assert _search() is None


def test_after_a_matter_read_the_client_seat_refuses_every_search_tool(client_seat):
    _read(
        "mcp_smokeball_get_matter",
        {"id": "m-1", "number": "2026-PI-101", "clientIds": [CLIENT_ID]},
    )
    for tool, args in (
        ("web_search", {"query": "weather in Phoenix"}),
        ("web_extract", {"urls": ["https://example.com/news"]}),
        ("web_research", {"question": "weather in Phoenix", "mode": "web"}),
    ):
        result = _search(tool, **args)
        assert result is not None and result["action"] == "block", tool
        assert "read client material" in result["message"]


def test_a_provenance_seeded_session_refuses_on_a_client_seat(client_seat):
    provenance.record_read(SID, "Docket 2026-PI-101 hearing on 2026-11-03")
    result = _search()
    assert result is not None and "read client material" in result["message"]


def test_layer_one_is_inert_on_a_seat_without_client_data(plain_seat):
    provenance.record_read(SID, "Docket 2026-PI-101 hearing on 2026-11-03")
    matter_binding.membership_for(SID).add("m-1", ["a@b.example"], complete=True)
    assert _search() is None


def test_client_data_seat_detection(monkeypatch):
    class Cfg:
        def __init__(self, connectors):
            self.connectors = connectors

    from shared import customer_config

    cases = [
        ({"PracticeManagement": {"backend": "mcp:smokeball"}}, True),
        ({"PracticeManagement": {"backend": "synthetic:no_pm"}}, False),
        (
            {"WebSearch": {"backend": "native:brave-free"}, "Email": {"backend": "mcp:agentmail"}},
            False,
        ),
        ({"PracticeManagement": {"backend": "mcp:smokeball", "enabled": False}}, False),
    ]
    for connectors, expected in cases:
        monkeypatch.setattr(
            customer_config.CustomerConfig,
            "from_volume",
            classmethod(lambda cls, c=connectors: Cfg(c)),
        )
        assert search_egress.seat_has_client_data_connector() is expected, connectors

    def boom(cls):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(customer_config.CustomerConfig, "from_volume", classmethod(boom))
    assert search_egress.seat_has_client_data_connector() is True


# ---------------------------------------------------------------------------
# Layer 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "status of 2026-PI-101",
        "SSN 123-45-6789 lookup",
        "A123456789 immigration",
        "receipt EAC2190012345",
        "No. 24-12345 ruling",
    ],
)
def test_identifier_shapes_are_refused_on_every_seat(plain_seat, query):
    result = _search(query=query)
    assert result is not None and result["action"] == "block"
    assert search_egress.KIND_IDENTIFIER in result["message"]
    for token in query.split():
        if any(ch.isdigit() for ch in token):
            assert token not in result["message"]


def test_a_read_bare_matter_number_is_refused(plain_seat):
    provenance.record_records(SID, [{"matterNumber": "201537", "dates": []}])
    result = _search(query="verdicts like 201537 in Arizona")
    assert result is not None and search_egress.KIND_REGISTER in result["message"]
    assert "201537" not in result["message"]


def test_a_read_date_alone_is_not_refused(plain_seat):
    provenance.record_read(SID, "Hearing 2026-11-03")
    assert _search(query="news from 2026-11-03") is None


def test_a_bound_matter_number_and_party_email_are_refused(plain_seat):
    m = matter_binding.membership_for(SID)
    m.add("m-9", ["jane.party@mail.example"], complete=False)
    m.add_alias("PI-7781", "m-9")
    r1 = _search(query="pi-7781 settlement")
    assert r1 is not None and search_egress.KIND_MATTER in r1["message"]
    r2 = _search(query="jane.party@mail.example reviews")
    assert r2 is not None and search_egress.KIND_PARTY_EMAIL in r2["message"]
    assert "jane.party" not in r2["message"]


def test_a_client_name_from_a_contact_role_is_refused(plain_seat):
    _read("mcp_smokeball_get_contact", _contact("c-9", "Rosalind", "Okonkwo", ["Plaintiff"]))
    for q in ("Rosalind Okonkwo accident", "okonkwo verdict", "OKONKWO news"):
        result = _search(query=q)
        assert result is not None and search_egress.KIND_PARTY_NAME in result["message"], q
        assert "Okonkwo" not in result["message"]


def test_a_client_named_by_the_matter_is_refused_in_either_read_order(plain_seat):
    _read("mcp_smokeball_get_contact", _contact(CLIENT_ID, "Teodoro", "Villanueva", []))
    assert _search(query="Villanueva") is None
    _read("mcp_smokeball_get_matter", {"id": "m-2", "clientIds": [CLIENT_ID]})
    result = _search(query="Villanueva crash report")
    assert result is not None and search_egress.KIND_PARTY_NAME in result["message"]


def test_matter_parties_side_client_marks_the_contact(plain_seat):
    _read(
        "mcp_smokeball_get_matter",
        {"id": "m-3", "parties": [{"contact_id": "c-77", "side": "client", "roles": []}]},
    )
    _read("mcp_smokeball_get_contact", _contact("c-77", "Marisol", "Etxeberria", []))
    assert _search(query="Etxeberria") is not None


def test_an_insurer_name_passes(plain_seat):
    _read(
        "mcp_smokeball_get_contact",
        {
            "id": INSURER_ID,
            "company": {"name": "Pinnacle Mutual"},
            "tags": [{"type": "Role", "name": "Insurer"}],
        },
    )
    _read(
        "mcp_smokeball_get_contact",
        _contact("c-adj", "Gregory", "Hollister", ["Client's Insurance Adjuster"]),
    )
    assert _search(query="Pinnacle Mutual claims practices") is None
    assert _search(query="Hollister adjuster reviews") is None


def test_a_firm_attorney_name_passes(plain_seat):
    _read("mcp_smokeball_get_contact", _contact("c-att", "Beatrice", "Lindqvist", ["Attorney"]))
    _read(
        "mcp_smokeball_search_staff",
        [{"id": "s-1", "firstName": "Beatrice", "lastName": "Lindqvist"}],
    )
    assert _search(query="Beatrice Lindqvist speaking engagement") is None


def test_names_are_whole_words_and_at_least_four_characters(plain_seat):
    _read("mcp_smokeball_get_contact", _contact("c-1", "Al", "Li", ["Client"]))
    _read("mcp_smokeball_get_contact", _contact("c-2", "Dana", "Ward", ["Client"]))
    assert _search(query="Li Al") is None
    assert _search(query="Edwards forward planning") is None
    assert _search(query="Ward") is not None


def test_names_are_not_seeded_from_non_smokeball_reads(plain_seat):
    trust.on_post_tool_call(
        tool_name="workspace_gmail_get",
        args={},
        result=json.dumps(_contact("c-x", "Philippa", "Arbuthnot", ["Client"])),
        session_id=SID,
    )
    assert _search(query="Arbuthnot") is None


def test_web_extract_screens_the_url_query_string(plain_seat):
    result = _search("web_extract", urls=["https://example.com/search?case=2026-PI-101"])
    assert result is not None and search_egress.KIND_IDENTIFIER in result["message"]
    assert _search("web_extract", urls=["https://example.com/blog/marketing"]) is None


def test_web_research_screens_question_and_channel(plain_seat):
    _read("mcp_smokeball_get_contact", _contact("c-1", "Wilhelmina", "Castellanos", ["Claimant"]))
    result = _search("web_research", question="Castellanos case update", mode="web")
    assert result is not None
    assert _search("web_research", question="local radio trends", mode="web") is None


# ---------------------------------------------------------------------------
# Audit: length and verdict only
# ---------------------------------------------------------------------------


def test_audit_fields_carry_length_and_verdict_only(plain_seat):
    q = "status of 2026-PI-101"
    _search(query=q, call_id="tc-refused")
    fields = search_egress.audit_fields("web_search", "tc-refused")
    assert fields == {"web_query_len": len(q), "web_query_verdict": "refused_screen"}
    _search(query="marketing trends", call_id="tc-pass")
    assert search_egress.audit_fields("web_search", "tc-pass") == {
        "web_query_len": len("marketing trends"),
        "web_query_verdict": "pass",
    }
    assert search_egress.audit_fields("web_search", "tc-pass") == {}
    assert search_egress.audit_fields("read_file", "tc-x") == {}


def test_structural_verdict_recorded(client_seat):
    provenance.record_read(SID, "Docket 2026-PI-101")
    _search(query="weather", call_id="tc-s")
    assert search_egress.audit_fields("web_search", "tc-s")["web_query_verdict"] == (
        "refused_structural"
    )


def test_the_audit_row_carries_no_query_text(plain_seat, monkeypatch):
    audit = load_plugin("hermes-smd-audit")
    emit = audit.emit
    q = "status of 2026-PI-101 for Okonkwo"
    _search(query=q, call_id="tc-row")
    rows: list = []

    class Writer:
        def write(self, event):
            rows.append(event)
            return "id"

    emit.emit_tool_event(
        Writer(),
        customer="seat",
        tool_name="web_search",
        args={"query": q},
        result="blocked",
        task_id="t",
        session_id=SID,
        tool_call_id="tc-row",
        duration_ms=1,
    )
    [event] = rows
    meta = event.metadata
    assert meta["web_query_len"] == len(q)
    assert meta["web_query_verdict"] == "refused_screen"
    blob = json.dumps(meta)
    assert "Okonkwo" not in blob and "2026-PI-101" not in blob


def test_non_search_tools_are_untouched(plain_seat):
    provenance.record_read(SID, "Docket 2026-PI-101")
    assert search_egress.evaluate(SID, "read_file", {"path": "2026-PI-101"}).verdict == "pass"
