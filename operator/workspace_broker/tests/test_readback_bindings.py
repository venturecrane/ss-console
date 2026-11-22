"""Which own rules a sent read-back carried, and which read-back a reply answers.

The broker half of confirm-by-reply (``workspace_broker/readback_bindings.py``):
the seat tells the broker which of a person's OWN open rule proposals a sent
read-back carried, keyed by the sent message's RFC 5322 id, and the broker reads
an inbound reply's own headers from the mailbox to say which one it answers.
Names, addresses and ids are invented.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import readback_bindings as rb
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.establishment import (
    EstablishmentStore,
    EstablishmentValidationError,
)
from workspace_broker.server import Broker

AGENT_UID = 1000
ATTORNEY = "pat@firm.example"
COLLEAGUE = "lee@firm.example"
SENT = "<CAread1@mail.firm.example>"
LAST_WEEK = "<CAread0@mail.firm.example>"
GRAPH_ID = "AAMkReplyGraphId0001="


class _Graph:
    """Graph read: the inbound reply's from and internetMessageHeaders."""

    def __init__(self, headers: list[dict], sender: str = ATTORNEY) -> None:
        self._read_credential_path = "/dev/null"
        self.message = {
            "id": GRAPH_ID,
            "from": {"emailAddress": {"address": sender}},
            "internetMessageHeaders": headers,
        }
        self.paths: list[str] = []

    def _mail_path(self, *parts: str) -> str:
        return "/" + "/".join(parts)

    def _request(self, path, method, body, credential_path=None, role=None):
        self.paths.append(path)
        return self.message


class _AgentMail:
    def __init__(self, message: dict) -> None:
        self.message = message

    def _path(self, *parts: str) -> str:
        return "/" + "/".join(parts)

    def _request(self, path, method, body):
        return self.message


def _broker(tmp_path: Path) -> Broker:
    spool = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (spool / child).mkdir(parents=True, exist_ok=True)
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = 42
    broker.agent_uid = AGENT_UID
    db_path = str(tmp_path / "audit.db")
    broker.ledger = LedgerWriter(db_path)
    broker.establishment = EstablishmentStore(spool, broker.ledger, pending_db_path=db_path)
    broker.db_path = db_path
    broker.audit_db_path = db_path
    broker.msgraph = None
    broker.agentmail = None
    return broker


def _call(broker: Broker, **request):
    return broker.handle(request, peer_pid=9999, peer_uid=AGENT_UID)


def _propose(broker: Broker, text: str, *, by: str = ATTORNEY, for_admin: bool = False) -> str:
    out = _call(
        broker,
        action="establish_propose",
        scope="person" if not for_admin else "firm_adjust",
        subject={"person": by} if not for_admin else {"output_class": "outbound", "property": "voice"},
        text=text,
        instructed_by=by,
        source_ref="msg-1",
        for_admin=for_admin,
    )
    assert out["ok"], out
    return out["proposal_id"]


def _owe(broker: Broker, ids: list[str], inbound: str, listed: bool = False, to: str = ATTORNEY, verified=()):
    return _call(
        broker,
        action=rb.VERB_OWED,
        inbound_message_id=inbound,
        recipient=to,
        proposal_ids=ids,
        listed=listed,
        verified_ids=list(verified),
    )


def _sent(broker: Broker, ids: list[str], sent: str = SENT, listed: bool = False, to: str = ATTORNEY):
    """The read-back is owed when rendered, and bound when the relay sends it."""
    inbound = f"AAMkAnswered-{sent.strip('<>')}"
    _owe(broker, ids, inbound, listed, to)
    return _call(broker, action=rb.VERB_SENT, inbound_message_id=inbound, sent_message_id=sent, recipient=to)


def _for(broker: Broker, inbound: str = GRAPH_ID, sender: str = ATTORNEY):
    return _call(broker, action=rb.VERB_FOR, inbound_message_id=inbound, sender=sender)


def test_an_own_rules_read_back_carries_no_tag(tmp_path):
    broker = _broker(tmp_path)
    out = _call(
        broker,
        action="establish_propose",
        scope="person",
        subject={"person": ATTORNEY},
        text="Keep my memos to one page.",
        instructed_by=ATTORNEY,
        source_ref="msg-1",
    )
    assert out["readback"] == "Keep my memos to one page."
    listed = _call(broker, action="establish_pending", sender=ATTORNEY)["pending"]
    assert listed[0]["readback"] == "Keep my memos to one page."


def test_a_reply_to_a_read_back_carrying_two_rules_binds_both(tmp_path):
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    assert _sent(broker, [a, b])["recorded"] == [a, b]
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    out = _for(broker)
    assert out["bound"] is True and out["proposal_ids"] == [a, b] and out["listed"] is False
    # The broker read the headers itself, on the message the turn named.
    assert broker.msgraph.paths == [f"/messages/{GRAPH_ID}?$select=id,from,internetMessageHeaders"]


def test_a_long_thread_binds_only_the_message_replied_to(tmp_path):
    broker = _broker(tmp_path)
    old = _propose(broker, "Last week's rule, never answered.")
    new = _propose(broker, "This week's rule sentence.")
    _sent(broker, [old], sent=LAST_WEEK)
    _sent(broker, [new])
    broker.msgraph = _Graph(
        [{"name": "In-Reply-To", "value": SENT}, {"name": "References", "value": f"{LAST_WEEK} {SENT}"}]
    )
    assert _for(broker)["proposal_ids"] == [new]


def test_no_reply_header_is_not_bound(tmp_path):
    broker = _broker(tmp_path)
    _sent(broker, [_propose(broker, "A rule sentence here.")])
    broker.msgraph = _Graph([{"name": "Subject", "value": "Re: rules"}])
    out = _for(broker)
    assert out["bound"] is False and out["header"] is False


def test_the_last_reference_stands_in_for_a_missing_in_reply_to(tmp_path):
    broker = _broker(tmp_path)
    a = _propose(broker, "A rule sentence here.")
    _sent(broker, [a])
    broker.msgraph = _Graph([{"name": "References", "value": f"{LAST_WEEK} {SENT}"}])
    assert _for(broker)["proposal_ids"] == [a]


def test_a_reply_from_someone_else_binds_nothing(tmp_path):
    broker = _broker(tmp_path)
    _sent(broker, [_propose(broker, "A rule sentence here.")])
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}], sender=COLLEAGUE)
    assert _for(broker, sender=COLLEAGUE)["bound"] is False
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}], sender=COLLEAGUE)
    assert _for(broker, sender=ATTORNEY)["bound"] is False


def test_a_rule_waiting_on_an_administrator_cannot_be_bound(tmp_path):
    broker = _broker(tmp_path)
    waiting = _propose(broker, "A firm rule sentence here.", by=COLLEAGUE, for_admin=True)
    with pytest.raises(EstablishmentValidationError, match="waiting on an administrator"):
        _sent(broker, [waiting], to=COLLEAGUE)


def test_somebody_elses_rule_cannot_be_bound_to_your_read_back(tmp_path):
    broker = _broker(tmp_path)
    theirs = _propose(broker, "Their rule sentence here.", by=COLLEAGUE)
    with pytest.raises(EstablishmentValidationError, match="not stated by the recipient"):
        _sent(broker, [theirs], to=ATTORNEY)


def test_the_numbered_list_is_bound_in_its_order(tmp_path):
    broker = _broker(tmp_path)
    ids = [_propose(broker, f"Rule number {n} sentence.") for n in (1, 2, 3)]
    _sent(broker, list(reversed(ids)), listed=True)
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    out = _for(broker)
    assert out["listed"] is True and out["proposal_ids"] == list(reversed(ids))


def test_an_agentmail_custody_reply_binds_by_its_in_reply_to(tmp_path):
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    _sent(broker, [a, b])
    broker.agentmail = _AgentMail({"from": f"Pat <{ATTORNEY}>", "in_reply_to": SENT, "references": [SENT]})
    assert _for(broker, inbound="<CAreply9@mail.firm.example>")["proposal_ids"] == [a, b]


def test_a_mailbox_that_cannot_be_read_is_not_bound(tmp_path):
    broker = _broker(tmp_path)
    _sent(broker, [_propose(broker, "A rule sentence here.")])

    class _Down(_Graph):
        def _request(self, *a, **k):
            raise RuntimeError("down")

    broker.msgraph = _Down([])
    out = _for(broker)
    assert out["ok"] is True and out["bound"] is False


def test_message_ids_match_with_or_without_brackets():
    assert rb.normalize_message_id("<a@b>") == rb.normalize_message_id("a@b") == "a@b"
    assert rb.normalize_message_id("not an id") == ""
    assert rb.parent_id("", "<x@y> <z@w>") == "z@w"


def test_the_read_back_verbs_are_agent_only_on_the_table():
    from workspace_broker import establish_verbs, verbs

    assert rb.VERBS == ("establish_readback_owed", "establish_readback_sent", "establish_readback_for")
    for name in establish_verbs.READBACK_VERBS:
        # The mailbox seed is SMD's, run as root on the seat; the rest are the agent's.
        want = verbs.ROOT if name == "establish_block_seed" else verbs.AGENT
        assert verbs.TABLE[name].auth == frozenset({want})
    assert {
        "establish_reopen_lapsed",
        "establish_restated",
        "establish_install_watch",
        "establish_install_check",
    } <= set(establish_verbs.READBACK_VERBS)


def test_an_owed_read_back_survives_a_restart_and_binds_at_the_send(tmp_path):
    """Review: the binding lived in the gateway's memory until a later flush.
    Now it is in the audit DB from the moment the read-back is rendered, and the
    relay's send turns it into a binding in the same step."""
    broker = _broker(tmp_path)
    a = _propose(broker, "Rule one sentence here.")
    _owe(broker, [a], "AAMkIn1=")
    restarted = _broker(tmp_path)  # a fresh broker on the same database
    out = _call(restarted, action=rb.VERB_SENT, inbound_message_id="AAMkIn1=", sent_message_id=SENT, recipient=ATTORNEY)
    assert out["bound"] is True and out["recorded"] == [a]
    restarted.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    assert _for(restarted)["proposal_ids"] == [a]


def test_a_send_that_owed_no_read_back_binds_nothing(tmp_path):
    broker = _broker(tmp_path)
    out = _call(broker, action=rb.VERB_SENT, inbound_message_id="AAMkOther=", sent_message_id=SENT, recipient=ATTORNEY)
    assert out == {"ok": True, "bound": False}


def test_an_unbound_reply_is_never_bound_to_an_earlier_list(tmp_path):
    """A pick with no reply header names numbers of a list the person may not
    be looking at: it binds to nothing, and the seat lists again (2026-11-20).
    FALSIFIER: offer the newest list sent to them."""
    broker = _broker(tmp_path)
    ids = [_propose(broker, f"Rule number {n} sentence.") for n in (1, 2)]
    _sent(broker, ids, listed=True)
    broker.msgraph = _Graph([{"name": "Subject", "value": "Re: rules"}])
    out = _for(broker)
    assert out["bound"] is False and out["proposal_ids"] == [] and "latest_list" not in out


def test_only_verified_sentences_are_reported_verified(tmp_path):
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    _owe(broker, [a, b], "AAMkIn2=", verified=[a])
    broker.msgraph = _Graph([])
    assert _for(broker)["verified_ids"] == [a]


def test_the_citation_exemption_reaches_only_the_rules_this_reply_answers(tmp_path):
    """Review 2026-11-20: every verified sentence the sender ever had was
    returned to every later turn. Bound, it is the rules the read-back carried;
    unbound, the sender's open own rules (the list it will show). FALSIFIER:
    return everything recorded for the sender."""
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    _owe(broker, [a, b], "AAMkIn2=", verified=[a, b])
    _sent(broker, [b])
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    assert _for(broker)["verified_ids"] == [b]
    _lapse(broker, a)
    broker.msgraph = _Graph([])
    assert _for(broker)["verified_ids"] == [b]


def _lapse(broker: Broker, proposal_id: str) -> None:
    conn = broker.establishment.pending._connect()
    with conn:
        conn.execute(
            "UPDATE pending_rules SET lapsed_at=?, lapse_notified_at=? WHERE proposal_id=?",
            (time.time(), time.time(), proposal_id),
        )
    conn.close()


def test_a_silently_lapsed_rule_is_reopened_once_as_a_fresh_proposal(tmp_path):
    """Only by a yes that REPLIES to the read-back that carried it (review
    2026-11-19). FALSIFIER: reopen on any yes and a late, unrelated "yes" to
    another email brings a lapsed rule back."""
    broker = _broker(tmp_path)
    old = _propose(broker, "Keep my memos to one page.")
    _sent(broker, [old])
    _lapse(broker, old)
    unrelated = _call(broker, action="establish_reopen_lapsed", sender=ATTORNEY, inbound_message_id=GRAPH_ID)
    assert unrelated["reopened"] is False  # no mailbox read: no reply header, nothing bound
    broker.msgraph = _Graph([{"name": "Subject", "value": "Re: lunch"}])
    assert (
        _call(broker, action="establish_reopen_lapsed", sender=ATTORNEY, inbound_message_id=GRAPH_ID)["reopened"]
        is False
    )
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    out = _call(broker, action="establish_reopen_lapsed", sender=ATTORNEY, inbound_message_id=GRAPH_ID)
    assert out["reopened"] is True and out["proposal_id"] != old
    assert out["readback"] == "Keep my memos to one page."
    again = _call(broker, action="establish_reopen_lapsed", sender=ATTORNEY, inbound_message_id=GRAPH_ID)
    assert again["reopened"] is False


def test_a_restated_rule_closes_the_older_one_silently(tmp_path):
    broker = _broker(tmp_path)
    old = _propose(broker, "Keep my memos short.")
    new = _propose(broker, "Keep my memos to one page.")
    out = _call(broker, action="establish_restated", sender=ATTORNEY, old_proposal_id=old, new_proposal_id=new)
    assert out["closed"] is True
    open_ids = [r["proposal_id"] for r in _call(broker, action="establish_pending", sender=ATTORNEY)["pending"]]
    assert open_ids == [new]


def test_a_restated_rule_can_never_be_listed_beside_its_restatement(tmp_path):
    """Review defect (d), 2026-11-19: a bare yes to a list confirmed a rule and
    its contradictory restatement. The older is closed when restated, so it can
    no longer be carried by any read-back. FALSIFIER: let a lapsed row be owed."""
    broker = _broker(tmp_path)
    old = _propose(broker, "Keep my memos short.")
    new = _propose(broker, "Keep my memos to one page.")
    _call(broker, action="establish_restated", sender=ATTORNEY, old_proposal_id=old, new_proposal_id=new)
    with pytest.raises(EstablishmentValidationError, match="lapsed"):
        _owe(broker, [old, new], "AAMkList=")
    assert _owe(broker, [new], "AAMkList=")["owed"] == [new]


def test_a_restatement_of_somebody_elses_rule_is_refused(tmp_path):
    broker = _broker(tmp_path)
    theirs = _propose(broker, "Their rule sentence here.", by=COLLEAGUE)
    mine = _propose(broker, "Keep my memos to one page.")
    with pytest.raises(EstablishmentValidationError):
        _call(broker, action="establish_restated", sender=ATTORNEY, old_proposal_id=theirs, new_proposal_id=mine)


def test_the_install_watch_survives_a_restart_and_reports_a_failed_install(tmp_path, monkeypatch):
    from workspace_broker import own_rule_verbs

    broker = _broker(tmp_path)
    a = _propose(broker, "Keep my memos to one page.")
    _call(broker, action="establish_install_watch", proposal_id=a, run_id="run-0001")
    restarted = _broker(tmp_path)
    monkeypatch.setattr(own_rule_verbs, "INSTALL_CHECK_AFTER_S", 0)
    monkeypatch.setattr(
        type(restarted.establishment),
        "status",
        lambda self, req: {"ok": True, "result": {"status": "refused"}},
    )
    out = _call(restarted, action="establish_install_check")
    assert out["failed"] == [{"proposal_id": a, "run_id": "run-0001", "why": "its install ended refused"}]
    # Review 2026-11-19: only an ERROR log before; now it is owed work, listed
    # by the triage until closed (FALSIFIER: drop the record and nothing lists it).
    from workspace_broker import smd_owed

    (owed,) = smd_owed.open_items(restarted.audit_db_path)["owed"]
    assert owed["kind"] == "rule_install_failed" and a in owed["detail"]
    assert _call(restarted, action="establish_install_check")["failed"] == []


# ---- 2026-11-21, the A&P seat: four yeses in six minutes, only the last recorded ----
PARAPHRASE = "<CAparaphrase@mail.firm.example>"
SIBLING = "<CAsibling@mail.firm.example>"
_THREAD = [{"name": "In-Reply-To", "value": PARAPHRASE}, {"name": "References", "value": f"{SENT} {PARAPHRASE}"}]


def _commit(broker: Broker, proposal_id: str) -> None:
    """Commit a rule the way a confirmed submit does: the store's own consume."""
    assert broker.establishment._require_pending().consume(proposal_id, f"run-{proposal_id}")


def test_a_yes_to_a_restated_message_binds_to_the_threads_read_back(tmp_path):
    """The Operator restated the rules in its own words in a later message of
    the thread; his yes replied to THAT message. It binds to the read-back code
    rendered earlier in the same thread. FALSIFIER: drop the thread fallback and
    the reply is unbound."""
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    _sent(broker, [a, b])
    broker.msgraph = _Graph(list(_THREAD))
    out = _for(broker)
    assert out["bound"] is True and out["proposal_ids"] == [a, b] and out["listed"] is False
    assert out["lookup"] == "earlier in the thread"


def test_the_thread_fallback_never_reaches_a_numbered_list(tmp_path):
    broker = _broker(tmp_path)
    ids = [_propose(broker, f"Rule number {n} sentence.") for n in (1, 2)]
    _sent(broker, ids, listed=True)
    broker.msgraph = _Graph(list(_THREAD))
    assert _for(broker)["bound"] is False


def test_the_thread_fallback_never_reaches_a_read_back_older_than_a_day(tmp_path, monkeypatch):
    broker = _broker(tmp_path)
    _sent(broker, [_propose(broker, "An old rule sentence.")])
    monkeypatch.setattr(rb, "THREAD_HOURS", -1)
    broker.msgraph = _Graph(list(_THREAD))
    assert _for(broker)["bound"] is False


def test_a_read_back_whose_rules_a_sibling_yes_recorded_reports_them_committed(tmp_path):
    """FALSIFIER: drop ``committed`` and the seat sees a read-back with nothing
    open, and tells him nothing is outstanding."""
    broker = _broker(tmp_path)
    a, b = _propose(broker, "Rule one sentence here."), _propose(broker, "Rule two sentence here.")
    _sent(broker, [a, b])
    _sent(broker, [a, b], sent=SIBLING)
    _commit(broker, a)
    _commit(broker, b)
    broker.msgraph = _Graph([{"name": "In-Reply-To", "value": SENT}])
    out = _for(broker)
    assert [r["proposal_id"] for r in out["committed"]] == [a, b]
    assert [r["text"] for r in out["committed"]] == ["Rule one sentence here.", "Rule two sentence here."]


def test_an_unbound_reply_carries_the_senders_rules_recorded_in_the_last_hour(tmp_path, monkeypatch):
    broker = _broker(tmp_path)
    a = _propose(broker, "Rule one sentence here.")
    theirs = _propose(broker, "Their rule sentence here.", by=COLLEAGUE)
    _commit(broker, a)
    _commit(broker, theirs)
    broker.msgraph = _Graph([{"name": "Subject", "value": "Re: rules"}])
    assert [r["proposal_id"] for r in _for(broker)["recent_committed"]] == [a]
    monkeypatch.setattr(rb, "RECENT_COMMITTED_SECONDS", -1)
    broker.msgraph = _Graph([{"name": "Subject", "value": "Re: rules"}])
    assert _for(broker)["recent_committed"] == []


def test_a_committed_sentence_proposed_again_creates_nothing(tmp_path):
    """FALSIFIER: the duplicate check sees only open rows, and the same sentence
    becomes a new rule asking for a yes to a rule already in force."""
    broker = _broker(tmp_path)
    a = _propose(broker, "Rule one sentence here.")
    _commit(broker, a)
    out = _call(
        broker,
        action="establish_propose",
        scope="person",
        subject={"person": ATTORNEY},
        text="Rule one sentence here.",
        instructed_by=ATTORNEY,
        source_ref="msg-2",
    )
    assert out["already_recorded"] is True and out["proposal_id"] == a
    assert _call(broker, action="establish_pending", sender=ATTORNEY)["pending"] == []
