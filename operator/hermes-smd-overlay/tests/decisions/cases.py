"""The synthetic cases, as data (stdlib only: ``golden_manifest.py`` pins the
names).

FROZEN with ``recorder.py``. Each decision runs from one baseline (a rostered
colleague's reply that goes out; an autonomous send that is allowed). A case
changes the baseline with ``knobs`` (the scenario: no origin, another tool) and
``force`` (a recorded callee's result, set at the decision's own boundary, so
the case pins the ORDER the function consults things in, not the predicate
inside each callee, which the existing tests already pin). Forced values are
real instances of the real classes (``New``), never stand-ins.

Families, per decision:

* ``guard``: one per exit the function has (every ``return`` and every hold).
* ``condition``: the operands of each compound condition, short-circuit included.
* ``pair``: every two guards whose levers do not overlap, applied together, so
  swapping two guards changes which one fires.
* ``raise``: generated at run time (``drive.raise_cases``): for every case above,
  an error injected at each direct call in turn, deduplicated by the calls made
  before it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class New:
    """A real instance: ``path`` resolved against the decision's globals (``@``
    for an absolute module path, else a builtin), called with these arguments."""

    path: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()


def new(path: str, *args: Any, **kwargs: Any) -> New:
    return New(path, args, tuple(sorted(kwargs.items())))


@dataclass(frozen=True)
class Ref:
    """An existing object, by the same resolution as ``New``."""

    path: str


@dataclass(frozen=True)
class Seq:
    """One value per call, in order (the last repeats)."""

    values: tuple[Any, ...]


@dataclass(frozen=True)
class Case:
    name: str
    family: str
    knobs: tuple[tuple[str, Any], ...] = ()
    force: tuple[tuple[str, Any], ...] = ()
    #: Guards only: whether the case ends the call there (pairs use only these).
    exits: bool = True


def case(
    name: str, family: str, *, exits: bool = True, knobs: dict | None = None, **force: Any
) -> Case:
    # A force key is a dotted callee; ``__`` stands for ``.`` in a keyword.
    return Case(
        name,
        family,
        tuple(sorted((knobs or {}).items())),
        tuple(sorted((k.replace("__", "."), v) for k, v in force.items())),
        exits,
    )


def _err(kind: str, message: str) -> New:
    return new(kind, message)


#: A BaseException that is not an Exception (``drive.Interrupt``).
INTERRUPT = "@tests.decisions.drive.Interrupt"


# --------------------------------------------------------------------------
# The reply relay (_relay_draft)
# --------------------------------------------------------------------------

_DENIED = new("relay.RateDecision", False, "rate_limited")
_BINDING = {
    "binding.SESSION_BINDINGS.get": new(
        "binding.Binding",
        "greg@whitfield.example",
        (("job_id", "job-1"), ("kind", "status_job")),
        "graph-msg-1",
    )
}
_CODE_SENT = {
    "binding.SESSION_BINDINGS.get": new(
        "binding.Binding",
        "greg@whitfield.example",
        (("job_id", "job-2"), ("kind", "drafting_job")),
        "graph-msg-2",
    )
}
_MSGRAPH = {"_email_adapter": "msgraph", "_send_msgraph_reply": "graph-sent-1"}
_SHARED = {
    "_shared_reply_people": ("greg@whitfield.example", "dana@whitfield.example"),
    "relay.shared_recipients_locked": True,
}
_FACTS = new("matter_file_facts.Facts", "FILES: 2 attached", "", "", frozenset({"2"}))
_FACTS_HELD = new(
    "matter_file_facts.Facts", "FILES: 2 attached", "", "the facts did not check", frozenset()
)
_REVIEW = {"matter_file_facts.searched": True, "matter_file_reply.review_by_smd": True}
_ONE_BATCH = new("matter_file_reply.Prepared", ((),))
_TWO_BATCHES = new("matter_file_reply.Prepared", ((), ()))
_MISMATCH = new("matter_gate.MatterVerdict", "mismatch", "not a party", ("2026-PI-101",))
_UNRESOLVED = new("matter_gate.MatterVerdict", "unresolved", "no party list read", ("2026-PI-101",))


def _f(**kw: Any) -> dict[str, Any]:
    return {k.replace("__", "."): v for k, v in kw.items()}


RELAY: tuple[Case, ...] = (
    case("baseline", "guard", exits=False),
    case("not_ready", "guard", knobs={"infra_ready": False}),
    case(
        "not_a_draft_tool",
        "guard",
        knobs={"direct": True, "tool_name": "mcp_agentmail_send_message"},
    ),
    case("draft_failed", "guard", relay__draft_call_failed=True),
    case(
        "config_unreadable",
        "guard",
        CustomerConfig__from_volume=_err("CustomerConfigError", "bad yaml"),
    ),
    case("config_oserror", "guard", CustomerConfig__from_volume=_err("OSError", "unreadable")),
    case("no_origin", "guard", knobs={"origin": "none"}),
    case("duplicate", "guard", _REPLIED__committed=True),
    case(
        "code_sent_job",
        "guard",
        knobs={"origin": "none"},
        _deliver_job_reply="",
        **_f(**_CODE_SENT),
    ),
    case("not_on_roster", "guard", cfg__sender_on_roster=False),
    case("redraft_cap", "guard", held_notice__over_cap=True),
    case("shared_unsupported", "guard", _shared_reply_people=Ref("_SHARED_UNSUPPORTED")),
    case("recipient_mismatch", "guard", relay__recipient_locked=False),
    case("no_inbox", "guard", knobs={"inbox_id": ""}),
    case("bound_unsupported", "guard", knobs={"origin": "none"}, **_f(**_BINDING)),
    case("device_unsupported", "guard", cfg__device_reply_target="paula@whitfield.example"),
    case(
        "gate_refused",
        "guard",
        relay__gate_body=new("relay.GateResult", False, "fabrication", ("tier2",)),
    ),
    case("champion_wall", "guard", knobs={"champion": True}, champion__reply_wall="rep_marker"),
    case("champion_answered", "guard", exits=False, knobs={"champion": True}),
    case("say_other_words", "guard", knobs={"say": "other"}),
    case("say_matched", "guard", exits=False, knobs={"say": "match"}),
    case("say_none_beside_a_say", "guard", exits=False, knobs={"say": "none_and_match"}),
    # Tier 1 (review 2026-11-19): a reply naming the machinery, no say this turn.
    case(
        "mechanics_named",
        "guard",
        knobs={
            "args": {
                "to": ["greg@whitfield.example"],
                "subject": "Re: New matter",
                "text": "We received it. It is in the drafting queue.",
                "html": "",
            }
        },
    ),
    case("checklist_hold", "guard", _checklist_violations=("staff_ids", "name the matter")),
    case("advertising_hold", "guard", _advertising_violations=("rule_7_1", "remove the guarantee")),
    case("empty_body", "guard", relay__draft_body=("Re: x\n", "", "")),
    case("matter_mismatch", "guard", matter_gate__evaluate=_MISMATCH, matter_gate__mode="block"),
    case("matter_unresolved", "guard", exits=False, matter_gate__evaluate=_UNRESOLVED),
    case(
        "files_refused",
        "guard",
        _prepare_matter_files=_err("matter_file_reply.FileRefused", "two matters"),
    ),
    case("review_unsupported", "guard", _review_unsupported="cannot release here", **_f(**_REVIEW)),
    case("review_hold", "guard", _review_unsupported="", _hold_for_review=None, **_f(**_REVIEW)),
    case(
        "facts_hold",
        "guard",
        _file_facts=_FACTS_HELD,
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case("no_limiter", "guard", knobs={"limiter": False}),
    case(
        "files_rate_limited",
        "guard",
        _LIMITER__check=_DENIED,
        _prepare_matter_files=_ONE_BATCH,
        matter_file_reply__review_by_smd=False,
    ),
    case("shared_rate_limited", "guard", _LIMITER__check=_DENIED, **_f(**_SHARED)),
    case("rate_held", "guard", _LIMITER__check=_DENIED),
    case("rate_enqueued", "guard", _LIMITER__check=_DENIED, _enqueue_hold=True),
    case("queued_behind_held", "guard", _held_pending_for=True),
    case(
        "fixed_wording",
        "guard",
        relay__send_reply=_err("relay.FixedWordingRefused", "use the words"),
    ),
    case("send_failed", "guard", relay__send_reply=_err("relay.RelaySendError", "503")),
    case("send_unknown", "guard", relay__send_reply=_err("relay.RelayOutcomeUnknown", "timeout")),
    case("send_fault", "guard", relay__send_reply=_err("RuntimeError", "socket closed")),
    case("msgraph_sent", "guard", exits=False, **_f(**_MSGRAPH)),
    case(
        "bound_sent",
        "guard",
        exits=False,
        knobs={"origin": "none"},
        _send_bound_reply="bound-sent-1",
        **_f(**_BINDING, _email_adapter="msgraph"),
    ),
    case("shared_sent", "guard", exits=False, **_f(**_SHARED)),
    case(
        "files_sent",
        "guard",
        exits=False,
        _prepare_matter_files=_ONE_BATCH,
        matter_file_reply__review_by_smd=False,
        **_f(**_MSGRAPH),
    ),
    case(
        "files_two_replies",
        "guard",
        exits=False,
        _prepare_matter_files=_TWO_BATCHES,
        matter_file_reply__review_by_smd=False,
        _send_file_continuations=None,
        **_f(**_MSGRAPH),
    ),
    # Conditions: each operand of a compound test, short-circuit included.
    case("device_same_address", "condition", cfg__device_reply_target="GREG@whitfield.example "),
    case(
        "device_redirect_sent",
        "condition",
        cfg__device_reply_target="paula@whitfield.example",
        **_f(**_MSGRAPH),
    ),
    case(
        "bound_redirected",
        "condition",
        knobs={"origin": "none"},
        cfg__device_reply_target="paula@whitfield.example",
        **_f(**_BINDING, _email_adapter="msgraph"),
    ),
    case(
        "bound_internal_tainted",
        "condition",
        knobs={"origin": "none"},
        _session_tainted=True,
        _send_bound_reply="bound-sent-2",
        **_f(**_BINDING, _email_adapter="msgraph"),
    ),
    case(
        "bound_outside_recipient",
        "condition",
        knobs={"origin": "none"},
        classify_recipients_typed=Ref("RecipientClass.CLIENT"),
        _send_bound_reply="bound-sent-3",
        **_f(**_BINDING, _email_adapter="msgraph"),
    ),
    case("classify_raises", "condition", classify_recipients_typed=_err("RuntimeError", "roster")),
    case("vendor_recipient", "condition", classify_recipients_typed=Ref("RecipientClass.VENDOR")),
    case(
        "provenance_raises", "condition", provenance__register_for=_err("RuntimeError", "register")
    ),
    case(
        "facts_clean",
        "condition",
        _file_facts=_FACTS,
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case(
        "facts_unstated",
        "condition",
        _file_facts=_FACTS,
        matter_file_facts__unstated="page 4",
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case(
        "facts_missing",
        "condition",
        _file_facts=_FACTS,
        matter_file_facts__missing="the facts marker is still in the body",
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case(
        "checklist_facts_held",
        "condition",
        _file_facts=_FACTS_HELD,
        _checklist_violations=("staff_ids", "x"),
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case(
        "checklist_facts_only",
        "condition",
        _file_facts=_FACTS,
        _checklist_violations=Seq((("staff_ids", "x"), None)),
        _review_unsupported="",
        _hold_for_review=None,
    ),
    case(
        "checklist_model_words",
        "condition",
        _file_facts=_FACTS,
        _checklist_violations=Seq((("staff_ids", "x"), ("staff_ids", "y"))),
    ),
    case(
        "checklist_no_facts_text",
        "condition",
        _file_facts=new("matter_file_facts.Facts", "", "", "", frozenset()),
        _checklist_violations=("staff_ids", "x"),
    ),
    case(
        "html_only_body",
        "condition",
        relay__draft_body=("Re: x\n<p>Hi</p>", "", "<p>Hi there.</p>"),
    ),
    case(
        "mismatch_report_mode",
        "condition",
        matter_gate__evaluate=_MISMATCH,
        matter_gate__mode="report",
    ),
    case(
        "unresolved_without_matters",
        "condition",
        matter_gate__evaluate=new("matter_gate.MatterVerdict", "unresolved", "no party list read"),
    ),
    case(
        "review_not_by_smd",
        "condition",
        matter_file_facts__searched=True,
        matter_file_reply__review_by_smd=False,
    ),
    case(
        "files_review_hold",
        "condition",
        _prepare_matter_files=_ONE_BATCH,
        matter_file_reply__review_by_smd=True,
        _hold_for_review=None,
    ),
    case("rate_enqueue_failed", "condition", _LIMITER__check=_DENIED, _enqueue_hold=False),
    case(
        "rate_no_reason",
        "condition",
        _LIMITER__check=new("relay.RateDecision", False),
        _enqueue_hold=True,
    ),
    case(
        "send_unknown_msgraph",
        "condition",
        **_f(_email_adapter="msgraph", _send_msgraph_reply=_err("relay.RelayOutcomeUnknown", "t")),
    ),
    case(
        "send_failed_bound",
        "condition",
        knobs={"origin": "none"},
        _send_bound_reply=_err("relay.RelaySendError", "x"),
        **_f(**_BINDING, _email_adapter="msgraph"),
    ),
    case("tool_call_id_absent", "condition", knobs={"tool_call_id": None, "session_id": None}),
    case("no_tool_name", "condition", knobs={"direct": True, "tool_name": None}),
    case("args_not_a_dict", "condition", knobs={"args": None}),
    case(
        "review_unsupported_facts_held",
        "condition",
        _file_facts=_FACTS_HELD,
        _review_unsupported="cannot release here",
    ),
    case("no_sender_key", "condition", sender_key=""),
    # A BaseException that is not an Exception passes every handler: each
    # ``except Exception`` lets it through, and the hook does not swallow it.
    case("interrupt_in_classify", "condition", classify_recipients_typed=_err(INTERRUPT, "stop")),
    case("interrupt_in_provenance", "condition", provenance__register_for=_err(INTERRUPT, "stop")),
    case("interrupt_in_send", "condition", relay__send_reply=_err(INTERRUPT, "stop")),
    case("interrupt_in_flow", "condition", _file_facts=_err(INTERRUPT, "stop")),
)


# --------------------------------------------------------------------------
# The trust wall (_evaluate_tool_call)
# --------------------------------------------------------------------------


def _decision(allowed: bool, audit: str, action: str, ceiling: str | None, reason: str) -> New:
    return new(
        "EnforcementDecision",
        allowed,
        reason,
        audit,
        Ref(f"ActionClass.{action}"),
        authored_ceiling=Ref(f"Ceiling.{ceiling}") if ceiling else None,
        effective_ceiling=Ref(f"Ceiling.{ceiling}") if ceiling else None,
    )


_AWAIT = _decision(
    False, "await_approval", "EXTERNAL_SEND", "CONFIRM", "confirm: held for approval"
)
_DRAFT = _decision(False, "draft", "EXTERNAL_SEND", "DRAFT_FOR_REVIEW", "draft: routed to review")
_REFUSE = _decision(False, "refuse", "EXTERNAL_SEND", None, "unauthored: refused")
_ALLOW = _decision(True, "allow", "EXTERNAL_SEND", "AUTONOMOUS", "autonomous")
_BLOCK = {"action": "block", "message": "Refused: forced by the corpus"}
_MULTI = ("2026-PI-101", "2026-PI-102")
_STORED = new(
    "@shared.pending_send.PendingSend",
    "mcp_agentmail_send_message",
    {"to": ["pat@client.example"], "subject": "Reviewed", "text": "The reviewed words."},
    frozenset({"pat@client.example"}),
    1.0,
    approved=True,
)
_ACT = new(
    "@shared.pending_acts.ConfirmedAct",
    "prop-1",
    "mcp_smokeball_create_matter",
    {"matter": "2026-PI-101", "instructed_by": "dana"},
    "dana@firm.example",
    "dana@firm.example",
    "msg-confirm-1",
    1.0,
)
_COMMIT = {"_reclassify_send": Ref("ActionClass.COMMITMENT")}

WALL: tuple[Case, ...] = (
    case("baseline", "guard", exits=False),
    case("empty_tool", "guard", knobs={"tool_name": ""}),
    case("banned", "guard", knobs={"tool_name": "email_forward"}),
    case("mixing_fence", "guard", matter_gate__content_read_refusal="two matters' documents"),
    case(
        "egress_refused",
        "guard",
        knobs={"tool_name": "web_search", "args": {"query": "who is 2026-PI-101"}},
        search_egress__evaluate=new(
            "search_egress.EgressVerdict", "refused_screen", 18, "matter_number"
        ),
    ),
    case(
        "egress_pass",
        "guard",
        exits=False,
        knobs={"tool_name": "web_search", "args": {"query": "court holidays"}},
        search_egress__evaluate=new("search_egress.EgressVerdict", "pass", 14),
    ),
    case(
        "volume_report",
        "guard",
        exits=False,
        read_volume__evaluate_read=new("read_volume.Verdict", None, "crossed 300 pages"),
    ),
    case(
        "volume_refusal",
        "guard",
        read_volume__evaluate_read=new("read_volume.Verdict", "over 300 pages"),
    ),
    case(
        "exposure_raises_read",
        "guard",
        knobs={"tool_name": "mcp_agentmail_get_thread", "args": {"thread_id": "t1"}},
        _resolve_persona_exposure=_err("RuntimeError", "bad yaml"),
    ),
    case(
        "exposure_raises_send", "guard", _resolve_persona_exposure=_err("RuntimeError", "bad yaml")
    ),
    case(
        "send_as_proposed",
        "guard",
        send_as__requested_from="dana@firm.example",
        enforce=_decision(False, "await_approval", "EXTERNAL_SEND_AS_STAFF", "CONFIRM", "confirm"),
        send_as__propose={"action": "block", "message": "Proposed to dana"},
    ),
    case(
        "send_as_refused",
        "guard",
        send_as__requested_from="dana@firm.example",
        enforce=_decision(False, "refuse", "EXTERNAL_SEND_AS_STAFF", None, "unauthored"),
    ),
    case(
        "commitment_proposed",
        "guard",
        enforce=_decision(False, "await_approval", "COMMITMENT", "CONFIRM", "confirm"),
        _propose_commitment_act={"action": "block", "message": "Proposed to dana"},
        **_f(**_COMMIT),
    ),
    case("await_captured", "guard", enforce=_AWAIT),
    case("matter_mismatch", "guard", matter_gate__evaluate=_MISMATCH, matter_gate__mode="block"),
    case(
        "multi_matter",
        "guard",
        matter_gate__multi_matter_session=_MULTI,
        matter_gate__multi_matter_mode="block",
    ),
    case(
        "mismatch_withheld_annotated",
        "guard",
        enforce=_DRAFT,
        matter_gate__evaluate=_MISMATCH,
        matter_gate__mode="block",
        matter_gate__multi_matter_session=_MULTI,
    ),
    case("structure_block", "guard", spec_gate__check_structure_floor=_BLOCK),
    case("content_floor_block", "guard", _apply_content_floor=_BLOCK),
    case("voice_block", "guard", voice_gate__check_voice_gate=_BLOCK),
    case("spec_block", "guard", spec_gate__check_spec_gate=_BLOCK),
    case("refused", "guard", enforce=_REFUSE),
    case("drafted", "guard", enforce=_DRAFT),
    case(
        "approved_replay",
        "guard",
        exits=False,
        PENDING_SEND__approved_match=_STORED,
        PENDING_SEND__take_for_send=_STORED,
    ),
    case(
        "confirmed_commitment",
        "guard",
        exits=False,
        enforce=_decision(True, "allow", "COMMITMENT", "AUTONOMOUS", "approved"),
        PENDING_ACTS__peek_confirmed=_ACT,
        PENDING_ACTS__take_in_flight=_ACT,
        **_f(**_COMMIT),
    ),
    # Conditions.
    case("tainted_turn", "condition", SESSION_TAINT__trust_class="unknown_external"),
    case(
        "tainted_code_fixed",
        "condition",
        knobs={"code_fixed_recipients": True},
        SESSION_TAINT__trust_class="unknown_external",
    ),
    case(
        "web_derived_admin",
        "condition",
        SESSION_TAINT__trust_class="web_derived",
        _web_derived_admin_send=True,
    ),
    case(
        "web_derived_other",
        "condition",
        SESSION_TAINT__trust_class="web_derived",
        _web_derived_admin_send=False,
    ),
    case(
        "turn_approval_arg",
        "condition",
        knobs={
            "args": {"to": ["pat@client.example"], "text": "Hi.", "_current_turn_approval": True}
        },
    ),
    case(
        "send_internal_class",
        "condition",
        _reclassify_send=Ref("ActionClass.EXTERNAL_SEND_INTERNAL"),
    ),
    case(
        "send_client_class", "condition", _reclassify_send=Ref("ActionClass.EXTERNAL_SEND_CLIENT")
    ),
    case(
        "send_vendor_class", "condition", _reclassify_send=Ref("ActionClass.EXTERNAL_SEND_VENDOR")
    ),
    case("not_a_send", "condition", _reclassify_send=Ref("ActionClass.INTERNAL_WRITE")),
    case(
        "destructive_act",
        "condition",
        _reclassify_send=Ref("ActionClass.DESTRUCTIVE"),
        act_broker__is_call_payload_act=True,
        enforce=_decision(False, "await_approval", "DESTRUCTIVE", "CONFIRM", "c"),
        _propose_commitment_act=_BLOCK,
    ),
    case(
        "destructive_plain",
        "condition",
        _reclassify_send=Ref("ActionClass.DESTRUCTIVE"),
        act_broker__is_call_payload_act=False,
    ),
    case("no_recipients", "condition", _resolve_send_recipients=frozenset()),
    case(
        "mismatch_report_mode",
        "condition",
        matter_gate__evaluate=_MISMATCH,
        matter_gate__mode="report",
    ),
    case(
        "multi_report_mode",
        "condition",
        matter_gate__multi_matter_session=_MULTI,
        matter_gate__multi_matter_mode="report",
    ),
    case(
        "multi_not_allowed", "condition", enforce=_DRAFT, matter_gate__multi_matter_session=_MULTI
    ),
    case(
        "not_autonomous_allowed",
        "condition",
        enforce=_decision(True, "allow", "EXTERNAL_SEND", "CONFIRM", "approved"),
    ),
    case(
        "templated_body",
        "condition",
        knobs={"args": {"to": ["pat@client.example"], "text": "Hi.", "_smd_templated_body": True}},
    ),
    case(
        "approved_replay_spent",
        "condition",
        PENDING_SEND__approved_match=_STORED,
        PENDING_SEND__take_for_send=None,
    ),
    case(
        "approved_args_not_dict",
        "condition",
        knobs={"args": None},
        PENDING_SEND__approved_match=_STORED,
    ),
    case(
        "confirmed_args_not_dict",
        "condition",
        knobs={"args": None},
        PENDING_ACTS__peek_confirmed=_ACT,
        **_f(**_COMMIT),
    ),
    case("await_no_recipients", "condition", enforce=_AWAIT, _resolve_send_recipients=frozenset()),
    case("banned_no_persona", "condition", knobs={"tool_name": "email_forward", "persona": ""}),
    case(
        "egress_args_none",
        "condition",
        knobs={"tool_name": "web_search", "args": None},
        search_egress__evaluate=new("search_egress.EgressVerdict", "pass", 0),
    ),
    case(
        "web_derived_args_none",
        "condition",
        knobs={"args": None},
        SESSION_TAINT__trust_class="web_derived",
    ),
    case(
        "approved_args_none",
        "condition",
        knobs={"args": None},
        _resolve_send_recipients=frozenset({"pat@client.example"}),
        PENDING_SEND__approved_match=_STORED,
    ),
    case(
        "mismatch_no_matters",
        "condition",
        matter_gate__evaluate=new("matter_gate.MatterVerdict", "mismatch", "not a party"),
        matter_gate__mode="block",
    ),
    case(
        "commitment_allowed_unconfirmed",
        "condition",
        enforce=_decision(True, "allow", "COMMITMENT", "AUTONOMOUS", "autonomous"),
        **_f(**_COMMIT),
    ),
    case("interrupt_in_exposure", "condition", _resolve_persona_exposure=_err(INTERRUPT, "stop")),
)

CASES: dict[str, tuple[Case, ...]] = {"relay": RELAY, "wall": WALL}


def pairs(cases: tuple[Case, ...]) -> list[Case]:
    """Every two exiting guards whose levers agree wherever they touch the same
    name (two rate holds both need the limiter to refuse; they still pair)."""
    guards = [c for c in cases if c.family == "guard" and c.exits]
    out = []
    for a, b in itertools.combinations(guards, 2):
        knobs, force = dict(a.knobs), dict(a.force)
        if any(k in knobs and knobs[k] != v for k, v in b.knobs):
            continue
        if any(k in force and force[k] != v for k, v in b.force):
            continue
        knobs.update(b.knobs)
        force.update(b.force)
        name = f"{a.name}+{b.name}"
        out.append(Case(name, "pair", tuple(sorted(knobs.items())), tuple(sorted(force.items()))))
    return out


def case_names() -> list[str]:
    return [f"{key}/{c.family}/{c.name}" for key, cs in CASES.items() for c in (*cs, *pairs(cs))]


__all__ = ["CASES", "Case", "New", "Ref", "Seq", "case_names", "new", "pairs"]
