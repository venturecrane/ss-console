"""operator_connector_sdk — the contract every author-built MCP connector is built on.

Deliberately minimal. It ships exactly the pieces with teeth:

- ``ConnectorServer`` — a stdio MCP server base that guarantees each tool ships a
  well-formed, non-empty ``inputSchema`` where Hermes' MCP client reads it.
- ``ConnectorManifest`` — the in-package self-description (capability, required
  secret *names*, ``auth_model``, and ``tool_classes`` as a conformance ORACLE).
- ``ResolutionGate`` / ``matter_arg``: a write on a matter the model chose
  opens only with a resolution minted for that matter, declared once per tool
  at registration (``resolution.py``).
- the conformance harness — spawns/introspects the server and proves the tool
  surface is well-formed and that every live tool is classified under its
  *runtime-prefixed* name (``mcp_<server>_<tool>``), the exact thing that has
  slipped governance before.

What is NOT here: auth-strategy base classes (deferred until a real
static/client_credentials connector exists) and the enforced tool->ActionClass
map. The enforced map is the hand-authored literal in the overlay
(``shared/action_classes.py``); the manifest's ``tool_classes`` is only the
oracle a conformance test checks that map against. Trust is reviewed in the
trust repo, not self-certified here.
"""

from __future__ import annotations

from .manifest import ACTION_CLASSES, AuthModel, ConnectorManifest, SecretSpec
from .naming import runtime_tool_name
from .resolution import ResolutionGate, matter_arg
from .server import ConnectorServer, ResultBound

__all__ = [
    "ACTION_CLASSES",
    "AuthModel",
    "ConnectorManifest",
    "ConnectorServer",
    "ResolutionGate",
    "ResultBound",
    "SecretSpec",
    "matter_arg",
    "runtime_tool_name",
]
