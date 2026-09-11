"""Smokeball connector conformance — the platform standard, plus a live stdio
round-trip via the real console-script. No live Smokeball calls (those need creds
and are exercised at the connect step); the tool surface introspects credlessly
because the REST client is built lazily."""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest
from operator_connector_sdk.naming import runtime_tool_name
from smokeball_connector.server import server

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.toml"

EXPECTED_TOOLS = {
    "auth_status",
    "list_matters",
    "get_matter",
    "list_matter_types",
    "get_stage_sets",
    "get_stage_to_matter_mappings",
    "get_contacts",
    "get_contact",
    "get_contact_relations",
    "list_tasks",
    "get_task",
    "create_task",
    "update_task",
    "list_events",
    "create_event",
    "update_event",
    "create_event_reminder",
    "search_staff",
    "get_staff",
    "get_roles_on_matter",
    "get_relationships_on_matter",
    "get_files_on_matter",
    "get_file",
    "get_download_url",
    "read_document",
    "list_folders",
    "create_folder",
    "create_matter",
    "add_file",
    "file_attachment_to_matter",
    "delete_file",
    "get_memos_on_matter",
    "get_bank_accounts",
    "get_matter_balances",
    "get_matter_billing_config",
    "get_fees",
    "get_expenses",
    "get_webhook_subscriptions",
    "get_event_types",
    "create_webhook_subscription",
    "create_memo",
    "render_docx_template",
    "render_docx_draft",
}

_SCRIPT = shutil.which("smokeball-mcp")


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(MANIFEST_PATH)


def test_manifest_loads_and_is_client_credentials() -> None:
    m = _manifest()
    assert m.name == "smokeball"
    assert m.capability == "PracticeManagement"
    assert m.auth_model is AuthModel.CLIENT_CREDENTIALS
    assert {s.runtime_env for s in m.required_secrets} == {
        "SMOKEBALL_CLIENT_ID",
        "SMOKEBALL_CLIENT_SECRET",
        "SMOKEBALL_API_KEY",
    }


def test_full_surface_matches() -> None:
    assert {t.name for t in server.tool_surface()} == EXPECTED_TOOLS


def test_conformance_every_tool_classified() -> None:
    # No expected_unclassified: every Smokeball tool the server exposes MUST be
    # declared, and every declaration MUST map to a live tool. Returns the
    # runtime-prefixed map the overlay's manifest<=map test consumes.
    runtime_map = conformance.run_all(server, _manifest())
    # Trust-account fund-movement tools are never exposed here.
    assert not any("transaction" in k or "protect" in k for k in runtime_map)
    # Writes carry their classes under the runtime name; reads are reads.
    assert runtime_map[runtime_tool_name("smokeball", "create_memo")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "create_webhook_subscription")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "add_file")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "delete_file")] == "destructive"
    assert runtime_map[runtime_tool_name("smokeball", "create_event")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "create_task")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "create_folder")] == "internal_write"
    assert runtime_map[runtime_tool_name("smokeball", "create_matter")] == "commitment"
    assert runtime_map[runtime_tool_name("smokeball", "list_events")] == "read"
    assert runtime_map[runtime_tool_name("smokeball", "get_matter_balances")] == "read"
    assert runtime_map[runtime_tool_name("smokeball", "list_matters")] == "read"


def test_write_surface_is_memo_document_and_deadline_engine() -> None:
    # The write surface: the internal-log memo, the document round-trip, the
    # deadline-engine / document-organization cut (events, tasks, folders), and
    # the Operator's own matter. Every write is internal_write except
    # delete_file (destructive, taint-gated) and create_matter (commitment,
    # confirm-gated). Trust fund-movement is never here.
    m = _manifest()
    writes = {t: c for t, c in m.tool_classes.items() if c != "read"}
    assert writes == {
        "create_memo": "internal_write",
        "create_webhook_subscription": "internal_write",
        "add_file": "internal_write",
        "file_attachment_to_matter": "internal_write",
        "delete_file": "destructive",
        "create_event": "internal_write",
        "update_event": "internal_write",
        "create_event_reminder": "internal_write",
        "create_task": "internal_write",
        "update_task": "internal_write",
        "create_folder": "internal_write",
        # The Operator's OWN matter, and the only write here that is not
        # internal_write or destructive (ss-console#2536). COMMITMENT because it
        # changes the firm's system of record and must never be autonomous: it
        # happens after a Named Administrator has been shown the exact matter
        # and has said yes. The overlay map already classifies it this way
        # (shared/action_classes.py:432), so this declaration adds no coordinated
        # overlay change, only the OVERLAY_REF bump that ships the confirm path.
        "create_matter": "commitment",
        # The .docx producer (2026-08-10, Captain directive #2222): renders a
        # gated markdown skeleton server-side and files it into the matter via
        # the same two-stage upload as add_file. Bytes never transit the model.
        "render_docx_template": "internal_write",
        # The FILLED-DRAFT producer (ss-console#2258). Same class and same
        # reasoning as its template sibling: the Operator saving its own work
        # product into the firm's record, bytes never transiting the model. The
        # two differ only in which artifact their content gate is written for.
        "render_docx_draft": "internal_write",
    }


@pytest.mark.skipif(_SCRIPT is None, reason="smokeball-mcp console-script not on PATH")
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
