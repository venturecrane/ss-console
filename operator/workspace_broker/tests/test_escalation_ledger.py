"""Unit tests for the shared escalation_ledger module (WP-A / WP-B).

Covers item identity + token derivation, corrupt-line handling, state
derivation, the fire policy (fire-once / re-fire window / ack-snooze /
terminal), and validate_append's acked-needs-prior-raise rule.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import escalation_ledger as el


# ---------------------------------------------------------------------------
# item_key + token
# ---------------------------------------------------------------------------


def test_item_key_stable_and_source_id_disambiguates() -> None:
    a = el.item_key("m-1", "task-1", "filing-deadline", date(2026, 7, 20))
    b = el.item_key("m-1", "task-1", "filing-deadline", "2026-07-20")
    assert a == b  # date and iso-string agree
    # Two same-day tasks on ONE matter differ only by source id — the
    # anti-collision guarantee the plan calls out.
    c = el.item_key("m-1", "task-2", "filing-deadline", date(2026, 7, 20))
    assert a != c


def test_item_key_ignores_label() -> None:
    """ss #2151 guard. ``label`` is model-composed free text and MUST NOT reach
    the hash. Putting it back re-breaks the ledger join."""
    base = el.item_key("m-1", "task-1", "task-deadline", date(2026, 7, 20))
    assert base == el.item_key("m-1", "task-1", "settlement-offer-lapsed", date(2026, 7, 20))
    assert base == el.item_key("m-1", "task-1", "", date(2026, 7, 20))
    assert base == el.item_key("m-1", "task-1", None, date(2026, 7, 20))


def test_pre_run_and_agent_labels_agree_on_one_task() -> None:
    """The live defect, reproduced. ``pre_run`` assigns a closed-set label; the
    agent's turn invents a descriptor. Before ss #2151 these hashed the SAME
    Smokeball task to different keys, so nothing ever joined: on the pilot, 160
    events produced 128 item states and none matched an open task. Fire-once and
    the seven-day ack snooze were both inert."""
    pre_run_side = el.item_key("m-7", "task-42", "task-deadline", date(2026, 8, 11))
    agent_side = el.item_key("m-7", "task-42", "rfa-confirm-service-date", date(2026, 8, 11))
    assert pre_run_side == agent_side
    assert el.token_for(pre_run_side) == el.token_for(agent_side)


def test_item_key_still_discriminates_on_stable_fields() -> None:
    """Dropping label must not collapse genuinely distinct items."""
    base = el.item_key("m-1", "task-1", "task-deadline", date(2026, 7, 20))
    assert base != el.item_key("m-2", "task-1", "task-deadline", date(2026, 7, 20))
    assert base != el.item_key("m-1", "task-2", "task-deadline", date(2026, 7, 20))
    assert base != el.item_key("m-1", "task-1", "task-deadline", date(2026, 7, 21))


# One authored deadline, as the several call sites actually spell it: the
# connector reads a ``date`` off the record; the agent's tool arg is a schema
# ``string``, and the model has written the bare day, the full timestamp the
# Smokeball payload carried, and a ``datetime`` handed straight through.
_ONE_EVENT_SPELLINGS = (
    date(2026, 8, 11),
    "2026-08-11",
    "  2026-08-11 ",
    "2026-08-11T00:00:00Z",
    "2026-08-11T14:32:07.512Z",
    "2026-08-11T14:32:07+00:00",
    datetime(2026, 8, 11, 14, 32, 7, tzinfo=timezone.utc),
)


def test_one_event_two_date_spellings_is_one_item() -> None:
    """ss #2289 fix 1. ``authored_date`` reaches ``item_key`` as free text the
    model typed (the append tool's schema types it ``string``), so the SAME
    deadline arrives spelled several ways across runs. Before normalization each
    spelling hashed to its own item: fire-once counted them separately and every
    per-item ACK code named whichever spelling happened to be in the last raise.
    Same defect family as the label — a key component that is not canonical."""
    keys = {el.item_key("m-1", "task-1", "task-deadline", spelling) for spelling in _ONE_EVENT_SPELLINGS}
    assert len(keys) == 1, f"one event, {len(keys)} identities: {sorted(keys)}"
    # ...and it is the SAME identity the connector side derives, which passes a
    # real ``date`` object read off the Smokeball record.
    assert keys == {el.item_key("m-1", "task-1", "task-deadline", date(2026, 8, 11))}


def test_unparseable_authored_date_is_rejected_not_hashed() -> None:
    """A date the module cannot canonicalize must NOT silently become part of an
    identity — that is how "tomorrow" and "2026-08-11" become two items. Reject
    at the seam so the turn sees the error while it can still fix the argument."""
    with pytest.raises(ValueError, match="authored_date"):
        el.item_key("m-1", "task-1", "task-deadline", "tomorrow")
    with pytest.raises(ValueError, match="authored_date"):
        el.item_key("m-1", "task-1", "task-deadline", "08/11/2026")


def test_source_and_matter_ids_are_normalized_before_hashing() -> None:
    """Smokeball ids are GUIDs; a model retyping one may pad or re-case it. The
    connector emits them verbatim off the wire, so both sides must fold the same
    way or the join forks on whitespace."""
    guid = "3C191BED-CDDA-48B9-A6ED-A51A349F3F94"
    base = el.item_key("m-1", guid.lower(), "task-deadline", date(2026, 8, 11))
    assert el.item_key("m-1", guid, "task-deadline", date(2026, 8, 11)) == base
    assert el.item_key("m-1", f"  {guid}  ", "task-deadline", date(2026, 8, 11)) == base
    assert el.item_key("  M-1 ", guid, "task-deadline", date(2026, 8, 11)) == base


def test_has_stable_identity() -> None:
    assert el.has_stable_identity("task-9", "m-1") is True
    assert el.has_stable_identity("", "m-1") is False
    assert el.has_stable_identity(None, "m-1") is False


def test_sentinel_exclusion_fires_on_a_real_unknown_matter_row() -> None:
    """ss #2289 fix 2. The guard excluded ``"unknown-matter"`` from ``source_id``
    — but ``_source_id_of`` never emits it and ``_matter_id_of`` does (both
    pre_run copies: it is the fallback when the nested matter link is absent).
    The exclusion could not fire on any row the connector actually writes.

    A row shaped exactly as the connector writes one for an unresolvable matter:
    a real task GUID, the matter sentinel. Half its identity tuple is invented,
    so the key moves the moment the matter resolves — it cannot carry a per-item
    ACK code, and this is the guard that has to say so."""
    real_task_guid = "3c191bed-cdda-48b9-a6ed-a51a349f3f94"
    assert el.has_stable_identity(real_task_guid, "unknown-matter") is False
    # The same task on a resolved matter keeps its per-item token.
    assert el.has_stable_identity(real_task_guid, "m-1") is True


def test_token_is_deterministic_and_typable() -> None:
    key = el.item_key("m-1", "task-1", "filing-deadline", date(2026, 7, 20))
    t1 = el.token_for(key)
    t2 = el.token_for(key)
    assert t1 == t2
    assert t1.startswith("ACK-") and len(t1) == 10
    body = t1[4:]
    assert all(ch in el._CROCKFORD for ch in body)  # no ambiguous I/L/O/U


# ---------------------------------------------------------------------------
# read_ledger — corrupt lines skipped (fail-noisy)
# ---------------------------------------------------------------------------


def test_read_ledger_missing_file_is_empty(tmp_path: Path) -> None:
    assert el.read_ledger(str(tmp_path / "nope.jsonl")) == []


def test_read_ledger_skips_corrupt_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    good = el.serialize_event(
        el.make_event(
            skill="deadline-miss-escalator",
            matter_id="m-1",
            item_key="k1",
            event="fired",
            attempt=1,
            token="ACK-000001",
            ts="2026-07-14T07:00:00.000Z",
        )
    )
    path.write_text(
        good
        + "\n"
        + "\n"  # blank
        + "{not json\n"  # corrupt
        + '{"v":1,"ts":"x","skill":"s"}\n'  # missing item_key/event -> skipped
        + good
        + "\n",
        encoding="utf-8",
    )
    events = el.read_ledger(str(path))
    assert len(events) == 2  # two good, three dropped
    assert all(e["event"] == "fired" for e in events)


# ---------------------------------------------------------------------------
# derive_state + should_fire
# ---------------------------------------------------------------------------


def _ev(event, *, key="k1", ts, attempt=1, token="ACK-000001", matter_id="m-1"):
    return el.make_event(
        skill="deadline-miss-escalator",
        matter_id=matter_id,
        item_key=key,
        event=event,
        attempt=attempt,
        token=token,
        ts=ts,
    )


def test_never_raised_fires() -> None:
    assert el.should_fire(None, date(2026, 7, 14), refire_days=3, ack_snooze_days=7) is True
    assert el.next_attempt(None) == 1


def test_fired_within_window_suppresses_then_refires() -> None:
    events = [_ev("fired", ts="2026-07-12T07:00:00.000Z")]
    state = el.derive_state(events)["k1"]
    assert state.attempts == 1
    # 1 day later, refire_days=3 -> still within window -> suppress
    assert el.should_fire(state, date(2026, 7, 13), refire_days=3, ack_snooze_days=7) is False
    # 3 days later -> window elapsed -> re-fire
    assert el.should_fire(state, date(2026, 7, 15), refire_days=3, ack_snooze_days=7) is True
    assert el.next_attempt(state) == 2


def test_acked_snoozes_then_resurfaces() -> None:
    events = [
        _ev("fired", ts="2026-07-10T07:00:00.000Z"),
        _ev("acked", ts="2026-07-10T09:00:00.000Z"),
    ]
    state = el.derive_state(events)["k1"]
    assert state.acked is True
    # within the snooze window -> suppressed
    assert el.should_fire(state, date(2026, 7, 15), refire_days=3, ack_snooze_days=7) is False
    # after ack_snooze_days -> re-surface (ack is a snooze, not a tombstone)
    assert el.should_fire(state, date(2026, 7, 17), refire_days=3, ack_snooze_days=7) is True


def test_refire_after_ack_clears_the_ack() -> None:
    events = [
        _ev("fired", ts="2026-07-10T07:00:00.000Z"),
        _ev("acked", ts="2026-07-10T09:00:00.000Z"),
        _ev("fired", ts="2026-07-17T07:00:00.000Z", attempt=2),
    ]
    state = el.derive_state(events)["k1"]
    assert state.acked is False  # the later raise supersedes the ack
    assert state.attempts == 2


def test_resolved_and_handed_off_are_terminal() -> None:
    """Terminal only ABSENT a later raise — a fresh fired/chased after either
    one re-opens the item (see the reopen tests below). With no later raise,
    both stay silent forever."""
    resolved = el.derive_state(
        [_ev("fired", ts="2026-07-10T07:00:00.000Z"), _ev("resolved", ts="2026-07-11T07:00:00.000Z")]
    )["k1"]
    assert el.should_fire(resolved, date(2026, 8, 1), refire_days=3, ack_snooze_days=7) is False
    handed = el.derive_state(
        [_ev("fired", ts="2026-07-10T07:00:00.000Z"), _ev("handed_off", ts="2026-07-11T07:00:00.000Z")]
    )["k1"]
    assert el.should_fire(handed, date(2026, 8, 1), refire_days=3, ack_snooze_days=7) is False


# ---------------------------------------------------------------------------
# Symmetric reset on a raise — a fresh raise re-opens the item (the live
# 2026-08-24..31 hold-loop defect: resolved was sticky forever, so the hold
# sentinel's fired -> resolved -> fired sequence folded to a released hold and
# the chase planned straight past the unresolved signer).
# ---------------------------------------------------------------------------


def test_a_raise_after_resolved_reopens_the_item() -> None:
    """Replay of the LIVE sequence off the pilot ledger: fired 08-24, resolved
    08-27, fired 08-31. The 08-31 raise must clear the resolution and govern
    the re-fire window."""
    events = [
        _ev("fired", ts="2026-08-24T14:00:00.000Z", attempt=1),
        _ev("resolved", ts="2026-08-27T14:00:00.000Z"),
        _ev("fired", ts="2026-08-31T14:00:00.000Z", attempt=2),
    ]
    state = el.derive_state(events)["k1"]
    assert state.resolved is False
    assert state.attempts == 2
    # The refire window runs from the 08-31 raise, not from anything older.
    assert el.should_fire(state, date(2026, 9, 1), refire_days=3, ack_snooze_days=7) is False
    assert el.should_fire(state, date(2026, 9, 3), refire_days=3, ack_snooze_days=7) is True


def test_a_raise_after_handed_off_reopens_the_item() -> None:
    """The asymmetric alternative (resetting only ``resolved``) would make a
    re-raised handed-off hold a silent black hole: the hold blocks chases while
    decide()'s handed_off guard suppresses every re-surface. A new alarm after
    a hand-off deserves a fresh hand-off."""
    events = [
        _ev("fired", ts="2026-07-10T07:00:00.000Z", attempt=1),
        _ev("handed_off", ts="2026-07-11T07:00:00.000Z"),
        _ev("fired", ts="2026-07-20T07:00:00.000Z", attempt=2),
    ]
    state = el.derive_state(events)["k1"]
    assert state.handed_off is False
    assert state.resolved is False
    assert el.should_fire(state, date(2026, 7, 24), refire_days=3, ack_snooze_days=7) is True


def test_resolve_raise_resolve_ends_resolved() -> None:
    """Ordering: the latest signal wins. A resolution recorded AFTER the
    re-raise closes the item again."""
    events = [
        _ev("fired", ts="2026-07-10T07:00:00.000Z", attempt=1),
        _ev("resolved", ts="2026-07-11T07:00:00.000Z"),
        _ev("fired", ts="2026-07-20T07:00:00.000Z", attempt=2),
        _ev("resolved", ts="2026-07-21T07:00:00.000Z"),
    ]
    state = el.derive_state(events)["k1"]
    assert state.resolved is True
    assert el.should_fire(state, date(2026, 8, 1), refire_days=3, ack_snooze_days=7) is False


def test_same_ts_ties_break_by_file_order() -> None:
    """The fold's sort is STABLE, so two events stamped the same millisecond
    keep the append-only file's own order. Pinned because the symmetric reset
    makes the raise/release order load-bearing: raise-then-release ends
    released, release-then-raise ends open."""
    ts = "2026-07-10T07:00:00.000Z"
    released = el.derive_state([_ev("fired", ts=ts), _ev("resolved", ts=ts)])["k1"]
    assert released.resolved is True
    reopened = el.derive_state([_ev("resolved", ts=ts), _ev("fired", ts=ts)])["k1"]
    assert reopened.resolved is False


def test_derive_state_orders_by_ts_regardless_of_input_order() -> None:
    # ack appears before fire in the list; ts ordering must still put fire first.
    events = [
        _ev("acked", ts="2026-07-10T09:00:00.000Z"),
        _ev("fired", ts="2026-07-10T07:00:00.000Z"),
    ]
    state = el.derive_state(events)["k1"]
    assert state.acked is True  # ack is the latest event
    assert state.attempts == 1


# ---------------------------------------------------------------------------
# validate_append
# ---------------------------------------------------------------------------


#: A broker that witnessed a dispatch to a person, and one that did not. The
#: witness is keyword-only with no default, so every call names which world it
#: is in — that is the point of the signature.
def _witnessed(_event: dict) -> bool:
    return True


def _unwitnessed(_event: dict) -> bool:
    return False


def test_validate_append_accepts_fired() -> None:
    el.validate_append([], _ev("fired", ts="2026-07-14T07:00:00.000Z"), send_witness=_witnessed)


def test_validate_append_rejects_acked_without_raise() -> None:
    with pytest.raises(ValueError):
        el.validate_append([], _ev("acked", ts="2026-07-14T07:00:00.000Z"), send_witness=_witnessed)


def test_validate_append_matches_ack_by_token() -> None:
    existing = [_ev("fired", ts="2026-07-14T07:00:00.000Z", token="ACK-ABCDEF", key="k9")]
    # same token, different item_key spelling still matches on token
    el.validate_append(
        existing,
        _ev("acked", ts="2026-07-14T08:00:00.000Z", token="ACK-ABCDEF", key="k9"),
        send_witness=_witnessed,
    )


def test_validate_append_rejects_unknown_event() -> None:
    with pytest.raises(ValueError):
        el.validate_append([], {"event": "boom", "item_key": "k1", "skill": "s", "ts": "x"}, send_witness=_witnessed)


def test_validate_append_requires_item_key_and_skill() -> None:
    with pytest.raises(ValueError):
        el.validate_append(
            [],
            {"event": "fired", "item_key": "", "skill": "s", "ts": "x"},
            send_witness=_witnessed,
        )
    with pytest.raises(ValueError):
        el.validate_append(
            [],
            {"event": "fired", "item_key": "k1", "skill": "", "ts": "x"},
            send_witness=_witnessed,
        )


# ---------------------------------------------------------------------------
# The raise witness — you cannot record that an alarm rang when it did not.
#
# Pilot-smokeball, 2026-08-26: five `fired` rows written in a turn whose only
# delivery attempt was a refused memo; refire_days=3 then silenced those five
# deadlines until 08-29. 2026-08-20: 77 appends, zero sends. The `acked` guard
# below has always refused to silence an alarm that never rang; these tests
# close the other door into the same silence.
# ---------------------------------------------------------------------------


def test_an_unwitnessed_fired_is_refused() -> None:
    with pytest.raises(ValueError, match="dispatched no message"):
        el.validate_append([], _ev("fired", ts="2026-08-26T14:02:06.416Z"), send_witness=_unwitnessed)


def test_an_unwitnessed_chased_is_refused_too() -> None:
    """`chased` is a RAISING_EVENTS member: it also claims a person was reached."""
    with pytest.raises(ValueError, match="dispatched no message"):
        el.validate_append([], _ev("chased", ts="2026-08-26T14:02:06.416Z"), send_witness=_unwitnessed)


def test_the_refusal_names_the_send_tool_and_says_retrying_will_not_help() -> None:
    """The overlay keeps the append handle alive after a broker refusal so the
    turn can retry the same identity, and this repo carries no runaway-loop brake.
    A refusal that reads as transient therefore invites a retry storm — so it must
    say what would change the answer, and that waiting will not."""
    with pytest.raises(ValueError) as excinfo:
        el.validate_append([], _ev("fired", ts="2026-08-26T14:02:06.416Z"), send_witness=_unwitnessed)
    message = str(excinfo.value)
    assert "smd_send_message" in message
    assert "fail identically" in message


def test_non_raising_events_never_consult_the_witness() -> None:
    """An ack/resolve is not a claim that anyone was reached, so it must not pay
    for a lookup — and must not be refused by a broker that cannot do one."""
    calls: list[dict] = []

    def _recording(event: dict) -> bool:
        calls.append(event)
        return False

    existing = [_ev("fired", ts="2026-08-01T07:00:00.000Z", token="ACK-ABCDEF", key="k9")]
    el.validate_append(
        existing,
        _ev("acked", ts="2026-08-01T08:00:00.000Z", token="ACK-ABCDEF", key="k9"),
        send_witness=_recording,
    )
    el.validate_append(existing, _ev("resolved", ts="2026-08-01T09:00:00.000Z", key="k9"), send_witness=_recording)
    assert calls == []


def test_the_witness_is_required_not_optional() -> None:
    """Fail-closed by construction: a caller that forgets the witness gets a
    TypeError, never a silently unguarded raise."""
    with pytest.raises(TypeError):
        el.validate_append([], _ev("fired", ts="2026-08-26T14:02:06.416Z"))  # type: ignore[call-arg]


def test_a_non_callable_witness_is_refused() -> None:
    with pytest.raises(ValueError, match="callable send_witness"):
        el.validate_append([], _ev("fired", ts="2026-08-26T14:02:06.416Z"), send_witness=True)


def test_the_witness_sees_the_event_so_it_can_scope_by_session() -> None:
    seen: list[str] = []

    def _capture(event: dict) -> bool:
        seen.append(str(event.get("session_id")))
        return True

    event = _ev("fired", ts="2026-08-25T14:01:13.458Z")
    event["session_id"] = "cron_6c073ab9b3fc_20260825_070034"
    el.validate_append([], event, send_witness=_capture)
    assert seen == ["cron_6c073ab9b3fc_20260825_070034"]


# ---------------------------------------------------------------------------
# Identity epoch (ss #2151) — a pre-epoch code must not read as acknowledged
# ---------------------------------------------------------------------------


def test_ack_against_pre_epoch_raise_is_refused_by_name() -> None:
    """A human replying with an ACK code from an old alert must be told the code
    is superseded, not quietly told the item was acked. The pre-epoch key names
    nothing live, so accepting it would report a silenced alarm that never rang —
    the same false-report class the identity fix exists to end."""
    stale = _ev("fired", ts="2026-08-11T14:05:18.711Z", token="ACK-8SQ6CJ", key="d6718838f50dfa54")
    stale["v"] = 1
    with pytest.raises(ValueError, match="ss #2151"):
        el.validate_append(
            [stale],
            _ev("acked", ts="2026-08-12T09:00:00.000Z", token="ACK-8SQ6CJ", key="d6718838f50dfa54"),
            send_witness=_witnessed,
        )


def test_ack_still_accepted_when_a_current_raise_exists() -> None:
    """The epoch guard must not block a legitimate ack. A pre-epoch row alongside
    a current raise for the same token resolves against the current one."""
    stale = _ev("fired", ts="2026-08-11T14:05:18.711Z", token="ACK-8SQ6CJ", key="k-live")
    stale["v"] = 1
    current = _ev("fired", ts="2026-08-12T14:00:00.000Z", token="ACK-8SQ6CJ", key="k-live")
    el.validate_append(
        [stale, current],
        _ev("acked", ts="2026-08-12T15:00:00.000Z", token="ACK-8SQ6CJ", key="k-live"),
        send_witness=_witnessed,
    )


def test_raise_with_missing_version_is_treated_as_pre_epoch() -> None:
    """Unknown provenance is not evidence of a current key."""
    stale = _ev("fired", ts="2026-08-11T14:05:18.711Z", token="ACK-ZZZZZZ", key="k-unknown")
    stale.pop("v", None)
    with pytest.raises(ValueError, match="ss #2151"):
        el.validate_append(
            [stale],
            _ev("acked", ts="2026-08-12T09:00:00.000Z", token="ACK-ZZZZZZ", key="k-unknown"),
            send_witness=_witnessed,
        )


def test_schema_version_is_at_the_identity_epoch() -> None:
    """New events must be written at (or above) the epoch, or every ack they
    later receive would be refused as superseded."""
    assert el.SCHEMA_VERSION >= el.IDENTITY_EPOCH


# ---------------------------------------------------------------------------
# Release events (resolved / handed_off) need a prior raise — the third door
# into silence. A mis-keyed release lands on a phantom key today and on a REAL
# key the day the caller's derivation drifts, silencing a different item.
# ---------------------------------------------------------------------------


def test_validate_append_rejects_resolved_without_raise() -> None:
    with pytest.raises(ValueError, match="no prior fired/chased raise"):
        el.validate_append([], _ev("resolved", ts="2026-08-27T14:00:00.000Z"), send_witness=_witnessed)


def test_validate_append_rejects_handed_off_without_raise() -> None:
    with pytest.raises(ValueError, match="no prior fired/chased raise"):
        el.validate_append([], _ev("handed_off", ts="2026-08-27T14:00:00.000Z"), send_witness=_witnessed)


def test_validate_append_accepts_release_with_prior_raise_by_item_key() -> None:
    existing = [_ev("fired", ts="2026-08-24T14:00:00.000Z", key="k9", token=None)]
    el.validate_append(
        existing,
        _ev("resolved", ts="2026-08-27T14:00:00.000Z", key="k9", token=None),
        send_witness=_witnessed,
    )
    el.validate_append(
        existing,
        _ev("handed_off", ts="2026-08-27T14:00:00.000Z", key="k9", token=None),
        send_witness=_witnessed,
    )


def test_release_against_pre_epoch_raise_only_is_refused() -> None:
    """A pre-epoch raise keyed the item under the superseded derivation, so it
    names nothing live: releasing against it would report a released alarm
    while the current-epoch item keeps firing."""
    stale = _ev("fired", ts="2026-08-01T14:00:00.000Z", key="k-old")
    stale["v"] = 1
    with pytest.raises(ValueError, match="ss #2151"):
        el.validate_append(
            [stale],
            _ev("resolved", ts="2026-08-27T14:00:00.000Z", key="k-old"),
            send_witness=_witnessed,
        )


def test_release_refusal_is_corrective_and_terminal() -> None:
    """The refusal must say what to do (nothing) and that retrying is futile —
    and it must NOT name the one event kind that would slip past this branch
    (an evasive recompose that swaps the refused kind for an ``acked`` is the
    exact loop the corrective-and-terminal wording exists to prevent)."""
    with pytest.raises(ValueError) as excinfo:
        el.validate_append([], _ev("resolved", ts="2026-08-27T14:00:00.000Z"), send_witness=_witnessed)
    message = str(excinfo.value)
    assert "Write nothing" in message
    assert "fail identically" in message
    assert "acked" not in message  # never name an alternate event kind


# ---------------------------------------------------------------------------
# The determination payload (hold releases, ss #2402 Part 3)
# ---------------------------------------------------------------------------


_DET = {
    "note": "plaintiff is a single adult; Minor/Deceased tags are layout artifacts",
    "role_snapshot_sha256": "ab" * 32,
    "confirmed_via": "matter_record",
}


def _resolved_with_determination(*, ts, key="k1", determination=_DET):
    row = _ev("resolved", ts=ts, key=key, token=None)
    row["determination"] = determination
    return row


def test_resolved_may_carry_determination_and_state_exposes_it() -> None:
    existing = [_ev("fired", ts="2026-08-24T14:00:00.000Z", token=None)]
    row = _resolved_with_determination(ts="2026-08-27T14:00:00.000Z")
    el.validate_append(existing, row, send_witness=_witnessed)
    state = el.derive_state(existing + [row])["k1"]
    assert state.resolved is True
    assert state.determination == _DET


def test_determination_survives_a_later_raise() -> None:
    """A raise re-opens the item but never erases the determination: consult
    validity is governed by the snapshot hash against the current roles, not by
    hold state."""
    events = [
        _ev("fired", ts="2026-08-24T14:00:00.000Z", token=None),
        _resolved_with_determination(ts="2026-08-27T14:00:00.000Z"),
        _ev("fired", ts="2026-08-31T14:00:00.000Z", attempt=2, token=None),
    ]
    state = el.derive_state(events)["k1"]
    assert state.resolved is False  # re-opened
    assert state.determination == _DET  # sticky


@pytest.mark.parametrize(
    "bad",
    [
        "not an object",
        {},
        {"note": "", "role_snapshot_sha256": "ab" * 32, "confirmed_via": "person"},
        {"note": "x" * 501, "role_snapshot_sha256": "ab" * 32, "confirmed_via": "person"},
        {"note": "ok", "role_snapshot_sha256": "XYZ", "confirmed_via": "person"},
        {"note": "ok", "role_snapshot_sha256": "AB" * 32, "confirmed_via": "person"},  # uppercase
        {"note": "ok", "role_snapshot_sha256": "ab" * 32, "confirmed_via": "vibes"},
        {"note": "ok", "role_snapshot_sha256": "ab" * 32, "confirmed_via": "person", "x": 1},
    ],
)
def test_malformed_determination_is_rejected(bad) -> None:
    existing = [_ev("fired", ts="2026-08-24T14:00:00.000Z", token=None)]
    with pytest.raises(ValueError):
        el.validate_append(
            existing,
            _resolved_with_determination(ts="2026-08-27T14:00:00.000Z", determination=bad),
            send_witness=_witnessed,
        )


def test_determination_on_non_resolved_is_rejected() -> None:
    existing = [_ev("fired", ts="2026-08-24T14:00:00.000Z", token=None)]
    for kind in ("fired", "chased", "acked", "handed_off"):
        row = _ev(kind, ts="2026-08-27T14:00:00.000Z")
        row["determination"] = _DET
        with pytest.raises(ValueError, match="resolved event"):
            el.validate_append(existing, row, send_witness=_witnessed)


def test_make_event_carries_determination_only_when_given() -> None:
    bare = el.make_event(skill="s", matter_id="m", item_key="k", event="resolved", attempt=0)
    assert "determination" not in bare
    carried = el.make_event(
        skill="s",
        matter_id="m",
        item_key="k",
        event="resolved",
        attempt=0,
        determination=_DET,
    )
    assert carried["determination"] == _DET
