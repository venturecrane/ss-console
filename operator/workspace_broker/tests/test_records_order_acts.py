"""records_orders_placed: the records orders the Operator placed, for the weekly chase.

What these pin, each with the harm it prevents:

* only committed place-order acts come back, so a calendar deletion or a
  proposal nobody confirmed is never chased as an order;
* the matter id comes from the act row, else from the stored proposal, and an
  order with neither is still returned (``matter_id: None``), so the chase can
  say it could not check it rather than drop it;
* nothing of the order's facilities or client leaves the broker;
* the act row written from now on carries the matter id (``committed_metadata``);
* a ledger that cannot be opened RAISES ("could not look", never "none");
* the verb is open to the agent uid and root only.

Run::

    cd operator && python3 -m pytest workspace_broker/tests/test_records_order_acts.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import act_records_order, records_order_acts, verbs
from workspace_broker.audit_ledger import CREATE_TABLE_SQL
from workspace_broker.establishment_constants import ACT_COMMITTED_ACTION_TYPE, CREATE_PENDING_RULES_SQL

MATTER = "11111111-1111-1111-1111-111111111111"
OLD_MATTER = "33333333-3333-3333-3333-333333333333"
ORDER_ID = "44444444-4444-4444-4444-444444444444"
REF = "0123456789abcdef0123456789abcdef"


def _ledger(tmp_path: Path) -> Path:
    path = tmp_path / "audit.db"
    conn = sqlite3.connect(path)
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_PENDING_RULES_SQL)
    conn.commit()
    conn.close()
    return path


def _act(path: Path, n: int, ts: str, meta: dict, action_type: str = ACT_COMMITTED_ACTION_TYPE) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO audit_log (id, ts, action_type, actor, metadata) VALUES (?,?,?,?,?)",
        (f"row{n}", ts, action_type, "operator", json.dumps(meta)),
    )
    conn.commit()
    conn.close()


def _proposal(path: Path, proposal_id: str, matter_id: str) -> None:
    payload = {"order": {"matter_id": matter_id, "client_name": "Jane Example", "locations": [{"custodian_name": "X"}]}}
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO pending_rules (proposal_id, scope, subject_json, text, text_sha256, instructed_by,"
        " created_at, expires_at, consumed_at, kind, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (proposal_id, "act", "{}", "t", "h", "a@x", 0.0, 1.0, 0.5, "act", json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def _place(**over) -> dict:
    meta = {
        "tool": act_records_order.PLACE_ORDER_TOOL,
        "proposal_id": "p1",
        "matter_id": MATTER,
        "vendor_order_record_id": ORDER_ID,
        "order_ref": REF,
        "location_count": 2,
    }
    meta.update(over)
    return meta


def test_only_committed_place_order_acts_come_back(tmp_path):
    path = _ledger(tmp_path)
    _act(path, 1, "2026-10-06T18:00:00.000Z", _place())
    _act(path, 2, "2026-10-07T18:00:00.000Z", {"tool": "mcp_smokeball_delete_event", "matter_id": MATTER})
    _act(path, 3, "2026-10-08T18:00:00.000Z", _place(), action_type="ACT_PROPOSED")
    orders = records_order_acts.read_orders(str(path))
    assert orders == [
        {
            "committed_at": "2026-10-06T18:00:00.000Z",
            "matter_id": MATTER,
            "vendor_order_record_id": ORDER_ID,
            "order_ref": REF,
            "location_count": 2,
        }
    ]


def test_an_older_row_takes_its_matter_from_the_proposal_and_an_unknown_one_is_kept(tmp_path):
    """FALSIFIER: skip the proposal join, or drop a row with no matter id, and this fails."""
    path = _ledger(tmp_path)
    _proposal(path, "p-old", OLD_MATTER)
    _act(path, 1, "2026-10-06T18:00:00.000Z", _place(proposal_id="p-old", matter_id=None))
    _act(path, 2, "2026-10-07T18:00:00.000Z", _place(proposal_id="p-gone", matter_id=None))
    orders = records_order_acts.read_orders(str(path))
    assert [o["matter_id"] for o in orders] == [OLD_MATTER, None]


def test_nothing_of_the_order_leaves_the_broker(tmp_path):
    path = _ledger(tmp_path)
    _proposal(path, "p1", MATTER)
    _act(path, 1, "2026-10-06T18:00:00.000Z", _place(matter_id=None))
    text = json.dumps(records_order_acts.read_orders(str(path)))
    assert "Jane" not in text and "custodian" not in text


def test_an_unreadable_ledger_raises(tmp_path):
    with pytest.raises(Exception):
        records_order_acts.read_orders(str(tmp_path / "missing.db"))
    with pytest.raises(ValueError):
        records_order_acts.read_orders(None)


def test_the_committed_row_now_carries_the_matter_id():
    payload = {"order": {"matter_id": MATTER, "order_ref": REF, "locations": [{}, {}]}}
    meta = act_records_order.committed_metadata(payload, {"ok": True, "ref": ORDER_ID})
    assert meta == {"location_count": 2, "order_ref": REF, "matter_id": MATTER, "vendor_order_record_id": ORDER_ID}


def test_the_verb_is_the_agent_uid_and_root_only():
    assert records_order_acts.VERB == "records_orders_placed"
    assert verbs.TABLE["records_orders_placed"].auth == frozenset({verbs.ROOT, verbs.AGENT})


def test_the_verb_dispatches_for_the_agent_uid(tmp_path):
    from workspace_broker.server import Broker

    path = _ledger(tmp_path)
    _act(path, 1, "2026-10-06T18:00:00.000Z", _place())
    broker = Broker.__new__(Broker)
    broker.gateway_pid = 4242
    broker.agent_uid = 1001
    broker.audit_db_path = str(path)
    reply = verbs.dispatch(broker, {"action": records_order_acts.VERB}, 9999, 1001)
    assert reply["ok"] is True and [o["matter_id"] for o in reply["orders"]] == [MATTER]
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, {"action": records_order_acts.VERB}, 9999, 2002)
