"""What a decision-less ("blind") wake must still do (2026-09-02).

THE INCIDENT. pilot-smokeball's Smokeball refresh token hit its 30-day
absolute expiry. ``pre_run`` took a fail-open path, and the seat produced a
scheduled tick with NEITHER a ``SUPPRESSED_WAKE`` nor an ``EMITTED_WAKE`` row
-- the dead-man's-switch signal SKILL.md step 5 names -- while the woken turn,
handed no rendered dispatch, composed a verification alert out of nothing
and sent it. Two guarantees failed in the same tick, and both had been written
down rather than enforced:

* **The row.** ``_try_write_emitted_wake`` takes a ``WakeDecision``, so every
  decision-less wake skipped it. The exemption was reasoned for the two bases
  where the writer is genuinely unusable and then applied to all of them,
  including a crash fail-open where the writer is fine. A blind wake is the
  MOST important tick to record, not the least: it is the one where the turn
  knows least and can invent most.
* **The body.** The one-line failure note was a SKILL.md instruction. An
  instruction to the model is not a control. It is now an envelope, dispatched
  out of turn on the same path as a real digest.

Neither write may suppress or delay the wake -- observability is never a gate
-- so both are best-effort and bounded, exactly as the decision path's row
already is.

Sibling module, path-loaded by ``pre_run.py`` (module-size ratchet:
``tests/operator-module-size.test.ts``), resolved from the synced skill dir
like ``dispatch_envelope.py`` and ``broker_writer.py``.

Kept parallel with the escalator's copy in SHAPE, not byte-for-byte: the two
skills are the only `templated` / `slot-templated` senders and their
blind-wake behaviour must not diverge. The DECIDED path's row is
``skill_helpers.try_write_emitted_wake`` in both (2026-09-25 review,
Architecture 8); this skill hands it ``plan_counts_total`` because it
serializes every plan, and the escalator hands it its own capped
``blind_wake.plan_counts``.
"""

from __future__ import annotations

import asyncio
import sys
import threading

#: The only two fail-open bases where an ``EMITTED_WAKE`` row is genuinely
#: unwritable: the first fires BECAUSE no writer resolved, the second BECAUSE a
#: write to that writer just failed. Asking a broken writer to record that it
#: is broken adds nothing. Every OTHER blind wake has a working writer and
#: must leave a row.
UNWRITABLE_BASES = frozenset({"no_audit_writer_fail_open", "suppress_heartbeat_failed_fail_open"})


#: The only keys a blind wake may add to the stdout wake line. A closed set:
#: the payload is injected verbatim into the woken turn's prompt, so anything
#: that leaks in here is something the turn will read as fact.
WAKE_PAYLOAD_KEYS = ("dispatch_expected", "dispatch_variant")


def wake_blind(
    basis: str,
    *,
    load_sibling,
    writer_factory,
    skill_name: str,
    next_scheduled_at: str,
) -> dict:
    """Render the note, write the row, return the wake-line additions.

    One entry point so ``pre_run`` carries a delegation rather than a copy of
    this policy (module-size ratchet).
    """
    extra = render_failure_note(basis, load_sibling)
    write_row(
        basis,
        writer_factory=writer_factory,
        skill_name=skill_name,
        next_scheduled_at=next_scheduled_at,
        extra=extra,
    )
    return {k: v for k, v in extra.items() if k in WAKE_PAYLOAD_KEYS}


def render_failure_note(basis: str, load_sibling) -> dict:
    """Write the failure-note envelope so the turn has a rendered body to
    deliver instead of a gap to fill. {} when it could not be written (no
    authored recipient, or render.py unavailable) -- an honest fail-closed
    floor, and the row below is then what makes the slot visible."""
    try:
        envelope_mod = load_sibling("dispatch_envelope.py", "cvt_dispatch_envelope")
        if envelope_mod is None:
            return {}
        return envelope_mod.write_failure_note_envelope(reason=basis) or {}
    except Exception as exc:  # noqa: BLE001 — observability never gates the wake
        sys.stderr.write("[pre_run] blind-wake failure note failed (" + str(exc) + ")\n")
        return {}


def write_row(
    basis: str,
    *,
    writer_factory,
    skill_name: str,
    next_scheduled_at: str,
    extra: dict,
) -> None:
    """Best-effort ``EMITTED_WAKE`` row for a wake with no decision behind it."""
    if basis in UNWRITABLE_BASES:
        return
    try:
        writer = writer_factory()
        if writer is None:
            return
        _run_to_completion(
            lambda: writer.write_emitted_wake(
                skill_name=skill_name,
                # No decision means no inputs digest to stamp. Empty is the
                # honest value; a fabricated digest would make a blind tick
                # look like it had read something.
                pre_run_inputs=b"",
                decision_basis=basis,
                next_scheduled_at=next_scheduled_at,
                extra_metadata={"blind_wake": True, **extra},
            )
        )
    except Exception as exc:  # noqa: BLE001 — observability never gates the wake
        sys.stderr.write("[pre_run] blind-wake row failed (" + str(exc) + ")\n")


def _run_to_completion(coro_factory) -> None:
    """Drive the writer's coroutine whether or not an event loop is running.

    ``ledger_unavailable_fail_open`` fires INSIDE ``run_once``, which
    ``main`` drives under ``asyncio.run``; a bare ``asyncio.run`` there
    raises ``RuntimeError`` (cannot be called from a running event loop),
    the ``except`` above swallows it, and the one row this module exists to
    write is never written. With a loop running, the coroutine is driven on
    a worker thread with its own loop and joined; the join blocks the
    calling loop for as long as the writer does, which is the same bound
    the decision path already accepts (the broker heartbeat timeout).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro_factory())
        return
    outcome: dict = {}

    def _worker() -> None:
        try:
            asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=_worker, name="cvt-blind-wake-row", daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
