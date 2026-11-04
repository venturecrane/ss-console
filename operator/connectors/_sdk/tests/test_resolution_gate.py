"""The resolution gate: declared once at registration, enforced on the MCP wrapper.

Every test drives a ConnectorServer through the real MCP client over the
in-memory transport, so each proves what the model sees and what reached the
tool body, not what a wrapper returns to a Python caller. The gate here is a
stand-in with the same contract a connector supplies (claim takes a token out
of circulation, restore puts it back, refusals carry a code); the Smokeball
gate over the real token store is tested in that connector's suite.
"""

from __future__ import annotations

from decimal import Decimal

import anyio
import pytest
from mcp.client import Client
from mcp.client._memory import InMemoryTransport
from operator_connector_sdk import conformance
from operator_connector_sdk.resolution import CODE_NO_MATTER, PARAM, ResolutionGate, matter_arg
from operator_connector_sdk.server import ConnectorServer

M1 = "matter-one"
M2 = "matter-two"

#: What ``add_charge`` received. This module postpones its annotations, as the
#: connectors' tool modules do, and ``Decimal`` is imported HERE only: if the
#: gate evaluated the signature in the SDK's globals, registration would raise.
CALLS: list[dict] = []


def future_add_charge(matter_id: str, amount: Decimal, note: str | None = None) -> dict:
    """Add one charge to a matter."""
    CALLS.append({"matter_id": matter_id, "amount": amount, "note": note})
    return {"status": "written", "matter_id": matter_id}


class Refused(Exception):
    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code


class FakeGate:
    """A token store with the connector contract. ``observed`` is what an
    observe-mode refusal recorded."""

    def __init__(self, mode: str = "enforce") -> None:
        self.live: dict[str, str] = {}
        self.observed: list[tuple[str, str | None, str]] = []
        self.mode_value = mode

    def mint(self, matter_id: str) -> str:
        token = f"resolution:{len(self.live) + 1:032x}"
        self.live[token] = matter_id
        return token

    def claim(self, token: object, matter_id: str) -> object:
        if not isinstance(token, str) or not token:
            raise Refused("missing", "no matter resolution: resolve the matter first (nothing was created)")
        bound = self.live.get(token)
        if bound is None:
            raise Refused("unknown", "that matter resolution is unknown: it was already used (nothing was created)")
        if bound != matter_id:
            raise Refused("bound_elsewhere", "that matter resolution was issued for a different matter")
        del self.live[token]
        return (token, bound)

    def restore(self, handle: object) -> None:
        token, bound = handle  # type: ignore[misc]
        self.live[token] = bound

    def gate(self) -> ResolutionGate:
        return ResolutionGate(
            claim=self.claim,
            restore=self.restore,
            refusal=Refused,
            mode=lambda: self.mode_value,
            observe=lambda tool, matter, code: self.observed.append((tool, matter, code)),
            missing_matter=lambda arg: f"no {arg} was given, so no matter resolution can open this write",
        )


def _server(fake: FakeGate | None, written: list[dict], *, raises: bool = False) -> ConnectorServer:
    srv = ConnectorServer("c", resolvers=("resolve_matter_by_id",))

    @srv.tool(requires_resolution=matter_arg())
    def create_task(matter_id: str, subject: str) -> dict:
        """Create one task on a matter."""
        if raises:
            raise RuntimeError("the tenant refused the write")
        written.append({"matter_id": matter_id, "subject": subject})
        return {"status": "created", "matter_id": matter_id}

    @srv.tool()
    def update_task(task_id: str, subject: str) -> dict:
        """Update a task; its matter is the task's own."""
        written.append({"task_id": task_id})
        return {"status": "updated"}

    if fake is not None:
        srv.install_resolution_gate(fake.gate())
    return srv


def _call(server: ConnectorServer, name: str, args: dict):
    async def go():
        async with Client(InMemoryTransport(server._mcp)) as client:
            return await client.call_tool(name, args)

    return anyio.run(go)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _schema(server: ConnectorServer, name: str) -> dict:
    return next(t for t in server.tool_surface() if t.name == name).input_schema


# ---- The contract the model reads -------------------------------------------


def test_the_schema_lists_matter_resolution_as_a_required_string() -> None:
    srv = _server(FakeGate(), [])
    schema = _schema(srv, "create_task")
    assert schema["properties"][PARAM]["type"] == "string"
    assert "default" not in schema["properties"][PARAM]
    assert PARAM in schema["required"]
    assert {"matter_id", "subject"} <= set(schema["required"])
    # An ungated tool is untouched.
    assert PARAM not in _schema(srv, "update_task")["properties"]


def test_the_description_names_the_resolver_in_one_appended_sentence() -> None:
    srv = _server(FakeGate(), [])
    tool = next(t for t in srv.tool_surface() if t.name == "create_task")
    assert tool.description.startswith("Create one task on a matter.")
    assert tool.description.rstrip().endswith("without it nothing is written.")
    assert "resolve_matter_by_id" in tool.description


def test_conformance_holds_every_gated_tool_to_a_required_param() -> None:
    srv = _server(FakeGate(), [])
    assert conformance.check_resolution_schemas(srv) == {"create_task"}


def test_conformance_fails_a_gated_tool_whose_schema_lets_the_param_look_optional(monkeypatch) -> None:
    from operator_connector_sdk import server as server_mod

    monkeypatch.setattr(server_mod, "_mark_required", lambda schema: None)
    with pytest.raises(AssertionError, match="not in inputSchema.required"):
        conformance.check_resolution_schemas(_server(FakeGate(), []))


def test_a_postponed_annotation_module_registers_and_its_types_survive() -> None:
    """The tool modules use ``from __future__ import annotations``; the gate's
    signature must be evaluated in the tool's own globals."""
    fake = FakeGate()
    srv = ConnectorServer("c")
    srv.tool(name="add_charge", requires_resolution=matter_arg())(future_add_charge)
    srv.install_resolution_gate(fake.gate())
    schema = _schema(srv, "add_charge")
    assert PARAM in schema["required"] and "amount" in schema["properties"]
    CALLS.clear()
    result = _call(srv, "add_charge", {"matter_id": M1, "amount": "12.50", PARAM: fake.mint(M1)})
    assert result.is_error is False
    assert str(CALLS[0]["amount"]) == "12.50"


def test_a_tool_that_checks_its_own_resolution_cannot_also_declare_one() -> None:
    srv = ConnectorServer("c")

    def stage(matter_id: str, matter_resolution: str) -> dict:
        return {}

    with pytest.raises(ValueError, match="already takes matter_resolution"):
        srv.tool(requires_resolution=matter_arg())(stage)


def test_python_callers_keep_the_plain_function() -> None:
    """The runner and the tests call the decorated name directly; the gate lives
    on the MCP wrapper only, so a code caller needs no token."""
    srv = ConnectorServer("c")

    def create_event(matter_id: str) -> str:
        return matter_id

    assert srv.tool(requires_resolution=matter_arg())(create_event) is create_event
    assert create_event(M1) == M1


# ---- Enforce -----------------------------------------------------------------


def test_enforce_refuses_without_a_token_and_the_body_never_runs() -> None:
    written: list[dict] = []
    result = _call(_server(FakeGate(), written), "create_task", {"matter_id": M1, "subject": "s"})
    assert result.is_error is True
    assert "ResolutionRefused: no matter resolution" in _text(result)
    assert written == []


def test_enforce_writes_once_on_a_live_token_and_pops_it_from_the_body() -> None:
    """The body's signature has no matter_resolution: if the wrapper did not pop
    it, the call would fail with an unexpected keyword."""
    fake, written = FakeGate(), []
    srv = _server(fake, written)
    token = fake.mint(M1)
    first = _call(srv, "create_task", {"matter_id": M1, "subject": "s", PARAM: token})
    assert first.is_error is False and written == [{"matter_id": M1, "subject": "s"}]
    second = _call(srv, "create_task", {"matter_id": M1, "subject": "s", PARAM: token})
    assert second.is_error is True and "already used" in _text(second)
    assert len(written) == 1


def test_enforce_refuses_a_token_bound_to_another_matter() -> None:
    fake, written = FakeGate(), []
    srv = _server(fake, written)
    result = _call(srv, "create_task", {"matter_id": M2, "subject": "s", PARAM: fake.mint(M1)})
    assert result.is_error is True and "different matter" in _text(result)
    assert written == []


def test_a_body_that_raises_leaves_the_token_spendable() -> None:
    """Spent by the write, not at the door: a failed write is corrected and
    retried on the same resolution."""
    fake = FakeGate()
    token = fake.mint(M1)
    failing = _server(fake, [], raises=True)
    assert _call(failing, "create_task", {"matter_id": M1, "subject": "s", PARAM: token}).is_error is True
    assert fake.live == {token: M1}
    written: list[dict] = []
    ok = _call(_server(fake, written), "create_task", {"matter_id": M1, "subject": "s", PARAM: token})
    assert ok.is_error is False and len(written) == 1


def test_enforce_names_the_missing_matter_argument() -> None:
    fake, written = FakeGate(), []
    result = _call(_server(fake, written), "create_task", {"matter_id": "  ", "subject": "s", PARAM: fake.mint(M1)})
    assert result.is_error is True
    assert "no matter_id was given" in _text(result)
    assert written == []


def test_no_gate_installed_refuses_in_every_mode() -> None:
    written: list[dict] = []
    result = _call(_server(None, written), "create_task", {"matter_id": M1, "subject": "s", PARAM: "x"})
    assert result.is_error is True and "no resolution gate installed" in _text(result)
    assert written == []


@pytest.mark.parametrize("mode", ["", "Observe", "off", None])
def test_an_unreadable_mode_enforces(mode) -> None:
    fake, written = FakeGate(mode=mode), []
    result = _call(_server(fake, written), "create_task", {"matter_id": M1, "subject": "s"})
    assert result.is_error is True and written == [] and fake.observed == []


def test_the_ungated_tool_needs_nothing() -> None:
    written: list[dict] = []
    result = _call(_server(FakeGate(), written), "update_task", {"task_id": "t1", "subject": "s"})
    assert result.is_error is False and written == [{"task_id": "t1"}]


# ---- Observe -----------------------------------------------------------------


def test_observe_records_each_would_refuse_reason_and_dispatches() -> None:
    fake, written = FakeGate(mode="observe"), []
    srv = _server(fake, written)
    other = fake.mint(M2)
    _call(srv, "create_task", {"matter_id": M1, "subject": "a"})
    _call(srv, "create_task", {"matter_id": M1, "subject": "b", PARAM: other})
    _call(srv, "create_task", {"matter_id": "", "subject": "c", PARAM: other})
    assert [w["subject"] for w in written] == ["a", "b", "c"]
    assert fake.observed == [
        ("create_task", M1, "missing"),
        ("create_task", M1, "bound_elsewhere"),
        ("create_task", None, CODE_NO_MATTER),
    ]
    # An observed refusal spends nothing.
    assert fake.live == {other: M2}


def test_observe_spends_a_live_token_exactly_as_enforce_would() -> None:
    """Observe counts what enforce would refuse, so a token reused in observe
    mode must show up as a refusal the second time."""
    fake, written = FakeGate(mode="observe"), []
    srv = _server(fake, written)
    token = fake.mint(M1)
    _call(srv, "create_task", {"matter_id": M1, "subject": "a", PARAM: token})
    _call(srv, "create_task", {"matter_id": M1, "subject": "b", PARAM: token})
    assert len(written) == 2
    assert fake.observed == [("create_task", M1, "unknown")]


def test_an_observation_that_cannot_be_recorded_never_blocks_the_write() -> None:
    fake, written = FakeGate(mode="observe"), []
    srv = _server(fake, written)

    def broken(*_a):
        raise OSError("broker down")

    gate = fake.gate()
    srv.install_resolution_gate(
        ResolutionGate(gate.claim, gate.restore, gate.refusal, gate.mode, broken, gate.missing_matter)
    )
    result = _call(srv, "create_task", {"matter_id": M1, "subject": "s"})
    assert result.is_error is False and len(written) == 1


# ---- Async tools -------------------------------------------------------------


def test_an_async_tool_is_gated_the_same_way() -> None:
    fake = FakeGate()
    srv = ConnectorServer("c")
    seen: list[str] = []

    @srv.tool(requires_resolution=matter_arg("target"))
    async def render(target: str) -> dict:
        seen.append(target)
        return {"ok": True}

    srv.install_resolution_gate(fake.gate())
    assert _call(srv, "render", {"target": M1}).is_error is True
    assert "no matter resolution" in _text(_call(srv, "render", {"target": M1}))
    assert _call(srv, "render", {"target": M1, PARAM: fake.mint(M1)}).is_error is False
    assert seen == [M1]
