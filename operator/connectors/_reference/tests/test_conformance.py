"""The reference connector runs the platform conformance suite against itself.
This is the template every real connector's tests follow.

It proves both directions of the governance contract that the platform exists to
guarantee:

- POSITIVE: a tool the connector means to govern (`record`) is classified, and
  is classified under its RUNTIME-prefixed name (`mcp_reference_record`) — the
  bug class (bare vs runtime name) that has slipped governance before.
- NEGATIVE: a tool nothing classifies (`surprise`) is flagged as ungoverned and
  fails the oracle unless explicitly expected — the structural precondition for
  the overlay's fail-closed REFUSED at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest
from operator_connector_sdk.naming import runtime_tool_name
from reference_connector.server import server

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.toml"


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(MANIFEST_PATH)


def test_manifest_loads_and_validates() -> None:
    m = _manifest()
    assert m.name == "reference"
    assert m.capability == "Reference"
    assert m.auth_model is AuthModel.STATIC
    assert [s.runtime_env for s in m.required_secrets] == ["REFERENCE_API_KEY"]


def test_full_surface_present() -> None:
    names = {t.name for t in server.tool_surface()}
    assert names == {"echo", "record", "surprise"}


def test_every_tool_has_nonempty_input_schema() -> None:
    for tool in server.tool_surface():
        props = tool.inputSchema["properties"]
        assert props, f"{tool.name}: empty inputSchema.properties"


def test_structural_conformance_passes_with_surprise_expected() -> None:
    runtime_map = conformance.run_all(
        server,
        _manifest(),
        banned=("forbidden_tool",),
        expected_unclassified=("surprise",),
    )
    # The oracle returns the runtime-prefixed classification the overlay test
    # will check against _RAW_TOOL_ACTION_CLASS_MAP.
    assert runtime_map == {
        "mcp_reference_echo": "read",
        "mcp_reference_record": "internal_write",
    }


def test_positive_binding_record_is_internal_write_under_runtime_name() -> None:
    runtime_map = conformance.run_all(server, _manifest(), expected_unclassified=("surprise",))
    key = runtime_tool_name("reference", "record")
    assert key == "mcp_reference_record"
    assert runtime_map[key] == "internal_write"


def test_unclassified_tool_fails_oracle_when_not_expected() -> None:
    # surprise is live but unclassified; without listing it as expected, the
    # oracle must fail — this is the structural guarantee behind runtime REFUSED.
    with pytest.raises(AssertionError):
        conformance.check_tool_classes(server, _manifest())


def test_phantom_classification_fails_oracle() -> None:
    m = _manifest()
    m.tool_classes["ghost"] = "read"  # no such live tool
    with pytest.raises(AssertionError):
        conformance.check_tool_classes(server, m, expected_unclassified=("surprise",))


def test_banned_tool_absent_is_enforced() -> None:
    with pytest.raises(AssertionError):
        conformance.check_no_banned_tools(server, banned=("echo",))
