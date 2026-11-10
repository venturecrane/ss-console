"""A store walled per person: ``partition: sender`` (2026-11-10, ss-console #3946).

THE CONTRACT. On a guide seat, a sales representative keeps notes and leads only
they can see. Each person's records live in their own directory under the store,
named by code from the turn's verified sender. These tests drive the REAL pieces
on the path a seat runs: a ``customer.yaml`` read by ``CustomerConfig``, the
broker's enrolled list read by ``shared.sender_enrollment``, the webhook
router's own origin derivation, the inbound plugin's real ``pre_llm_call`` bind
on an agent thread, and the record-store plugin's real handlers called the way
Hermes' ``registry.dispatch`` calls them: ``handler(args, task_id=...,
session_id=...)`` on a fresh worker thread.

WHY ON REAL THREADS, AFTER A CRON TURN. ``pre_tool_call`` carries no session id
and the fallback resolver is thread-local; once a cron turn and an email turn
have run on their own threads, a thread that resolves without an id gets
"ambiguous", and a wall built on that fallback would either refuse everyone or,
worse, guess. Two people interleaved on parked worker threads, after a cron turn,
is the shape in which a thread-local fallback cannot pass by accident.

Each test was run against the record store as it was on origin/main (b5f4cbb22),
where ``partition`` was an unknown key and the store was one shared directory:
every wall test failed there (B's list showed A's record, B read A's record, a
cron turn listed everything). See the PR body for the run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from shared import inbound, inbound_message, provenance, record_store
from shared.person_prefs import person_slug
from tests.conftest import FakePluginContext, load_plugin
from tests.test_turn_origin_tool_thread import _hermes_render, _on_thread, _unpark

A = "rep-a@example.test"
B = "rep-b@example.test"
ADMIN = "admin@example.test"
CRON = "cron_abc123def456_20261110_090000"
SA = "20261110_160101_aaaa0001"
SB = "20261110_160102_bbbb0002"
NOTES = "rep-notes"
PROGRESS = "rep-progress"
A_MARK = "CANARY-A-7f3k9q"
B_MARK = "CANARY-B-2m8x4w"


def _seat_yaml(volume: Path) -> str:
    return dedent(
        f"""
        customer_id: guide-test
        seat:
          kind: internal
          product: operator
          role: guide
        sender_enrollment:
          enabled: true
        scope:
          inbound_allow_from: [{ADMIN}]
          admins: [{ADMIN}]
        record_stores:
          - name: {NOTES}
            path: {volume}/rep/notes
            partition: sender
          - name: {PROGRESS}
            path: {volume}/rep/progress
            partition: sender
            managed: true
        """
    ).strip()


def _enroll(path: Path, *addresses: str) -> None:
    entries = [
        {"address": a, "label": "t", "code_id": "c_1", "enrolled_at": "x"} for a in addresses
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_ctx: FakePluginContext):
    volume = tmp_path / "opt-data"
    volume.mkdir()
    # The seat's volume root is /opt/data; a test's is its own temp directory.
    # The path rule itself (shared.record_store.path_problem) is what runs.
    monkeypatch.setattr(record_store, "VOLUME_ROOT", volume)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_seat_yaml(volume), encoding="utf-8")
    enrolled = tmp_path / "enrolled.json"
    _enroll(enrolled, A, B)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    monkeypatch.setenv("SMD_SENDER_ENROLLMENT_ENROLLED", str(enrolled))
    provenance._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()
    plugin = load_plugin("hermes-smd-record-store")
    plugin.register(fake_ctx)
    assert set(fake_ctx.tools) == {"record_store_list", "record_store_read", "record_store_write"}
    seat = {
        "plugin": plugin,
        "tools": fake_ctx.tools,
        "inbound": load_plugin("hermes-smd-inbound"),
        "router": load_plugin("hermes-smd-webhook-router"),
        "volume": volume,
        "enrolled": enrolled,
        "notes": volume / "rep" / "notes",
    }
    yield seat
    _unpark()
    provenance._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


# ---------------------------------------------------------------------------
# the live order: a cron turn, emails arriving, agent turns, tool threads
# ---------------------------------------------------------------------------


def _cron_turn() -> None:
    _on_thread(provenance.note_session, CRON)


def _email_turn(seat: dict, sender: str, session: str, mid: str, body: str) -> None:
    """The router derives the origin from the seam DTO and records it under the
    EMPTY dispatch session the live path carries; the agent thread's
    ``pre_llm_call`` (trust's session note, then the inbound plugin's bind)
    binds it to ``session`` from the prompt's own message id."""
    from bootstrap.translate import _INBOUND_EMAIL_PROMPT

    payload = {
        "source": "agentmail",
        "event_type": "message.received",
        "message": {
            "inbox_id": "guide@agentmail.to",
            "message_id": mid,
            "from": sender,
            "subject": "notes",
            "text": body,
        },
    }
    dto = inbound_message.normalize_inbound("agentmail", payload)
    origin = seat["router"]._origin_from_dto(dto, content=body)
    assert origin is not None and origin.sender_address == sender
    inbound.SESSION_INBOUND_ORIGIN.record("", origin)
    prompt = _hermes_render(_INBOUND_EMAIL_PROMPT, payload)

    def pre_llm_call() -> None:
        provenance.note_session(session)
        seat["inbound"].on_pre_llm_call(
            session_id=session,
            task_id=f"task-{session}",
            turn_id="turn-1",
            user_message=prompt,
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="webhook",
            parent_session_id="",
            sender_id="webhook:agentmail",
        )

    _on_thread(pre_llm_call)
    bound = inbound.SESSION_INBOUND_ORIGIN.bound_this_turn(session)
    assert bound is not None and bound.sender_address == sender


def _call(seat: dict, tool: str, args: dict, session: str | None) -> dict[str, Any]:
    """``registry.dispatch``'s call shape, on a fresh worker thread. Returns the
    parsed result, or ``{"error": message}`` when the handler raised (what the
    model is handed as ``Tool execution failed: ...``)."""
    handler = seat["tools"][tool]["handler"]
    kwargs: dict[str, Any] = {"task_id": "task-x", "user_task": None}
    if session is not None:
        kwargs["session_id"] = session
    try:
        out = _on_thread(handler, args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the model sees the message
        return {"error": str(exc)}
    return json.loads(out)


def _two_people(seat: dict) -> None:
    _cron_turn()
    _email_turn(seat, A, SA, "<a-1@example.test>", f"note for later {A_MARK}")
    _email_turn(seat, B, SB, "<b-1@example.test>", f"my lead {B_MARK}")


def _files(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


# ---------------------------------------------------------------------------
# the wall
# ---------------------------------------------------------------------------


def test_each_person_lists_reads_and_writes_only_their_own(seat) -> None:
    _two_people(seat)
    wrote = _call(
        seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": f"{A_MARK}"}, SA
    )
    assert wrote.get("created") is True, wrote
    # Interleaved: B in the middle of A's turn.
    assert _call(seat, "record_store_list", {"store": NOTES}, SB)["records"] == []
    b_read = _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SB)
    assert "no record named 'acme.md'" in b_read.get("error", ""), b_read
    assert A_MARK not in json.dumps(b_read)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": B_MARK}, SB)
    a_list = _call(seat, "record_store_list", {"store": NOTES}, SA)
    assert [r["name"] for r in a_list["records"]] == ["acme.md"]
    a_read = _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SA)
    assert a_read["content"] == A_MARK
    b_back = _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SB)
    assert b_back["content"] == B_MARK
    # On disk: two directories, each person's own, named by code.
    assert sorted(p.name for p in seat["notes"].iterdir()) == sorted(
        [person_slug(A), person_slug(B)]
    )
    assert (seat["notes"] / person_slug(A) / "acme.md").read_text() == A_MARK


@pytest.mark.parametrize(
    "name",
    [
        "../{a}/acme.md",
        "..",
        "../acme.md",
        "{a}/acme.md",
        "/{notes}/{a}/acme.md",
        "/etc/passwd",
        "{a}",
        "{A}/acme.md",
        "acme.md/../../{a}/acme.md",
        "․․/{a}/acme.md",
        "．．／{a}／acme.md",
        ".acme.md",
        "acme.md\x00.md",
    ],
)
def test_no_name_lands_in_another_persons_directory(seat, name: str) -> None:
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    a_dir = seat["notes"] / person_slug(A)
    before = _files(a_dir)
    target = name.format(a=person_slug(A), A=person_slug(A).upper(), notes=seat["notes"])
    for overwrite in (False, True):
        out = _call(
            seat,
            "record_store_write",
            {"store": NOTES, "name": target, "content": B_MARK, "overwrite": overwrite},
            SB,
        )
        read = _call(seat, "record_store_read", {"store": NOTES, "name": target}, SB)
        assert A_MARK not in json.dumps(read)
        assert out.get("error") or out.get("name") == target
    assert _files(a_dir) == before, "B's write changed A's directory"
    for path in seat["notes"].rglob("*"):
        if path.is_file() and path.parent != seat["notes"] / person_slug(B):
            assert path.parent == a_dir, f"a record landed outside both walls: {path}"


def test_a_lookalike_or_case_changed_address_is_never_another_persons_directory() -> None:
    lookalike = "rep-а@example.test"  # Cyrillic a
    assert person_slug(lookalike) != person_slug(A)
    assert person_slug("REP-A@EXAMPLE.TEST") == person_slug(A)  # the same mailbox
    assert person_slug("rep.a@example.test") != person_slug(A)
    assert person_slug("rep_a@example.test") != person_slug(A)


def test_a_symlink_in_ones_own_directory_reaches_nothing(seat) -> None:
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    _call(seat, "record_store_write", {"store": NOTES, "name": "mine.md", "content": B_MARK}, SB)
    b_dir = seat["notes"] / person_slug(B)
    os.symlink(seat["notes"] / person_slug(A) / "acme.md", b_dir / "link.md")
    read = _call(seat, "record_store_read", {"store": NOTES, "name": "link.md"}, SB)
    assert "outside its store" in read.get("error", ""), read
    wrote = _call(
        seat,
        "record_store_write",
        {"store": NOTES, "name": "link.md", "content": B_MARK, "overwrite": True},
        SB,
    )
    assert "outside its store" in wrote.get("error", ""), wrote
    assert (seat["notes"] / person_slug(A) / "acme.md").read_text() == A_MARK


def test_a_directory_swapped_for_a_symlink_is_refused(seat) -> None:
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    os.symlink(seat["notes"] / person_slug(A), seat["notes"] / person_slug(B))
    out = _call(seat, "record_store_list", {"store": NOTES}, SB)
    assert "not a plain directory" in out.get("error", ""), out


# ---------------------------------------------------------------------------
# turns that have no person
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["record_store_list", "record_store_read", "record_store_write"])
def test_a_turn_no_email_opened_is_refused(seat, tool: str) -> None:
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    args = {"store": NOTES, "name": "acme.md", "content": "x"}
    refusal = seat["plugin"].PARTITION_REFUSED
    for session in (CRON, None, "", "20261110_170000_cccc0003"):
        out = _call(seat, tool, args, session)
        assert out == {"error": refusal}, (session, out)


def test_a_session_handed_a_second_email_speaks_for_neither(seat) -> None:
    """``bound_this_turn`` is strict: one session, one email. A session that has
    seen two (a sticky origin) has no person, rather than the first one's."""
    _two_people(seat)
    _email_turn(seat, A, SA, "<a-1@example.test>", "first")
    from bootstrap.translate import _INBOUND_EMAIL_PROMPT

    payload = {
        "message": {"inbox_id": "g", "message_id": "<a-2@example.test>", "from": A, "text": "2"}
    }
    seat["inbound"].on_pre_llm_call(
        session_id=SA,
        task_id="t",
        user_message=_hermes_render(_INBOUND_EMAIL_PROMPT, payload),
        conversation_history=[],
        is_first_turn=False,
        model="m",
        platform="webhook",
        sender_id="webhook:agentmail",
    )
    out = _call(seat, "record_store_list", {"store": NOTES}, SA)
    assert out == {"error": seat["plugin"].PARTITION_REFUSED}


def test_a_sender_removed_mid_session_is_refused_at_the_next_call(seat) -> None:
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    _enroll(seat["enrolled"], B)  # SMD removed A; A's turn is still running
    refusal = {"error": seat["plugin"].PARTITION_REFUSED}
    assert _call(seat, "record_store_list", {"store": NOTES}, SA) == refusal
    assert _call(seat, "record_store_read", {"store": NOTES, "name": "acme.md"}, SA) == refusal
    assert _call(seat, "record_store_list", {"store": NOTES}, SB)["records"] == []


def test_an_in_flight_turn_cannot_recreate_a_purged_directory(seat) -> None:
    """The purge sequence (``workspace_broker/person_purge.py``): the person
    leaves the enrolled list, then their directory is deleted. A turn of theirs
    that was already running writes afterwards and lands nothing."""
    import shutil

    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    _enroll(seat["enrolled"], B)
    shutil.rmtree(seat["notes"] / person_slug(A))
    out = _call(
        seat, "record_store_write", {"store": NOTES, "name": "later.md", "content": A_MARK}, SA
    )
    assert out == {"error": seat["plugin"].PARTITION_REFUSED}
    assert not (seat["notes"] / person_slug(A)).exists()


def test_a_removal_that_lands_during_the_write_undoes_it(seat, monkeypatch) -> None:
    """The roster is checked before the write and read back after it: a removal
    that lands between the two leaves nothing behind."""
    _two_people(seat)
    real_write = record_store.write_record

    def write_then_removed(*args: Any, **kwargs: Any) -> dict:
        receipt = real_write(*args, **kwargs)
        _enroll(seat["enrolled"], B)
        return receipt

    monkeypatch.setattr(record_store, "write_record", write_then_removed)
    out = _call(
        seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA
    )
    assert out == {"error": seat["plugin"].PARTITION_REFUSED}
    assert not (seat["notes"] / person_slug(A)).exists()


# ---------------------------------------------------------------------------
# a store only code writes
# ---------------------------------------------------------------------------


def test_a_managed_store_refuses_the_models_write(seat) -> None:
    _two_people(seat)
    out = _call(
        seat, "record_store_write", {"store": PROGRESS, "name": "p.json", "content": "{}"}, SA
    )
    assert out == {"error": seat["plugin"].MANAGED_REFUSED}
    assert not (seat["volume"] / "rep" / "progress").exists()


def test_code_writes_and_reads_a_named_persons_own_records(seat) -> None:
    receipt = record_store.code_write(PROGRESS, A, "progress.json", '{"lesson": 2}')
    assert receipt["created"] is True
    assert record_store.code_read(PROGRESS, A, "progress.json") == '{"lesson": 2}'
    assert [r["name"] for r in record_store.code_list(PROGRESS, A)] == ["progress.json"]
    assert record_store.code_list(PROGRESS, B) == []
    with pytest.raises(record_store.RecordStoreError, match="no record named"):
        record_store.code_read(PROGRESS, B, "progress.json")
    # The model reads its own progress through the same wall.
    _two_people(seat)
    a_view = _call(seat, "record_store_read", {"store": PROGRESS, "name": "progress.json"}, SA)
    assert a_view["content"] == '{"lesson": 2}'
    b_view = _call(seat, "record_store_list", {"store": PROGRESS}, SB)
    assert b_view["records"] == []


def test_code_cannot_write_for_a_person_not_on_the_seat(seat) -> None:
    with pytest.raises(record_store.PartitionRefused):
        record_store.code_write(PROGRESS, "stranger@example.test", "p.json", "{}")
    assert not (seat["volume"] / "rep" / "progress").exists()
    with pytest.raises(record_store.RecordStoreError, match="one named person"):
        record_store.code_write(PROGRESS, None, "p.json", "{}")


# ---------------------------------------------------------------------------
# what the seat still reads
# ---------------------------------------------------------------------------


def test_the_library_still_reads_and_a_partition_never_reads_by_path(seat, tmp_path) -> None:
    """With a partitioned store authored, the file-tool path fence still lets
    the product library under the skill roots be read, and refuses a record
    read by path (``read_file`` is on the email surface)."""
    plugin = seat["plugin"]
    _two_people(seat)
    _call(seat, "record_store_write", {"store": NOTES, "name": "acme.md", "content": A_MARK}, SA)
    pre, _post = plugin.path_fence.make_hooks(plugin._store_roots)
    library = "/app/skills/product-guide/references/INDEX.md"
    assert pre(tool_name="read_file", args={"path": library}) is None
    assert pre(tool_name="skill_view", args={"name": "product-guide"}) is None
    record = seat["notes"] / person_slug(A) / "acme.md"
    blocked = pre(tool_name="read_file", args={"path": str(record)})
    assert blocked and blocked["action"] == "block"


def test_validators_refuse_a_partition_with_a_reader_across_it() -> None:
    from shared.customer_config import CustomerConfig

    for extra in (
        {"owner_field": "agent"},
        {"readers": ["x@example.test"]},
        {"writers": ["x@y.z"]},
    ):
        cfg = CustomerConfig(
            {
                "customer_id": "s",
                "record_stores": [
                    {"name": "a", "path": "/opt/data/a", "partition": "sender", **extra}
                ],
            }
        )
        with pytest.raises(record_store.RecordStoreError, match="cannot be combined"):
            record_store.authored_policies(cfg)
    for bad in ({"partition": "owner"}, {"managed": "yes"}):
        cfg = CustomerConfig(
            {"customer_id": "s", "record_stores": [{"name": "a", "path": "/opt/data/a", **bad}]}
        )
        with pytest.raises(record_store.RecordStoreError):
            record_store.authored_policies(cfg)
