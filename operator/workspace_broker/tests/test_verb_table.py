"""The verb table gates before it dispatches, for every verb, behaviourally.

WHAT THIS PINS (code review 2026-09-10, Architecture 1). Authorization in the
broker used to be positional: whether a verb was gated depended on where in a
313-line if-chain it sat. The table in ``verbs.py`` makes the gate a declared
property of each verb. These tests do not read the table's text; they drive
``dispatch`` with a peer that satisfies NO class and assert every verb refuses
with ``PermissionError`` before any handler runs, then drive each verb with a
peer that satisfies one of its classes and assert the refusal is NOT a
``PermissionError`` (the handler may still refuse a broker built via
``__new__`` with a ``ValueError``, which is the point: the gate ran first and
passed, and only then did the verb look at its own configuration).

WHAT WOULD MAKE IT FALSE (Law 12). Move ``check_auth`` after the handler call
and the wrong-peer case gets a ``ValueError`` from an unconfigured ledger
instead of the refusal. Register a verb with an empty or unknown auth set and
``Verb.__post_init__`` raises at import. Add a verb without a ``Verb`` entry
and the fall-through refuses it by name, which the last test pins.

Run::

    cd operator && python3 -m pytest workspace_broker/tests/test_verb_table.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import verbs
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 1001
STRANGER_PID = 9999
STRANGER_UID = 2002


def _bare_broker() -> Broker:
    """A broker with credentials to compare against and nothing configured, so
    a handler that runs can only refuse with a ValueError about its config."""
    broker = Broker.__new__(Broker)
    broker.customer_slug = "seat"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = None
    broker.job_ledger = None
    broker.establishment = None
    broker.agentmail = None
    broker.msgraph = None
    broker.medchron = None
    broker.escalation_ledger_path = None
    return broker


def _peer_for(cls: str) -> tuple[int, int | None]:
    return {
        verbs.GATEWAY: (GATEWAY_PID, STRANGER_UID),
        verbs.AGENT: (STRANGER_PID, AGENT_UID),
        verbs.ROOT: (STRANGER_PID, 0),
        verbs.ANY: (STRANGER_PID, STRANGER_UID),
    }[cls]


def test_every_verb_declares_a_known_non_empty_auth_set() -> None:
    assert len(verbs.VERBS) >= 40
    for verb in verbs.VERBS:
        assert verb.auth, verb.name
        assert verb.auth <= verbs.AUTH_CLASSES, (verb.name, verb.auth)
    assert len({v.name for v in verbs.VERBS}) == len(verbs.VERBS)


def test_an_unknown_auth_class_cannot_be_registered() -> None:
    with pytest.raises(ValueError, match="unknown or empty auth set"):
        verbs.Verb("bogus", frozenset({"captain"}), lambda *_: {})
    with pytest.raises(ValueError, match="unknown or empty auth set"):
        verbs.Verb("bogus", frozenset(), lambda *_: {})


@pytest.mark.parametrize("verb", [v for v in verbs.VERBS if verbs.ANY not in v.auth], ids=lambda v: v.name)
def test_a_peer_in_no_class_is_refused_before_the_handler_runs(verb: verbs.Verb) -> None:
    broker = _bare_broker()
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, {"action": verb.name}, STRANGER_PID, STRANGER_UID)


@pytest.mark.parametrize("verb", [v for v in verbs.VERBS if verbs.ANY not in v.auth], ids=lambda v: v.name)
def test_each_declared_class_opens_the_gate(verb: verbs.Verb) -> None:
    """For every class a verb declares, a peer in that class gets PAST the
    gate: whatever happens next is the handler's business, never a refusal of
    the caller. A gate that refused a declared class would be a table that
    lies about who may call."""
    for cls in verb.auth:
        broker = _bare_broker()
        pid, uid = _peer_for(cls)
        try:
            verbs.dispatch(broker, {"action": verb.name}, pid, uid)
        except PermissionError as exc:
            # The medchron group re-checks behind the table with its own
            # message; the table must still have agreed with it.
            raise AssertionError(f"{verb.name} refused a peer in its declared class {cls}: {exc}") from exc
        except Exception:  # noqa: BLE001 - past the gate, an unconfigured handler refuses however it likes
            pass


def test_the_agent_class_is_fail_closed_when_the_agent_uid_is_unresolvable() -> None:
    broker = _bare_broker()
    broker.agent_uid = None
    verb = verbs.TABLE["suppressed_wake_append"]
    assert verb.auth == frozenset({verbs.AGENT})
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, {"action": verb.name}, STRANGER_PID, AGENT_UID)


def test_health_is_the_only_ungated_verb_and_answers_any_peer(tmp_path: Path) -> None:
    ungated = sorted(v.name for v in verbs.VERBS if verbs.ANY in v.auth)
    assert ungated == ["health"]
    broker = _bare_broker()
    broker.credential_path = tmp_path / "credential.json"
    broker.customer_path = tmp_path / "customer.yaml"
    broker.customer_path.write_text("customer_id: seat\n")

    class _Ops:
        def supported_operations(self) -> list[str]:
            return ["workspace_noop"]

    broker.operations = _Ops()
    reply = verbs.dispatch(broker, {"action": "health"}, STRANGER_PID, STRANGER_UID)
    assert reply == {
        "ok": True,
        "credential_ready": False,
        "customer_ready": True,
        "audit_ready": False,
        "jobs_ready": False,
        "supported_ops": ["workspace_noop"],
    }


def test_an_unknown_action_is_gateway_gated_then_refused_by_name() -> None:
    broker = _bare_broker()
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, {"action": "audit_update"}, STRANGER_PID, AGENT_UID)
    with pytest.raises(ValueError, match="operation and object payload are required"):
        verbs.dispatch(broker, {"action": "audit_update"}, GATEWAY_PID, AGENT_UID)
    with pytest.raises(ValueError, match="unsupported job action: job_bogus"):
        broker.job_ledger = object()
        verbs.dispatch(broker, {"action": "job_bogus"}, GATEWAY_PID, AGENT_UID)


def test_refusal_vocabulary_per_class() -> None:
    broker = _bare_broker()
    with pytest.raises(PermissionError, match="did not originate from the gateway process"):
        verbs.dispatch(broker, {"action": "audit_append"}, STRANGER_PID, AGENT_UID)
    with pytest.raises(PermissionError, match="requires a caller running as the agent uid"):
        verbs.dispatch(broker, {"action": "emitted_wake_append"}, GATEWAY_PID, STRANGER_UID)
    with pytest.raises(PermissionError, match="is not permitted for this caller"):
        verbs.dispatch(broker, {"action": "medchron_job_record"}, GATEWAY_PID, AGENT_UID)
