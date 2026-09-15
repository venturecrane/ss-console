"""Lean-reads governance in the SDK: a declared-boundable list is truncated to
recent-N (stamped + pageable); everything else is passed through untouched; and
any oversized read we did NOT bound is logged LOUD (never a silent fail-open).

Fail-closed by design: bounding happens ONLY when a tool declares a ``ResultBound``
(it guarantees newest-first order + a real page path). A tool that returns a
complete set for dedup/reconciliation simply omits the declaration and is never
truncated.
"""

from __future__ import annotations

import logging

from operator_connector_sdk import ResultBound
from operator_connector_sdk.server import ConnectorServer, _govern_result


def test_declared_list_over_cap_is_bounded_and_stamped() -> None:
    out = _govern_result({"value": [1, 2, 3, 4, 5], "size": 5}, ResultBound(2, "call with offset=2"), "c", "t")
    assert out["value"] == [1, 2]
    assert out["_lean_reads"] == {"truncated": True, "returned": 2, "total": 5, "hint": "call with offset=2"}


def test_declared_list_under_cap_is_untouched() -> None:
    out = _govern_result({"value": [1, 2, 3]}, ResultBound(10, "h"), "c", "t")
    assert out == {"value": [1, 2, 3]}
    assert "_lean_reads" not in out


def test_undeclared_list_is_never_bounded() -> None:
    # Complete-set contract: no ResultBound -> never truncated, even if long.
    out = _govern_result({"value": list(range(100))}, None, "c", "t")
    assert out["value"] == list(range(100))
    assert "_lean_reads" not in out


def test_non_list_result_passes_through() -> None:
    assert _govern_result({"id": "x", "status": "Open"}, ResultBound(1, "h"), "c", "t") == {
        "id": "x",
        "status": "Open",
    }


def test_bare_list_bounded_wraps_in_envelope() -> None:
    out = _govern_result([1, 2, 3, 4], ResultBound(2, "h"), "c", "t")
    assert out == {"value": [1, 2], "_lean_reads": {"truncated": True, "returned": 2, "total": 4, "hint": "h"}}


def test_oversized_unbounded_read_fails_loud(caplog) -> None:
    big = {"value": [{"blob": "x" * 1000} for _ in range(60)]}  # > 40k chars, no bound declared
    with caplog.at_level(logging.WARNING, logger="operator_connector_sdk"):
        out = _govern_result(big, None, "smokeball", "get_files_on_matter")
    assert out is big  # passed through untouched
    msgs = [r.getMessage() for r in caplog.records]
    assert any("oversized unbounded read" in m for m in msgs)
    assert any("get_files_on_matter" in m and "smokeball" in m for m in msgs)


def test_bounded_read_does_not_warn(caplog) -> None:
    big = {"value": [{"blob": "x" * 1000} for _ in range(60)]}  # oversized, but declared boundable
    with caplog.at_level(logging.WARNING, logger="operator_connector_sdk"):
        out = _govern_result(big, ResultBound(max_items=5, page_hint="h"), "c", "t")
    assert len(out["value"]) == 5
    assert not any("oversized" in r.getMessage() for r in caplog.records)


def test_governance_never_raises_on_unserializable() -> None:
    class Bad:  # not JSON-serializable and json.dumps(default=str) still fine, but exercise the path
        pass

    out = _govern_result({"value": [Bad()]}, None, "c", "t")
    assert isinstance(out, dict) and "value" in out  # passed through, no raise


# ---- integration: the tool() wrapper preserves the MCPServer inputSchema ----


def test_tool_wrapper_preserves_input_schema() -> None:
    srv = ConnectorServer("t")

    @srv.tool(bound=ResultBound(max_items=2, page_hint="call with offset=2"))
    def list_things(matter_id: str, limit: int = 500) -> dict:
        """List things."""
        return {"value": [1, 2, 3, 4, 5]}

    tool = next(t for t in srv.tool_surface() if t.name == "list_things")
    props = tool.input_schema["properties"]
    assert "matter_id" in props and "limit" in props
    assert tool.input_schema.get("required") == ["matter_id"]


def test_undeclared_tool_registers_and_keeps_schema() -> None:
    srv = ConnectorServer("t")

    @srv.tool()
    def get_one(matter_id: str) -> dict:
        """Get one."""
        return {"id": matter_id}

    tool = next(t for t in srv.tool_surface() if t.name == "get_one")
    assert "matter_id" in tool.input_schema["properties"]
