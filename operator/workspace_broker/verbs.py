"""The broker's verb table: every verb names who may call it, and the
dispatcher checks that before anything else.

WHY A TABLE (code review 2026-09-10, Architecture 1, carried three reviews).
``Broker.handle`` was a 313-line if-chain at cyclomatic complexity 51 whose
authorization was POSITIONAL: a gateway-PID gate sat two thirds of the way
down, five hand-rolled agent-uid checks sat above it, and whether a verb was
gated at all depended on where in the chain someone had typed it. A verb
added above the gate was ungated by accident; the medchron group already
showed the alternative (a table with ``_gate`` as its first statement).

THE CONTRACT. Each ``Verb`` declares an ``auth`` set. ``dispatch`` resolves
the peer's classes from the socket credentials (pid, uid) FIRST and refuses
with ``PermissionError`` unless at least one declared class matches; only
then does it call the handler. A handler never sees an unauthorized caller,
and none re-checks the peer. ``tests/broker-verb-registry.test.ts`` pins the
verb set and that every row carries an auth class;
``workspace_broker/tests/test_verb_table.py`` drives this dispatcher with a
wrong peer for every verb and asserts the refusal, so the gate is
behavioural, not textual.

THE FOUR CLASSES.

``GATEWAY``    the peer PID is the gateway process. Reserved for verbs that
               must not be reachable from a cron pre_run child or an
               execute_code turn: the generic audit append, both transmit
               channels, the job control plane, the reviewed-Workspace pair.
``AGENT``      the peer uid is the agent uid (resolved lazily, see
               ``Broker._resolve_agent_uid``; unresolvable stays fail-closed).
               The one-pinned-action_type appenders, the escalation ledger,
               the establishment verbs.
``ROOT``       uid 0: the runner daemon, the rehearsal, a Captain-side skill.
``ANY``        no gate. Only ``health``, which is read-only.

A verb may declare several classes (any-of); the medchron group does, and its
own ``_gate`` still runs behind this one with the same answer.

Unknown actions are gateway-gated and then refused by name, in the order and
vocabulary the old fall-through used (``workspace_verbs.unknown_action``,
``job_verbs.unknown_job_action``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import audit_verbs, establish_verbs, job_verbs, transmit_verbs, workspace_verbs
from .medchron_verbs import medchron_dispatch
from .send_witness import append_escalation_event

Handler = Callable[[Any, str, dict[str, Any], int, "int | None"], dict[str, Any]]

GATEWAY = "gateway"
AGENT = "agent_uid"
ROOT = "root"
ANY = "any"
AUTH_CLASSES: frozenset[str] = frozenset({GATEWAY, AGENT, ROOT, ANY})


@dataclass(frozen=True)
class Verb:
    name: str
    auth: frozenset[str]
    handler: Handler

    def __post_init__(self) -> None:
        if not self.auth or not self.auth <= AUTH_CLASSES:
            raise ValueError(f"verb {self.name!r} declares an unknown or empty auth set: {sorted(self.auth)}")


def _only(cls: str) -> frozenset[str]:
    return frozenset({cls})


# The medchron group's compound gates, as documented in medchron_verbs.py.
GATEWAY_OR_ROOT = frozenset({GATEWAY, ROOT})
GATEWAY_ROOT_OR_AGENT = frozenset({GATEWAY, ROOT, AGENT})
ROOT_OR_AGENT = frozenset({ROOT, AGENT})


def _medchron(broker: Any, action: str, request: dict[str, Any], pid: int, uid: int | None) -> dict[str, Any]:
    return medchron_dispatch(broker.medchron, action, request, pid, uid)


def _escalation(broker: Any, _action: str, request: dict[str, Any], _pid: int, uid: int | None) -> dict[str, Any]:
    # Body lives in send_witness.append_escalation_event, beside the witness
    # it has to consult. It re-checks the agent uid with the same sentence;
    # that is defence in depth, not a second policy.
    return append_escalation_event(broker, request, uid)


def _health(broker: Any, _action: str, _request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    return {
        "ok": True,
        "credential_ready": broker.credential_path.is_file(),
        "customer_ready": broker.customer_path.is_file(),
        "audit_ready": broker.ledger is not None,
        "jobs_ready": broker.job_ledger is not None,
        "supported_ops": broker.operations.supported_operations(),
    }


# One line per verb ON PURPOSE: tests/broker-verb-registry.test.ts reads this
# table by regex, so a reviewer sees a verb (and its gate) being added in the
# diff, and the registry test goes red until EXPECTED_VERBS names it too.
VERBS: tuple[Verb, ...] = (
    Verb("health", _only(ANY), _health),
    # One-pinned-action_type appenders reachable from cron pre_run children
    # and execute_code turns (agent uid, non-gateway PID).
    Verb("suppressed_wake_append", _only(AGENT), audit_verbs.suppressed_wake_append),
    Verb("emitted_wake_append", _only(AGENT), audit_verbs.emitted_wake_append),
    Verb("webhook_suppressed_append", _only(AGENT), audit_verbs.webhook_suppressed_append),
    Verb("correction_propose", _only(AGENT), audit_verbs.correction_propose),
    Verb("escalation_event_append", _only(AGENT), _escalation),
    # ADR 0085 establishment (fourteen verbs, one handler, one lock).
    Verb("establish_stage_document", _only(AGENT), establish_verbs.establish),
    Verb("establish_propose", _only(AGENT), establish_verbs.establish),
    Verb("establish_pending", _only(AGENT), establish_verbs.establish),
    Verb("establish_submit", _only(AGENT), establish_verbs.establish),
    Verb("establish_status", _only(AGENT), establish_verbs.establish),
    Verb("act_propose", _only(AGENT), establish_verbs.establish),
    Verb("act_commit", _only(AGENT), establish_verbs.establish),
    Verb("establish_decline", _only(AGENT), establish_verbs.establish),
    Verb("establish_lapse_notified", _only(AGENT), establish_verbs.establish),
    Verb("establish_notify_claim", _only(AGENT), establish_verbs.establish),
    Verb("establish_notify_release", _only(AGENT), establish_verbs.establish),
    Verb("ops_propose", _only(AGENT), establish_verbs.establish),
    Verb("ops_resolve", _only(AGENT), establish_verbs.establish),
    Verb("ops_ask_sent", _only(AGENT), establish_verbs.establish),
    # ss#2614 routine 11; compound gates per medchron_verbs.py.
    Verb("medchron_job_submit", GATEWAY_OR_ROOT, _medchron),
    Verb("medchron_job_status", GATEWAY_ROOT_OR_AGENT, _medchron),
    Verb("medchron_allowance", GATEWAY_ROOT_OR_AGENT, _medchron),
    Verb("medchron_job_list", ROOT_OR_AGENT, _medchron),
    Verb("medchron_job_record", _only(ROOT), _medchron),
    # Gateway-only from here down.
    Verb("audit_append", _only(GATEWAY), audit_verbs.audit_append),
    Verb("agentmail_send", _only(GATEWAY), transmit_verbs.agentmail),
    Verb("agentmail_reply", _only(GATEWAY), transmit_verbs.agentmail),
    Verb("msgraph_send", _only(GATEWAY), transmit_verbs.msgraph),
    Verb("msgraph_reply", _only(GATEWAY), transmit_verbs.msgraph),
    Verb("job_create", _only(GATEWAY), job_verbs.job_create),
    Verb("job_list_claimable", _only(GATEWAY), job_verbs.job_list_claimable),
    Verb("job_list", _only(GATEWAY), job_verbs.job_list),
    Verb("job_read", _only(GATEWAY), job_verbs.job_read),
    Verb("job_cancel", _only(GATEWAY), job_verbs.job_cancel),
    Verb("job_claim", _only(GATEWAY), job_verbs.job_claim),
    Verb("job_heartbeat", _only(GATEWAY), job_verbs.job_heartbeat),
    Verb("job_record", _only(GATEWAY), job_verbs.job_record),
    Verb("job_idem_begin", _only(GATEWAY), job_verbs.job_idem_begin),
    Verb("job_idem_complete", _only(GATEWAY), job_verbs.job_idem_complete),
    Verb("authorize", _only(GATEWAY), workspace_verbs.authorize),
    Verb("execute", _only(GATEWAY), workspace_verbs.execute),
)

TABLE: dict[str, Verb] = {verb.name: verb for verb in VERBS}
if len(TABLE) != len(VERBS):
    raise RuntimeError("duplicate verb name in the broker verb table")
if set(establish_verbs.VERBS) != {v.name for v in VERBS if v.handler is establish_verbs.establish}:
    raise RuntimeError("the establishment rows above and establish_verbs.VERBS disagree")


def peer_classes(broker: Any, peer_pid: int, peer_uid: int | None) -> frozenset[str]:
    """The auth classes this peer satisfies, from its socket credentials."""
    classes = {ANY}
    if peer_pid == broker.gateway_pid:
        classes.add(GATEWAY)
    if peer_uid == 0:
        classes.add(ROOT)
    agent_uid = broker._resolve_agent_uid()
    if agent_uid is not None and peer_uid == agent_uid:
        classes.add(AGENT)
    return frozenset(classes)


def _refusal(action: str, auth: frozenset[str]) -> PermissionError:
    if auth == _only(GATEWAY):
        return PermissionError("request did not originate from the gateway process")
    if auth == _only(AGENT):
        return PermissionError(f"{action} requires a caller running as the agent uid")
    return PermissionError(f"{action} is not permitted for this caller")


def check_auth(broker: Any, action: str, auth: frozenset[str], peer_pid: int, peer_uid: int | None) -> None:
    if not (auth & peer_classes(broker, peer_pid, peer_uid)):
        raise _refusal(action, auth)


def dispatch(broker: Any, request: dict[str, Any], peer_pid: int, peer_uid: int | None) -> dict[str, Any]:
    action = request.get("action")
    verb = TABLE.get(action) if isinstance(action, str) else None
    if verb is not None:
        check_auth(broker, verb.name, verb.auth, peer_pid, peer_uid)
        return verb.handler(broker, verb.name, request, peer_pid, peer_uid)
    name = action if isinstance(action, str) else ""
    check_auth(broker, name, _only(GATEWAY), peer_pid, peer_uid)
    if name.startswith("job_"):
        return job_verbs.unknown_job_action(broker, name, request, peer_pid, peer_uid)
    return workspace_verbs.unknown_action(broker, name, request, peer_pid, peer_uid)
