"""Cron attribution under Hermes v0.22.5 owner executions (2026-10-30 fleet defect).

From the v0.22.5 upgrade (ss-console #3581, 2026-10-28) every agent cron fire
runs as an owner execution whose session id is ``local-<sha256>``
(``hermes_state_keys.local_identity``), not ``cron_<job>_<stamp>``. The
attribution module only parsed the old form, so no cron turn resolved to its
routine: the pre-rendered dispatch never claimed its envelope and every
scheduled email (deadline escalator, statute watch, daily lists) sat unsent on
the seat. Pilot's last good dispatch: 2026-10-28 14:00Z
(vfy_01M65J13YRTJ2JTP0GGAA3C55T).

The authoritative signal is the scheduler's own ContextVar binding, mirrored
here by fakes with the exact shapes the pinned Hermes source defines
(``gateway/session_cron.py::bind_execution`` and
``cron/execution_identity.py::CronExecution``). Every positive has a negative:
a resolver that let a non-cron turn claim a cron job's pre-rendered email would
be worse than the defect.
"""

from __future__ import annotations

import json
import os
import sys
import types
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from shared import cron_attribution, prerendered_dispatch, send_dispatch
from shared.send_dispatch import DispatchResult

JOB_ID = "13b1ef8eed64"
EXECUTION_ID = "e0c1d2"
SESSION = "local-" + "ab" * 32  # the v0.22.5 shape: a sha256, no job in it
SKILL = "deadline-miss-escalator"
PERSONA = "operator"
STARTED = "2026-10-29T07:00:56.725715-07:00"  # the execution ledger's isoformat


@dataclass(frozen=True)
class _CronExecution:  # cron.execution_identity.CronExecution at the pinned sha
    job_id: str
    job_name: str
    execution_id: str
    source: str
    scheduled_instant: str | None
    started_at: str | None
    profile: str


_OWNER: ContextVar[tuple | None] = ContextVar("cron_owner_execution", default=None)
_IDENTITY: ContextVar[_CronExecution | None] = ContextVar("hermes_cron_execution", default=None)


@pytest.fixture(autouse=True)
def _hermes(monkeypatch, tmp_path):
    cron_pkg = types.ModuleType("cron")
    identity_mod = types.ModuleType("cron.execution_identity")
    identity_mod.current_cron_execution = _IDENTITY.get  # type: ignore[attr-defined]
    gateway_pkg = types.ModuleType("gateway")
    session_cron = types.ModuleType("gateway.session_cron")
    session_cron.current_execution = _OWNER.get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.execution_identity", identity_mod)
    monkeypatch.setitem(sys.modules, "gateway", gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.session_cron", session_cron)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = tmp_path / "profiles" / PERSONA / "cron"
    store.mkdir(parents=True)
    (store / "jobs.json").write_text(
        json.dumps(
            {"jobs": [{"id": JOB_ID, "name": f"op-managed:{PERSONA}:{SKILL}", "skills": [SKILL]}]}
        ),
        encoding="utf-8",
    )
    cron_attribution._CACHE.clear()
    prerendered_dispatch._IN_TURN.clear()
    yield
    send_dispatch.set_sender(None)
    prerendered_dispatch._IN_TURN.clear()
    cron_attribution._CACHE.clear()


def _bind(session_id=SESSION, *, job=JOB_ID, execution=EXECUTION_ID, identity=True):
    """Bind as ``session_cron.execute`` + ``scheduler_authority._run_identified`` do."""
    tokens: list[Token[Any]] = [_OWNER.set(("authority", session_id, job, execution))]
    if identity:
        tokens.append(
            _IDENTITY.set(
                _CronExecution(
                    job_id=JOB_ID,
                    job_name=f"op-managed:{PERSONA}:{SKILL}",
                    execution_id=EXECUTION_ID,
                    source="builtin",
                    scheduled_instant="2026-10-29T14:00:00+00:00",
                    started_at=STARTED,
                    profile=PERSONA,
                )
            )
        )
    return tokens


def _unbind(tokens):
    _OWNER.reset(tokens[0])
    if len(tokens) > 1:
        _IDENTITY.reset(tokens[1])


def test_an_owner_execution_session_resolves_to_its_routine():
    tokens = _bind()
    try:
        assert cron_attribution.parse_cron_session(SESSION) == JOB_ID
        routine = cron_attribution.resolve_routine(SESSION)
        assert routine is not None and routine.skill == SKILL and routine.persona == PERSONA
        # The same naive digits the legacy id carried: the scheduler's clock.
        assert cron_attribution.parse_cron_session_started_at(SESSION) == datetime(
            2026, 10, 29, 7, 0, 56, 725715
        )
    finally:
        _unbind(tokens)


def test_the_signal_is_the_pinned_hermes_binding_itself(monkeypatch):
    """The fakes above, checked against the REAL pinned Hermes: bind exactly as
    ``gateway/session_cron.py::execute`` and ``cron/scheduler_authority.py::
    _run_identified`` do, and the resolver must name the job. A Hermes bump that
    renames or reshapes either binding fails here, not silently on the seats."""
    src = os.environ.get("HERMES_AGENT_SRC", "")
    if not src:
        if os.environ.get("CI"):
            pytest.fail("CI must check out the pinned Hermes and set HERMES_AGENT_SRC")
        pytest.skip("HERMES_AGENT_SRC not set")
    for name in ("cron", "cron.execution_identity", "gateway", "gateway.session_cron"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(src)
    try:
        from cron.execution_identity import enter_cron_execution, exit_cron_execution
        from gateway.session_cron import _execution, bind_execution
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("CI"):
            raise
        pytest.skip(f"pinned Hermes not importable here: {exc}")
    job = {"id": JOB_ID, "name": f"op-managed:{PERSONA}:{SKILL}"}
    owner = bind_execution("authority", SESSION, JOB_ID, EXECUTION_ID)
    identity = enter_cron_execution(job, EXECUTION_ID, {"source": "builtin", "started_at": STARTED})
    try:
        assert cron_attribution.parse_cron_session(SESSION) == JOB_ID
        assert cron_attribution.parse_cron_session("local-" + "cd" * 32) is None
        assert cron_attribution.parse_cron_session_started_at(SESSION) == datetime(
            2026, 10, 29, 7, 0, 56, 725715
        )
    finally:
        exit_cron_execution(identity)
        _execution.reset(owner)
    assert cron_attribution.parse_cron_session(SESSION) is None


def test_the_legacy_cron_id_still_resolves_without_a_binding():
    assert cron_attribution.parse_cron_session(f"cron_{JOB_ID}_20261028_070005") == JOB_ID


def test_a_local_session_outside_any_cron_fire_claims_nothing():
    # An inbound email or an interactive turn: same id shape, no binding.
    assert cron_attribution.parse_cron_session(SESSION) is None
    assert cron_attribution.resolve_routine(SESSION) is None
    assert cron_attribution.parse_cron_session_started_at(SESSION) is None


@pytest.mark.parametrize(
    "binding",
    [
        {"session_id": "local-" + "cd" * 32},  # the fire belongs to another session
        {"job": "ffffffffffff"},  # owner and identity disagree on the job
        {"execution": "other-execution"},  # ... or on the execution
        {"identity": False},  # a delegated child: Hermes reads identity as None
    ],
)
def test_a_binding_that_does_not_name_this_session_claims_nothing(binding):
    tokens = _bind(**binding)
    try:
        assert cron_attribution.parse_cron_session(SESSION) is None
        assert cron_attribution.resolve_routine(SESSION) is None
        assert cron_attribution.parse_cron_session_started_at(SESSION) is None
    finally:
        _unbind(tokens)


def _write_envelope(tmp_path):
    directory = tmp_path / "profiles" / PERSONA / ".smd" / "pre_run"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dispatches": [
            {
                "recipients": ["ops@firm.example"],
                "cc": [],
                "routing_leg": "central",
                "subject": "[Deadlines] 1 need you, 2026-10-29",
                "full_body": "## Needs you today (1)\n\n1. matter 2026-PI-101 [ACK-AAAAAA]\n",
                "skeleton_body": "## Deadline digest (details unavailable)\n\n1 item.\n",
                "body_sha256_full": "a" * 64,
                "body_sha256_skeleton": "b" * 64,
                "appends": [],
            }
        ],
    }
    path = directory / f"{SKILL}.dispatch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return DispatchResult(sent=True, message_id="m1", recipients=("ops@firm.example",))


def test_the_cron_fire_dispatches_its_pre_rendered_email(tmp_path):
    path = _write_envelope(tmp_path)
    sender = _Sender()
    send_dispatch.set_sender(sender)
    tokens = _bind()
    try:
        note = prerendered_dispatch.dispatch_prerendered(SESSION)
    finally:
        _unbind(tokens)
    assert note and "already delivered to ops@firm.example" in note
    [call] = sender.calls
    assert call["to"] == ["ops@firm.example"]
    assert not path.exists()  # consumed: a second turn cannot re-send it


def test_a_non_cron_local_turn_cannot_claim_the_pre_rendered_email(tmp_path):
    path = _write_envelope(tmp_path)
    sender = _Sender()
    send_dispatch.set_sender(sender)
    assert prerendered_dispatch.dispatch_prerendered(SESSION) is None
    tokens = _bind(session_id="local-" + "cd" * 32)  # a concurrent fire, another session
    try:
        assert prerendered_dispatch.dispatch_prerendered(SESSION) is None
    finally:
        _unbind(tokens)
    assert sender.calls == []
    assert path.exists()


def test_the_stale_envelope_rule_is_unchanged_under_the_new_naming(tmp_path):
    # A file left from before the fix is older than the 20-minute window: the
    # next fire leaves it in place rather than sending a days-old digest.
    path = _write_envelope(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    payload["started_at"] = old.isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(payload), encoding="utf-8")
    sender = _Sender()
    send_dispatch.set_sender(sender)
    tokens = _bind()
    try:
        assert prerendered_dispatch.dispatch_prerendered(SESSION) is None
    finally:
        _unbind(tokens)
    assert sender.calls == [] and path.exists()
