"""A failing tool's own words reach the agent.

mcp 2.x forwards the text of a raised ``ToolError`` to the client and masks every
other exception as a bare ``Error executing tool <name>``. Our connectors raise
ordinary exceptions whose text is the diagnosis (``SmokeballApiError`` carries the
HTTP status and body). These tests drive a ConnectorServer through the real MCP
client over the in-memory transport, so they prove what the model sees, not what
the wrapper raises.
"""

from __future__ import annotations

import logging

import anyio
from mcp.client import Client
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver.exceptions import ToolError
from operator_connector_sdk.server import ConnectorServer


class _ApiError(RuntimeError):
    """Stands in for SmokeballApiError: a plain RuntimeError whose text carries the cause."""


def _call(server: ConnectorServer, name: str, args: dict):
    async def go():
        async with Client(InMemoryTransport(server._mcp)) as client:
            return await client.call_tool(name, args)

    return anyio.run(go)


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def test_sync_tool_exception_text_reaches_the_client(caplog) -> None:
    srv = ConnectorServer("smokeball")

    @srv.tool()
    def create_memo(matter_id: str, text: str):
        raise _ApiError(f"Smokeball POST /matters/{matter_id}/memos -> HTTP 404: matter not found")

    with caplog.at_level(logging.ERROR, logger="operator_connector_sdk"):
        result = _call(srv, "create_memo", {"matter_id": "m1", "text": "hi"})
    assert result.is_error is True
    body = _text(result)
    assert body.startswith("Error executing tool create_memo: ")
    assert "_ApiError: Smokeball POST /matters/m1/memos -> HTTP 404: matter not found" in body
    # The traceback still lands on the server side.
    errs = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert errs and "create_memo" in errs[0].getMessage()


def test_async_tool_exception_text_reaches_the_client() -> None:
    srv = ConnectorServer("c")

    @srv.tool()
    async def get_file(file_id: str):
        raise ValueError(f"file_id {file_id!r} is not a UUID")

    result = _call(srv, "get_file", {"file_id": "nope"})
    assert result.is_error is True
    assert "ValueError: file_id 'nope' is not a UUID" in _text(result)


def test_explicit_tool_error_passes_through_unwrapped() -> None:
    srv = ConnectorServer("c")

    @srv.tool()
    def refuse(x: int):
        raise ToolError("Refused: x must be positive")

    result = _call(srv, "refuse", {"x": -1})
    assert result.is_error is True
    assert _text(result) == "Error executing tool refuse: Refused: x must be positive"


def test_success_path_is_untouched() -> None:
    srv = ConnectorServer("c")

    @srv.tool()
    def ok(x: int):
        return {"doubled": x * 2}

    result = _call(srv, "ok", {"x": 2})
    assert result.is_error is False
    assert '"doubled": 4' in _text(result) or result.structured_content == {"doubled": 4}


def test_python_callers_keep_the_connector_exception_type() -> None:
    # The decorator hands back the original function: the connector's own helpers
    # and tests call it as Python and catch the connector's exception types. Only
    # the MCP boundary translates.
    srv = ConnectorServer("c")

    @srv.tool()
    def fails(x: int):
        raise _ApiError("HTTP 403: insufficient scope")

    try:
        fails(1)
    except _ApiError as exc:
        assert "HTTP 403" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("direct call must raise the connector's own type")
    result = _call(srv, "fails", {"x": 1})
    assert result.is_error is True
    assert "_ApiError: HTTP 403: insufficient scope" in _text(result)
