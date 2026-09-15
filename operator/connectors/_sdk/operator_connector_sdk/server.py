"""ConnectorServer — the stdio MCP server base every author-built connector uses.

Thin wrapper over the MCP SDK's MCPServer (mcp 2.x; FastMCP before 2.0). Two jobs:

1. Guarantee tools register with a well-formed, non-empty ``inputSchema`` placed
   where Hermes' MCP client reads it. (MCPServer derives the schema from the tool
   signature and type hints and emits it under the correct ``inputSchema`` key,
   so this avoids the historical bug where tools shipped with empty param
   schemas and the model could not call them.)
2. Expose the tool surface synchronously (``tool_surface``) so the conformance
   harness can enumerate exactly what the server offers.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import Tool

logger = logging.getLogger("operator_connector_sdk")

# Lean reads (context-cost governance). A read result larger than this (serialized
# chars) that we did NOT bound is logged LOUD, per (connector, tool) — so an
# oversized read that silently inflates the agent's retained context is OBSERVABLE,
# never a silent fail-open (the codebase's recurring defect). ~40k chars ≈ ~10k
# tokens; well above a normal single-record read.
_OVERSIZED_RESULT_CHARS = 40_000


@dataclass(frozen=True)
class ResultBound:
    """A tool's DECLARATION that its list result is safe to bound to the most
    recent ``max_items`` items. Fail-closed and opt-in: a tool is bounded ONLY if
    it declares this, and a tool declares it ONLY when it (a) guarantees a
    newest-first order and (b) offers ``page_hint`` as a real path to the dropped
    older items. A tool whose contract is "return the complete set"
    (dedup/reconciliation) must NOT declare a bound — it is never truncated."""

    max_items: int
    page_hint: str


def _envelope_items(result: Any) -> list | None:
    """The list a read returned: the HATEOAS ``value`` list, or a bare list; else
    None (single object / tracking link / None are never lists to bound)."""
    if isinstance(result, dict) and isinstance(result.get("value"), list):
        return result["value"]
    if isinstance(result, list):
        return result
    return None


def _result_size(result: Any) -> int:
    try:
        return len(result) if isinstance(result, str) else len(json.dumps(result, default=str))
    except Exception:  # noqa: BLE001 — sizing must never raise
        return 0


def _stamp_bounded(result: Any, kept: list, *, total: int, hint: str) -> Any:
    marker = {"truncated": True, "returned": len(kept), "total": total, "hint": hint}
    if isinstance(result, dict):
        result["value"] = kept
        result["_lean_reads"] = marker
        return result
    return {"value": kept, "_lean_reads": marker}  # wrap a bare list so the marker survives


def _govern_result(result: Any, bound: ResultBound | None, connector: str, tool_name: str) -> Any:
    """Bound a declared-safe oversized list to recent-N (stamped + pageable); and
    fail LOUD on any oversized read we did not bound. Never raises."""
    try:
        bounded = False
        items = _envelope_items(result)
        if items is not None and bound is not None and len(items) > bound.max_items:
            result = _stamp_bounded(result, items[: bound.max_items], total=len(items), hint=bound.page_hint)
            bounded = True
        if not bounded and _result_size(result) > _OVERSIZED_RESULT_CHARS:
            logger.warning(
                "lean-reads: oversized unbounded read: connector=%s tool=%s chars=%d "
                "(declare a ResultBound if recent-N is safe, or return a leaner representation)",
                connector,
                tool_name,
                _result_size(result),
            )
        return result
    except Exception:  # noqa: BLE001 — governance must never break the tool
        logger.warning("lean-reads: governance failed for %s.%s; passing through", connector, tool_name, exc_info=True)
        return result


class ConnectorServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self._mcp = MCPServer(name)

    def tool(self, *args, bound: ResultBound | None = None, **kwargs):
        """Register a tool. Delegates to MCPServer; the input schema is derived from
        the function signature and type hints. Optional ``bound`` declares the list
        result safe to bound to recent-N (see :class:`ResultBound`) — fail-closed:
        omit it and the result is never truncated, only observed if oversized."""
        mcp_register = self._mcp.tool(*args, **kwargs)

        def register(fn):
            wrapped = self._wrap_result(fn, bound)
            return mcp_register(wrapped)

        return register

    def _wrap_result(self, fn, bound: ResultBound | None):
        # Preserve signature/annotations via functools.wraps so MCPServer still
        # derives the inputSchema from the ORIGINAL function (inspect.signature
        # follows __wrapped__). Handle sync AND async tools.
        tool_name = getattr(fn, "__name__", "?")
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*a, **k):
                return _govern_result(await fn(*a, **k), bound, self.name, tool_name)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*a, **k):
            return _govern_result(fn(*a, **k), bound, self.name, tool_name)

        return wrapper

    def tool_surface(self) -> list[Tool]:
        """The exact set of tools this server exposes, with their inputSchemas.
        Synchronous convenience over MCPServer's async ``list_tools`` — call from
        sync code (tests, conformance), not from inside a running event loop."""
        return anyio.run(self._mcp.list_tools)

    def run_stdio(self) -> None:
        """Serve over stdio — the transport Hermes launches the connector with."""
        self._mcp.run(transport="stdio")
