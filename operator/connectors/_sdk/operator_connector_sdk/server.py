"""ConnectorServer — the stdio MCP server base every author-built connector uses.

Thin wrapper over the MCP SDK's MCPServer (mcp 2.x; FastMCP before 2.0). Two jobs:

1. Guarantee tools register with a well-formed, non-empty ``inputSchema`` placed
   where Hermes' MCP client reads it. (MCPServer derives the schema from the tool
   signature and type hints and emits it under the correct ``inputSchema`` key,
   so this avoids the historical bug where tools shipped with empty param
   schemas and the model could not call them.)
2. Expose the tool surface synchronously (``tool_surface``) so the conformance
   harness can enumerate exactly what the server offers.
3. Keep a failing tool's own words in front of the agent. mcp 2.x forwards the
   text of a raised ``ToolError`` and masks every other exception as a bare
   ``Error executing tool <name>`` (a "crash"; the text stays on the server).
   Our connectors raise ordinary exceptions whose text IS the diagnosis:
   ``SmokeballApiError`` carries the HTTP status and response body precisely so
   the agent sees WHY a write failed and stops instead of retrying. mcp 1.x
   forwarded that text; the 2.x migration (#2796) silently dropped it, and the
   pilot's first live turn on 2.x retried a failing ``create_memo`` four times
   into Hermes' breaker with nothing but the tool's name to go on. The wrapper
   below re-raises as ``ToolError`` with the type and text, and logs the
   traceback itself so nothing is lost on the server side either.
4. Gate a write on a matter resolution when the tool declares
   ``requires_resolution`` (``resolution.py`` says why and how): declared once
   here, so no tool body has to remember it.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import Tool

from . import resolution
from .resolution import ResolutionGate

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


def _as_tool_error(exc: Exception, connector: str, tool_name: str) -> ToolError:
    """Translate a tool's exception into the anticipated-failure form mcp 2.x
    forwards to the model, keeping the exception type and text. The traceback
    goes to the server log at ERROR here because the SDK logs a ToolError at
    INFO without one."""
    logger.error("tool %s.%s raised %s: %s", connector, tool_name, type(exc).__name__, exc, exc_info=exc)
    return ToolError(f"{type(exc).__name__}: {exc}")


class _GatedMCPServer(MCPServer):
    """MCPServer whose tool listing marks ``matter_resolution`` REQUIRED on every
    gated tool. The signature gives the parameter a default so that observe mode
    can see a call that left it out (``resolution.py``); the schema the model
    reads must still say required, because that is the contract it follows."""

    def __init__(self, name: str, gated: Mapping[str, Any]) -> None:
        super().__init__(name)
        self._gated = gated  # the ConnectorServer's own dict: tools register after construction

    async def list_tools(self):  # type: ignore[override]
        tools = await super().list_tools()
        for t in tools:
            if t.name in self._gated:
                _mark_required(t.input_schema)
        return tools


def _mark_required(schema: Any) -> None:
    if not isinstance(schema, dict):
        return
    required = schema.setdefault("required", [])
    if isinstance(required, list) and resolution.PARAM not in required:
        required.append(resolution.PARAM)
    prop = (schema.get("properties") or {}).get(resolution.PARAM)
    if isinstance(prop, dict):
        prop.pop("default", None)


class ConnectorServer:
    def __init__(self, name: str, *, resolvers: tuple[str, ...] = ()) -> None:
        """``resolvers`` names this connector's tools that mint a matter
        resolution; a gated tool's description tells the model to call one."""
        self.name = name
        self._gated: dict[str, Callable[[dict[str, Any]], str | None]] = {}
        self._mcp = _GatedMCPServer(name, self._gated)
        self._resolvers = tuple(resolvers)
        self._gate: ResolutionGate | None = None

    def install_resolution_gate(self, gate: ResolutionGate) -> None:
        """Install the connector's gate. Read at CALL time, so a connector may
        install it after its tools register."""
        self._gate = gate

    def gated_tools(self) -> dict[str, Callable[[dict[str, Any]], str | None]]:
        """Each gated tool's name and the resolver that reads its target matter."""
        return dict(self._gated)

    def tool(
        self,
        *args,
        bound: ResultBound | None = None,
        requires_resolution: Callable[[dict[str, Any]], str | None] | None = None,
        **kwargs,
    ):
        """Register a tool. Delegates to MCPServer; the input schema is derived from
        the function signature and type hints. Optional ``bound`` declares the list
        result safe to bound to recent-N (see :class:`ResultBound`) — fail-closed:
        omit it and the result is never truncated, only observed if oversized.

        Optional ``requires_resolution`` gates the tool on a matter resolution: a
        callable that reads the target ``matter_id`` from the call's arguments
        (``resolution.matter_arg()`` for the common case). See ``resolution.py``.

        The decorator hands back the ORIGINAL function. The MCP boundary (result
        governance, exception translation) lives on the registered wrapper only;
        Python callers of the decorated name (the connector's own helpers, its
        tests, the medchron runner) keep the plain Python contract: complete
        results and the connector's own exception types, which their ``except``
        clauses are written against."""

        def register(fn):
            wrapped = self._wrap_result(fn, bound)
            options = dict(kwargs)
            if requires_resolution is not None:
                tool_name = options.get("name") or (args[0] if args and isinstance(args[0], str) else fn.__name__)
                wrapped = self._gate_wrapper(fn, wrapped, requires_resolution, tool_name)
                if options.get("description"):
                    options["description"] = f"{options['description'].rstrip()}\n\n{self._sentence()}"
                self._gated[tool_name] = requires_resolution
            self._mcp.tool(*args, **options)(wrapped)
            return fn

        return register

    def _sentence(self) -> str:
        return resolution.doc_sentence(self._resolvers)

    def _gate_wrapper(self, fn, wrapped, resolver: Callable[[dict[str, Any]], str | None], tool_name: str):
        """The resolution door around an already-wrapped tool: pop the token,
        claim it for the resolved matter, run the tool, put it back on a raise."""

        def door(a: tuple, k: dict[str, Any]) -> resolution.Admission:
            token = k.pop(resolution.PARAM, None)
            arguments = resolution.call_arguments(fn, a, k)
            try:
                return resolution.admit(self._gate, tool_name, resolver, arguments, token)
            except resolution.GateRefused as exc:
                raise ToolError(f"ResolutionRefused: {exc}") from exc

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(wrapped)
            async def agated(*a, **k):
                admission = door(a, k)
                try:
                    return await wrapped(*a, **k)
                except BaseException:
                    resolution.release(self._gate, admission)
                    raise

            resolution.augment(fn, agated, self._sentence())
            return agated

        @functools.wraps(wrapped)
        def gated(*a, **k):
            admission = door(a, k)
            try:
                return wrapped(*a, **k)
            except BaseException:
                resolution.release(self._gate, admission)
                raise

        resolution.augment(fn, gated, self._sentence())
        return gated

    def _wrap_result(self, fn, bound: ResultBound | None):
        # Preserve signature/annotations via functools.wraps so MCPServer still
        # derives the inputSchema from the ORIGINAL function (inspect.signature
        # follows __wrapped__). Handle sync AND async tools.
        tool_name = getattr(fn, "__name__", "?")
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*a, **k):
                try:
                    result = await fn(*a, **k)
                except (ToolError, MCPError):
                    raise  # already in the form the SDK forwards / a protocol error
                except Exception as exc:
                    raise _as_tool_error(exc, self.name, tool_name) from exc
                return _govern_result(result, bound, self.name, tool_name)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                result = fn(*a, **k)
            except (ToolError, MCPError):
                raise
            except Exception as exc:
                raise _as_tool_error(exc, self.name, tool_name) from exc
            return _govern_result(result, bound, self.name, tool_name)

        return wrapper

    def tool_surface(self) -> list[Tool]:
        """The exact set of tools this server exposes, with their inputSchemas.
        Synchronous convenience over MCPServer's async ``list_tools`` — call from
        sync code (tests, conformance), not from inside a running event loop."""
        return anyio.run(self._mcp.list_tools)

    def run_stdio(self) -> None:
        """Serve over stdio — the transport Hermes launches the connector with."""
        self._mcp.run(transport="stdio")
