"""What a decision-less ("blind") wake must still do (2026-09-02).

THE INCIDENT. pilot-smokeball's Smokeball refresh token hit its 30-day
absolute expiry. ``pre_run`` took a fail-open path, and the seat produced a
scheduled tick with NEITHER a ``SUPPRESSED_WAKE`` nor an ``EMITTED_WAKE`` row
-- the dead-man's-switch signal SKILL.md step 5 names -- while the woken turn,
handed no rendered dispatch, composed a deadline digest out of nothing and
sent it. Two guarantees failed in the same tick, and both had been written
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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # The skill's own pre_run module, which loads this file beside itself at
    # runtime; a type-only import so the WakeDecision annotation names a real
    # class (pyright 2026-09-10: it was an undefined name).
    from pre_run import WakeDecision

import asyncio
import hashlib
import json
import sys

#: The only two fail-open bases where an ``EMITTED_WAKE`` row is genuinely
#: unwritable: the first fires BECAUSE no writer resolved, the second BECAUSE a
#: write to that writer just failed. Asking a broken writer to record that it
#: is broken adds nothing. Every OTHER blind wake has a working writer and
#: must leave a row.
UNWRITABLE_BASES = frozenset({"no_audit_writer_fail_open", "suppress_heartbeat_failed_fail_open"})


def render_failure_note(basis: str, load_sibling) -> dict:
    """Write the failure-note envelope so the turn has a rendered body to
    deliver instead of a gap to fill. {} when it could not be written (no
    authored recipient, or render.py unavailable) -- an honest fail-closed
    floor, and the row below is then what makes the slot visible."""
    try:
        envelope_mod = load_sibling("dispatch_envelope.py", "escalator_dispatch_envelope")
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
        asyncio.run(
            writer.write_emitted_wake(
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


# ---------------------------------------------------------------------------
# The DECISION path's row, moved here from pre_run.py so that every
# EMITTED_WAKE write -- blind and decided -- lives in one module. pre_run
# keeps the deciding and the stdout wake line.
# ---------------------------------------------------------------------------

#: Mirrors pre_run._MAX_SERIALIZED_PLANS. Duplicated deliberately: the
#: sibling must load standalone, and a drift here shows up as a plan-count
#: disagreement between the row and the wake line, which the escalator
#: suite asserts field for field.
_MAX_SERIALIZED_PLANS = 50


def plan_counts(decision: "WakeDecision") -> dict:
    """The cap's own accounting, computed the one way ``_emit_wake`` computes it.

    Duplicating the slice in the audit path would let the row and the wake line
    disagree about how much was handed over — a discrepancy nobody would look
    for, in the one record kept to catch discrepancies.
    """
    counts: dict = {}
    if decision.plans:
        emitted = len(decision.plans[:_MAX_SERIALIZED_PLANS])
        counts = {
            "plans_total": len(decision.plans),
            "plans_emitted": emitted,
            "plans_truncated": emitted < len(decision.plans),
        }
    if decision.digest is not None:
        # ss #2405: the projection's fingerprint + headline counts go on the
        # EMITTED_WAKE row, so a post-hoc audit pass can diff the SENT digest
        # against what the gate projected — the copy-verbatim contract's
        # enforcement seam (a SKILL.md sentence alone is the mechanism that
        # already failed).
        canonical = json.dumps(decision.digest, sort_keys=True, separators=(",", ":"))
        counts["digest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        counts["digest_needs_you"] = len(decision.digest.get("needs_you") or [])
        admin = decision.digest.get("admin_confirms") or {}
        counts["digest_admin_total"] = int(admin.get("total") or 0)
    return counts


async def try_write_emitted_wake(
    audit_writer_factory,
    decision,
    *,
    skill_name: str,
    next_scheduled_at: str,
) -> None:
    """Best-effort EMITTED_WAKE row for a real-decision wake (#2253).

    The suppress path logged its reasoning and the wake path logged nothing, so
    the ledger held a record of every tick the gate stayed quiet and no record
    of the ticks it fired. On 2026-08-10 the escalator woke with the Smokeball
    connector down and sent an alert stating a date it could not read; the only
    way anyone found it was reading the mailbox.

    BEST-EFFORT IS THE CONTRACT, and it inverts the suppress path's on purpose.
    Below, an audit failure escalates to a wake, because a silent suppress is
    indistinguishable from a broken gate. Here the wake is already the decision,
    so every failure — no writer wired, socket down, broker refusal, a writer
    object too old to have the method — is swallowed. A wake that a failed audit
    write could suppress or delay would be a gate made of observability.

    It is not free, and the cost is stated rather than assumed away: the
    broker-socket writer blocks for up to `_HEARTBEAT_TIMEOUT_SECONDS` against a
    hung broker — the same bound the suppress path already accepts. Bounded, and
    never a change of decision.

    This is the DECIDED path. The decision-less path is ``write_row`` above,
    which used to write nothing at all -- see this module's header for the
    2026-09-02 tick that exposed it. The only wakes that still leave no row are
    ``UNWRITABLE_BASES``, where the writer itself is the thing that failed.
    """
    try:
        writer = audit_writer_factory()
        if writer is None:
            return
        await writer.write_emitted_wake(
            skill_name=skill_name,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata={**decision.extra_metadata, **plan_counts(decision)},
        )
    except Exception:  # noqa: BLE001 — observability never gates the wake
        pass
