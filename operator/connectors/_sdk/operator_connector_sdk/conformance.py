"""The reusable conformance suite every connector package runs.

These checks are what make "adding connector #N is cheap *and* safe" true: a
package that passes them is wired the same way as every other and cannot ship a
malformed or silently-ungoverned tool surface.

Two layers of classification check live in two repos, by design:

- HERE (ss-console, no overlay dependency): every live tool is either declared
  in the manifest's ``tool_classes`` or explicitly expected to be unclassified;
  the runtime-prefixed name is the unit of declaration. This catches the bare-vs-
  runtime-name bug locally, before the overlay is even involved.
- OVERLAY (PR-2): the manifest's declared classes are checked *against* the
  hand-authored ``_RAW_TOOL_ACTION_CLASS_MAP`` (the authority). That is where
  enforcement lives; it is not duplicated here.
"""

from __future__ import annotations

from collections.abc import Iterable

from .manifest import ConnectorManifest
from .naming import runtime_tool_name
from .resolution import PARAM as RESOLUTION_PARAM
from .server import ConnectorServer


def check_tool_schemas(server: ConnectorServer) -> list[str]:
    """Every exposed tool has a well-formed object inputSchema (present where
    Hermes reads it). Returns the tool names."""
    tools = server.tool_surface()
    if not tools:
        raise AssertionError(f"{server.name}: exposes no tools")
    for t in tools:
        schema = t.input_schema
        if not isinstance(schema, dict):
            raise AssertionError(f"{server.name}.{t.name}: inputSchema is not a dict")
        if schema.get("type") != "object":
            raise AssertionError(f"{server.name}.{t.name}: inputSchema.type != 'object'")
        if "properties" not in schema:
            raise AssertionError(f"{server.name}.{t.name}: inputSchema has no 'properties'")
    return [t.name for t in tools]


def check_resolution_schemas(server: ConnectorServer) -> set[str]:
    """Every tool registered with ``requires_resolution`` is live and its
    advertised inputSchema lists ``matter_resolution`` as a REQUIRED string, so
    the model reads the rule in the contract it follows. A gated tool whose
    schema lets the argument look optional is a gate the model is told it may
    skip. Returns the gated tool names."""
    gated = set(server.gated_tools())
    surface = {t.name: t.input_schema for t in server.tool_surface()}
    for name in sorted(gated):
        schema = surface.get(name)
        if schema is None:
            raise AssertionError(f"{server.name}.{name}: declared requires_resolution but is not on the surface")
        prop = (schema.get("properties") or {}).get(RESOLUTION_PARAM)
        if not isinstance(prop, dict) or prop.get("type") != "string":
            raise AssertionError(f"{server.name}.{name}: inputSchema has no string {RESOLUTION_PARAM!r}")
        if RESOLUTION_PARAM not in (schema.get("required") or []):
            raise AssertionError(f"{server.name}.{name}: {RESOLUTION_PARAM!r} is not in inputSchema.required")
    return gated


def check_no_banned_tools(server: ConnectorServer, banned: Iterable[str]) -> None:
    """The server must not expose any tool the connector declares as banned
    (defense in depth: never on the menu *and* refused at the trust layer)."""
    names = {t.name for t in server.tool_surface()}
    leaked = names & set(banned)
    if leaked:
        raise AssertionError(f"{server.name}: exposes banned tools {sorted(leaked)}")


def check_manifest(manifest: ConnectorManifest) -> None:
    """The manifest validates and is internally consistent."""
    if not manifest.name or not manifest.capability:
        raise AssertionError("manifest: name and capability are required")


def check_tool_classes(
    server: ConnectorServer,
    manifest: ConnectorManifest,
    *,
    expected_unclassified: Iterable[str] = (),
) -> dict[str, str]:
    """Every live tool is accounted for: either declared in the manifest's
    ``tool_classes`` or explicitly listed in ``expected_unclassified`` (a tool
    the connector deliberately leaves ungoverned to prove fail-closed refusal).

    A live tool that is neither is a silent governance gap and fails here. A
    declared class with no matching live tool is a phantom classification and
    fails here. Returns the runtime-prefixed name -> class map the overlay test
    will check against ``_RAW_TOOL_ACTION_CLASS_MAP``.
    """
    live = {t.name for t in server.tool_surface()}
    declared = set(manifest.tool_classes)
    expected_unclassified = set(expected_unclassified)

    phantom = declared - live
    if phantom:
        raise AssertionError(
            f"{server.name}: tool_classes declares {sorted(phantom)} but the server exposes no such tool"
        )

    ungoverned = live - declared - expected_unclassified
    if ungoverned:
        raise AssertionError(
            f"{server.name}: live tools {sorted(ungoverned)} are neither classified "
            f"nor listed as expected-unclassified — they would fall through to the "
            f"fail-closed default at runtime"
        )

    overlap = declared & expected_unclassified
    if overlap:
        raise AssertionError(
            f"{server.name}: {sorted(overlap)} are both classified and listed as expected-unclassified — pick one"
        )

    return {runtime_tool_name(manifest.name, tool): cls for tool, cls in manifest.tool_classes.items()}


def run_all(
    server: ConnectorServer,
    manifest: ConnectorManifest,
    *,
    banned: Iterable[str] = (),
    expected_unclassified: Iterable[str] = (),
) -> dict[str, str]:
    """Run the full structural conformance suite. Returns the runtime-prefixed
    name -> ActionClass map (for the overlay's manifest<=map test to consume)."""
    check_manifest(manifest)
    check_tool_schemas(server)
    check_resolution_schemas(server)
    check_no_banned_tools(server, banned)
    return check_tool_classes(server, manifest, expected_unclassified=expected_unclassified)
