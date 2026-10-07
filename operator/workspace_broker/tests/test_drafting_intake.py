"""The drafting lane's own copies of two demand intake helpers
(drafting_intake.py) behave exactly like demand's, case for case.

The copies exist so a change to the live demand lane cannot move the drafting
contract silently. These tests drive both implementations through the same
inputs; a divergence fails here and is then decided on purpose (change the
copy, or update this test to state the new difference).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import demand_verbs, drafting_intake, msgraph_lookup

LIBRARY_CASES = {
    "authored": "self_initiation:\n  document_library:\n    operator_matter:\n      number: ' OPS-LIB '\n",
    "blank": "self_initiation:\n  document_library:\n    operator_matter:\n      number: '  '\n",
    "missing": "scope: {}\n",
    "not_a_string": "self_initiation:\n  document_library:\n    operator_matter:\n      number: 42\n",
    "unparseable": "self_initiation: [\n",
}


@pytest.mark.parametrize("case", sorted(LIBRARY_CASES))
def test_operator_library_number_matches_demand(tmp_path, case) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text(LIBRARY_CASES[case])
    assert drafting_intake.operator_library_number(path) == demand_verbs.operator_library_number(path)
    assert drafting_intake.operator_library_number(tmp_path / "absent.yaml") is None


SENDER = "admin@firm.example"
FOUND = {"sender": SENDER, "internet_message_id": "<abc@mail.firm.example>"}
YAML = "scope:\n  inbound_allow_from:\n    - '@firm.example'\n"

GRAPH_CASES = {
    "resolved": ({"request_graph_id": "AAMkGRAPH0001=", "requested_by": SENDER, "x": 1}, FOUND),
    "both_ids": ({"request_graph_id": "AAMk1", "request_ref": "<a@b.example>"}, FOUND),
    "bad_id": ({"request_graph_id": "a/b", "requested_by": SENDER}, FOUND),
    "not_in_inbox": ({"request_graph_id": "AAMk1", "requested_by": SENDER}, None),
    "outside_sender": (
        {"request_graph_id": "AAMk1", "requested_by": "x@else.example"},
        {"sender": "x@else.example", "internet_message_id": "<c@d.example>"},
    ),
    "wrong_requester": ({"request_graph_id": "AAMk1", "requested_by": "other@firm.example"}, FOUND),
}


@pytest.mark.parametrize("case", sorted(GRAPH_CASES))
def test_request_graph_id_resolution_matches_demand(tmp_path, monkeypatch, case) -> None:
    envelope, found = GRAPH_CASES[case]
    path = tmp_path / "customer.yaml"
    path.write_text(YAML)
    broker = SimpleNamespace(msgraph=object(), customer_path=path)
    monkeypatch.setattr(msgraph_lookup, "received_by_graph_id", lambda _ops, _gid: found)
    ours = drafting_intake.resolve_request_graph_id(broker, dict(envelope), 0)
    theirs = demand_verbs._resolve_request_graph_id(broker, dict(envelope), 0)
    assert ours == theirs
    if case == "resolved":
        assert ours["envelope"]["request_ref"] == FOUND["internet_message_id"]
    else:
        assert "refused" in ours


def test_request_graph_id_is_root_only_in_both(tmp_path) -> None:
    broker = SimpleNamespace(msgraph=object(), customer_path=tmp_path / "c.yaml")
    for fn in (drafting_intake.resolve_request_graph_id, demand_verbs._resolve_request_graph_id):
        with pytest.raises(PermissionError):
            fn(broker, {"request_graph_id": "AAMk1"}, 1000)
