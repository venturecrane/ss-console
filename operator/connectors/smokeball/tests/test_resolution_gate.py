"""The Smokeball half of the matter-resolution gate.

Four pieces, each tested against the real module rather than a stand-in:

  * ``resolve_matter_by_id``: the id and the number the model read must name one
    live matter, or nothing is minted, and a mismatch never teaches the model
    the number that would open the id it had;
  * the token store's ``claim`` / ``restore``: one resolution opens one write,
    even when two calls race, and a write that raised hands it back;
  * the mode switch read off the seat's customer.yaml: absent is observe, a
    value outside the two enforces;
  * the gate wired end to end: a ConnectorServer with ``resolution_gate``
    installed, the real token store, the real mode reader, and the real
    observation writer pointed at a fake broker socket.

The tenant is an httpx.MockTransport; no live calls.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client import Client
from mcp.client._memory import InMemoryTransport
from operator_connector_sdk import conformance, matter_arg
from operator_connector_sdk.server import ConnectorServer

from smokeball_connector import matter_by_id as mbi
from smokeball_connector import resolution_gate
from smokeball_connector import resolution_mode as rm
from smokeball_connector import resolution_token as rt
from smokeball_connector import write_record as wr
from smokeball_connector.client import SmokeballClient

M1 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M2 = "a1b2c3d4-0000-4000-8000-00000000d107"
NUMBER = "2026-PI-101"
OTHER_NUMBER = "2026-PI-107"


@pytest.fixture(autouse=True)
def _empty_store() -> None:
    rt._reset_for_tests()


class Tenant:
    """``matters`` maps an id to its record; ``status`` forces one HTTP status."""

    def __init__(self, status: int | None = None) -> None:
        self.matters = {
            M1: {"id": M1, "number": NUMBER, "title": "Motor Vehicle Accident"},
            M2: {"id": M2, "number": OTHER_NUMBER, "title": "Premises"},
        }
        self.status = status
        self.paths: list[str] = []

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.paths.append(request.url.path)
        if self.status is not None:
            return httpx.Response(self.status, text="boom")
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "matters" and parts[1] in self.matters:
            return httpx.Response(200, json=self.matters[parts[1]])
        return httpx.Response(404, json={"error": "not found"})


def _by_id(tenant: Tenant, matter_id: Any, matter_number: Any) -> dict[str, Any]:
    return mbi.resolve_matter_by_id(tenant.client(), matter_id=matter_id, matter_number=matter_number)


# ---- resolve_matter_by_id -------------------------------------------------------


def test_the_id_and_number_of_one_live_matter_mint_a_resolution_for_it() -> None:
    out = _by_id(Tenant(), M1, NUMBER)
    assert out["verdict"] == "unique"
    assert out["matter_id"] == M1 and out["matter_number"] == NUMBER
    assert out["matched_on"] == ["matter_id", "matter_number"]
    assert rt.verify(out["matter_resolution"], M1).matched_on == ("matter_id", "matter_number")
    # The same shape resolve_invoice_matter's unique verdict carries.
    assert {"searched", "ignored", "verdict", "matter_id", "matter_number", "matched_on", "matter_resolution"} <= set(
        out
    )


def test_number_spacing_and_case_do_not_matter_as_in_the_number_search() -> None:
    assert _by_id(Tenant(), M1, " 2026-pi-101 ")["verdict"] == "unique"


def test_an_id_carried_from_another_matter_mints_nothing_and_never_names_the_live_number() -> None:
    """The defect this read exists for: the right number, the wrong id. The
    refusal must not hand back the number that would open the id it had."""
    out = _by_id(Tenant(), M2, NUMBER)
    assert out["verdict"] == "none"
    assert "matter_resolution" not in out
    assert OTHER_NUMBER not in json.dumps(out)
    assert rt._LIVE == {}


def test_no_matter_at_that_id_is_none() -> None:
    out = _by_id(Tenant(), "00000000-0000-4000-8000-000000000000", NUMBER)
    assert out["verdict"] == "none" and rt._LIVE == {}


@pytest.mark.parametrize("status", [500, 503, 403])
def test_a_read_that_fails_is_search_failed_never_an_answer(status: int) -> None:
    out = _by_id(Tenant(status=status), M1, NUMBER)
    assert out["verdict"] == "search_failed"
    assert rt._LIVE == {}


@pytest.mark.parametrize(
    ("matter_id", "matter_number"),
    [
        ("", NUMBER),
        (M1, ""),
        (M1, None),
        (None, NUMBER),
        ("2026-PI-101", NUMBER),  # a number where the id goes
        (f"{M1}/layouts", NUMBER),  # a path, not an id
        (7, NUMBER),
    ],
)
def test_anything_but_an_id_and_a_number_is_refused_before_any_request(matter_id: Any, matter_number: Any) -> None:
    tenant = Tenant()
    out = _by_id(tenant, matter_id, matter_number)
    assert out["verdict"] == "none"
    assert tenant.paths == []
    assert rt._LIVE == {}


def test_a_record_that_answers_for_a_different_id_mints_nothing() -> None:
    tenant = Tenant()
    tenant.matters[M1] = {"id": M2, "number": NUMBER}
    assert _by_id(tenant, M1, NUMBER)["verdict"] == "none"
    assert rt._LIVE == {}


def test_the_read_is_on_the_surface_and_its_schema_takes_both_facts() -> None:
    from smokeball_connector.server import server

    tool = next(t for t in server.tool_surface() if t.name == "resolve_matter_by_id")
    assert set(tool.input_schema["required"]) == {"matter_id", "matter_number"}
    assert "matter_resolution" not in tool.input_schema["properties"]


# ---- The token store: claim and restore --------------------------------------


@pytest.mark.parametrize(
    ("token", "code"),
    [
        (None, rt.CODE_MISSING),
        ("", rt.CODE_MISSING),
        ("unique", rt.CODE_MISSING),
        ("resolution:nothex", rt.CODE_MALFORMED),
        (f"resolution:{'a' * 32}", rt.CODE_UNKNOWN),
    ],
)
def test_every_refusal_carries_its_code(token: Any, code: str) -> None:
    with pytest.raises(rt.ResolutionRefused) as raised:
        rt.claim(token, M1)
    assert raised.value.code == code
    assert code in rt.CODES


def test_a_token_bound_elsewhere_and_an_expired_one_are_told_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    token = rt.mint(M1, NUMBER, ("matter_id", "matter_number"))
    with pytest.raises(rt.ResolutionRefused) as elsewhere:
        rt.claim(token, M2)
    assert elsewhere.value.code == rt.CODE_BOUND_ELSEWHERE
    monkeypatch.setattr(rt, "TTL_SECONDS", -1.0)
    with pytest.raises(rt.ResolutionRefused) as expired:
        rt.claim(token, M1)
    assert expired.value.code == rt.CODE_EXPIRED


def test_a_claimed_token_opens_nothing_else_until_it_is_restored() -> None:
    token = rt.mint(M1, NUMBER, ("matter_id", "matter_number"))
    held = rt.claim(token, M1)
    with pytest.raises(rt.ResolutionRefused) as again:
        rt.claim(token, M1)
    assert again.value.code == rt.CODE_UNKNOWN
    rt.restore(held)
    assert rt.claim(token, M1).resolution.matter_id == M1


def test_a_restore_never_extends_a_resolution_past_its_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    token = rt.mint(M1, NUMBER, ("matter_id", "matter_number"))
    held = rt.claim(token, M1)
    rt.restore(held)
    monkeypatch.setattr(rt, "TTL_SECONDS", -1.0)
    with pytest.raises(rt.ResolutionRefused) as raised:
        rt.claim(token, M1)
    assert raised.value.code == rt.CODE_EXPIRED


def test_two_racing_claims_on_one_token_admit_exactly_one() -> None:
    token = rt.mint(M1, NUMBER, ("matter_id", "matter_number"))
    start = threading.Barrier(8)
    won: list[object] = []
    lost: list[str] = []

    def race() -> None:
        start.wait()
        try:
            won.append(rt.claim(token, M1))
        except rt.ResolutionRefused as exc:
            lost.append(exc.code)

    threads = [threading.Thread(target=race) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and lost == [rt.CODE_UNKNOWN] * 7


# ---- The mode switch ------------------------------------------------------------


def _yaml(tmp_path: Path, body: str) -> str:
    path = tmp_path / "customer.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


_SMOKEBALL = "connectors:\n  PracticeManagement:\n    adapter: smokeball\n    backend: mcp:smokeball\n"


def test_absent_everywhere_is_observe(tmp_path: Path) -> None:
    assert rm.load_resolution_mode(str(tmp_path / "missing.yaml")).mode == "observe"
    assert rm.load_resolution_mode(_yaml(tmp_path, _SMOKEBALL)).mode == "observe"
    assert rm.load_resolution_mode(_yaml(tmp_path, "customer_id: x\n")).mode == "observe"


@pytest.mark.parametrize("value", ["observe", "enforce"])
def test_the_authored_value_is_the_mode(tmp_path: Path, value: str) -> None:
    out = rm.load_resolution_mode(_yaml(tmp_path, f"{_SMOKEBALL}    matter_resolution_mode: {value}\n"))
    assert out.mode == value and out.error is None


@pytest.mark.parametrize("value", ["off", "Observe", "true", "''"])
def test_a_value_outside_the_two_enforces_and_says_why(tmp_path: Path, value: str) -> None:
    out = rm.load_resolution_mode(_yaml(tmp_path, f"{_SMOKEBALL}    matter_resolution_mode: {value}\n"))
    assert out.mode == "enforce" and out.error


def test_an_unparseable_file_enforces(tmp_path: Path) -> None:
    assert rm.load_resolution_mode(_yaml(tmp_path, "connectors: [unclosed\n")).mode == "enforce"


def test_the_key_on_another_connector_is_not_this_connector_s_switch(tmp_path: Path) -> None:
    body = "connectors:\n  Email:\n    backend: mcp:agentmail\n    matter_resolution_mode: enforce\n"
    assert rm.load_resolution_mode(_yaml(tmp_path, body)).mode == "observe"


# ---- The gate, end to end ------------------------------------------------------


class _Ledger:
    """Stands in for the broker's socket: what ``record_resolution_observation`` sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rows: list[dict[str, Any]] = []
        monkeypatch.setattr(wr, "socket_path", lambda: "/run/smd-workspace-broker/broker.sock")
        monkeypatch.setattr(wr, "resolve_actor", lambda: "operator")
        monkeypatch.setattr(wr, "_send", lambda _path, row: self.rows.append(row))


def _gated_server(written: list[dict[str, Any]], *, raises: bool = False) -> ConnectorServer:
    srv = ConnectorServer("smokeball", resolvers=rt.RESOLVER_TOOLS)

    @srv.tool(requires_resolution=matter_arg())
    def create_task(matter_id: str, subject: str) -> dict[str, Any]:
        """Create a task."""
        if raises:
            raise RuntimeError("the tenant refused the task")
        written.append({"matter_id": matter_id, "subject": subject})
        return {"status": "created"}

    resolution_gate.install(srv)
    return srv


def _call(server: ConnectorServer, args: dict[str, Any]):
    async def go():
        async with Client(InMemoryTransport(server._mcp)) as client:
            return await client.call_tool("create_task", args)

    return anyio.run(go)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _seat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str | None) -> None:
    body = _SMOKEBALL + (f"    matter_resolution_mode: {mode}\n" if mode else "")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", _yaml(tmp_path, body))


def test_enforce_refuses_a_bare_matter_id_with_the_token_s_sentence(monkeypatch, tmp_path: Path) -> None:
    _seat(monkeypatch, tmp_path, "enforce")
    ledger, written = _Ledger(monkeypatch), []
    result = _call(_gated_server(written), {"matter_id": M1, "subject": "s"})
    assert result.is_error is True
    assert "ResolutionRefused: no matter resolution: call resolve_matter_by_id or resolve_invoice_matter" in _text(
        result
    )
    assert written == [] and ledger.rows == []


def test_enforce_writes_on_a_by_id_resolution_once_and_refuses_it_for_another_matter(monkeypatch, tmp_path) -> None:
    _seat(monkeypatch, tmp_path, "enforce")
    written: list[dict[str, Any]] = []
    srv = _gated_server(written)
    token = _by_id(Tenant(), M1, NUMBER)["matter_resolution"]
    elsewhere = _call(srv, {"matter_id": M2, "subject": "s", "matter_resolution": token})
    assert elsewhere.is_error is True and "different matter" in _text(elsewhere)
    assert _call(srv, {"matter_id": M1, "subject": "s", "matter_resolution": token}).is_error is False
    again = _call(srv, {"matter_id": M1, "subject": "s", "matter_resolution": token})
    assert again.is_error is True and "already used" in _text(again)
    assert written == [{"matter_id": M1, "subject": "s"}]


def test_a_write_that_raised_hands_the_resolution_back(monkeypatch, tmp_path: Path) -> None:
    _seat(monkeypatch, tmp_path, "enforce")
    token = _by_id(Tenant(), M1, NUMBER)["matter_resolution"]
    assert _call(_gated_server([], raises=True), {"matter_id": M1, "subject": "s", "matter_resolution": token}).is_error
    written: list[dict[str, Any]] = []
    assert (
        _call(_gated_server(written), {"matter_id": M1, "subject": "s", "matter_resolution": token}).is_error is False
    )
    assert len(written) == 1


def test_observe_is_the_default_and_records_a_would_refuse_row_naming_no_argument_text(monkeypatch, tmp_path) -> None:
    _seat(monkeypatch, tmp_path, None)
    ledger, written = _Ledger(monkeypatch), []
    result = _call(_gated_server(written), {"matter_id": M1, "subject": "privileged words"})
    assert result.is_error is False and len(written) == 1
    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row["action_type"] == "SMOKEBALL_WRITE" and row["phase"] == "would_refuse"
    assert (row["tool"], row["matter_id"], row["reason"], row["actor"]) == ("create_task", M1, "missing", "operator")
    assert "privileged words" not in json.dumps(row)


def test_observe_records_nothing_for_a_call_enforce_would_admit(monkeypatch, tmp_path: Path) -> None:
    _seat(monkeypatch, tmp_path, "observe")
    ledger, written = _Ledger(monkeypatch), []
    token = _by_id(Tenant(), M1, NUMBER)["matter_resolution"]
    assert (
        _call(_gated_server(written), {"matter_id": M1, "subject": "s", "matter_resolution": token}).is_error is False
    )
    assert ledger.rows == [] and len(written) == 1


def test_off_a_seat_an_observation_is_not_recorded_and_the_write_proceeds(monkeypatch, tmp_path) -> None:
    _seat(monkeypatch, tmp_path, "observe")
    monkeypatch.setattr(wr, "socket_path", lambda: "")
    sent: list[Any] = []
    monkeypatch.setattr(wr, "_send", lambda *a: sent.append(a))
    written: list[dict[str, Any]] = []
    assert _call(_gated_server(written), {"matter_id": M1, "subject": "s"}).is_error is False
    assert sent == [] and len(written) == 1


# ---- The connector's own server -------------------------------------------------


def test_the_connector_server_has_its_gate_and_names_the_resolvers_it_serves() -> None:
    from smokeball_connector.server import server

    assert server._gate is not None
    assert server._resolvers == rt.RESOLVER_TOOLS
    live = {t.name for t in server.tool_surface()}
    assert set(rt.RESOLVER_TOOLS) <= live
    conformance.check_resolution_schemas(server)
