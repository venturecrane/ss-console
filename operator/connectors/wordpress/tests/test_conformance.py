"""wordpress connector conformance: the platform standard plus a live stdio
round-trip through the real console-script. The tool surface introspects
credlessly because the client is built lazily."""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest
from operator_connector_sdk.naming import runtime_tool_name
from wordpress_connector.server import server

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.toml"

EXPECTED_TOOLS = {
    "list_posts",
    "get_post",
    "list_categories",
    "list_tags",
    "list_media",
    "upload_media",
    "create_pending_post",
    "update_pending_post",
}

_SCRIPT = shutil.which("wordpress-mcp")


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(MANIFEST_PATH)


def test_manifest_loads_and_is_static() -> None:
    m = _manifest()
    assert m.name == "wordpress"
    assert m.capability == "Website"
    assert m.auth_model is AuthModel.STATIC
    assert {s.runtime_env for s in m.required_secrets} == {
        "WORDPRESS_SITE_URL",
        "WORDPRESS_USERNAME",
        "WORDPRESS_APP_PASSWORD",
    }


def test_full_surface_matches() -> None:
    assert {t.name for t in server.tool_surface()} == EXPECTED_TOOLS


def test_conformance_every_tool_classified() -> None:
    runtime_map = conformance.run_all(server, _manifest())
    assert runtime_map[runtime_tool_name("wordpress", "create_pending_post")] == "internal_write"
    assert runtime_map[runtime_tool_name("wordpress", "list_posts")] == "read"


def test_no_publish_or_delete_tool_exists() -> None:
    names = {t.name for t in server.tool_surface()}
    assert not any("publish" in n or "delete" in n for n in names)
    conformance.check_no_banned_tools(server, banned=("publish_post", "delete_post", "delete_media"))


def test_no_tool_takes_a_status_on_the_write_path() -> None:
    for tool in server.tool_surface():
        if tool.name in ("create_pending_post", "update_pending_post", "upload_media"):
            assert "status" not in tool.input_schema["properties"], tool.name


def test_write_surface_is_internal_only() -> None:
    m = _manifest()
    writes = {t: c for t, c in m.tool_classes.items() if c != "read"}
    assert writes == {
        "upload_media": "internal_write",
        "create_pending_post": "internal_write",
        "update_pending_post": "internal_write",
    }


@pytest.mark.skipif(_SCRIPT is None, reason="wordpress-mcp console-script not on PATH")
def test_stdio_serves_over_console_script() -> None:
    async def _roundtrip() -> None:
        async with Client(StdioServerParameters(command=_SCRIPT)) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == EXPECTED_TOOLS
            for t in listed.tools:
                assert t.input_schema.get("type") == "object"

    anyio.run(_roundtrip)
