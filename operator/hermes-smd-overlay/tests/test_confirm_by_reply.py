"""A person's own rule: read back with no id, confirmed by the email replied to.

The seat half of R2.1-R2.4 (``plugins/hermes-smd-establishment/readback_confirm.py``
and the confirmation hook). The broker is a fake that answers the two binding
verbs the way ``workspace_broker/readback_bindings.py`` does; the decisions it
feeds (``shared/rule_confirm.resolve``) are the real ones. Every name, address,
rule and id here is invented.

The cases the build was asked for, each its own test: a "Yes." replying to a
read-back that carried two rules confirms both; a "Yes." in a long thread that
also quotes last week's read-back confirms only the replied-to message's rules;
a "Yes." with no reply header and three open rules gets the numbered list, and
"1 and 3" then confirms those two; a rule waiting on an administrator is
untouched; and the same on a seat whose mail custody is AgentMail.
"""

from __future__ import annotations

import json

import pytest

from shared import admin_possession, outbound_gate, readback_send
from shared.inbound import SESSION_INBOUND_ORIGIN, SESSION_TAINT
from tests.conftest import load_plugin
from tests.rule_turns import bound_turn, relay_register

ATTORNEY = "pat@firm.example"
PARALEGAL = "sam@firm.example"
GRAPH_ID = "AAMkReplyGraphId0001="
SENT = "<CAread1@mail.firm.example>"
A, B, C, OLD, WAIT = "1a1a1a1a", "2b2b2b2b", "3c3c3c3c", "0f0f0f0f", "4d4d4d4d"
TEXT = {
    A: "In memos, object under the cited section, see Example v. Sample.",
    B: "In memos, put the answer in the first line.",
    C: "In memos, no headings.",
    OLD: "In memos, sign off with the attorney's name.",
}
ROW_SUBJECT = {"output_class": "work_product", "property": "voice", "doc_kinds": ["memo"]}


class _Cfg:
    def __init__(self, adapter=""):
        self.connectors = {"Email": {"enabled": True, "adapter": adapter}} if adapter else {}

    @property
    def admins(self):
        return [ATTORNEY]

    def sender_is_admin(self, sender):
        return str(sender or "").strip().lower() == ATTORNEY

    def sender_on_roster(self, sender):
        return True

    def authored_person_name(self, _address):
        return None


def _own(proposal_id, by=ATTORNEY):
    return {
        "proposal_id": proposal_id,
        "kind": "rule",
        "scope": "firm_adjust",
        "subject": dict(ROW_SUBJECT),
        "text": TEXT[proposal_id],
        "readback": TEXT[proposal_id],
        "instructed_by": by,
        "for_admin": False,
        "state": "open",
    }


def _waiting():
    return {
        "proposal_id": WAIT,
        "kind": "rule",
        "scope": "firm_adjust",
        "subject": {"output_class": "outbound_client", "property": "voice"},
        "text": "In client letters, be brief.",
        "readback": f"[rule {WAIT}] In client letters, be brief.",
        "instructed_by": PARALEGAL,
        "for_admin": True,
        "state": "open",
    }


@pytest.fixture
def seat(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-establishment")
    state: dict = {"pending": [], "requests": [], "binding": None, "adapter": ""}

    def broker(payload):
        state["requests"].append(payload)
        action = payload.get("action")
        if action == "establish_pending":
            if payload.get("proposal_id"):
                return {"ok": True, "pending": []}
            out = {"ok": True, "pending": list(state["pending"])}
            if payload.get("include_recent_committed"):
                # The broker's own answer (``readback_bindings._recent_committed``).
                out["recent_committed"] = list(state.get("recent", []))
            return out
        if action == "drafting_job_status":
            return {"ok": True, "jobs": list(state.get("jobs", []))}
        if action == "establish_readback_for":
            binding = state["binding"]
            if binding is None:
                return {
                    "ok": True,
                    "bound": False,
                    "header": False,
                    "proposal_ids": [],
                    "recent_committed": state.get("recent", []),
                }
            return {"ok": True, "bound": True, "header": True, **binding}
        if action == "establish_readback_owed":
            return {"ok": True, "owed": payload["proposal_ids"]}
        if action == "establish_reopen_lapsed":
            return state.get("reopen") or {"ok": True, "reopened": False}
        if action == "establish_restated":
            state.setdefault("restated", []).append(payload)
            return {"ok": True, "closed": True}
        if action == "establish_propose":
            if state.get("already"):
                return {"ok": True, "already_recorded": True, "duplicate_of": A, "proposal_id": A}
            return {
                "ok": True,
                "duplicate_of": None,
                "proposal_id": A,
                "scope": payload["scope"],
                "subject": payload["subject"],
                "for_admin": payload["for_admin"],
                "readback": payload["text"],
            }
        return {"ok": True, "run_id": f"run-{len(state['requests'])}"}

    class _CfgCls:
        @classmethod
        def from_volume(cls, path=None):
            return _Cfg(state["adapter"])

    monkeypatch.setattr(mod, "_broker_request", broker)
    monkeypatch.setattr(mod, "CustomerConfig", _CfgCls)
    monkeypatch.setenv("SMD_ADMIN_POSSESSION_DB_PATH", str(tmp_path / "possession.db"))
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp_path / "requests.db"))
    yaml = tmp_path / "customer.yaml"
    yaml.write_text("logic_in_code:\n  wording_rules: enforce\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml))
    for store in (mod._ADMIN_STASH, mod._CONFIRMED_STASH, mod._READBACK_OWED, mod._CONFIRMED_SAY):
        store.clear()
    outbound_gate._reset_firm_sentences_for_tests()
    SESSION_INBOUND_ORIGIN._origins.clear()
    SESSION_TAINT._tainted.clear()
    relay_register(monkeypatch)
    yield mod, state
    SESSION_TAINT._tainted.clear()
    outbound_gate._reset_firm_sentences_for_tests()


def _reply(mod, message, session="sess-1", sender=ATTORNEY):
    bound_turn(session, sender, GRAPH_ID)
    return mod.on_pre_llm_call(session_id=session, sender_id=sender, user_message=message)[
        "context"
    ]


def _committed(state):
    return [r["proposal_id"] for r in state["requests"] if r.get("action") == "establish_submit"]


LONG_THREAD = f"""Yes.

On Tue, Operator <ops@firm.example> wrote:
> {TEXT[A]}
> {TEXT[B]}
> Reply yes to confirm.

On the Tuesday before, Operator <ops@firm.example> wrote:
> {TEXT[OLD]}
> Reply yes to confirm.
"""


def test_a_yes_replying_to_a_read_back_of_two_rules_confirms_both(seat):
    mod, state = seat
    state["pending"] = [_own(A), _own(B)]
    state["binding"] = {"proposal_ids": [A, B], "listed": False}
    context = _reply(mod, "Yes.")
    assert _committed(state) == [A, B]
    assert [r for r in state["requests"] if r["action"] == "establish_readback_for"] == [
        {"action": "establish_readback_for", "inbound_message_id": GRAPH_ID, "sender": ATTORNEY}
    ]
    say = mod._CONFIRMED_SAY["sess-1"]
    assert say == (
        f"Got it. These are recorded:\n\n- From the next memos: {TEXT[A]}\n- From the next memos: {TEXT[B]}"
    )
    assert say in context
    for proposal_id in (A, B):
        assert proposal_id not in context
    assert "may not appear" not in context


def test_a_yes_in_a_long_thread_confirms_only_the_replied_to_rules(seat):
    mod, state = seat
    state["pending"] = [_own(OLD), _own(A), _own(B)]
    state["binding"] = {"proposal_ids": [A, B], "listed": False}
    _reply(mod, LONG_THREAD)
    assert _committed(state) == [A, B]
    assert OLD not in _committed(state)


def test_no_reply_header_and_three_open_rules_gets_the_list_then_1_and_3(seat):
    mod, state = seat
    state["pending"] = [_own(A), _own(B), _own(C)]
    context = _reply(mod, "Yes.")
    assert _committed(state) == []
    block = (
        "These rules are waiting for your yes:\n\n"
        f"1. {TEXT[A]} (This rule covers memos, for any attorney, for any reader, and nothing else.)\n"
        f"2. {TEXT[B]} (This rule covers memos, for any attorney, for any reader, and nothing else.)\n"
        f"3. {TEXT[C]} (This rule covers memos, for any attorney, for any reader, and nothing else.)\n\n"
        "Reply all to confirm every one, or the numbers of the ones you want."
    )
    assert block in context
    assert not any(p in context for p in (A, B, C))
    # The list is owed to the person word for word, and bound once it is sent.
    assert block in mod._READBACK_OWED["sess-1"]
    # Owed to the broker the moment it is rendered (durable), for this email...
    owed = [r for r in state["requests"] if r["action"] == "establish_readback_owed"]
    assert owed == [
        {
            "action": "establish_readback_owed",
            "inbound_message_id": GRAPH_ID,
            "recipient": ATTORNEY,
            "proposal_ids": [A, B, C],
            "listed": True,
            "verified_ids": [],
        }
    ]
    # ...and bound by the relay's send, in the same step as the send.
    calls: list[dict] = []
    assert readback_send.bind_sent(
        GRAPH_ID, SENT, ATTORNEY, send=lambda p: (calls.append(p), {"ok": True, "bound": True})[1]
    )
    assert calls == [
        {
            "action": "establish_readback_sent",
            "inbound_message_id": GRAPH_ID,
            "sent_message_id": SENT,
            "recipient": ATTORNEY,
        }
    ]
    # The next reply answers that list.
    state["binding"] = {"proposal_ids": [A, B, C], "listed": True}
    _reply(mod, "1 and 3", session="sess-2")
    assert _committed(state) == [A, C]


def test_a_rule_waiting_on_an_administrator_is_unaffected(seat):
    mod, state = seat
    state["pending"] = [_waiting(), _own(A)]
    state["binding"] = {"proposal_ids": [A], "listed": False}
    _reply(mod, "Yes.")
    assert _committed(state) == [A]
    assert WAIT not in mod._CONFIRMED_STASH.get("sess-1", set())
    # And its own path is exactly what it was: "apply that" with its tag.
    state["binding"] = None
    context = _reply(mod, f"apply that [rule {WAIT}]", session="sess-3")
    assert mod._CONFIRMED_STASH["sess-3"] == {WAIT}
    assert f"[rule {WAIT}]" in context and mod.TOOL_SUBMIT in context


def test_on_an_agentmail_custody_seat_an_unproven_mailbox_falls_back_for_that_row(seat):
    """The possession challenge stops the code commit; that row takes the path
    every confirmation took before (stashed, committed by the model's submit,
    which runs the gate), and the model is told no display tag."""
    mod, state = seat
    state["adapter"] = "agentmail"
    state["pending"] = [_own(A), _own(B)]
    state["binding"] = {"proposal_ids": [A, B], "listed": False}
    context = _reply(mod, "Yes.")
    assert _committed(state) == []
    assert mod._CONFIRMED_STASH["sess-1"] == {A, B}
    assert f"[rule {A}]" not in context
    assert "never write it, or any id, in a reply" in context


def test_on_an_agentmail_custody_seat_a_proven_mailbox_commits_by_code(seat):
    mod, state = seat
    state["adapter"] = "agentmail"
    nonce = admin_possession.verdict(ATTORNEY, [ATTORNEY])["nonce"]
    assert admin_possession.try_confirm(ATTORNEY, f"my code {nonce}", [ATTORNEY])
    state["pending"] = [_own(A), _own(B)]
    state["binding"] = {"proposal_ids": [A, B], "listed": False}
    _reply(mod, "Yes.")
    assert _committed(state) == [A, B]


def test_a_rule_no_drafting_lane_reads_is_recorded(seat, monkeypatch, tmp_path):
    mod, state = seat
    (tmp_path / "customer.yaml").write_text("customer_id: x\n", "utf-8")
    state["pending"] = [_own(A)]
    state["binding"] = {"proposal_ids": [A], "listed": False}
    _reply(mod, "Yes.")
    assert mod._CONFIRMED_SAY["sess-1"] == "Got it. That is recorded."


def test_a_single_rule_a_lane_reads_says_from_the_next(seat):
    mod, state = seat
    state["pending"] = [_own(B)]
    state["binding"] = {"proposal_ids": [B], "listed": False}
    _reply(mod, "Yes, thanks")
    assert mod._CONFIRMED_SAY["sess-1"] == f"Got it. From the next memos: {TEXT[B]}"


def test_pending_shows_a_just_committed_rule_and_no_ids(seat):
    mod, state = seat
    state["pending"] = [_own(A), _own(B)]
    state["binding"] = {"proposal_ids": [A], "listed": False}
    _reply(mod, "Yes.")
    state["pending"] = [_own(B)]
    out = json.loads(mod._pending({"sender": ATTORNEY}, session_id="sess-1"))
    states = sorted((r["text"], r["state"]) for r in out["pending"])
    assert states == sorted([(TEXT[A], "committed"), (TEXT[B], "open")])
    assert all("proposal_id" not in r for r in out["pending"])


def test_the_propose_result_carries_no_id_and_the_read_back_asks_for_a_yes(seat):
    mod, state = seat
    mod.on_pre_llm_call(
        session_id="sess-p", sender_id=ATTORNEY, user_message=f"From now on: {TEXT[A]}"
    )
    out = json.loads(
        mod._propose(
            {
                "scope": "firm_adjust",
                "subject": dict(ROW_SUBJECT),
                "text": TEXT[A],
                "instructed_by": ATTORNEY,
                "source_ref": "msg-1",
            },
            session_id="sess-p",
        )
    )
    assert "proposal_id" not in out and A not in json.dumps(out)
    assert out["readback"].endswith("\n\nReply yes to confirm.")
    assert out["readback"].startswith(TEXT[A])


def test_the_read_back_with_its_citation_passes_the_gate_and_a_paraphrase_does_not(seat):
    """R2.4 at the seam: the rule's own sentence, with the case name the
    attorney wrote, leaves; the same citation in the model's words does not."""
    mod, state = seat
    mod.on_pre_llm_call(
        session_id="sess-p", sender_id=ATTORNEY, user_message=f"From now on: {TEXT[A]}"
    )
    out = json.loads(
        mod._propose(
            {
                "scope": "firm_adjust",
                "subject": dict(ROW_SUBJECT),
                "text": TEXT[A],
                "instructed_by": ATTORNEY,
                "source_ref": "msg-1",
            },
            session_id="sess-p",
        )
    )
    firm = outbound_gate.firm_sentences_for("sess-p")
    assert outbound_gate.evaluate(
        f"Here it is.\n\n{out['readback']}", None, "law-firm", firm_sentences=firm
    ).allowed
    paraphrase = "Noted: you want objections that rely on Example v. Sample."
    assert not outbound_gate.evaluate(paraphrase, None, "law-firm", firm_sentences=firm).allowed


def test_an_unbound_pick_is_listed_again_and_confirms_nothing(seat):
    """2026-11-20: "1 and 3" with no reply header naming a list names numbers
    of a list the person may not be looking at. It confirms nothing and gets
    the list again. FALSIFIER: resolve it against the newest list sent."""
    mod, state = seat
    state["pending"] = [_own(A), _own(B), _own(C)]
    state["binding"] = None
    context = _reply(mod, "1 and 3")
    assert _committed(state) == []
    assert "Reply all to confirm every one" in context


def test_a_yes_after_a_silent_lapse_reads_the_rule_back_again(seat):
    """R8: nothing open, a plain yes, and the person's own rule lapsed: it is
    proposed again and read back. Never "it lapsed"."""
    mod, state = seat
    state["pending"] = []
    state["reopen"] = {
        "ok": True,
        "reopened": True,
        "proposal_id": B,
        "readback": TEXT[B],
        "subject": dict(ROW_SUBJECT),
    }
    context = _reply(mod, "Yes.")
    assert TEXT[B] in context and "Reply yes to confirm." in context
    assert "laps" not in context.lower() and "expir" not in context.lower()
    assert B not in context


def test_a_yes_after_a_silent_lapse_that_is_not_plain_reopens_nothing(seat):
    mod, state = seat
    state["pending"] = []
    state["reopen"] = {"ok": True, "reopened": True, "proposal_id": B, "readback": TEXT[B]}
    _reply(mod, "Yes, and when is the Jones hearing?")
    assert not [r for r in state["requests"] if r["action"] == "establish_reopen_lapsed"]


def _propose(mod, text, session="sess-p", **extra):
    return json.loads(
        mod._propose(
            {
                "scope": "firm_adjust",
                "subject": dict(ROW_SUBJECT),
                "text": text,
                "instructed_by": ATTORNEY,
                "source_ref": "msg-1",
                **extra,
            },
            session_id=session,
        )
    )


def test_a_restated_rule_withdraws_the_older_one_from_the_read_back(seat, monkeypatch):
    mod, state = seat
    mod.on_pre_llm_call(
        session_id="sess-p", sender_id=ATTORNEY, user_message=f"{TEXT[A]} {TEXT[B]}"
    )
    ids = iter([A, B])
    real = mod._broker_request

    def broker(payload):
        if payload.get("action") == "establish_propose":
            return {**real(payload), "proposal_id": next(ids)}
        return real(payload)

    monkeypatch.setattr(mod, "_broker_request", broker)
    _propose(mod, TEXT[A])
    out = _propose(mod, TEXT[B], restates="1")
    assert state["restated"] == [
        {
            "action": "establish_restated",
            "sender": ATTORNEY,
            "old_proposal_id": A,
            "new_proposal_id": B,
        }
    ]
    assert TEXT[A] not in out["readback"] and out["readback"].startswith(TEXT[B])


def test_without_restates_nothing_is_closed(seat):
    mod, state = seat
    mod.on_pre_llm_call(session_id="sess-p", sender_id=ATTORNEY, user_message=TEXT[A])
    _propose(mod, TEXT[A])
    assert "restated" not in state


def test_only_a_sentence_from_the_persons_own_email_is_exempt_from_the_citation_scan(seat):
    """R2.4 (review): the model's propose argument earns the exemption only when
    code finds it verbatim in the person's own email this turn."""
    mod, _state = seat
    mod.on_pre_llm_call(
        session_id="sess-v", sender_id=ATTORNEY, user_message="Please make memos shorter."
    )
    _propose(mod, TEXT[A], session="sess-v")
    assert outbound_gate.firm_sentences_for("sess-v") == ()


def test_the_turn_read_back_never_carries_last_turns_rules(seat):
    mod, _state = seat
    mod._OWN_TURN["sess-p"] = {"ffffffff": "A stale rule sentence from before."}
    mod.on_pre_llm_call(session_id="sess-p", sender_id=ATTORNEY, user_message="Hello")
    assert "sess-p" not in mod._OWN_TURN


# ---- 2026-11-21, the A&P seat: a late yes, a sibling thread, a re-proposal ----


def _view(proposal_id):
    return {"proposal_id": proposal_id, "text": TEXT[proposal_id], "subject": dict(ROW_SUBJECT)}


def test_a_yes_to_a_read_back_a_sibling_yes_recorded_says_already_recorded(seat):
    """Nothing is open (a yes in another thread committed both rules); this yes
    replies to a read-back of the same two. FALSIFIER: remove the
    ``already_recorded.note`` call and the turn says nothing true about them
    (the live reply was "no rules outstanding", then a re-proposal)."""
    mod, state = seat
    state["pending"] = []
    state["binding"] = {"proposal_ids": [A, B], "listed": False, "committed": [_view(A), _view(B)]}
    context = _reply(mod, "Yes.")
    say = f"Got it. These are already recorded:\n\n- {TEXT[A]}\n- {TEXT[B]}"
    assert mod._CONFIRMED_SAY["sess-1"] == say and say in context
    assert "never say a rule was not recorded" in context
    assert "waiting for your yes" not in context and A not in context
    assert not [
        r
        for r in state["requests"]
        if r["action"] in ("establish_submit", "establish_reopen_lapsed")
    ]
    # One read of the reply's headers for the whole turn.
    assert len([r for r in state["requests"] if r["action"] == "establish_readback_for"]) == 1


def test_a_plain_yes_binding_nothing_soon_after_a_commit_says_already_recorded(seat):
    mod, state = seat
    state["pending"] = []
    state["recent"] = [_view(B)]
    context = _reply(mod, "Yes.")
    assert f"Got it. That is already recorded: {TEXT[B]}" in context


def test_a_yes_with_its_own_question_after_a_commit_is_not_answered_for_it(seat):
    mod, state = seat
    state["pending"] = []
    state["recent"] = [_view(B)]
    context = _reply(mod, "Yes, and when is the Jones hearing?") or ""
    assert "already recorded" not in context


def test_a_bound_yes_with_rules_still_open_confirms_them_as_before(seat):
    mod, state = seat
    state["pending"] = [_own(B)]
    state["binding"] = {"proposal_ids": [A, B], "listed": False, "committed": [_view(A)]}
    _reply(mod, "Yes.")
    assert _committed(state) == [B]


def test_a_sentence_already_committed_is_never_read_back_again(seat):
    """FALSIFIER: drop ``propose_answer`` and the model gets a read-back asking
    for a yes to a rule already in force."""
    mod, state = seat
    state["already"] = True
    mod.on_pre_llm_call(session_id="sess-p", sender_id=ATTORNEY, user_message=TEXT[A])
    out = _propose(mod, TEXT[A])
    assert out == {
        "ok": True,
        "already_recorded": True,
        "instruction": mod.already_recorded.PROPOSE_INSTRUCTION,
    }
    assert "sess-p" not in mod._READBACK_OWED
    assert not [r for r in state["requests"] if r["action"] == "establish_readback_owed"]


def _gate(mod, body, session="sess-1"):
    return mod.on_pre_tool_call(
        tool_name="email_send", session_id=session, args={"to": [ATTORNEY], "body": body}
    )


def test_a_committed_rules_reply_is_held_with_the_truth_not_a_failed_commit(seat):
    """The in-effect gate on a rule code committed this turn: a negation earlier
    in the sentence hedges the claim, and a real overclaim is held with a message
    saying the rule IS recorded. FALSIFIER: the old message ("the commit did not
    succeed"), and the 24-character window that read "cannot ... in effect" as a
    claim."""
    mod, state = seat
    state["pending"] = [_own(B)]
    state["binding"] = {"proposal_ids": [B], "listed": False}
    _reply(mod, "Yes.")
    say = mod._CONFIRMED_SAY["sess-1"]
    assert _gate(mod, f"{say}\n\nI cannot say for certain yet that the rule is in effect.") is None
    held = _gate(mod, f"{say}\n\nThe rule is in effect now.")
    assert held["action"] == "block"
    assert "It IS recorded" in held["message"] and "did not succeed" not in held["message"]
    assert "waiting on their yes" in held["message"] and B not in held["message"]


# ---- 2026-11-23, the A&P seat: a follow-up after a rule was confirmed -------------
# The attorney confirmed a discovery-response rule at 11:01. At 11:03 "Please
# apply this to this file" was told nothing was applied and asked which documents
# the rule was for; at 11:07 a reworded copy was proposed and committed as a
# second rule. Every sentence, address, number and id below is invented.

RFP = "5e5e5e5e"
RFP_SUBJECT = {
    "output_class": "work_product",
    "property": "voice",
    "doc_kinds": ["discovery_response"],
}
RFP_RULE = (
    "When a request for production asks for documents across several categories of "
    "damages, cite all objections and state that no further response will be made."
)
REWORDED = (
    "For requests for production seeking documents in multiple damages categories, "
    "assert every objection and state no further production will follow."
)
QUEUED = (
    "Received. The discovery responses for matter 900201 are with us, and you'll get a "
    "reply in this thread when the draft is filed in the matter."
)


def _recorded():
    return {"proposal_id": RFP, "text": RFP_RULE, "subject": dict(RFP_SUBJECT)}


def _revise_job(**extra):
    from datetime import datetime, timezone

    return {
        "id": "01JOBREVISE0000000000000000",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "state": "submitted",
        "reason": "lane_unproven:document-drafter.revise_discovery_response",
        "document_class": "discovery_response",
        "requester": ATTORNEY,
        "matter_number": "900201",
        "revise_of": "01JOBDELIVERED00000000000000",
        **extra,
    }


def _propose_gate(mod, text, session="sess-1", **subject):
    args = {
        "scope": "firm_adjust",
        "subject": {"output_class": "work_product", "property": "voice", **subject},
        "text": text,
        "instructed_by": ATTORNEY,
        "source_ref": "msg-2",
    }
    return mod.on_pre_tool_call(tool_name="establish_propose", session_id=session, args=args)


def _proposed(state):
    return [r for r in state["requests"] if r.get("action") == "establish_propose"]


def test_apply_this_to_this_file_after_a_commit_says_recorded_and_queued(seat):
    """The 11:03 sequence. FALSIFIER: without the follow-up lookup and gate, the
    turn learns nothing of the rule, both proposals are refused with "ask them
    which documents the rule is for", and the reply says nothing was applied."""
    mod, state = seat
    state["recent"] = [_recorded()]
    state["jobs"] = [_revise_job()]
    context = _reply(mod, "Please apply this to this file")
    assert "ALREADY recorded" in context and RFP_RULE in context and QUEUED in context
    # establish_pending shows the rule as committed, not gone.
    listed = json.loads(mod._pending({"sender": ATTORNEY}, session_id="sess-1"))["pending"]
    assert [(r["text"], r["state"]) for r in listed] == [(RFP_RULE, "committed")]
    assert all("proposal_id" not in r for r in listed)
    say = f"Got it. That is already recorded: {RFP_RULE}\n\n{QUEUED}"
    for kinds in ({"doc_kinds": ["discovery_response"]}, {}):
        held = _propose_gate(mod, RFP_RULE, **kinds)
        assert held["action"] == "block" and say in held["message"]
        assert "Refused" not in held["message"]
        assert "ask them which documents the rule is for" not in held["message"]
        assert mod._CONFIRMED_SAY["sess-1"] == say
    assert _proposed(state) == []


def test_a_reworded_copy_of_a_recorded_rule_is_never_proposed_again(seat):
    """The 11:07 sequence: same sender, same reach, same property, a different
    sentence. FALSIFIER: dedupe by the sentence's hash alone lets it through, and
    his yes commits a second rule over the same documents."""
    mod, state = seat
    state["recent"] = [_recorded()]
    _reply(
        mod,
        "Discovery responses and apply to all files. Please redo the responses in this "
        "file per this rule, specifically #4",
    )
    held = _propose_gate(mod, REWORDED, doc_kinds=["discovery_response"])
    assert held["action"] == "block"
    assert f"Got it. That is already recorded: {RFP_RULE}" in held["message"]
    assert _proposed(state) == []


def test_a_new_rule_from_the_same_sender_after_a_commit_is_still_proposed(seat):
    mod, state = seat
    state["recent"] = [_recorded()]
    new = "In discovery responses, put each objection on its own line."
    _reply(mod, new)
    verdict = _propose_gate(mod, new, doc_kinds=["discovery_response"])
    assert verdict is None or "already recorded" not in verdict["message"]
    out = _propose(mod, new, session="sess-1", subject=dict(RFP_SUBJECT))
    assert out["readback"].startswith(new)


def test_a_first_rule_naming_no_kind_still_asks_which_documents(seat):
    mod, _state = seat
    _reply(mod, "Make them shorter from now on.")
    verdict = _propose_gate(mod, "Keep them shorter.")
    assert verdict["action"] == "block"
    assert "ask them which documents the rule is for" in verdict["message"]


def test_no_open_job_on_those_documents_says_only_the_rule(seat):
    """A job delivered, on another kind, or another sender's is not "queued"."""
    mod, state = seat
    state["recent"] = [_recorded()]
    state["jobs"] = [
        _revise_job(state="delivered"),
        _revise_job(document_class="memo"),
        _revise_job(requester=PARALEGAL),
    ]
    _reply(mod, "Please apply this to this file")
    held = _propose_gate(mod, RFP_RULE)
    assert mod._CONFIRMED_SAY["sess-1"] == f"Got it. That is already recorded: {RFP_RULE}"
    assert "Received." not in held["message"]


CHANGES_WITH_RULES = """Please make these changes:

Response #3: drop the second sentence.
Response #7: add that we will produce the pay stubs we have.
The cover letter is fine as is.

Anytime a request asks for wage loss documents, a privacy objection should be included
that says tax returns are privileged. This should be a firm rule.
"""
WAGE_RULE = (
    "When a request asks for wage loss documents, include a privacy objection that tax "
    "returns are privileged."
)


def test_rules_in_changes_to_discovery_responses_reach_discovery_responses(seat):
    """The 11:58 case: line-by-line changes to a delivered discovery response
    plus standing rules, in an email that also mentions a letter. FALSIFIER:
    the reach read the kind as "letter" only, refused the rules for discovery
    responses, and the attorney was asked to restate them."""
    mod, _state = seat
    _reply(mod, CHANGES_WITH_RULES)
    verdict = _propose_gate(mod, WAGE_RULE, doc_kinds=["discovery_response"])
    assert verdict is None or "names only" not in verdict["message"]
    assert verdict is None or "Refused" not in verdict["message"]
    assert "discovery_response" in mod.rule_reach.kinds_named(CHANGES_WITH_RULES)
