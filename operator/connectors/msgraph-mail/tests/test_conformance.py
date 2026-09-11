"""msgraph-mail connector conformance — the platform standard, plus a live stdio
round-trip via the real console-script. No live Graph calls (those need creds and
are exercised at the connect step); the tool surface introspects credlessly because
the Graph client is built lazily."""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from msgraph_mail_connector.server import server
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest
from operator_connector_sdk.naming import runtime_tool_name

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.toml"

EXPECTED_TOOLS = {
    "list_messages",
    "read_message",
    "poll_delta",
    "create_draft",
    "send_message",
    "reply_message",
}

_SCRIPT = shutil.which("msgraph-mail-mcp")


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(MANIFEST_PATH)


def test_manifest_loads_and_is_client_credentials() -> None:
    m = _manifest()
    assert m.name == "msgraph-mail"
    assert m.capability == "Email"
    assert m.auth_model is AuthModel.CLIENT_CREDENTIALS
    assert {s.runtime_env for s in m.required_secrets} == {
        "MSGRAPH_TENANT_ID",
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MSGRAPH_MAILBOX",
    }


def test_full_surface_matches() -> None:
    assert {t.name for t in server.tool_surface()} == EXPECTED_TOOLS


def test_conformance_every_tool_classified() -> None:
    # No expected_unclassified: every tool the server exposes MUST be declared, and
    # every declaration MUST map to a live tool. Returns the runtime-prefixed map the
    # overlay's manifest<=map test consumes.
    runtime_map = conformance.run_all(server, _manifest())
    # No delete tool exists on this connector at all.
    assert not any("delete" in k for k in runtime_map)
    # Reads are reads; the draft is internal; the two sends are external_send.
    assert runtime_map[runtime_tool_name("msgraph-mail", "list_messages")] == "read"
    assert runtime_map[runtime_tool_name("msgraph-mail", "read_message")] == "read"
    assert runtime_map[runtime_tool_name("msgraph-mail", "poll_delta")] == "read"
    assert runtime_map[runtime_tool_name("msgraph-mail", "create_draft")] == "internal_write"
    assert runtime_map[runtime_tool_name("msgraph-mail", "send_message")] == "external_send"
    assert runtime_map[runtime_tool_name("msgraph-mail", "reply_message")] == "external_send"


def test_write_surface_is_draft_and_two_sends() -> None:
    # The write surface: the internal draft + the two external sends. No destructive
    # tool exists (no delete path by design).
    m = _manifest()
    writes = {t: c for t, c in m.tool_classes.items() if c != "read"}
    assert writes == {
        "create_draft": "internal_write",
        "send_message": "external_send",
        "reply_message": "external_send",
    }
    assert not any(c == "destructive" for c in m.tool_classes.values())


@pytest.mark.skipif(_SCRIPT is None, reason="msgraph-mail-mcp console-script not on PATH")
def test_stdio_serves_over_console_script() -> None:
    async def _roundtrip() -> None:
        async with stdio_client(StdioServerParameters(command=_SCRIPT)) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {t.name for t in listed.tools} == EXPECTED_TOOLS
                for t in listed.tools:
                    assert t.inputSchema.get("type") == "object"

    anyio.run(_roundtrip)
