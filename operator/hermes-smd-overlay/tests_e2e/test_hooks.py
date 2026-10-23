"""The overlay's hooks, as the pinned Hermes fires them on a real turn.

Each test drives one scripted tool call through ``AIAgent`` and reads the
evidence at the seam the hook acts on: the stub broker for a send that must
not leave, the seat's audit ledger for a turn that must be recorded, the
order Hermes registered the ``pre_tool_call`` gates in (which is the order it
consults them), and the log lines of the overlay's hook-probe plugin.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from tests_e2e._drive import drive
from tests_e2e._seat import OVERLAY_ROOT, OWNER_EMAIL, SHADOW_MODULE, write_couples_seat

#: The six hooks ``hermes-smd-hook-probe`` attaches to (its ``plugin.yaml``).
PROBE_HOOKS = frozenset(
    {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "post_llm_call",
        "transform_tool_result",
        "on_session_end",
    }
)


def _shadow():
    module = sys.modules.get(SHADOW_MODULE)
    assert module is not None, (
        "the harness shadow plugin was not loaded by Hermes' plugin loader; "
        "check plugins.enabled in the temp seat"
    )
    return module


def test_a_banned_send_is_refused_before_hermes_dispatches_it(harness, agent):
    from shared.action_classes import BANNED_TOOLS

    shadow = _shadow()
    assert shadow.BANNED_SEND_TOOL in BANNED_TOOLS, "the fixture's tool must be a banned name"
    calls_before = len(shadow.BANNED_HANDLER_CALLS)

    outcome = drive(
        harness,
        agent,
        shadow.BANNED_SEND_TOOL,
        {"to": [OWNER_EMAIL], "subject": "e2e", "text": "must not leave"},
    )

    assert not outcome.dispatched, outcome.describe()
    assert len(shadow.BANNED_HANDLER_CALLS) == calls_before, "the banned handler ran"
    # The job worker's background sweeps (health, job_list_claimable) may land
    # in the same window; anything else would be the send, or its grant.
    housekeeping = {"health", "job_list_claimable"}
    sends = [r for r in outcome.broker_requests if r.get("action") not in housekeeping]
    assert sends == [], f"a banned send reached the broker: {sends}"
    assert "autonomous email send is forbidden" in outcome.model_saw, outcome.model_saw
    blocked = [kw for name, kw in outcome.hooks_fired if name == "pre_tool_call"]
    assert blocked, "pre_tool_call never fired for the banned send"


def _turn_rows(audit_db: Path, session_id: str) -> list[dict]:
    if not audit_db.exists():
        return []
    conn = sqlite3.connect(str(audit_db))
    try:
        rows = conn.execute(
            "SELECT metadata FROM audit_log WHERE action_type = 'LLM_TURN_COMPLETED'"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    out = []
    for (raw,) in rows:
        meta = json.loads(raw or "{}")
        if meta.get("session_id") == session_id:
            out.append(meta)
    return out


def test_post_llm_call_writes_the_turn_to_the_audit_ledger(harness, agent):
    """The end-of-turn write runs on the overlay's hook worker thread, as it
    does on a seat (``shared.hook_worker``; nothing here forces it inline), so
    the read waits for it with a bound."""
    outcome = drive(harness, agent, "escalation_state", {})
    assert outcome.dispatched, outcome.describe()
    session_id = str(agent.session_id)

    fired = [kw for name, kw in outcome.hooks_fired if name == "post_llm_call"]
    assert fired, "Hermes did not fire post_llm_call on a completed turn"

    deadline = time.monotonic() + 10.0
    rows = _turn_rows(harness.audit_db, session_id)
    while not rows and time.monotonic() < deadline:
        time.sleep(0.1)
        rows = _turn_rows(harness.audit_db, session_id)
    assert rows, f"no LLM_TURN_COMPLETED row for session {session_id} in {harness.audit_db}"
    assert rows[-1].get("customer") == "e2e-seat"


def _plugin_yaml_order() -> list[str]:
    import yaml

    manifest = yaml.safe_load((OVERLAY_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    return [Path(p).name for p in manifest["plugins"]]


def test_pre_tool_call_gates_register_in_plugin_yaml_order(harness):
    """Hermes consults ``pre_tool_call`` callbacks in registration order and the
    first block wins, so the order in ``plugin.yaml`` is a policy decision
    (space-gate refuses before trust spends work). This pins that the order
    Hermes actually recorded is the order the manifest declares."""
    declared = _plugin_yaml_order()
    registered = [
        owner for owner in harness.hooks.registration_order("pre_tool_call") if owner in declared
    ]
    assert registered, "no overlay pre_tool_call registrations were recorded"
    expected = [name for name in declared if name in registered]
    assert registered == expected, (
        f"pre_tool_call registration order {registered} differs from plugin.yaml {expected}"
    )
    assert registered.index("hermes-smd-space-gate") < registered.index("hermes-smd-trust")


def test_on_a_couples_seat_an_unresolved_space_write_is_refused_by_the_space_gate(
    harness, agent, monkeypatch, tmp_path
):
    """Space mode is decided per call from the seat's customer.yaml, so the
    same loaded plugins are pointed at a validated couples seat. A Workspace
    write on a turn whose space cannot be resolved must be refused with the
    space-gate's message: the trust gate would refuse too, so seeing the
    space-gate's text is also the runtime proof that it is consulted first."""
    couples = write_couples_seat(tmp_path / "customer.yaml")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(couples))
    space_gate = importlib.import_module("hermes_plugins.hermes_smd_space_gate")
    tool = "workspace_docs_create"
    assert space_gate.is_candidate(tool)

    outcome = drive(harness, agent, tool, {"title": "e2e shared list"})

    assert not outcome.dispatched, outcome.describe()
    expected = space_gate._UNRESOLVED.format(tool=tool)
    assert expected in outcome.model_saw, outcome.model_saw
    authorized = [r for r in outcome.broker_requests if r.get("action") in ("authorize", "execute")]
    assert authorized == [], f"the refused write still reached the broker: {authorized}"


class _ProbeLines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.hooks: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            return
        if payload.get("event") == "hermes_smd_hook_probe":
            self.hooks.append(str(payload.get("hook_name")))


@pytest.fixture
def probe_lines():
    logger = logging.getLogger("hermes_plugins.hermes_smd_hook_probe")
    handler = _ProbeLines()
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def test_the_hook_probe_sees_all_six_hooks_fire(harness, agent, probe_lines):
    outcome = drive(harness, agent, "escalation_state", {})
    assert outcome.dispatched, outcome.describe()
    missing = sorted(PROBE_HOOKS - set(probe_lines.hooks))
    assert not missing, (
        f"the hook probe did not see {missing} fire on a turn with one tool call; "
        f"it saw {probe_lines.hooks}"
    )
