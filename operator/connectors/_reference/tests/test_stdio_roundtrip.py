"""End-to-end proof that the connector serves over real stdio MCP via the
console-script the registry launches — not the module form.

This is the rail the overlay's `command` (an absolute path to
/opt/connectors/reference/.venv/bin/reference-mcp) must satisfy: spawn the
script, speak the protocol, list tools, call one. If the console-script entry
point is wrong, this fails here instead of as a runtime MCP-spawn error on a
live Machine.
"""

from __future__ import annotations

import shutil

import anyio
import pytest
from mcp import Client, StdioServerParameters

# Resolve the installed console-script the same way the gateway would (on PATH
# of the connector's venv). Skip cleanly if the package was imported but not
# installed as a script (e.g. a bare `pytest` against the source tree).
_SCRIPT = shutil.which("reference-mcp")


async def _roundtrip(command: str) -> None:
    # mcp 2.x: Client spawns the subprocess and runs the initialize handshake
    # itself; the 1.x stdio_client + ClientSession pair is the layer beneath it.
    async with Client(StdioServerParameters(command=command)) as client:
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {"echo", "record", "surprise"}
        for t in listed.tools:
            assert t.input_schema.get("type") == "object"

        result = await client.call_tool("echo", {"text": "ping"})
        assert result.content[0].text == "ping"


@pytest.mark.skipif(_SCRIPT is None, reason="reference-mcp console-script not on PATH")
def test_stdio_roundtrip_via_console_script() -> None:
    anyio.run(_roundtrip, _SCRIPT)
