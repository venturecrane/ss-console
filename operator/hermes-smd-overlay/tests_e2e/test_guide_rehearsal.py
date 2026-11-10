"""The guide seat at $0: two people, a wall between them, and a canary inventory.

``test_guide_rehearsal_run.py`` runs in its own process (Hermes' plugin manager is a
process global this pytest process already gave to the harness seat) and drives
the authored guide seat through the webhook router's real intake and Hermes'
own agent loop with a scripted model: person A keeps a note and a lead and
states a preference; person B lists, reads and writes across the wall and tries
the store only code writes; A reads her own back; a scheduled turn tries to
list. Every email carries the sender's unique marker string.

Then the CANARY INVENTORY (``_canary.py``): A's marker is searched for in every
file on the seat's volume (raw bytes, and every SQLite table row by row), in the
model stub's request log, in the captured log lines and in what reached the
broker. Every hit must sit in the PURGE SET (what removing A deletes) or the
NAMED-RETAINED SET (what SMD keeps and says so). Then A is purged with the real
``workspace_broker/person_purge.py`` and the inventory runs again: nothing of A's
may remain in the purge set, nothing anywhere undeclared, and every hit of B's
must still be where it was. The last test shows the inventory can fail.

Later slices extend this rehearsal (a new store is one ``Location`` here and one
layer in the purge) and are judged by it.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests_e2e._canary import Inventory, Location, classify, inventory, search
from tests_e2e.test_guide_rehearsal_run import A_MARK, B_MARK, DAILY_LIMIT, STORES, A, B

OVERLAY = Path(__file__).resolve().parents[1]
BROKER_PACKAGE = OVERLAY.parent / "workspace_broker"


def _person_purge() -> Any:
    """``workspace_broker.person_purge`` as the seat imports it (it imports its
    sibling ``nofollow``), registered by path so ``operator/`` never joins
    ``sys.path`` and shadows the overlay's own packages."""
    if "workspace_broker" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "workspace_broker",
            BROKER_PACKAGE / "__init__.py",
            submodule_search_locations=[str(BROKER_PACKAGE)],
        )
        assert spec and spec.loader
        package = importlib.util.module_from_spec(spec)
        sys.modules["workspace_broker"] = package
        spec.loader.exec_module(package)
    return importlib.import_module("workspace_broker.person_purge")


def purge_set(slug: str) -> list[Location]:
    """Where removing the person must clear (``person_purge``'s layers)."""
    return [
        Location("seat", f"rep/*/{slug}/*", why="their directory in each walled store"),
        Location("seat", "agent-state.db", "peer_preferences", why="per-sender memory"),
        Location("seat", "requests.db", "requests", why="the request index: their requests"),
        Location("seat", "requests.db", "replies", why="the request index: replies to them"),
        Location("seat", "held_replies.db", "held_replies", why="a reply held for them"),
        Location("seat", "smd/help_state.db", why="help-pointer state, hashed"),
        Location("seat", "smd/sender_daily.db", why="the per-sender daily count, hashed"),
        Location("seat", "*.smd/sent_lines/*.json", why="labels of sends they received"),
        Location("seat", "*.smd/pre_run/*.dispatch*.json", why="scheduled sends naming them"),
    ]


#: What SMD keeps, by decision, and says so (ADR 0106 amendment, 2026-11-10).
RETAINED: list[Location] = [
    Location("seat", "profiles/*/state.db", why="session transcripts (Hermes state.db)"),
    Location("seat", "profiles/*/sessions/*", why="session transcripts (Hermes sessions/)"),
    Location("model", "*", why="what the seat sent the model provider"),
    Location("logs", "*", why="the seat's log stream"),
    Location("broker", "*", why="what reached the broker (sends, the mail vendor's copy)"),
]


def surfaces(root: Path) -> dict[str, Path]:
    return {
        "seat": root / "opt-data",
        "model": root / "model-requests.jsonl",
        "logs": root / "captured.log",
        "broker": root / "broker-requests.json",
        # Declared nowhere: a request card that carried a person's words to
        # SMD would be an undeclared hit (escalation.request_card_text: false).
        "cards": root / "cards.json",
        # Every other state file the harness points somewhere.
        "state": root / "state",
    }


def _slug(address: str) -> str:
    return _person_purge().person_slug(address)


def _take(root: Path, marker: str, address: str) -> Inventory:
    return inventory(marker, surfaces(root), purge_set(_slug(address)), RETAINED)


#: The one rehearsal of this process, shared by every test module that reads
#: it (the training and desk slices add their own test files), so the seat is
#: driven once per pytest run, not once per module.
_STATE: dict[str, Any] = {}


def guide_rehearsal() -> dict[str, Any]:
    """The rehearsal's state, run on first use and kept for the process; its
    temp seat is removed when the process exits."""
    if not _STATE:
        _STATE.update(_run_rehearsal())
        atexit.register(shutil.rmtree, _STATE["root"], True)
    return _STATE


@pytest.fixture(scope="module")
def rehearsal() -> Any:
    return guide_rehearsal()


def _run_rehearsal() -> dict[str, Any]:
    # AF_UNIX paths are capped near 104 bytes on macOS; keep the root short.
    root = Path(tempfile.mkdtemp(prefix="e2eg", dir="/tmp"))
    out = root / "evidence.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests_e2e.test_guide_rehearsal_run",
            "--root",
            str(root),
            "--out",
            str(out),
        ],
        cwd=str(OVERLAY),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=220,
        check=False,
    )
    assert proc.returncode == 0, f"the rehearsal process failed:\n{proc.stderr[-6000:]}"
    evidence = json.loads(out.read_text(encoding="utf-8"))
    state: dict[str, Any] = {"root": root, "evidence": evidence}
    state["before_a"] = _take(root, A_MARK, A)
    state["before_b"] = _take(root, B_MARK, B)
    purge = _person_purge()
    volume = root / "opt-data"
    targets = purge.PurgeTargets(
        data_root=volume,
        partition_roots=[volume / "rep" / name.removeprefix("rep-") for name in STORES],
    )
    state["purge_report"] = purge.purge(A, targets)
    state["after_a"] = _take(root, A_MARK, A)
    state["after_b"] = _take(root, B_MARK, B)
    return state


def _turn(rehearsal: dict, label: str) -> dict:
    return next(t for t in rehearsal["evidence"]["turns"] if t["label"] == label)


def _results(turn: dict, tool: str) -> list[str]:
    return [c["result"] for c in turn["calls"] if c["tool"] == tool]


# ---------------------------------------------------------------------------
# the wall, in the real agent loop
# ---------------------------------------------------------------------------


def test_every_turn_ran_its_script_through_the_router_and_the_agent(rehearsal) -> None:
    for turn in rehearsal["evidence"]["turns"]:
        if turn["router_verdict"] == {"action": "skip"}:
            continue  # dropped by the daily limit before any turn (part 2)
        assert turn["exit"].startswith("text_response"), turn
        assert turn["pending"] == 0, f"{turn['label']}: the scripted calls were never requested"
        if turn["sender"]:
            assert turn["router_verdict"]["action"] == "route_to_skill", turn["router_verdict"]
        offered = {t.removeprefix("mcp__") for t in turn["offered"]}
        assert {"record_store_list", "record_store_read", "record_store_write"} <= offered


def test_a_keeps_her_own_and_b_sees_none_of_it(rehearsal) -> None:
    a = _turn(rehearsal, "a-keeps-a-note")
    assert all('"created": true' in r for r in _results(a, "record_store_write")), a
    b = _turn(rehearsal, "b-looks-across-the-wall")
    lists = [json.loads(r) for r in _results(b, "record_store_list")]
    assert [x["records"] for x in lists] == [[], []]
    [read] = _results(b, "record_store_read")
    assert "no record named 'acme.md'" in read and A_MARK not in read
    writes = _results(b, "record_store_write")
    assert '"created": true' in writes[0]
    assert "kept by the seat itself" in writes[1]
    back = _turn(rehearsal, "a-reads-her-own")
    [listing] = [json.loads(r) for r in _results(back, "record_store_list")]
    assert [r["name"] for r in listing["records"]] == ["acme.md"]
    [mine] = _results(back, "record_store_read")
    assert A_MARK in mine and B_MARK not in mine


def test_a_scheduled_turn_reaches_no_ones_records(rehearsal) -> None:
    turn = _turn(rehearsal, "a-scheduled-turn-has-no-person")
    [result] = _results(turn, "record_store_list")
    assert "no verified sender" in result
    assert A_MARK not in result and B_MARK not in result


# ---------------------------------------------------------------------------
# the canary inventory
# ---------------------------------------------------------------------------


def test_every_place_a_marker_landed_is_declared(rehearsal) -> None:
    for name in ("before_a", "before_b"):
        inv: Inventory = rehearsal[name]
        assert inv.undeclared == [], f"{name}: undeclared hits\n{inv.report()}"
        assert inv.purge, f"{name}: the run left nothing in the purge set\n{inv.report()}"
        assert inv.retained, (
            f"{name}: the transcripts and the model log should carry it\n{inv.report()}"
        )
    layers = {(h.path, h.table) for h in rehearsal["before_a"].purge}
    for expected in (
        ("agent-state.db", "peer_preferences"),
        ("requests.db", "requests"),
        (f"rep/notes/{_slug(A)}/acme.md", None),
        (f"rep/leads/{_slug(A)}/acme.md", None),
    ):
        assert expected in layers, f"{expected} missing from\n{rehearsal['before_a'].report()}"


def test_after_the_purge_only_the_named_retained_set_holds_a(rehearsal) -> None:
    report = rehearsal["purge_report"]
    assert report.clean, [f.line() for f in report.remaining]
    after = rehearsal["after_a"]
    assert after.purge == [], f"the purge left A's words in the purge set\n{after.report()}"
    assert after.undeclared == [], f"undeclared after the purge\n{after.report()}"
    assert {h.surface for h in after.retained} >= {"seat", "model"}, after.report()


def test_the_purge_left_everything_of_b(rehearsal) -> None:
    before = {(h.surface, h.path, h.table) for h in rehearsal["before_b"].hits}
    after = {(h.surface, h.path, h.table) for h in rehearsal["after_b"].hits}
    # The WAL of a database A shared with B is checkpointed by the purge, so
    # B's bytes move from the -wal file into the database file. Rows are the
    # contract: every table hit of B's is still there.
    rows_before = {k for k in before if k[2] is not None}
    rows_after = {k for k in after if k[2] is not None}
    assert rows_before <= rows_after, sorted(rows_before - rows_after)
    assert rehearsal["after_b"].undeclared == []
    assert (Path(rehearsal["root"]) / "opt-data" / "rep" / "notes" / _slug(B) / "beta.md").exists()


def test_on_the_real_run_a_missing_declaration_fails(rehearsal) -> None:
    """Every purge-set location this run wrote to is load-bearing: drop one and
    the real run's hits there become undeclared. (This run sends no reply and
    holds none, so the reply, held-reply, sent-line and envelope layers are
    exercised by ``workspace_broker/tests/test_person_purge.py`` instead.)"""
    hits = rehearsal["before_a"].hits
    used = [loc for loc in purge_set(_slug(A)) if any(loc.covers(h) for h in hits)]
    assert len(used) == 3, [str(loc) for loc in used]  # partitions, memory, requests
    for dropped in used:
        kept = [loc for loc in purge_set(_slug(A)) if loc != dropped]
        inv = classify(hits, kept, RETAINED)
        assert inv.undeclared, f"dropping {dropped} changed nothing\n{inv.report()}"


def test_the_inventory_fails_on_an_undeclared_store(tmp_path) -> None:
    """The instrument can return the other answer: a marker in a file nobody
    declared, and a deleted row's bytes left in a database, are undeclared."""
    volume = tmp_path / "opt-data"
    (volume / "rep" / "notes" / "someone").mkdir(parents=True)
    (volume / "rep" / "notes" / "someone" / "n.md").write_text(A_MARK)
    (volume / "stray-cache").mkdir()
    (volume / "stray-cache" / "copy.txt").write_text(f"cached {A_MARK}")
    db = volume / "requests.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA secure_delete = OFF")
    conn.execute("CREATE TABLE requests (sender TEXT, request_text TEXT)")
    conn.execute("INSERT INTO requests VALUES (?, ?)", (A, f"keep {A_MARK}" + " pad" * 200))
    conn.commit()
    conn.execute("DELETE FROM requests")
    conn.commit()
    conn.close()
    surf = {"seat": volume}
    inv = classify(search(A_MARK, surf), purge_set("someone"), RETAINED)
    undeclared = {h.path for h in inv.undeclared}
    assert "stray-cache/copy.txt" in undeclared, inv.report()
    assert "requests.db" in undeclared, f"a deleted row's bytes must not pass\n{inv.report()}"
    assert {h.path for h in inv.purge} == {"rep/notes/someone/n.md"}


# ---------------------------------------------------------------------------
# replies that arrive, or say plainly they did not (part 2, #3946)
# ---------------------------------------------------------------------------


def _held_line() -> str:
    spec = importlib.util.spec_from_file_location(
        "_e2e_held_notice", OVERLAY / "plugins" / "hermes-smd-reply" / "held_notice.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.FIXED_LINE


def _daily_notice() -> str:
    spec = importlib.util.spec_from_file_location(
        "_e2e_daily_limit", OVERLAY / "plugins" / "hermes-smd-webhook-router" / "daily_limit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.NOTICE


def test_a_held_reply_ends_in_the_fixed_line(rehearsal) -> None:
    held = _turn(rehearsal, "a-reply-is-held")
    assert held["replies"] == [_held_line()], held
    assert "95,000" not in json.dumps(held["replies"])
    for label in ("a-keeps-a-note", "a-reads-her-own"):
        assert _turn(rehearsal, label)["replies"] == ["Done."]


def test_past_the_daily_limit_one_notice_then_silence(rehearsal) -> None:
    admitted = [
        t
        for t in rehearsal["evidence"]["turns"]
        if t["sender"] == A and t["router_verdict"] != {"action": "skip"}
    ]
    assert len(admitted) == DAILY_LIMIT
    first = _turn(rehearsal, "a-over-the-limit")
    second = _turn(rehearsal, "a-over-again")
    assert first["router_verdict"] == second["router_verdict"] == {"action": "skip"}
    assert first["replies"] == [_daily_notice()]
    assert second["replies"] == []


def test_request_cards_carry_no_words(rehearsal) -> None:
    cards = rehearsal["evidence"]["cards"]
    assert cards, "the seat posted no request cards; the check below would be empty"
    for card in cards:
        assert card["text_withheld"] is True
        assert card.get("request_text") is None and card.get("reply_text") is None
    assert all(h.surface != "cards" for h in rehearsal["before_a"].hits)


def test_the_triage_flags_the_held_reply_and_the_limited_sender(rehearsal) -> None:
    """The hourly work-health triage (operator/bin/work-health.py) reads the
    seat's ledger. Fed this run's own rows, an hour past the no-reply window,
    it raises an item for the held reply and for each limited email."""
    from datetime import datetime, timedelta, timezone

    operator = str(OVERLAY.parent)
    if operator not in sys.path:
        sys.path.insert(0, operator)
    from bin.lib import work_health as wh

    rows = rehearsal["evidence"]["ledger_rows"]
    cfg = wh.load_config()
    latest = max(wh.parse_ts(r["ts"]) for r in rows)
    now = latest + timedelta(minutes=cfg.no_reply_for("smd-guide") + 60)
    items = wh.request_items("smd-guide", rows, cfg, now.astimezone(timezone.utc))
    reasons = sorted(
        m.group(1)
        for i in items
        if i.kind == "reply_held" and (m := re.search(r"held \(([a-z_]+)\)", i.what))
    )
    assert reasons == ["sender_daily_limit", "sender_daily_limit", "trust_gate_refused"], [
        (i.kind, i.what) for i in items
    ]
    assert isinstance(now, datetime)
