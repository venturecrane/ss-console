"""Removing a person removes their data, and the probe proves it (ss-console #3946).

The seat is a temp volume written by the overlay's OWN writers (the request
index, the held-reply store, the help-pointer state, the per-sender memory
store through ``D1Client``, the sent-line labels), so what the purge is tested
against is the shape those modules really write, not a guess at it. Two people,
each with a unique marker in everything they leave behind. Purge one; the probe
must find nothing of theirs and everything of the other's, and a raw byte scan
of every file on the volume must not find the purged person's marker (deleted
SQLite rows are overwritten, not left on a free page).

The probe is shown able to fail: planted leftovers make it, and the command
line, report them and exit nonzero.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from workspace_broker import person_purge
from workspace_broker import sender_enrollment as se
from workspace_broker.sender_enrollment_cli import main as cli_main

OVERLAY = Path(__file__).resolve().parents[2] / "hermes-smd-overlay"
A = "rep-a@example.test"
B = "rep-b@example.test"
A_MARK = "CANARY-A-purge-91xq"
B_MARK = "CANARY-B-purge-47mz"


def _overlay(rel: str, name: str) -> ModuleType:
    """An overlay module by path (each is stdlib only), with the overlay root on
    ``sys.path`` so its ``shared.*`` imports resolve."""
    if str(OVERLAY) not in sys.path:
        sys.path.insert(0, str(OVERLAY))
    spec = importlib.util.spec_from_file_location(name, OVERLAY / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    data = tmp_path / "opt-data"
    home = data / "profiles" / "guide"
    home.mkdir(parents=True)
    notes = data / "rep" / "notes"
    leads = data / "rep" / "leads"
    config = tmp_path / "customer.yaml"
    config.write_text(
        "customer_id: guide-test\n"
        "record_stores:\n"
        f"  - name: rep-notes\n    path: {notes}\n    partition: sender\n"
        f"  - name: rep-leads\n    path: {leads}\n    partition: sender\n"
        f"  - name: shared-ref\n    path: {data / 'ref'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("SMD_SENT_LINES_DIR", raising=False)
    store = se.EnrollmentStore(tmp_path / "enrollment")
    with store._locked():
        store._write_enrolled([{"address": a, "label": "t", "code_id": "c_1"} for a in (A, B)])
    return {
        "data": data,
        "home": home,
        "notes": notes,
        "leads": leads,
        "config": config,
        "store": store,
        "targets": person_purge.PurgeTargets(data_root=data, partition_roots=[notes, leads]),
    }


def _leave_traces(seat: dict, person: str, mark: str) -> None:
    """Everything a person leaves on a guide seat, through the real writers."""
    data, home = seat["data"], seat["home"]
    slug = person_purge.person_slug(person)
    for root in (seat["notes"], seat["leads"]):
        (root / slug).mkdir(parents=True, exist_ok=True)
        (root / slug / "acme.md").write_text(f"lead notes {mark}", encoding="utf-8")
    request_index = _overlay("shared/request_index.py", "_purge_request_index")
    mid = f"<{mark}@example.test>"
    db = str(data / "requests.db")
    assert request_index.record_inbound(
        vendor_message_id=mid, sender=f"Rep <{person}>", subject=f"re {mark}", body=f"please keep {mark}", path=db
    )
    request_index.record_reply_opening(mid, f"Kept: {mark}", sent_message_id=f"<sent-{mark}@x>", path=db)
    held = _overlay("plugins/hermes-smd-reply/held_store.py", "_purge_held_store")
    store = held.HeldReplyStore(str(data / "held_replies.db"))
    store.enqueue(
        sender=person,
        sender_class="internal",
        adapter="agentmail",
        inbox_id="g",
        message_id=mid,
        send_text=f"held {mark}",
        send_html="",
        body_digest="d",
        hold_reason="rate_limited_per_sender",
    )
    store.close()
    help_state = _overlay("plugins/hermes-smd-initiation/help_state.py", "_purge_help_state")
    assert help_state.record(help_state.KIND_POINTER, person, path=data / "smd" / "help_state.db")
    d1 = _overlay("shared/d1_client.py", "_purge_d1")
    schemas = _overlay("plugins/hermes-smd-peer-memory/schemas.py", "_purge_peer_schemas")
    client = d1.D1Client("SMD_D1_AGENT_STATE_BINDING", "guide-test", db_path=str(data / "agent-state.db"))
    for ddl in schemas.PEER_MEMORY_DDLS:
        client.execute(ddl)
    client.execute(
        "INSERT INTO peer_preferences (id, customer_slug, peer_id, preference, source) VALUES (?, ?, ?, ?, ?)",
        f"pp-{mark}",
        "guide-test",
        person,
        f"prefers bullets {mark}",
        "stated",
    )
    sent_lines = _overlay("shared/sent_lines.py", "_purge_sent_lines")
    ref = (mark.encode().hex() * 4)[:32]
    assert sent_lines.record(ref, {1: f"Call Acme {mark}"}, recipients=[person])
    envelope = home / ".smd" / "pre_run" / "rep-desk.dispatch.consumed.json"
    envelope.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(envelope.read_text()) if envelope.exists() else {"dispatches": []}
    raw["dispatches"].append({"recipients": [person], "subject": "today", "body": f"due: {mark}"})
    envelope.write_text(json.dumps(raw), encoding="utf-8")


def _raw_hits(root: Path, needle: str) -> list[str]:
    """Every file under ``root`` whose BYTES contain ``needle`` (a deleted row
    left on a SQLite free page counts)."""
    want = needle.encode()
    return sorted(str(p) for p in root.rglob("*") if p.is_file() and want in p.read_bytes())


def _layers(findings: list[person_purge.Finding]) -> set[str]:
    return {f.layer for f in findings}


EVERY_LAYER = {
    "partitioned store directory",
    "per-sender memory (peer_preferences)",
    "request index (requests)",
    "request index (replies)",
    "held replies (held_replies)",
    "help state (help_senders)",
    "help state (help_events)",
    "sent-line labels",
    "scheduled-send envelope",
}


def test_purge_removes_one_person_and_leaves_the_other(seat: dict) -> None:
    _leave_traces(seat, A, A_MARK)
    _leave_traces(seat, B, B_MARK)
    targets = seat["targets"]
    before_a = person_purge.probe(A, targets)
    before_b = person_purge.probe(B, targets)
    assert _layers(before_a) == EVERY_LAYER, "the probe must see every layer before it can see them gone"
    assert _raw_hits(seat["data"], A_MARK)

    report = person_purge.purge(A, targets)

    assert report.clean, [f.line() for f in report.remaining]
    assert _layers(report.removed) == EVERY_LAYER
    assert person_purge.probe(A, targets) == []
    after_b = person_purge.probe(B, targets)
    assert [(f.layer, f.location, f.count) for f in after_b] == [(f.layer, f.location, f.count) for f in before_b]
    assert _raw_hits(seat["data"], A_MARK) == [], "a purged row's bytes are still on the volume"
    assert _raw_hits(seat["data"], B_MARK), "B's data must be untouched"
    envelope = json.loads((seat["home"] / ".smd/pre_run/rep-desk.dispatch.consumed.json").read_text())
    assert [d["recipients"] for d in envelope["dispatches"]] == [[B]]


def test_the_probe_reports_planted_leftovers_and_exits_nonzero(seat: dict, capsys) -> None:
    _leave_traces(seat, B, B_MARK)
    targets = seat["targets"]
    assert person_purge.probe(A, targets) == []
    # Plant one leftover in a directory and one in a table.
    slug = person_purge.person_slug(A)
    (seat["leads"] / slug).mkdir(parents=True)
    (seat["leads"] / slug / "left.md").write_text(A_MARK)
    conn = sqlite3.connect(str(seat["data"] / "held_replies.db"))
    conn.execute(
        "INSERT INTO held_replies (created_at, sender, adapter, message_id, hold_reason) VALUES (1, ?, 'x', 'm', 'r')",
        (A,),
    )
    conn.commit()
    conn.close()
    found = person_purge.probe(A, targets)
    assert _layers(found) == {"partitioned store directory", "held replies (held_replies)"}
    argv = ["purge", "--probe", A, "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    assert cli_main(argv, store=seat["store"]) == 3
    out = capsys.readouterr().out
    assert "still on this seat" in out and "held replies" in out


def test_a_probe_that_cannot_read_the_config_is_never_clean(seat: dict) -> None:
    argv = ["purge", "--probe", "orphan@example.test", "--data-root", str(seat["data"])]
    assert cli_main([*argv, "--config", str(seat["config"])], store=seat["store"]) == 0
    assert cli_main([*argv, "--config", str(seat["config"].parent / "missing.yaml")], store=seat["store"]) == 3


@pytest.mark.parametrize("isolate", [False, True])
def test_remove_drops_the_row_then_purges_and_probes(seat: dict, isolate: bool) -> None:
    """``isolate=True`` is the root path's fork: a child removes the row (as the
    broker uid on a seat; a non-root test cannot drop, so the child keeps this
    uid) and reports through a pipe, the parent purges."""
    _leave_traces(seat, A, A_MARK)
    _leave_traces(seat, B, B_MARK)
    argv = ["remove", A, "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    code = cli_main(
        argv,
        store=seat["store"],
        purge_targets=seat["targets"],
        isolate=isolate,
    )
    assert code == 0
    assert A not in seat["store"].enrolled_addresses()
    assert B in seat["store"].enrolled_addresses()
    assert person_purge.probe(A, seat["targets"]) == []
    assert _raw_hits(seat["data"], A_MARK) == []
    assert person_purge.probe(B, seat["targets"])


def test_purge_clears_an_address_that_is_no_longer_enrolled(seat: dict) -> None:
    orphan = "gone@example.test"
    _leave_traces(seat, orphan, A_MARK)
    argv = ["purge", orphan, "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    assert cli_main(argv, store=seat["store"], isolate=False) == 0
    assert person_purge.probe(orphan, seat["targets"]) == []
    # `remove` of an address that is not enrolled still purges, and says so.
    _leave_traces(seat, orphan, A_MARK)
    remove = ["remove", orphan, "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    assert cli_main(remove, store=seat["store"], purge_targets=seat["targets"], isolate=False) == 1
    assert person_purge.probe(orphan, seat["targets"]) == []


def test_a_still_enrolled_address_fails_the_probe(seat: dict) -> None:
    argv = ["purge", "--probe", A, "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    assert cli_main(argv, store=seat["store"]) == 3


def test_the_purge_slug_is_the_overlays_slug() -> None:
    prefs = _overlay("shared/person_prefs.py", "_purge_person_prefs")
    addresses = [
        A,
        B,
        "REP-A@Example.Test",
        "a.b@x.com",
        "a-b@x.com",
        "rep-а@example.test",
        "élodie@café.example",
        "x" * 80 + "@long.example",
        "+tag@example.test",
        "o'brien@example.test",
    ]
    for address in addresses:
        assert person_purge.person_slug(address) == prefs.person_slug(address), address
        assert person_purge.normalize_person_address(address) == prefs.normalize_person_address(address)
    for bad in ("", "nobody", "@x.com", "a@b", "a@@b.com"):
        assert person_purge.normalize_person_address(bad) is None
    sent_lines = _overlay("shared/sent_lines.py", "_purge_sent_lines_parity")
    assert sent_lines.recipient_digest(" Rep-A@Example.Test ") == person_purge.recipient_digest(A)
