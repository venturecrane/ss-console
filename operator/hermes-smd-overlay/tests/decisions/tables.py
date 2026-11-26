"""What the decision corpus records, as data (stdlib only, so
``operator/bin/goldens/golden_manifest.py`` can pin it from any interpreter).

FROZEN for the split PRs (wave 4, B and C). A change here changes the
instrument, not the code it measures; ``golden_manifest.py`` pins every table
below, so a dropped entry fails the integrity job unless
``bin/goldens/allowed-instrument-changes.json`` names it.

Names are dotted as the decision function writes them and resolve against the
globals of the function that makes the call (the decision itself, or a helper
the census follows into). A name starting ``@`` is absolute: a module in
``sys.modules`` and an attribute path under it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """One decision function and how its calls are recorded."""

    key: str
    #: The function's name and its file under the overlay.
    name: str
    file: str
    #: Called directly by the decision (or by a helper it calls that is in none
    #: of these tables): recorded in order, with arguments and result.
    wrap: tuple[str, ...]
    #: A method called on a local value: the name as written, and the class
    #: attribute the recorder wraps (resolved against the same globals).
    local: tuple[tuple[str, str], ...]
    #: Called but not recorded, each with why that loses nothing.
    unwrapped: tuple[tuple[str, str], ...]
    #: Effects that leave the function, recorded at ANY depth (inside a wrapped
    #: call too), so a row a helper writes is in the record.
    edges: tuple[str, ...]
    #: Module state snapshotted before and after each call.
    registers: tuple[str, ...]
    #: The subset of ``registers`` cleared to a fresh instance before each test
    #: or case, so no record depends on what ran before it.
    resets: tuple[str, ...]
    #: The plugin's other modules as they were when the corpus was recorded. A
    #: function the decision calls that no table names, defined in the
    #: decision's own file or in a module NOT listed here (one a split adds), is
    #: a helper the census follows into; one from a module listed here is an
    #: unrecorded call.
    library: tuple[str, ...] = ()


_LOG = (
    ("logger.debug", "a log line is not a decision"),
    ("logger.info", "a log line is not a decision"),
    ("logger.warning", "a log line is not a decision"),
    ("logger.exception", "a log line is not a decision"),
)

_REPLY_REGISTERS = (
    "_REPLIED",
    "_HOLD_COUNTS",
    "_HELD_LINES",
    "_ACT_BODIES",
    "_WORKING_DRAFTS",
    "_JOB_REPLIES",
    "_HOLD_NOTICES",
    "_DECIDED",
    "_CURRENT_CALL",
    "_TURN_SENDS",
    "_TEXT_SEEN",
    "_TEXT_DELIVERED",
    "_LIMITER",
    "binding.SESSION_BINDINGS",
)

#: Shared registers both functions can read or write.
SHARED_REGISTERS = (
    "@shared.inbound.SESSION_INBOUND_ORIGIN",
    "@shared.inbound.SESSION_TAINT",
    "@shared.pending_send.PENDING_SEND",
    "@shared.pending_acts.PENDING_ACTS",
    "@shared.trust_decision.TRUST_DECISIONS",
    "@shared.casework_acts.CASEWORK_ACTS",
    "@shared.read_volume._sessions",
    "@shared.read_volume._unbound_routes",
    "@shared.search_egress._verdicts",
    "@shared.search_egress._names",
    "@shared.search_egress._client_contacts",
    "@shared.search_egress._pending_names",
    "@shared.send_anchor._SCOPE",
    "@shared.turn_sources.TURN_SOURCES",
)

#: Effects both functions can reach through a helper: the broker socket (every
#: module's alias of it is wrapped too), the Graph broker's one call path, and
#: the spec gate's own audit client.
SHARED_EDGES = (
    "@shared.workspace_broker.request",
    "@shared.msgraph_broker._call",
    "@shared.spec_gate._emit_spec_gate_audit",
)

RELAY = Decision(
    key="relay",
    name="_relay_draft",
    file="plugins/hermes-smd-reply/__init__.py",
    wrap=(
        "CustomerConfig.from_volume",
        "_ACT_BODIES.note",
        "_HOLD_COUNTS.count",
        "_HOLD_NOTICES.pending",
        "_LIMITER.check",
        "_REPLIED.committed",
        "_WORKING_DRAFTS.take",
        "_WORKING_DRAFTS.track",
        "_advertising_violations",
        "_checklist_violations",
        "_commit_reply",
        "_deliver_job_reply",
        "_discard_drafts",
        "_email_adapter",
        "_emit_reply_event",
        "_enqueue_hold",
        "_file_facts",
        "_held",
        "_held_pending_for",
        "_hold_for_review",
        "_is_job_reply",
        "_notify_hold",
        "_pending_count",
        "_prepare_matter_files",
        "_reply_guard_key",
        "_reply_recipient",
        "_review_unsupported",
        "relay_checks.say_held",
        "_seat_doc",
        "_send_bound_reply",
        "_send_failed",
        "_send_file_continuations",
        "_sent_trace_incomplete",
        "_send_msgraph_reply",
        "_session_tainted",
        "_shared_reply_people",
        "_transmitted_body",
        "binding.SESSION_BINDINGS.get",
        "champion.reply_wall",
        "classify_recipients_typed",
        "held_notice.over_cap",
        "held_notice.policy",
        "inbound.SESSION_INBOUND_ORIGIN.bound_this_turn",
        "inbound.content_digest",
        "matter_file_facts.insert",
        "matter_file_facts.missing",
        "matter_file_facts.model_words",
        "matter_file_facts.searched",
        "matter_file_facts.unstated",
        "matter_file_facts.without_names",
        "matter_file_reply.Prepared.descriptors",
        "matter_file_reply.audit_fields",
        "matter_file_reply.discard",
        "matter_file_reply.review_by_smd",
        "matter_file_review.released_count",
        "matter_gate.evaluate",
        "matter_gate.matter_ref_for",
        "matter_gate.mode",
        "provenance.register_for",
        "provenance.resolve_session",
        "relay.authored_digests",
        "relay.draft_body",
        "relay.draft_call_failed",
        "relay.draft_recipients",
        "relay.gate_body",
        "relay.recipient_locked",
        "relay.send_reply",
        "relay.shared_recipients_locked",
        "reply_could_not.metadata",
        "report_render.html_text_content",
        "request_index.record_reply_opening",
        "send_policy.live_send_policy",
        "sender_key",
        "working_drafts.draft_id_of",
    ),
    local=(
        ("cfg.device_reply_target", "CustomerConfig.device_reply_target"),
        ("cfg.sender_on_roster", "CustomerConfig.sender_on_roster"),
        ("bound.as_request", "binding.Binding.as_request"),
        ("bound.origin", "binding.Binding.origin"),
        ("_register.captions", "provenance.ProvenanceRegister.captions"),
        ("_register.money", "provenance.ProvenanceRegister.money"),
    ),
    unwrapped=(
        *_LOG,
        ("kwargs.get", "reads the hook's input, recorded whole on entry"),
        ("origin.sender_address.strip", "a str method on a recorded value"),
        ("<call>.get", "a dict read on a recorded result (bound.as_request())"),
        ("<call>.lower", "a str method on a recorded value"),
        ("relay.RateDecision", "builds a value; what it decides reaches the recorded calls"),
        (
            "_TURN_SAYS.get",
            "reads the whole-reply say a tool answered this turn (step 5a, 2026-11-19); "
            "None in every case but the say knob, whose check is the recorded relay_checks.say_held",
        ),
        (
            "turn_says.mechanics_hold",
            "tier 1 (review 2026-11-19): the phrase scan of a reply to a firm address and "
            "its redraft cap; None in every case but mechanics_named, whose hold is the "
            "recorded _held",
        ),
        (
            "turn_says.limit_hold",
            "the limit rule (2026-11-24): a reply to anyone but SMD that states a limit of "
            "the Operator's own, and its redraft cap; None in every case but limit_named, "
            "whose hold is the recorded _held",
        ),
        (
            "reply_sent.notify",
            "after a reply is on the wire, binds the read-back it carried and marks the "
            "help line offered (2026-11-20); never raises and returns nothing, so it "
            "decides nothing the relay does; proven through the real send record in "
            "tests/test_rule_confirm_live_path.py",
        ),
        (
            "outbound_gate.gate_kwargs",
            "reads this session's own verified rule sentences for the citation exemption "
            "(R2.4, 2026-11-19); {} in every case, and its effect reaches the recorded "
            "relay.gate_body as its firm_sentences argument",
        ),
        (
            "champion.walls",
            "reads the enrolled list and the seat's product-guide audience setting; "
            "non-empty only for a client champion recipient (the champion knob) or an "
            "audience seat, whose wall is the recorded champion.reply_wall",
        ),
    ),
    edges=(
        "_emit_reply_event",
        "_D1_CLIENT.execute",
        "_HOLD_NOTICES.record",
        "_HOLD_COUNTS.note",
        "_HELD_STORE.enqueue",
        "_notify_hold",
        "_send_failed",
        "relay.send_reply",
        "msgraph_broker.send_reply",
        "msgraph_broker.send_bound_reply",
        "msgraph_broker.discard_draft",
        "msgraph_broker.bind_reply",
        "matter_file_review.hold",
        "matter_file_review.finish",
        "request_index.record_reply_opening",
        "_REPLIED.commit",
    ),
    registers=(*_REPLY_REGISTERS, *SHARED_REGISTERS),
    resets=tuple(r for r in (*_REPLY_REGISTERS, *SHARED_REGISTERS) if r != "_LIMITER"),
    library=(
        "act_delivery.py",
        "binding.py",
        "held_notice.py",
        "held_store.py",
        "job_reply.py",
        "notice.py",
        "relay.py",
        "sweeper.py",
        "text_delivery.py",
        "working_drafts.py",
    ),
)

WALL = Decision(
    key="wall",
    name="_evaluate_tool_call",
    file="plugins/hermes-smd-trust/enforce.py",
    wrap=(
        "PENDING_ACTS.peek_confirmed",
        "PENDING_ACTS.take_in_flight",
        "PENDING_SEND.approved_match",
        "PENDING_SEND.capture",
        "PENDING_SEND.take_for_send",
        "SESSION_TAINT.trust_class",
        "_apply_content_floor",
        "_audit_decision",
        "_extract_send_body",
        "_propose_commitment_act",
        "_reclassify_send",
        "_record_decision",
        "_resolve_active_persona",
        "_resolve_current_turn_approval",
        "_resolve_persona_exposure",
        "_resolve_send_recipients",
        "_resolve_vertical_floors",
        "_taint_gate_input",
        "_web_derived_admin_send",
        "act_broker.is_call_payload_act",
        "act_broker.tool_arguments",
        "classify_tool",
        "enforce",
        "matter_gate.body_from_args",
        "matter_gate.content_read_refusal",
        "matter_gate.evaluate",
        "matter_gate.mode",
        "matter_gate.multi_matter_mode",
        "matter_gate.multi_matter_session",
        "read_volume.evaluate_read",
        "search_egress.evaluate",
        "search_egress.record_verdict",
        "send_anchor.capture_context",
        "send_anchor.note_replay",
        "send_as.propose",
        "send_as.requested_from",
        "spec_gate.check_spec_gate",
        "spec_gate.check_structure_floor",
        "turn_principal.requester",
        "voice_gate.check_voice_gate",
    ),
    local=(),
    unwrapped=(
        *(entry for entry in _LOG if entry[0] != "logger.debug"),
        ("_BANNED_REFUSAL_MESSAGE.get", "a constant table read"),
        ("args.clear", "the call's args are recorded on entry and again on return"),
        ("args.update", "the call's args are recorded on entry and again on return"),
        ("args.get", "the call's args are recorded on entry and again on return"),
        ("copy.deepcopy", "copies a recorded value"),
        ("dataclasses.replace", "rebuilds the recorded enforce() result"),
        ("fragments.append", "a local list; its contents reach _record_decision"),
        ("<str>.join", "a str method on recorded values"),
    ),
    edges=(
        "_record_decision",
        "TRUST_DECISIONS.record",
        "PENDING_SEND.capture",
        "PENDING_ACTS.note_proposed",
    ),
    registers=SHARED_REGISTERS,
    resets=SHARED_REGISTERS,
    library=(
        "__init__.py",
        "approval.py",
        "outbound.py",
        "outbound_send.py",
        "send_as.py",
        "spec_read.py",
        "voice_gate.py",
    ),
)

DECISIONS: tuple[Decision, ...] = (RELAY, WALL)

#: Hook entry points whose RETURN is recorded in a test's timeline: the string
#: the transform hook hands the model, and the block directive the wall's
#: callers return. (function name, file under the overlay)
HOOKS: tuple[tuple[str, str], ...] = (
    ("on_transform_tool_result", "plugins/hermes-smd-reply/__init__.py"),
    ("on_post_tool_call", "plugins/hermes-smd-reply/__init__.py"),
    ("evaluate_tool_call", "plugins/hermes-smd-trust/enforce.py"),
    ("on_pre_tool_call", "plugins/hermes-smd-trust/__init__.py"),
)

#: Every overlay test file that reaches either function (the full suite's
#: reach check, ``reach.py``, fails when one is missing). Each is replayed
#: under the recorder and its record is a golden.
SELECTION: tuple[str, ...] = (
    "tests/test_act_delivery.py",
    "tests/test_approval_binding.py",
    "tests/test_authored_body_digest.py",
    "tests/test_blocked_after_allow_row.py",
    "tests/test_brief_taint_exemption.py",
    "tests/test_calendar_tools.py",
    "tests/test_champion_audience.py",
    "tests/test_champion_wall.py",
    "tests/test_commitment_acts.py",
    "tests/test_confirm_approval.py",
    "tests/test_confirm_dispatch.py",
    "tests/test_content_floor_html_body.py",
    "tests/test_couples_email.py",
    "tests/test_device_senders.py",
    "tests/test_event_deletion_act.py",
    "tests/test_job_reply_sent_by_seat.py",
    "tests/test_matter_file_facts.py",
    "tests/test_matter_file_reply.py",
    "tests/test_matter_file_review.py",
    "tests/test_matter_gate_placement.py",
    "tests/test_msgraph_seam_governance.py",
    "tests/test_msgraph_transports.py",
    "tests/test_outbound_gate.py",
    "tests/test_output_checklist_code_fixed.py",
    "tests/test_output_checklist_wired.py",
    "tests/test_pre_hook_blocks_recorded.py",
    "tests/test_read_volume_gate_placement.py",
    "tests/test_recipient_aware_send.py",
    "tests/test_records_order_act.py",
    "tests/test_relay_fault_after_transmit.py",
    "tests/test_reply.py",
    "tests/test_reply_binding.py",
    "tests/test_reply_held_notice.py",
    "tests/test_reply_waiting_owed.py",
    "tests/test_reply_text_delivery.py",
    "tests/test_reply_text_delivery_concurrency.py",
    "tests/test_search_egress.py",
    "tests/test_security_phase1.py",
    "tests/test_send_as.py",
    "tests/test_send_outcome_unknown.py",
    "tests/test_structure_floor_wired.py",
    "tests/test_transport_render_parity.py",
    "tests/test_trust_decision.py",
    "tests/test_trust_enforce.py",
    "tests/test_voice_gate_spec_regime.py",
    "tests/test_voice_live_gate.py",
    "tests/test_web_derived_trust.py",
)

#: Branch directions no input can take, each with why: (decision, the source
#: line the jump is on, ``left`` or ``right``, why). Every other direction of
#: every conditional jump in either function must be taken by some case.
INFEASIBLE: tuple[tuple[str, str, str, str], ...] = (
    (
        "relay",
        "reply_to = device_target if redirected and device_target else origin.sender_address",
        "right",
        "redirected is bool(device_target) and ..., so device_target is truthy whenever redirected is",
    ),
    (
        "relay",
        'tool_name=str(kwargs.get("tool_name") or ""),',
        "left",
        "past the first guard the tool name is one of _CREATE_DRAFT_TOOLS, never empty",
    ),
)

#: Tests in SELECTION whose record is not taken, each with why. Empty unless a
#: test's decisions depend on thread timing.
UNSTABLE: tuple[tuple[str, str], ...] = ()

__all__ = [
    "DECISIONS",
    "HOOKS",
    "INFEASIBLE",
    "RELAY",
    "SELECTION",
    "SHARED_EDGES",
    "SHARED_REGISTERS",
    "UNSTABLE",
    "WALL",
    "Decision",
]
