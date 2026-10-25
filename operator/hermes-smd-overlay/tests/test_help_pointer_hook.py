"""The platform decides when the help pointer is due and hands the reply its line.

ss-console#3423, live finding on pilot-smokeball (image rel-711996f6): a rostered
admin asked "How many open matters can you see right now? Just the number is
fine." The turn ran matter-inbox-router -> mcp_smokeball_list_matters ->
create_draft -> REPLY_SENT and the reply was "11.": the model never called
``operator_help_signals``, so the first-email pointer never appeared. A prompt
instruction to call a tool on every turn is not followed reliably. The decision
moved into the initiation plugin's ``pre_llm_call`` hook, which injects the
exact line only when it is due.

What these pin, each with the harm it prevents:

* a first email from a rostered sender on a bound seat gets the line, once,
  and the offer is recorded durably (else the observed defect is back);
* a second email gets no block (else the pointer nags);
* a seat that does not bind and enable operator-help never gets one (else
  people are pointed at a help answer the seat cannot give);
* a help-phrased email gets no pointer (a help answer never carries it), and
  no offer is recorded for it;
* the email prompt's own trusted instructions are not read as the person's
  question (they say "what you can see", which made every email a seat ask);
* off-roster and unresolved senders get nothing;
* a broken state file or a raising signals call costs the line, never the hook;
* the evaluation runs once per inbound even when the hook fires again in the
  same turn (it records an offer, so a second run would be a second decision).
"""

from __future__ import annotations

import pytest

from shared import request_index
from shared.inbound import SESSION_INBOUND_ORIGIN, InboundOrigin
from tests.conftest import load_plugin

_SENDER = "Chris@Example-Firm.test"
_STRANGER = "someone@elsewhere.test"
_ORDINARY = "How many open matters can you see right now? Just the number is fine."


@pytest.fixture
def plugin():
    return load_plugin("hermes-smd-initiation")


@pytest.fixture
def paths(tmp_path, monkeypatch):
    req = tmp_path / "requests.db"
    state = tmp_path / "smd" / "help_state.db"
    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(req))
    monkeypatch.setenv("SMD_HELP_STATE_DB_PATH", str(state))
    return {"requests": req, "state": state}


class _Cfg:
    def __init__(self, *, help_enabled: object = True, help_name: str = "operator-help"):
        skills = [{"name": "matter-inbox-router", "enabled": True}]
        if help_enabled is not None:
            skills.append({"name": help_name, "enabled": help_enabled})
        self.personas = [{"slug": "operator", "skills": skills}]

    def sender_on_roster(self, sender):
        return isinstance(sender, str) and sender.lower().endswith("@example-firm.test")

    def sender_is_admin(self, sender):
        return True


def _seat(plugin, monkeypatch, cfg: _Cfg | None = None) -> None:
    cfg = cfg or _Cfg()
    monkeypatch.setattr(plugin, "_load_config", lambda: cfg)


def _prompt(message_id: str, body: str, sender: str = _SENDER) -> str:
    """The REAL AgentMail email-reply prompt, rendered for one message."""
    from bootstrap.translate import _INBOUND_EMAIL_PROMPT

    return (
        _INBOUND_EMAIL_PROMPT.replace("{message.from}", sender)
        .replace("{message.subject}", "matters")
        .replace("{message.message_id}", message_id)
        .replace("{message.text}", body)
    )


def _arrive(message_id: str, sender: str = _SENDER, thread: str = "t-1", index: bool = True):
    """What the webhook router does before the turn: index the request and
    record the verified origin in the dispatch-unkeyed handoff."""
    if index:
        assert request_index.record_inbound(
            vendor_message_id=message_id,
            sender=sender.lower(),
            subject="s",
            thread_ref=thread,
            received_at=None,
            body="please do the thing",
        )
    SESSION_INBOUND_ORIGIN.record(
        "",
        InboundOrigin(
            sender_address=sender.lower(),
            message_id=message_id,
            inbox_id="op@x",
            conversation_id=thread,
        ),
    )


def _hook(plugin, session: str, message_id: str, body: str = _ORDINARY, **over) -> str | None:
    kwargs = {
        "session_id": session,
        "sender_id": "webhook:agentmail",
        "platform": "webhook",
        "user_message": _prompt(message_id, body, over.pop("sender", _SENDER)),
    }
    kwargs.update(over)
    out = plugin.on_pre_llm_call(**kwargs)
    return out["context"] if out else None


def _offered(plugin) -> bool:
    return plugin.help_state.read_sender(_SENDER, None)["pointer_offered"]


# --------------------------------------------------------------------------- #
# The observed defect, closed
# --------------------------------------------------------------------------- #


def test_a_first_email_gets_the_exact_line_and_it_is_recorded(plugin, paths, monkeypatch):
    """The pilot turn, replayed: an ordinary question, first email, bound seat."""
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1")
    assert ctx is not None
    assert plugin._HELP_LINE_HEADER in ctx
    assert plugin.help_state.POINTER_LINE in ctx
    assert ctx.count(plugin.help_state.POINTER_LINE) == 1
    assert "phrase this kind of request" not in ctx
    # The exclusions travel with the line, and the model is told not to ask.
    assert "declines an action reserved to administrators" in ctx
    # A first-time person's chronology question keeps the line: the exclusion was
    # model-judged, ignored on pilot-smokeball 2026-10-25, and the first reply is
    # when the pointer helps most (vfy_01M5T6K0S6P1WJYPP9GHHRGNRQ).
    assert "chronology" not in ctx.split(plugin._HELP_LINE_HEADER, 1)[1]
    assert "do not call operator_help_signals" in ctx
    # The authority statement still rides the same return.
    assert "INITIATION AUTHORITY" in ctx
    assert _offered(plugin) is True


def test_the_line_text_is_the_single_constant_with_no_em_dash(plugin):
    for line in (plugin.help_state.POINTER_LINE,):
        assert "—" not in line and "–" not in line
        assert "{" not in line and "}" not in line
    assert plugin.help_state.POINTER_LINE == (
        "If you're ever unsure how to ask me for something, just ask me how we can work together."
    )


def test_a_second_email_gets_no_block(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    assert plugin.help_state.POINTER_LINE in _hook(plugin, "s-1", "m-1")
    _arrive("m-2", thread="t-2")
    ctx = _hook(plugin, "s-2", "m-2")
    assert ctx is not None and "INITIATION AUTHORITY" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx


def test_a_repeated_ask_in_a_thread_gets_no_line(plugin, paths, monkeypatch):
    """A fourth email in one thread is measured, never answered with a help line.

    ashton-price 2026-10-25: the old struggle line went to a firm person following
    up on queued chronology work; repetition cannot tell waiting from stuck."""
    _seat(plugin, monkeypatch)
    for i in range(1, 6):
        _arrive(f"m-{i}")
        ctx = _hook(plugin, f"s-{i}", f"m-{i}")
        assert "phrase this kind of request" not in (ctx or "")
        if i > 1:
            assert plugin._HELP_LINE_HEADER not in (ctx or "")
    assert not hasattr(plugin.help_state, "STRUGGLE_LINE")


# --------------------------------------------------------------------------- #
# Where it never appears
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cfg",
    [
        _Cfg(help_enabled=None),
        _Cfg(help_enabled=False),
        _Cfg(help_name="operator-introduce"),
    ],
    ids=["not-bound", "bound-disabled", "other-skill"],
)
def test_an_unbound_seat_never_gets_a_block_or_a_record(plugin, paths, monkeypatch, cfg):
    """FALSIFIER: drop the ``_help_bound`` gate and every case here fails."""
    _seat(plugin, monkeypatch, cfg)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1")
    assert ctx is not None and "INITIATION AUTHORITY" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx
    assert _offered(plugin) is False


def test_a_help_question_gets_the_nudge_and_no_pointer_and_no_record(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1", body="How do we work together? What's the best way to ask?")
    assert "depth 'help'" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx
    # Nothing was decided for this message, so nothing was recorded as offered.
    assert _offered(plugin) is False


def test_the_prompts_own_instructions_are_not_read_as_a_seat_question(plugin, paths, monkeypatch):
    """The rendered prompt says "who you are, what you can see"; the person
    asked a number. Reading the whole prompt fired the facts nudge on every
    email and would have suppressed the pointer on every email."""
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1")
    assert "call operator_seat_facts and compose your answer" not in ctx
    assert plugin.help_state.POINTER_LINE in ctx


def test_a_seat_question_in_the_body_still_gets_the_facts_nudge(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1", body="Who are you, and what is running each day?")
    assert "call operator_seat_facts and compose your answer" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx
    assert _offered(plugin) is False


def test_an_off_roster_sender_gets_nothing(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)
    _arrive("m-1", sender=_STRANGER)
    assert _hook(plugin, "s-1", "m-1", sender=_STRANGER) is None


def test_an_unresolved_turn_gets_no_block(plugin, paths, monkeypatch):
    """No verified email bound to this turn (the prompt names a message the
    router never recorded): authority may still resolve elsewhere, but no line."""
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-unknown")
    assert ctx is None or plugin._HELP_LINE_HEADER not in ctx
    assert _offered(plugin) is False


def test_a_message_the_index_never_saw_gets_no_block(plugin, paths, monkeypatch):
    """Auto-submitted mail is not indexed by the router: not a person to point."""
    _seat(plugin, monkeypatch)
    _arrive("m-1", index=False)
    ctx = _hook(plugin, "s-1", "m-1")
    assert plugin._HELP_LINE_HEADER not in ctx


def test_a_non_webhook_turn_gets_no_block(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1", platform="cli")
    assert ctx is None or plugin._HELP_LINE_HEADER not in ctx
    assert _offered(plugin) is False


# --------------------------------------------------------------------------- #
# Fail quiet
# --------------------------------------------------------------------------- #


def test_a_broken_state_file_costs_the_line_and_never_raises(plugin, paths, monkeypatch, tmp_path):
    _seat(plugin, monkeypatch)
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("SMD_HELP_STATE_DB_PATH", str(blocker / "help_state.db"))
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1")
    assert ctx is not None and "INITIATION AUTHORITY" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx


def test_a_raising_signals_call_costs_the_line_and_never_raises(plugin, paths, monkeypatch):
    _seat(plugin, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(plugin.help_state, "sender_signals", boom)
    _arrive("m-1")
    ctx = _hook(plugin, "s-1", "m-1")
    assert ctx is not None and "INITIATION AUTHORITY" in ctx
    assert plugin._HELP_LINE_HEADER not in ctx


# --------------------------------------------------------------------------- #
# Once per inbound
# --------------------------------------------------------------------------- #


def test_the_evaluation_runs_once_per_inbound_however_often_the_hook_fires(
    plugin, paths, monkeypatch
):
    """FALSIFIER: drop the ``_first_evaluation`` check and the signals call runs
    on every fire for the same message (counted below)."""
    _seat(plugin, monkeypatch)
    calls: list[str] = []
    real = plugin.help_state.sender_signals

    def counted(sender, message_id, **kw):
        calls.append(message_id)
        return real(sender, message_id, **kw)

    monkeypatch.setattr(plugin.help_state, "sender_signals", counted)
    _arrive("m-1")
    first = _hook(plugin, "s-1", "m-1")
    again = [_hook(plugin, "s-1", "m-1") for _ in range(3)]
    assert plugin.help_state.POINTER_LINE in first
    assert all(plugin._HELP_LINE_HEADER not in (ctx or "") for ctx in again)
    assert calls == ["m-1"]
