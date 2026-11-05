"""Every overlay tool, called by the model, dispatched by the pinned Hermes.

``tests/test_tool_handler_dispatch_contract.py`` checks handler SIGNATURES
against Hermes' dispatch shape. This file checks the thing itself: for every
tool the overlay registers, the model stub scripts one call, the real
``AIAgent`` runs the turn, the overlay's hooks decide, Hermes' registry
dispatches, and the test reads what came back. A handler Hermes cannot call
returns ``Tool execution failed: TypeError`` from ``registry.dispatch``, which
is exactly what SMD-OPERATOR-1B was (2026-08-13, ``smd_send_message``).

Three outcomes are distinguished, and each is asserted:

* dispatched, and the registry result is not a ``TypeError``: the shape
  holds. A handler that raises something else on synthetic arguments (a
  missing message id, an unconfigured vendor key) was still entered with the
  model's arguments, which is what this file is about.
* dispatched, and broker-backed: the payload the model supplied reached the
  stub broker (``BROKER_EFFECTS``). This is the empty-payload half of the
  08-13 defect, which a shape check cannot see.
* refused before dispatch: only the tools in ``GATED``, each with the refusal
  it must still produce. The set is frozen in both directions, so a tool that
  starts dispatching, or a new tool that is refused, fails here until the
  set is edited with a reason.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests_e2e._drive import Outcome, drive
from tests_e2e._seat import OWNER_EMAIL

#: Registered-tool floor, the same floor as the static guard. A sub-plugin that
#: stops registering trips it before the per-tool loop can pass vacuously.
MIN_REGISTERED_TOOLS = 30

#: The tool this harness exists for, pinned by name.
PINNED_TOOL = "smd_send_message"

#: Tools the overlay's own gates refuse in ``pre_tool_call`` on this seat, so
#: Hermes never dispatches them here. Each maps to a fragment of the refusal it
#: must still produce. Their handler signatures stay covered by the static
#: guard; their dispatch is not exercised in PR 1, and the CI summary lists them.
GATED: dict[str, str] = {
    # A rule, a correction or an operations request is recorded under the
    # verified sender of an inbound email. Only a gateway email turn binds one,
    # and the gateway platform is not driven in PR 1.
    "correction_capture": "only when an administrator on the firm's",
    "establish_pending": "no verified sender",
    "establish_propose": "no verified sender",
    "establish_stage_document": "only the firm's Operator admins",
    "establish_status": "only the firm's Operator admins",
    "establish_submit": "only the firm's Operator admins",
    "operations_request": "no verified sender",
    # code_execution is unauthored on the hosted template (refused by design).
    "start_background_job": "code_execution refused",
    # DESTRUCTIVE needs a current-turn approval no scripted turn can give.
    "workspace_gmail_archive": "refused: no authored exposure (fail-closed, ADR 0056) or a floor refuses it",
    "workspace_gmail_modify": "refused: no authored exposure (fail-closed, ADR 0056) or a floor refuses it",
}

#: Broker-backed tools and the broker action their HANDLER sends. Every scalar
#: argument the model supplied must appear in that request.
BROKER_EFFECTS: dict[str, str] = {
    "demand_allowance": "demand_allowance",
    "demand_job_status": "demand_job_status",
    "drafting_allowance": "drafting_allowance",
    "drafting_job_status": "drafting_job_status",
    "job_cancel": "job_cancel",
    "job_status": "job_read",
    "litigation_job_status": "litigation_job_status",
    "medchron_allowance": "medchron_allowance",
    "medchron_job_status": "medchron_job_status",
    "medchron_job_submit": "medchron_job_submit",
    "negotiation_job_status": "negotiation_job_status",
    "note_job_status": "note_job_status",
    "smd_send_message": "agentmail_send",
    "workspace_calendar_create_draft": "execute",
    "workspace_calendar_get": "execute",
    "workspace_calendar_list": "execute",
    "workspace_calendar_update_draft": "execute",
    "workspace_docs_append": "execute",
    "workspace_docs_create": "execute",
    "workspace_docs_get": "execute",
    "workspace_drive_export": "execute",
    "workspace_drive_get": "execute",
    "workspace_drive_list": "execute",
    "workspace_gmail_create_draft": "execute",
    "workspace_gmail_get": "execute",
    "workspace_gmail_search": "execute",
    "workspace_sheets_create": "execute",
    "workspace_sheets_get_values": "execute",
    "workspace_sheets_update_values": "execute",
}

#: Arguments beyond the schema's required set. The send carries a body, so the
#: effect assertion can see the 08-13 empty-payload defect.
EXTRA_ARGS: dict[str, dict[str, Any]] = {
    PINNED_TOOL: {"text": f"e2e body for {PINNED_TOOL}"},
}


def _tool_schema(name: str) -> dict[str, Any]:
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry

    entry = registry.get_entry(name) or registry.get_entry(
        name, scope=get_plugin_manager().scope_key
    )
    assert entry is not None, f"{name} is registered but has no registry entry"
    return entry.schema


def scripted_args(name: str) -> dict[str, Any]:
    """Schema-shaped arguments with a value traceable to this tool and field."""
    params = _tool_schema(name).get("parameters") or {}
    props = params.get("properties") or {}
    args: dict[str, Any] = {}
    for field in params.get("required") or []:
        kind = (props.get(field) or {}).get("type")
        if kind == "array":
            args[field] = [OWNER_EMAIL]
        elif kind in ("integer", "number"):
            args[field] = 7
        elif kind == "boolean":
            args[field] = False
        elif kind == "object":
            args[field] = {}
        else:
            args[field] = f"e2e-{name}-{field}"
    args.update(EXTRA_ARGS.get(name, {}))
    return args


def _leaves(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [leaf for v in value.values() for leaf in _leaves(v)]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v)]
    return [value]


def _effect_violation(outcome: Outcome, args: dict[str, Any], action: str) -> str | None:
    requests = [r for r in outcome.broker_requests if r.get("action") == action]
    if not requests:
        seen = [r.get("action") for r in outcome.broker_requests]
        return f"{outcome.tool}: no {action!r} request reached the broker (saw {seen})"
    wire = json.dumps(requests[-1])
    missing = [
        leaf
        for leaf in _leaves(args)
        if isinstance(leaf, str) and leaf and json.dumps(leaf) not in wire
    ]
    if missing:
        return (
            f"{outcome.tool}: the {action!r} request lacks model-supplied values {missing}; "
            f"the handler did not forward the arguments Hermes passed it: {wire[:400]}"
        )
    return None


def test_the_overlay_registers_a_real_population(harness):
    tools = harness.overlay_tools()
    assert len(tools) >= MIN_REGISTERED_TOOLS, (
        f"only {len(tools)} overlay tools registered through Hermes' loader "
        f"(floor {MIN_REGISTERED_TOOLS}); a sub-plugin probably failed to load: {tools}"
    )
    assert PINNED_TOOL in tools, f"{PINNED_TOOL} is not registered: {tools}"


def test_the_frozen_sets_name_registered_tools(harness):
    """A stale entry in either set would quietly shrink what is checked."""
    tools = set(harness.overlay_tools())
    stale = sorted((set(GATED) | set(BROKER_EFFECTS)) - tools)
    assert not stale, f"GATED/BROKER_EFFECTS name tools the overlay no longer registers: {stale}"
    assert not set(GATED) & set(BROKER_EFFECTS)


def test_every_overlay_tool_dispatches_through_hermes(harness, agent):
    violations: list[str] = []
    dispatched = 0
    for tool in harness.overlay_tools():
        args = scripted_args(tool)
        outcome = drive(harness, agent, tool, args)
        if tool in GATED:
            if outcome.dispatched:
                violations.append(
                    f"{tool}: now dispatches on this seat; move it out of GATED "
                    f"(result {outcome.result[:200]!r})"
                )
            elif GATED[tool] not in outcome.model_saw:
                violations.append(
                    f"{tool}: refused, but not with the expected reason "
                    f"{GATED[tool]!r}: {outcome.model_saw[:300]!r}"
                )
            continue
        if not outcome.dispatched:
            violations.append(
                f"{tool}: Hermes never dispatched it; refused before dispatch with "
                f"{outcome.model_saw[:300]!r}. If that refusal is by design, add it to GATED."
            )
            continue
        dispatched += 1
        if "TypeError" in outcome.result:
            violations.append(
                f"{tool}: Hermes cannot call this handler the way it dispatches "
                f"(handler(args, **kwargs)): {outcome.result[:300]!r}"
            )
        if tool in BROKER_EFFECTS:
            problem = _effect_violation(outcome, args, BROKER_EFFECTS[tool])
            if problem:
                violations.append(problem)
    assert not violations, f"[{agent.api_mode}] dispatch contract violations:\n  " + "\n  ".join(
        violations
    )
    assert dispatched >= MIN_REGISTERED_TOOLS, (
        f"only {dispatched} tools dispatched; the sweep measured too little to mean anything"
    )


def test_the_send_carries_the_model_payload(harness, agent):
    """The 08-13 pair, stated on its own: the send dispatches, and the broker
    receives the recipients, subject and body the model wrote, not an empty
    payload behind a "Sent" string."""
    args = {"to": [OWNER_EMAIL], "subject": "e2e subject", "text": "e2e body text"}
    outcome = drive(harness, agent, PINNED_TOOL, args)
    assert outcome.dispatched, outcome.describe()
    assert "TypeError" not in outcome.result, outcome.describe()
    sends = [r for r in outcome.broker_requests if r.get("action") == "agentmail_send"]
    assert len(sends) == 1, outcome.broker_requests
    payload = sends[0]["payload"]
    assert payload.get("to") == [OWNER_EMAIL]
    assert payload.get("subject") == "e2e subject"
    assert payload.get("text") == "e2e body text"
    assert outcome.model_saw.startswith("Sent"), outcome.model_saw


@pytest.mark.parametrize("tool", sorted(GATED))
def test_gated_reasons_are_overlay_gates(tool):
    """Every GATED reason is a substring authored in the overlay (not text a
    test made up), so the expectation is anchored in source."""
    from tests_e2e._seat import OVERLAY_ROOT

    needle = GATED[tool]
    sources = [
        *(OVERLAY_ROOT / "plugins").rglob("*.py"),
        *(OVERLAY_ROOT / "shared").rglob("*.py"),
    ]
    assert any(needle in path.read_text(encoding="utf-8") for path in sources), (
        f"{tool}: the expected refusal {needle!r} appears nowhere in plugins/ or shared/"
    )
