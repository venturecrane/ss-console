"""The skipped-cron session sweep, against the pinned Hermes SessionDB (captain-mark 2026-11-10).

Every store here is a REAL Hermes ``state.db`` (``hermes_state.SessionDB`` from
HERMES_AGENT_SRC), so the deletes, the retirement fence, the policy and routing
cleanup and the admission tombstones are Hermes' own code, not a model of it.
What the fixture writes by hand is only what a cron fire leaves on a seat, in
the shapes read off captain-mark: an open ``source='cron'`` row, its
``gateway.local_policy.v1:<id>`` record naming it as ``entry.session_id``, the
``gateway_routing`` row pointing at it and one terminal admission.

The gateway side (owner loop, ``retire_live_sessions``, ``track_mutation``) is
stubbed: those need a running gateway runner, and what is pinned there is the
call shape and the threading, not Hermes' bookkeeping.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from shared import cron_session_sweep as sweep

from .guard_needs import HERMES_PROVIDE, require

POLICY = "gateway.local_policy.v1:"
HOUR = 3600.0


@pytest.fixture
def hermes(monkeypatch, tmp_path):
    src = os.environ.get("HERMES_AGENT_SRC")
    if src:
        monkeypatch.syspath_prepend(src)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        import hermes_state
        import hermes_state_runtime
    except ImportError:
        hermes_state = None
    require(hermes_state, "Hermes core (hermes_state) is not importable", HERMES_PROVIDE)
    return types.SimpleNamespace(SessionDB=hermes_state.SessionDB, runtime=hermes_state_runtime)


class Store:
    """A real Hermes store plus the writes a cron fire leaves behind."""

    def __init__(self, hermes, path: Path) -> None:
        self.db = hermes.SessionDB(path)
        self.epoch = hermes.runtime.begin_runtime_epoch(self.db, instance_id="test")
        self.path = path
        self._n = 0

    def sql(self, statement: str, *args):
        import sqlite3

        con = sqlite3.connect(self.path)
        try:
            rows = con.execute(statement, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def cron_fire(
        self, *, age_s: float = 2 * HOUR, source: str = "cron", status: str = "terminal"
    ) -> str:
        """One fire as the gateway leaves it: open row, policy, routing, one admission."""
        self._n += 1
        sid = f"local-{self._n:064x}"
        self.db.create_session(sid, source=source)
        started = time.time() - age_s
        self.sql("UPDATE sessions SET started_at=? WHERE id=?", started, sid)
        receipt = {
            "session_id": sid,
            "request_id": f"cron:job{self._n}:exec{self._n}",
            "route": f"agent:main:local:dm:{sid}",
            "entry": {"session_id": sid, "session_key": f"agent:main:local:dm:{sid}"},
            "policy": {
                "source": source,
                "request_json": json.dumps({"cron_job": {"prompt": "x" * 4000}}),
            },
        }
        self.sql("INSERT INTO state_meta(key,value) VALUES(?,?)", POLICY + sid, json.dumps(receipt))
        self.sql(
            "INSERT INTO gateway_routing(scope,session_key,entry_json,updated_at) VALUES('',?,?,?)",
            receipt["route"],
            json.dumps(receipt["entry"]),
            started,
        )
        self.sql(
            "INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,"
            "lineage_json,payload_json,payload_digest,intent,status,outcome,owner_epoch,generation) "
            "VALUES(?,?,?,?,'[]','{\"text\":\"\"}','d','queue',?,?,?,?)",
            f"adm{self._n}",
            receipt["request_id"],
            "cron-principal",
            sid,
            status,
            "completed" if status == "terminal" else None,
            self.epoch,
            1,
        )
        return sid

    def footprint(self, sid: str) -> dict[str, int]:
        """Every place one fire lives."""
        routing = sum(
            1
            for (raw,) in self.sql("SELECT entry_json FROM gateway_routing")
            if json.loads(raw).get("session_id") == sid
        )
        return {
            "session": len(self.sql("SELECT 1 FROM sessions WHERE id=?", sid)),
            "policy": len(self.sql("SELECT 1 FROM state_meta WHERE key=?", POLICY + sid)),
            "routing": routing,
            "admission": len(
                self.sql("SELECT 1 FROM session_admissions WHERE target_session_id=?", sid)
            ),
        }

    def sweep(self, **kw) -> sweep.SweepResult:
        return sweep.sweep_store(self.db, epoch=self.epoch, **kw)


GONE = {"session": 0, "policy": 0, "routing": 0, "admission": 0}
WHOLE = {"session": 1, "policy": 1, "routing": 1, "admission": 1}


def assert_removed(store: Store, sid: str) -> None:
    assert store.footprint(sid) == GONE, f"{sid} still has {store.footprint(sid)}"


@pytest.fixture
def store(hermes, tmp_path):
    s = Store(hermes, tmp_path / "state.db")
    yield s
    s.db.close()


def test_a_skipped_fire_goes_with_its_policy_routing_and_admission(store):
    sids = [store.cron_fire() for _ in range(3)]
    assert all(store.footprint(s) == WHOLE for s in sids)

    result = store.sweep()

    assert (result.selected, result.ended, result.deleted) == (3, 3, 3)
    assert sorted(result.deleted_ids) == sorted(sids)
    for sid in sids:
        assert_removed(store, sid)


def test_the_admission_leaves_only_a_compacted_tombstone_hermes_reclaims(store):
    from hermes_state_mutation_retirement import prune_retired_tombstones
    from hermes_state_terminal import ADMISSION_PREFIX

    sid = store.cron_fire()
    store.sweep()
    (raw,) = store.sql("SELECT value FROM state_meta WHERE key=?", ADMISSION_PREFIX + "adm1")[0]
    assert json.loads(raw)["payload_json"] == "{}"  # compacted: no copy of the fire's input
    # A sweep's retired marker is the expiring kind, so it ages out too.
    (marker,) = store.sql(
        "SELECT value FROM state_meta WHERE key=?", "gateway.retired_session.v1." + sid
    )[0]
    assert json.loads(marker)["expires"] is True

    store.db._execute_write(lambda conn: prune_retired_tombstones(conn, older_than=-1))
    assert store.sql("SELECT count(*) FROM state_meta WHERE key LIKE ?", "%adm1%") == [(0,)]
    assert store.sql("SELECT count(*) FROM state_meta WHERE key LIKE ?", "%" + sid + "%") == [(0,)]


def test_everything_that_is_not_a_skipped_old_cron_fire_is_kept(store):
    gone = store.cron_fire()
    with_messages = store.cron_fire()
    store.db.append_message(with_messages, "user", "a real run")
    not_cron = store.cron_fire(source="telegram")
    too_young = store.cron_fire(age_s=10 * 60)
    leased = store.cron_fire()
    assert store.db.try_acquire_session_turn_lease(
        leased, f"pid={os.getpid()}:turn=1", ttl_seconds=600
    )
    titled = store.cron_fire()
    store.db.set_session_title(titled, "a named run")
    live_admission = store.cron_fire(status="queued")
    parent = store.cron_fire()
    store.db.create_session("child", source="cron", parent_session_id=parent)

    result = store.sweep()

    assert result.deleted_ids == [gone]
    assert_removed(store, gone)
    for sid in (with_messages, not_cron, too_young, leased, titled, live_admission, parent):
        footprint = store.footprint(sid)
        assert footprint["session"] == footprint["policy"] == footprint["routing"] == 1, sid
        (ended,) = store.sql("SELECT end_reason FROM sessions WHERE id=?", sid)[0]
        assert ended is None, f"{sid} was ended though it is kept"


def test_one_pass_is_bounded_and_the_next_pass_continues(store):
    sids = [store.cron_fire(age_s=(10 - i) * HOUR) for i in range(5)]

    first = store.sweep(limit=2)
    assert (first.selected, first.deleted) == (2, 2)
    assert sorted(first.deleted_ids) == sorted(sids[:2])  # oldest first
    assert all(store.footprint(s) == WHOLE for s in sids[2:])

    second = store.sweep(limit=2)
    assert sorted(second.deleted_ids) == sorted(sids[2:4])
    assert store.footprint(sids[4]) == WHOLE


def test_the_prune_touches_only_rows_this_sweep_ended(store):
    """An ended, empty cron row some other path ended is not this sweep's to delete."""
    other = store.cron_fire()
    store.db.end_session(other, "cron_complete")
    store.sweep()
    assert store.footprint(other) == WHOLE


def test_negative_control_without_the_delete_the_removal_check_fails(store, monkeypatch):
    """The assertion the positive test relies on can fail: remove the delete and it does."""
    sid = store.cron_fire()
    monkeypatch.setattr(store.db, "prune_sessions", lambda **_kw: 0)
    store.sweep()
    with pytest.raises(AssertionError):
        assert_removed(store, sid)


# -- the gateway side ----------------------------------------------------------


@pytest.fixture
def gateway_stubs(monkeypatch):
    retired: list[list[str]] = []
    tracked: list[object] = []
    mutations = types.ModuleType("gateway.session_mutations")
    mutations.retire_live_sessions = lambda authority, ids: retired.append(list(ids))
    workers = types.ModuleType("gateway.session_runtime_workers")

    def track_mutation(authority, operation):
        task = asyncio.ensure_future(operation)
        tracked.append(task)
        return task

    workers.track_mutation = track_mutation
    monkeypatch.setitem(sys.modules, "gateway", types.ModuleType("gateway"))
    monkeypatch.setitem(sys.modules, "gateway.session_mutations", mutations)
    monkeypatch.setitem(sys.modules, "gateway.session_runtime_workers", workers)
    return types.SimpleNamespace(retired=retired, tracked=tracked)


class _Authority:
    def __init__(self, store: Store, draining: bool = False) -> None:
        self.db = store.db
        self.epoch = store.epoch
        self.draining = draining

    def _require_admission_open(self) -> None:
        if self.draining:
            raise RuntimeError("runtime_draining")


def _loop_in_thread():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def test_run_pass_sweeps_on_the_owner_loop_and_retires_live_state(
    store, gateway_stubs, monkeypatch
):
    sids = [store.cron_fire() for _ in range(2)]
    loop, thread = _loop_in_thread()
    db_threads: list[int] = []
    real = sweep.sweep_store

    def spy(*a, **kw):
        db_threads.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(sweep, "sweep_store", spy)
    try:
        home = store.path.parent
        results = sweep.run_pass(
            homes=[home], owner_for_home=lambda h: (_Authority(store), loop) if h == home else None
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert results[str(home)].deleted == 2
    assert [sorted(ids) for ids in gateway_stubs.retired] == [sorted(sids)]
    assert len(gateway_stubs.tracked) == 1  # a tracked mutation, so a drain waits for it
    assert db_threads and db_threads[0] not in (thread.ident, threading.get_ident())  # off the loop
    for sid in sids:
        assert_removed(store, sid)


def test_a_draining_owner_and_a_foreign_store_are_left_alone(store, gateway_stubs):
    sid = store.cron_fire()
    loop, thread = _loop_in_thread()
    try:
        home = store.path.parent
        drained = sweep.run_pass(
            homes=[home], owner_for_home=lambda h: (_Authority(store, True), loop)
        )
        foreign = sweep.run_pass(homes=[home], owner_for_home=lambda h: None)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert drained == {} and foreign == {}
    assert store.footprint(sid) == WHOLE


def test_profile_homes_finds_the_root_and_each_profile_store(tmp_path):
    (tmp_path / "state.db").write_bytes(b"")
    for name in ("captain-mark", "empty"):
        (tmp_path / "profiles" / name).mkdir(parents=True)
    (tmp_path / "profiles" / "captain-mark" / "state.db").write_bytes(b"")
    assert sweep.profile_homes(str(tmp_path)) == [tmp_path, tmp_path / "profiles" / "captain-mark"]


def test_the_thread_starts_only_in_the_gateway_process(monkeypatch):
    monkeypatch.setattr(sweep, "_started", False)
    monkeypatch.setenv("SMD_GATEWAY_PID", str(os.getpid() + 1))
    assert sweep.start_sweeper_thread(stop_event=threading.Event()) is False
    stop = threading.Event()
    stop.set()
    monkeypatch.setenv("SMD_GATEWAY_PID", str(os.getpid()))
    assert sweep.start_sweeper_thread(stop_event=stop) is True
    assert sweep.start_sweeper_thread(stop_event=stop) is False  # idempotent
