"""Fixtures for the real-dispatch harness (ss W3, the ``overlay-e2e`` CI job).

Everything here runs the PINNED Hermes, not a fake of it: Hermes' own plugin
loader discovers the overlay from a translated seat profile, Hermes' own
``AIAgent`` runs the turn, Hermes' own hook dispatcher fires the overlay's
hooks, and Hermes' own tool registry dispatches the call. Only the edges are
stubbed: the model (a local Anthropic Messages server, or the patched OpenAI
client on the chat-completions parametrization) and the capability broker (a
Unix socket that records what reached it).

The tree is skipped when ``HERMES_AGENT_SRC`` is unset, so a laptop without a
Hermes checkout still runs ``pytest tests_e2e`` cleanly; under ``CI`` an unset
``HERMES_AGENT_SRC`` is a usage error, because a skipped harness would report
green having exercised nothing.

Process-wide by necessity: Hermes' plugin manager and tool registry are
process globals, so the seat is session-scoped and built once. Tests that need
a clean slate read "what happened since" through the recorders' ``mark()``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests_e2e._broker import StubBroker
from tests_e2e._model_stub import AnthropicMessagesStub, FakeOpenAIClient, ModelScript
from tests_e2e._seat import (
    OVERLAY_PLUGIN,
    PERSONA_SLUG,
    SEAT_SLUG,
    SHADOW_PLUGIN,
    build_seat_home,
    install_network_guard,
    scrub_environment,
)

HERMES_SRC = os.environ.get("HERMES_AGENT_SRC", "")

#: The model a seat's template authors; the stub answers whatever is asked.
MODEL = "claude-sonnet-4-6"

#: The fake credential the model stub is called with. Never a real key: the
#: environment scrub removes any real one before this is set.
_STUB_API_KEY = "sk-ant-e2e-harness-not-a-key"

#: The overlay's path-valued env knobs (``contracts/consumes.yaml``) that
#: default under ``/opt/data`` on a seat, each pointed into the temp seat.
_STATE_PATHS: dict[str, str] = {
    "SMD_ADMIN_POSSESSION_DB_PATH": "admin_possession.db",
    "SMD_ATTACHMENT_SPOOL_DIR": "attachment_spool",
    "SMD_CASEWORK_LEDGER_PATH": "casework.db",
    "SMD_CONNECTOR_LEDGER_PATH": "connector_ledger.db",
    "SMD_ESCALATION_LEDGER_PATH": "escalation.db",
    "SMD_EXPOSURE_OVERRIDE_DB_PATH": "exposure_override.db",
    "SMD_GATE_CAP_DB_PATH": "gate_cap.db",
    "SMD_HELD_REPLY_DB_PATH": "held_reply.db",
    "SMD_MCP_STORE_DIR": "mcp_store",
    "SMD_MSGRAPH_STATE_PATH": "msgraph_state.json",
    "SMD_REQUEST_CARDS_DB_PATH": "smd-gate/request_cards.db",
    "SMD_REQUEST_INDEX_DB_PATH": "requests.db",
    "SMD_SENT_LINES_DIR": "sent_lines",
    "SMD_SPEC_DIR": "spec",
    "SMD_STICKY_STOP_DB_PATH": "smd/sticky_stop.db",
    "SMD_VOICE_VAULT_DIR": "voice_vault",
}

#: The two model transports. ``anthropic_messages`` is what every seat runs;
#: ``chat_completions`` is Hermes' default path, kept so a handler-shape defect
#: cannot hide behind one adapter's argument handling.
TRANSPORTS = ("anthropic_messages", "chat_completions")


def pytest_configure(config: pytest.Config) -> None:
    # Before ANY seat code runs: a developer shell may carry production
    # credentials, and translate copies env-held connector secrets into the
    # profile it writes.
    scrub_environment()
    # The pinned Hermes lazily pip-installs optional SDKs at agent build (an
    # edge-tts install was observed on the first probe). The harness is
    # hermetic: nothing installs, nothing leaves loopback.
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    install_network_guard()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if HERMES_SRC:
        return
    if os.environ.get("CI"):
        raise pytest.UsageError(
            "tests_e2e under CI requires HERMES_AGENT_SRC (the pinned Hermes checkout); "
            "a skipped harness would report green having exercised nothing"
        )
    skip = pytest.mark.skip(reason="HERMES_AGENT_SRC not set (the pinned Hermes checkout)")
    for item in items:
        item.add_marker(skip)


# --------------------------------------------------------------------------
# Recorders
# --------------------------------------------------------------------------


@dataclass
class HookRecorder:
    """Every plugin hook registration (in order) and every hook invocation."""

    registered: list[tuple[str, str]] = field(default_factory=list)
    invoked: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def registration_order(self, hook: str) -> list[str]:
        """The owning sub-plugin of each callback registered for ``hook``."""
        return [owner for name, owner in self.registered if name == hook]

    def mark(self) -> int:
        return len(self.invoked)

    def fired_since(self, mark: int, hook: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.invoked[mark:] if name == hook]


@dataclass
class DispatchRecord:
    tool: str
    args: dict[str, Any]
    result: Any = None


@dataclass
class DispatchRecorder:
    """Every call Hermes' tool registry dispatched, with what it returned."""

    calls: list[DispatchRecord] = field(default_factory=list)

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> list[DispatchRecord]:
        return list(self.calls[mark:])


def _callback_owner(callback: Any) -> str:
    """The sub-plugin a callback belongs to. The overlay loads each sub-plugin as
    ``hermes_plugins.<slug>`` and its canonicalizing wrapper keeps the original
    callback on ``__wrapped__``."""
    inner = callback
    while getattr(inner, "__wrapped__", None) is not None:
        inner = inner.__wrapped__
    module = getattr(inner, "__module__", "") or ""
    parts = module.split(".")
    if parts[0] == "hermes_plugins" and len(parts) > 1:
        return parts[1].replace("_", "-")
    return module


@dataclass
class Harness:
    root: Path
    profile: Path
    customer_yaml: Path
    script: ModelScript
    broker: StubBroker
    hooks: HookRecorder
    dispatches: DispatchRecorder
    #: ``{tool_name: registering plugin_id}`` for every plugin-registered tool.
    tool_owners: dict[str, str]
    audit_db: Path
    model_base_url: str

    def overlay_tools(self) -> list[str]:
        return sorted(n for n, owner in self.tool_owners.items() if owner == OVERLAY_PLUGIN)


#: Surfaces this harness does not drive, whatever the run shows. The CI summary
#: prints them next to the hooks that never fired, so a Hermes promotion PR that
#: cites this job cites a known scope.
NOT_EXERCISED_SURFACES: tuple[str, ...] = (
    "the gateway process and its platforms (webhook, Telegram, email): the harness "
    "drives AIAgent directly, so pre_gateway_dispatch and every inbound-origin "
    "binding (verified sender, couples space) are not exercised",
    "MCP connectors: mcp_servers is dropped from the temp seat (PR 2 adds the reference connector)",
    "the capability broker's own logic: a stub answers every verb",
    "the non-gateway tool wall: SMD_AUDIT_BROKER_SOCKET is unset (direct audit)",
    "auxiliary title generation: disabled in the temp seat",
)


def _write_summary(harness: Harness) -> None:
    """What this run exercised, for the CI job summary (``E2E_SUMMARY_PATH``)."""
    target = os.environ.get("E2E_SUMMARY_PATH", "")
    if not target:
        return
    from tests_e2e.test_dispatch_contract import GATED

    overlay_owners = {
        owner for _, owner in harness.hooks.registered if owner.startswith("hermes-smd-")
    }
    registered_hooks = sorted(
        {name for name, owner in harness.hooks.registered if owner in overlay_owners}
    )
    fired = {name for name, _ in harness.hooks.invoked}
    dispatched = sorted({c.tool for c in harness.dispatches.calls})
    overlay_tools = harness.overlay_tools()
    summary = {
        "registered_overlay_tools": len(overlay_tools),
        "dispatched_overlay_tools": len([t for t in overlay_tools if t in dispatched]),
        "gated_not_dispatched": sorted(GATED),
        "pre_tool_call_order": harness.hooks.registration_order("pre_tool_call"),
        "overlay_hooks_registered": registered_hooks,
        "overlay_hooks_fired": sorted(set(registered_hooks) & fired),
        "overlay_hooks_not_fired": sorted(set(registered_hooks) - fired),
        "surfaces_not_exercised": list(NOT_EXERCISED_SURFACES),
        # Model requests from outside the turn under test (Hermes' own
        # background work), answered with closing text and kept off the script.
        "foreign_model_requests": harness.script.foreign_total,
    }
    Path(target).write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _install_recorders(hooks: HookRecorder, dispatches: DispatchRecorder, owners: dict) -> None:
    """Wrap Hermes' own seams, before discovery, so the recording is of what
    Hermes did rather than of what a test assumed it would do."""
    from hermes_cli import plugins as hplugins
    from tools.registry import registry

    original_register_hook = hplugins.PluginContext.register_hook

    def register_hook(self: Any, hook_name: str, callback: Any) -> Any:
        hooks.registered.append((hook_name, _callback_owner(callback)))
        return original_register_hook(self, hook_name, callback)

    hplugins.PluginContext.register_hook = register_hook  # type: ignore[method-assign]

    original_register_tool = hplugins.PluginContext.register_tool

    def register_tool(self: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        owners[name] = str(getattr(self, "plugin_id", "") or "")
        return original_register_tool(self, name, *args, **kwargs)

    hplugins.PluginContext.register_tool = register_tool  # type: ignore[method-assign]

    original_invoke = hplugins.PluginManager.invoke_hook

    def invoke_hook(self: Any, hook_name: str, **kwargs: Any) -> Any:
        hooks.invoked.append((hook_name, dict(kwargs)))
        return original_invoke(self, hook_name, **kwargs)

    hplugins.PluginManager.invoke_hook = invoke_hook  # type: ignore[method-assign]

    # Patched on the singleton, so every caller (model_tools, the agent's
    # executors) goes through the recorder. ``registry.dispatch`` turns a
    # handler exception into an error string; that string is what is recorded,
    # because it is exactly what the model is handed.
    original_dispatch = registry.dispatch

    def dispatch(name: str, args: dict, **kwargs: Any) -> Any:
        record = DispatchRecord(tool=name, args=dict(args) if isinstance(args, dict) else {})
        dispatches.calls.append(record)
        record.result = original_dispatch(name, args, **kwargs)
        return record.result

    registry.dispatch = dispatch  # type: ignore[method-assign]


# --------------------------------------------------------------------------
# The seat
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def hermes_src() -> str:
    assert HERMES_SRC, "collection skips this tree when HERMES_AGENT_SRC is unset"
    if HERMES_SRC not in sys.path:
        sys.path.insert(0, HERMES_SRC)
    return HERMES_SRC


@pytest.fixture(scope="session")
def harness(hermes_src: str) -> Iterator[Harness]:
    # AF_UNIX paths are capped near 104 bytes on macOS; pytest's tmp paths are
    # longer, so the seat lives under a short /tmp dir.
    root = Path(tempfile.mkdtemp(prefix="e2e", dir="/tmp"))
    script = ModelScript()
    model = AnthropicMessagesStub(script)
    broker = StubBroker(str(root / "b.sock"))
    hooks = HookRecorder()
    dispatches = DispatchRecorder()
    owners: dict[str, str] = {}
    audit_db = root / "audit.db"
    state = root / "state"
    try:
        for leaf in _STATE_PATHS.values():
            (state / leaf).parent.mkdir(parents=True, exist_ok=True)
        os.environ.update(
            {
                "SMD_CUSTOMER_SLUG": SEAT_SLUG,
                # Both slug spellings a seat carries (fly.toml sets CUSTOMER_SLUG,
                # the overlay reads SMD_CUSTOMER_SLUG), and the persona channel a
                # seat actually populates (enforce._resolve_active_persona).
                "CUSTOMER_SLUG": SEAT_SLUG,
                "SMD_ACTIVE_PERSONA": PERSONA_SLUG,
                "SMD_CUSTOMER_YAML_PATH": str(root / "customer.yaml"),
                # Direct audit transport: rows land in this sqlite file, which the
                # hook tests read back. SMD_AUDIT_BROKER_SOCKET stays unset, which
                # is also what turns the non-gateway wall off (gateway_identity).
                "SMD_D1_AUDIT_BINDING": str(audit_db),
                "SMD_WORKSPACE_BROKER_SOCKET": broker.sock_path,
                "ANTHROPIC_API_KEY": _STUB_API_KEY,
                **{name: str(state / leaf) for name, leaf in _STATE_PATHS.items()},
            }
        )
        profile = build_seat_home(
            root, model_base_url=model.base_url, extra_plugins=[SHADOW_PLUGIN]
        )
        os.environ["HERMES_HOME"] = str(profile)
        _install_recorders(hooks, dispatches, owners)

        from hermes_cli.plugins import discover_plugins

        discover_plugins(force=True)
        built = Harness(
            root=root,
            profile=profile,
            customer_yaml=root / "customer.yaml",
            script=script,
            broker=broker,
            hooks=hooks,
            dispatches=dispatches,
            tool_owners=owners,
            audit_db=audit_db,
            model_base_url=model.base_url,
        )
        yield built
        _write_summary(built)
    finally:
        model.stop()
        broker.stop()
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def session_db(harness: Harness) -> Iterator[Any]:
    from hermes_state import SessionDB

    db = SessionDB(harness.root / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


def _build_agent(api_mode: str, session_db: Any, session_id: str) -> Any:
    """The agent a seat's job worker builds (``shared.job_worker_runtime``).

    ``anthropic_messages`` goes through ``build_hermes_agent`` unchanged, so the
    runtime provider resolves from the seat's own translated ``model:`` block.
    ``chat_completions`` mirrors its constructor arguments with the transport
    swapped, because no seat config selects that path."""
    from shared.job_worker_runtime import build_hermes_agent

    if api_mode == "anthropic_messages":
        agent = build_hermes_agent(
            model=MODEL, session_id=session_id, max_iterations=6, session_db=session_db
        )
        assert agent.api_mode == "anthropic_messages", agent.api_mode
        return agent

    from run_agent import AIAgent

    return AIAgent(
        model=MODEL,
        api_key="sk-e2e-harness-not-a-key",
        base_url="http://127.0.0.1:9/v1",
        provider="custom",
        api_mode="chat_completions",
        max_iterations=6,
        quiet_mode=True,
        load_soul_identity=True,
        skip_memory=True,
        platform="job",
        session_id=session_id,
        session_db=session_db,
        disabled_toolsets=["cronjob", "messaging", "clarify"],
    )


@pytest.fixture(scope="session", params=TRANSPORTS)
def agent(request: pytest.FixtureRequest, harness: Harness, session_db: Any) -> Iterator[Any]:
    """One agent per transport, built once: building an ``AIAgent`` resolves
    the provider, the tool surface and the system prompt, which is the
    expensive part. The chat-completions transport patches the OpenAI client
    at the symbol Hermes' own tests patch at the pin."""
    api_mode = request.param
    if api_mode == "anthropic_messages":
        yield _build_agent(api_mode, session_db, "e2e-anthropic")
        return
    from unittest.mock import patch

    def fake_openai(*_args: Any, **kwargs: Any) -> FakeOpenAIClient:
        return FakeOpenAIClient(harness.script, **kwargs)

    with patch("agent.process_bootstrap.OpenAI", side_effect=fake_openai):
        built = _build_agent(api_mode, session_db, "e2e-chat")
        assert built.api_mode == "chat_completions", built.api_mode
        yield built
