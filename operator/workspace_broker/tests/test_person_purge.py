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
import os
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from workspace_broker import person_orphans, person_purge
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
    audit_contract = _overlay("shared/audit_contract.py", "_purge_audit_contract")
    daily = _overlay("plugins/hermes-smd-webhook-router/daily_limit.py", "_purge_daily_limit")
    key = audit_contract.sender_key(person)
    assert daily.decide(key, 5, path=data / "smd" / "sender_daily.db").admit
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
    "per-sender daily count (sender_daily)",
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
    assert "STILL on this seat" in out and "held replies" in out


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


# ---------------------------------------------------------------------------
# Could not look is never absent (PR #3956 review, finding 1)
# ---------------------------------------------------------------------------


def _run(seat: dict, capsys, *argv: str) -> tuple[int, str]:
    """The CLI as a real invocation runs it (row removed in-process here: the
    broker child needs root to drop), with its stdout and stderr."""
    tail = ["--data-root", str(seat["data"]), "--config", str(seat["config"])]
    code = cli_main([*argv, *tail], store=seat["store"], purge_targets=seat["targets"], isolate=False)
    out = capsys.readouterr()
    return code, out.out + out.err


def _assert_failed_closed(seat: dict, code: int, out: str, layer: str) -> None:
    assert code == 3, out
    assert f"could not examine {layer}" in out, out
    assert "found nothing" not in out and "nothing of theirs found" not in out, out
    assert A in seat["store"].enrolled_addresses(), "the row went although a layer could not be examined"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 directory; the broker uid on a seat cannot")
@pytest.mark.parametrize(
    ("closed", "layer"),
    [
        ("opt-data", "the volume"),
        ("opt-data/smd", "help state"),
        ("opt-data/rep/leads", "partitioned store directory"),
        ("opt-data/profiles/guide/.smd/pre_run", "scheduled-send envelope"),
    ],
)
def test_a_directory_it_may_not_read_fails_closed_before_the_row_goes(seat: dict, capsys, closed, layer) -> None:
    """The broker-uid run: a directory the uid may not read. Python 3.14's
    ``Path.exists`` answers False there; this must be "could not examine",
    exit 3, the enrollment row and every file untouched."""
    _leave_traces(seat, A, A_MARK)
    target = seat["data"].parent / closed
    target.chmod(0)
    try:
        for argv in (["purge", "--probe", A], ["remove", A], ["purge", A]):
            code, out = _run(seat, capsys, *argv)
            _assert_failed_closed(seat, code, out, layer)
    finally:
        target.chmod(0o755)
    assert _layers(person_purge.probe(A, seat["targets"])) == EVERY_LAYER, "something was removed"


def _deny(monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    """``os.stat`` and ``os.lstat`` raise PermissionError for one database,
    whichever of them the code under test asks."""
    for name in ("stat", "lstat"):
        real = getattr(os, name)

        def denied(path, *args, _real=real, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)) and os.fsdecode(path).endswith(suffix):
                raise PermissionError(13, "Permission denied", os.fsdecode(path))
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(os, name, denied)


@pytest.mark.parametrize("argv", [["purge", "--probe", A], ["remove", A], ["purge", A]])
def test_a_database_it_may_not_stat_fails_closed_before_the_row_goes(seat: dict, capsys, monkeypatch, argv) -> None:
    _leave_traces(seat, A, A_MARK)
    _deny(monkeypatch, "requests.db")
    code, out = _run(seat, capsys, *argv)
    _assert_failed_closed(seat, code, out, "request index")
    monkeypatch.undo()
    assert _layers(person_purge.probe(A, seat["targets"])) == EVERY_LAYER, "something was removed"


# ---------------------------------------------------------------------------
# Root never follows a link the agent could plant (PR #3956 review, finding 2)
# ---------------------------------------------------------------------------


def _outside(tmp_path: Path) -> Path:
    precious = tmp_path / "outside-the-volume" / "precious"
    precious.parent.mkdir()
    precious.write_bytes(b"PRECIOUS, not a database file" * 300)
    return precious


@pytest.mark.parametrize("sidecar", ["-shm", "-wal"])
def test_a_planted_sidecar_link_is_refused_and_its_target_untouched(seat: dict, capsys, tmp_path, sidecar) -> None:
    _leave_traces(seat, A, A_MARK)
    precious = _outside(tmp_path)
    before = (precious.read_bytes(), precious.stat().st_uid, precious.stat().st_gid, precious.stat().st_mode)
    side = Path(f"{seat['data'] / 'held_replies.db'}{sidecar}")
    side.unlink(missing_ok=True)
    side.symlink_to(precious)
    for argv in (["purge", "--probe", A], ["remove", A]):
        code, out = _run(seat, capsys, *argv)
        _assert_failed_closed(seat, code, out, "held replies")
        assert "symbolic link" in out, out
        assert (
            precious.read_bytes(),
            *[getattr(precious.stat(), k) for k in ("st_uid", "st_gid", "st_mode")],
        ) == before
        assert side.is_symlink() and side.readlink() == precious


def test_a_link_in_a_partitioned_store_is_removed_as_a_link(seat: dict, tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "kept").mkdir(parents=True)
    (elsewhere / "kept" / "keep.md").write_text("not theirs")
    seat["leads"].mkdir(parents=True)
    (seat["leads"] / person_purge.person_slug(A)).symlink_to(elsewhere / "kept")
    report = person_purge.purge(A, seat["targets"])
    assert report.clean, [f.line() for f in report.remaining]
    assert not (seat["leads"] / person_purge.person_slug(A)).is_symlink()
    assert (elsewhere / "kept" / "keep.md").read_text() == "not theirs"


def test_a_partition_root_that_is_a_link_is_not_followed(seat: dict, capsys, tmp_path) -> None:
    """The agent owns the volume, so it can swap a store's directory for a link
    to anywhere. Root must refuse it, never delete through it."""
    _leave_traces(seat, A, A_MARK)
    elsewhere = tmp_path / "elsewhere"
    seat["leads"].rename(elsewhere)
    seat["leads"].symlink_to(elsewhere)
    theirs = elsewhere / person_purge.person_slug(A) / "acme.md"
    assert theirs.is_file()
    code, out = _run(seat, capsys, "purge", A)
    assert code == 3, out
    assert "could not examine partitioned store directory" in out and "symbolic link" in out, out
    assert theirs.is_file(), "root deleted through a link"
    assert (seat["notes"] / person_purge.person_slug(A)).is_dir(), "a refused run removed something"


def test_a_delete_that_fails_is_reported_not_raised(seat: dict, monkeypatch) -> None:
    """An OSError while deleting (here: unlinking a sent-line file) is a
    "could not purge" finding, and the run is never clean."""
    _leave_traces(seat, A, A_MARK)
    real_unlink = os.unlink

    def refuse(path, *args, **kwargs):
        if str(path).endswith(".json"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse)
    report = person_purge.purge(A, seat["targets"])
    assert not report.clean
    assert any(f.line().startswith("could not purge sent-line labels") for f in report.remaining), [
        f.line() for f in report.remaining
    ]


def test_the_database_work_runs_in_the_owner_child(seat: dict, monkeypatch) -> None:
    """The forked child path (a uid drop needs root; here the child keeps this
    uid) purges exactly as in-process does."""
    monkeypatch.setattr(person_purge, "_ALWAYS_FORK", True)
    _leave_traces(seat, A, A_MARK)
    _leave_traces(seat, B, B_MARK)
    report = person_purge.purge(A, seat["targets"])
    assert report.clean, [f.line() for f in report.remaining]
    assert _layers(report.removed) == EVERY_LAYER
    assert _raw_hits(seat["data"], A_MARK) == []
    assert _raw_hits(seat["data"], B_MARK)


@pytest.mark.parametrize("name", ["sqlite_master", "requests; DROP TABLE requests", "", "Requests", None])
def test_a_table_outside_the_allowlist_is_refused(tmp_path: Path, name) -> None:
    """Every table name reaches SQL only through the allowlist: the real delete
    and column readers refuse any other name before a statement runs."""
    conn = sqlite3.connect(str(tmp_path / "x.db"))
    conn.execute("CREATE TABLE requests (sender TEXT)")
    conn.execute("INSERT INTO requests VALUES ('a@example.test')")
    try:
        with pytest.raises(ValueError, match="not a table this purge knows"):
            person_purge._delete_rowids(conn, name, [1])
        with pytest.raises(ValueError, match="not a table this purge knows"):
            person_purge._columns(conn, name)
        assert conn.execute("SELECT count(*) FROM requests").fetchone() == (1,)
    finally:
        conn.close()


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


def test_orphan_partitions_are_listed_by_slug_and_count_only(seat: dict, capsys) -> None:
    """The hourly job's question: a walled-store directory nobody listed owns.
    Slugs and counts only, never content; the command exits 3 while any exist."""
    _leave_traces(seat, A, A_MARK)
    _leave_traces(seat, "gone@example.test", "CANARY-ORPHAN-1")
    found = person_orphans.orphan_partitions(seat["targets"], [A, B]).found
    slug = person_purge.person_slug("gone@example.test")
    assert found == {str(seat["notes"]): [slug], str(seat["leads"]): [slug]}
    assert person_orphans.orphan_partitions(seat["targets"], [A, B, "gone@example.test"]).clean
    argv = ["orphans", "--data-root", str(seat["data"]), "--config", str(seat["config"])]
    assert cli_main(argv, store=seat["store"]) == 3
    out = capsys.readouterr().out
    assert slug in out and "CANARY-ORPHAN-1" not in out
    assert cli_main(["purge", "gone@example.test", *argv[1:]], store=seat["store"], isolate=False) == 0
    capsys.readouterr()
    assert cli_main(argv, store=seat["store"]) == 0


def test_orphans_refuses_when_it_cannot_read_who_is_listed(seat: dict) -> None:
    argv = ["orphans", "--data-root", str(seat["data"]), "--config", str(seat["config"].parent / "x.yaml")]
    assert cli_main(argv, store=seat["store"]) == 2


def test_orphans_never_reads_a_planted_link_as_a_store(seat: dict) -> None:
    """A store root that is a link is refused (could not examine), never
    followed and never reported as holding no orphans."""
    seat["leads"].parent.mkdir(parents=True, exist_ok=True)
    elsewhere = seat["data"].parent / "elsewhere"
    elsewhere.mkdir()
    seat["leads"].symlink_to(elsewhere, target_is_directory=True)
    result = person_orphans.orphan_partitions(seat["targets"], [A, B])
    assert not result.clean and result.problems


@pytest.mark.parametrize(
    ("words", "names_a"),
    [
        (f"Avery Example ({A})\n- Readiness: ready", True),
        (f"Progress for {A}.", True),
        ("Progress for REP-A@Example.Test today", True),
        (f"Progress for x{A}", False),
        (f"Progress for b{A}", False),
        (f"Progress for {A}ing", False),
        (f"Progress for {A}.au", False),
        (f"Progress for {A}-old", False),
        ("Progress for rep-a@example.tester", False),
    ],
)
def test_an_entry_names_a_person_only_by_their_whole_address(words: str, names_a: bool) -> None:
    """``a@x.com`` is never ``aa@x.com``, ``data@x.com`` or ``a@x.com.au``: an
    envelope entry is purged for A only when its words carry A's whole address."""
    entry = {"recipients": ["admin@example.test"], "subject": "Representative progress", "full_body": words}
    assert person_purge._envelope_mentions(entry, A) is names_a


def test_purge_clears_a_representatives_progress_and_the_report_that_names_them(seat: dict) -> None:
    """Training (ss-console #3946): a representative's progress record lives in
    a store walled per person (``rep-progress``), and the weekly report to SMD's
    administrators names each representative in its BODY, never as a recipient.
    Purging A removes A's progress directory and every report entry naming A;
    B's progress stays, and the probe reads both before and after."""
    progress = seat["data"] / "rep" / "progress"
    for person in (A, B):
        directory = progress / person_purge.person_slug(person)
        directory.mkdir(parents=True)
        (directory / "progress.json").write_text('{"schema":1,"lessons_complete":["l01"]}', encoding="utf-8")
    report = {
        "recipients": ["admin@example.test"],
        "cc": [],
        "subject": "Representative progress and readiness",
        "full_body": f"Hello,\n\nAvery Example ({A})\n- Readiness: ready\n\n{B}\n- Readiness: not ready yet",
        "skeleton_body": "Hello,\n\nThis week's representative progress report could not be sent in full.",
    }
    other = {**report, "full_body": f"{B}\n- Readiness: not ready yet"}
    envelope = seat["home"] / ".smd" / "pre_run" / "rep-training.dispatch.consumed.json"
    envelope.parent.mkdir(parents=True, exist_ok=True)
    envelope.write_text(json.dumps({"skill": "rep-training", "dispatches": [report, other]}), encoding="utf-8")
    targets = person_purge.PurgeTargets(data_root=seat["data"], partition_roots=[progress])

    before = person_purge.probe(A, targets)
    assert {"partitioned store directory", "scheduled-send envelope"} <= _layers(before)

    result = person_purge.purge(A, targets)

    assert result.clean, [f.line() for f in result.remaining]
    assert not (progress / person_purge.person_slug(A)).exists()
    assert (progress / person_purge.person_slug(B) / "progress.json").is_file()
    kept = json.loads(envelope.read_text(encoding="utf-8"))["dispatches"]
    assert kept == [other] and A not in envelope.read_text(encoding="utf-8")
