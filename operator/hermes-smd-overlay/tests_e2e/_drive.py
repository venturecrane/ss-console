"""Drive one scripted tool call through the real agent loop and report what
happened at every seam: the hooks Hermes fired, whether the registry
dispatched, what the handler returned, what reached the broker, and what the
model was handed back."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests_e2e._model_stub import ScriptedTurn

#: What Hermes' registry returns when a handler raised (``tools/registry.py``
#: ``dispatch``): ``tool_error("Tool execution failed: <Type>: <message>")``.
HANDLER_RAISED = "Tool execution failed:"


@dataclass
class Outcome:
    tool: str
    #: Whether Hermes' registry dispatched the call (a ``pre_tool_call`` block
    #: means it did not).
    dispatched: bool
    #: The registry's return value for this tool, when dispatched.
    result: str
    #: The tool result the model received on its next request.
    model_saw: str
    broker_requests: list[dict[str, Any]]
    hooks_fired: list[tuple[str, dict[str, Any]]]
    final_response: str

    def raised(self) -> bool:
        return HANDLER_RAISED in self.result

    def describe(self) -> str:
        return (
            f"{self.tool}: dispatched={self.dispatched} result={self.result[:300]!r} "
            f"model_saw={self.model_saw[:300]!r}"
        )


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def fresh_session(agent: Any) -> str:
    """Start a new session on the same agent, the way Hermes' own ``/new`` does
    (``hermes_cli/cli_session_mixin.py`` at the pin): end the old session row,
    set ``session_id``, ``reset_session_state``, rewind the flush cursor, and
    create the new row. Every overlay register that is per-session (taint from
    an untrusted read, the turn's verified sender, a held send) starts empty,
    so one scripted call cannot change the verdict on the next.

    The row matters. Rotating ``session_id`` without it makes every message
    write fail its foreign key, and Hermes then ends some turns with
    ``reason=session_persistence_failed`` before the tool runs: the harness's
    first flake (1 run in 6 under load) was exactly that, read as "tool never
    dispatched"."""
    old = str(getattr(agent, "session_id", "") or "")
    session_db = getattr(agent, "_session_db", None)
    if session_db is not None and old:
        try:
            session_db.end_session(old, "new_session")
        except Exception:  # noqa: BLE001 -- /new suppresses this too; the old row may not exist
            pass
    agent.session_id = f"e2e-{uuid.uuid4().hex[:12]}"
    agent.reset_session_state(old_session_id=old)
    agent._last_flushed_db_idx = 0
    agent._session_db_created = False
    agent._ensure_db_session()
    assert session_db is None or agent._session_db_created, (
        f"Hermes did not create the session row for {agent.session_id}; every message "
        "write in the turn would fail its foreign key"
    )
    return str(agent.session_id)


def fresh_ladder() -> None:
    """Empty the sticky-stop ladder (``shared.cost_breaker``) in the temp seat.

    The ladder is per (customer, persona), not per session, and its refusal
    arm climbs on every plugin block: the dispatch sweep refuses a dozen tools
    on purpose (no verified sender, no authored exposure), and on the first
    full run the seat reached HARD_STOP partway through the second transport,
    after which every tool read "paused". That is the seat behaving correctly;
    it is not what the sweep measures, so each scripted call starts from an
    empty ladder. The file is the harness's own (``SMD_STICKY_STOP_DB_PATH``)."""
    from shared.cost_breaker import db_path

    path = Path(db_path())
    if not path.exists():
        return
    conn = sqlite3.connect(str(path))
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sticky_stop_state'"
        ).fetchone()
        if has_table:
            conn.execute("DELETE FROM sticky_stop_state")
            conn.commit()
    finally:
        conn.close()


def drive(harness: Any, agent: Any, tool: str, args: dict[str, Any], prompt: str = "") -> Outcome:
    """Script the model to call ``tool(args)`` once, run one real turn, and
    collect the evidence."""
    fresh_ladder()
    fresh_session(agent)
    marker = f"[e2e-turn {uuid.uuid4().hex}]"
    harness.script.reset(marker)
    harness.script.push(ScriptedTurn(tool_calls=[(tool, args)]))
    broker_mark = harness.broker.mark()
    hook_mark = harness.hooks.mark()
    dispatch_mark = harness.dispatches.mark()

    result = agent.run_conversation(f"{prompt or f'Use {tool} once.'} {marker}")

    exit_reason = str((result or {}).get("turn_exit_reason") or "")
    assert exit_reason.startswith("text_response"), (
        f"the {tool} turn ended with turn_exit_reason={exit_reason!r}, not the scripted closing "
        "text; the harness itself broke the turn, so nothing about this tool was measured"
    )
    assert harness.script.pending() == 0, (
        f"the scripted {tool} call was never requested: the agent loop ended before "
        "asking the model, so nothing about dispatch was exercised"
    )
    transports = {r.transport for r in harness.script.requests}
    assert transports == {agent.api_mode}, (
        f"the turn reached the model over {transports}, not the agent's {agent.api_mode!r}; "
        "the transport under test was not the one exercised"
    )
    calls = [c for c in harness.dispatches.since(dispatch_mark) if c.tool == tool]
    tool_results = harness.script.last_tool_results()
    return Outcome(
        tool=tool,
        dispatched=bool(calls),
        result=_as_text(calls[-1].result) if calls else "",
        model_saw=tool_results[-1]["content"] if tool_results else "",
        broker_requests=harness.broker.since(broker_mark),
        hooks_fired=list(harness.hooks.invoked[hook_mark:]),
        final_response=str((result or {}).get("final_response") or ""),
    )
