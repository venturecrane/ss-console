"""The gate as the connector actually serves it: the real ``server`` object,
the real registrations, the real token store and mode reader.

``test_conformance`` pins WHICH tools are gated. This proves what a gated
registration does on the served surface in enforce mode, through the MCP
client the gateway uses: a matter write with no resolution never reaches the
tenant, the resolution ``resolve_matter_by_id`` mints opens exactly one write
on that matter, and a firm-general task (no matter named) needs none.
The tenant is an httpx.MockTransport; no live calls.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client import Client
from mcp.client._memory import InMemoryTransport

from smokeball_connector import matter_by_id as mbi
from smokeball_connector import resolution_token as rt
from smokeball_connector import server as srv_mod
from smokeball_connector.client import SmokeballClient

M1 = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"
M2 = "a1b2c3d4-0000-4000-8000-00000000d107"
NUMBER = "2026-PI-101"
STAFF = "5b1c0a3e-1111-4222-8333-444455556666"


class Tenant:
    """Matters by id; every POST /tasks is kept and read back as written."""

    def __init__(self) -> None:
        self.matters = {M1: {"id": M1, "number": NUMBER}, M2: {"id": M2, "number": "2026-PI-107"}}
        self.posted: list[dict[str, Any]] = []

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and parts == ["tasks"]:
            body = json.loads(request.content)
            self.posted.append(body)
            return httpx.Response(200, json={"id": f"task-{len(self.posted)}"})
        if request.method == "GET" and len(parts) == 2 and parts[0] == "tasks":
            index = int(parts[1].split("-")[1]) - 1
            return httpx.Response(200, json={"id": parts[1], **self.posted[index]})
        if request.method == "GET" and len(parts) == 2 and parts[0] == "matters" and parts[1] in self.matters:
            return httpx.Response(200, json=self.matters[parts[1]])
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Tenant:
    rt._reset_for_tests()
    seat = tmp_path / "customer.yaml"
    seat.write_text(
        "connectors:\n  PracticeManagement:\n    backend: mcp:smokeball\n    matter_resolution_mode: enforce\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(seat))
    fake = Tenant()
    client = fake.client()
    monkeypatch.setattr(srv_mod, "_get_client", lambda: client)
    return fake


def _call(name: str, args: dict[str, Any]):
    async def go():
        async with Client(InMemoryTransport(srv_mod.server._mcp)) as client:
            return await client.call_tool(name, args)

    return anyio.run(go)


def _text(result: Any) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def test_a_task_on_a_matter_with_no_resolution_never_reaches_the_tenant(tenant: Tenant) -> None:
    result = _call("create_task", {"staff_id": STAFF, "subject": "Call the adjuster", "matter_id": M1})
    assert result.is_error is True
    assert "ResolutionRefused" in _text(result)
    assert tenant.posted == []


def test_a_by_id_resolution_opens_one_task_on_that_matter_only(tenant: Tenant) -> None:
    token = mbi.resolve_matter_by_id(tenant.client(), matter_id=M1, matter_number=NUMBER)["matter_resolution"]
    elsewhere = _call(
        "create_task", {"staff_id": STAFF, "subject": "Call", "matter_id": M2, "matter_resolution": token}
    )
    assert elsewhere.is_error is True and tenant.posted == []
    ok = _call("create_task", {"staff_id": STAFF, "subject": "Call", "matter_id": M1, "matter_resolution": token})
    assert ok.is_error is False
    assert [p["matterId"] for p in tenant.posted] == [M1]
    again = _call("create_task", {"staff_id": STAFF, "subject": "Call", "matter_id": M1, "matter_resolution": token})
    assert again.is_error is True and len(tenant.posted) == 1


def test_a_firm_general_task_names_no_matter_and_needs_no_resolution(tenant: Tenant) -> None:
    result = _call("create_task", {"staff_id": STAFF, "subject": "Office supplies"})
    assert result.is_error is False
    assert len(tenant.posted) == 1 and "matterId" not in tenant.posted[0]


def _as_a_seat_with_no_key_reads_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """customer.yaml with the Smokeball connector and no matter_resolution_mode."""
    seat = tmp_path / "customer.yaml"
    seat.write_text("connectors:\n  PracticeManagement:\n    backend: mcp:smokeball\n", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(seat))


def _filler(prop: dict[str, Any]) -> Any:
    kinds = {p.get("type") for p in prop.get("anyOf", [prop])}
    if "string" in kinds:
        return "x"
    if "integer" in kinds or "number" in kinds:
        return 1
    if "boolean" in kinds:
        return True
    return [] if "array" in kinds else {}


def _old_style_call(tool: Any) -> dict[str, Any]:
    """The call a model on today's skills makes: every required argument, this matter,
    and no matter_resolution, because nothing has told it about one."""
    schema = tool.input_schema
    args = {k: _filler(schema["properties"][k]) for k in schema.get("required", []) if k != "matter_resolution"}
    args["matter_id"] = M1
    return args


def _served_tools() -> list[Any]:
    return [t for t in srv_mod.server.tool_surface() if t.name in srv_mod.server.gated_tools()]


@pytest.mark.parametrize("tool", sorted(srv_mod.server.gated_tools()))
def test_an_old_style_call_dispatches_on_a_seat_that_authors_no_mode(
    tool: str, tenant: Tenant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The release that declares the gate changes nothing on a seat: no token, the body
    runs and the outcome is the plain function's own, and one would_refuse row says so."""
    _as_a_seat_with_no_key_reads_it(monkeypatch, tmp_path)
    rows: list[tuple[str, str | None, str]] = []
    live_gate = srv_mod.server._gate
    monkeypatch.setattr(srv_mod.server, "_gate", replace(live_gate, observe=lambda *row: rows.append(row)))
    served = next(t for t in _served_tools() if t.name == tool)
    args = _old_style_call(served)

    result = _call(tool, args)

    assert "ResolutionRefused" not in _text(result)
    assert "validation error" not in _text(result).lower(), _text(result)
    assert rows == [(tool, M1, "missing")]
    registered = srv_mod.server._mcp._tool_manager.get_tool(tool).fn
    try:
        inspect.unwrap(registered)(**args)
    except Exception as exc:  # noqa: BLE001 - the plain function's own failure is the comparison
        assert result.is_error is True
        assert f"{type(exc).__name__}: {exc}" in _text(result), _text(result)
    else:
        assert result.is_error is False, _text(result)


def test_every_gated_listing_on_an_observing_seat_asks_nothing_of_the_model(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _as_a_seat_with_no_key_reads_it(monkeypatch, tmp_path)
    tools = _served_tools()
    assert {t.name for t in tools} == set(srv_mod.server.gated_tools())
    for t in tools:
        assert "matter_resolution" not in t.input_schema.get("required", []), t.name
        assert "without it nothing is written" not in (t.description or ""), t.name
        assert "matter_resolution is required" not in (t.description or ""), t.name


def test_every_gated_listing_on_an_enforcing_seat_states_the_contract(tenant: Tenant) -> None:
    for t in _served_tools():
        assert "matter_resolution" in t.input_schema["required"], t.name
        assert "matter_resolution is required" in (t.description or ""), t.name


def _seat_with_fence(tmp_path: Path, mode: str | None) -> Path:
    seat = tmp_path / "fence-seat.yaml"
    block = f"logic_in_code:\n  conflict_fence: {mode}\n" if mode else ""
    seat.write_text(f"connectors:\n  PracticeManagement:\n    backend: mcp:smokeball\n{block}", encoding="utf-8")
    return seat


CHECK_ARGS = {"sender_name": "Victor Hale", "sender_email": "vhale@example.com", "subject_line": "Re: settlement"}


@pytest.mark.parametrize(
    ("mode", "listed"),
    [(None, False), ("observe", False), ("enforce", True), ("not-a-mode", True)],
)
def test_conflict_check_is_listed_only_where_the_fence_enforces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str | None, listed: bool
) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(_seat_with_fence(tmp_path, mode)))
    assert ("conflict_check" in {t.name for t in srv_mod.server.tool_surface()}) is listed


@pytest.mark.parametrize("mode", [None, "observe"])
def test_a_direct_conflict_check_on_an_observing_seat_is_refused_and_touches_nothing(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str | None
) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(_seat_with_fence(tmp_path, mode)))
    seen: list[str] = []
    client = tenant.client()
    client._http = httpx.Client(
        transport=httpx.MockTransport(lambda request: (seen.append(str(request.url)), tenant.handle(request))[1])
    )
    monkeypatch.setattr(srv_mod, "_get_client", lambda: client)
    result = _call("conflict_check", CHECK_ARGS)
    assert result.is_error is True
    assert "ToolNotAvailable" in _text(result)
    assert seen == [] and tenant.posted == []


def test_a_direct_conflict_check_on_an_enforcing_seat_runs(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(_seat_with_fence(tmp_path, "enforce")))
    result = _call("conflict_check", CHECK_ARGS)
    assert "ToolNotAvailable" not in _text(result)
    assert '"verdict"' in _text(result)


def test_the_conflict_check_description_states_no_rule_of_its_own() -> None:
    """What a verdict means for the turn is the skill's and the fence's to say."""
    text = next(t.description for t in srv_mod.server.enforced_surface() if t.name == "conflict_check")
    assert "only when a skill's written step names" in text
    for claim in ("STOP", "nothing is written", "may be written", "before anything", "send nothing"):
        assert claim not in text, claim


#: The listing on main (regenerate after a deliberate listing change, with what
#: ``_listing()`` below returns, printed as this JSON, in the PR that changes it).
MAIN_SURFACE = Path(__file__).parent / "fixtures" / "main_tool_surface.json"


def _listing() -> dict[str, dict[str, Any]]:
    return {
        t.name: {
            "required": sorted(t.input_schema.get("required", [])),
            "properties": sorted(t.input_schema.get("properties", {})),
            "description_sha256": hashlib.sha256((t.description or "").encode()).hexdigest(),
        }
        for t in srv_mod.server.tool_surface()
    }


def test_an_observing_seats_surface_is_mains_tool_for_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The menu a seat that authors nothing sees: the same tools, the same required
    arguments and the same words as main. The one addition is the optional
    matter_resolution on a gated tool, which a skill may pass and nothing asks for."""
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(_seat_with_fence(tmp_path, None)))
    main = json.loads(MAIN_SURFACE.read_text(encoding="utf-8"))
    here = _listing()
    assert set(here) == set(main)
    for name, listed in main.items():
        assert here[name]["required"] == listed["required"], name
        assert here[name]["description_sha256"] == listed["description_sha256"], name
        grew = set(here[name]["properties"]) - set(listed["properties"])
        assert set(listed["properties"]) <= set(here[name]["properties"]), name
        assert grew == ({"matter_resolution"} if name in srv_mod.server.gated_tools() else set()), name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("create_memo", {"matter_id": M1, "text": "A memo"}),
        ("create_folder", {"matter_id": M1, "name": "Correspondence"}),
        ("add_workbook", {"matter_id": M1, "file_name": "Costs.xlsx", "sheets": [{"name": "Costs", "rows": []}]}),
        ("render_funding_case_eval", {"matter_id": M1}),
    ],
)
def test_every_strict_matter_write_refuses_without_a_resolution(tenant: Tenant, tool: str, args: dict) -> None:
    result = _call(tool, args)
    assert result.is_error is True
    assert "ResolutionRefused" in _text(result)
    assert tenant.posted == []
