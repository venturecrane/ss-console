"""The audit-integrity ALARM DRILL (ss#2500) — proving the alarm can ring.

Lifted out of ``audit-chain-watch.py`` when that file crossed the module-size
ceiling. It is a clean seam: the drill shares only ``evaluate_export`` and
``emit_alert`` with the daily run, and it has its own exit codes, its own safety
rules and its own tests.

WHY IT EXISTS. Everything under this alarm was proven months ago — every seat's
chain verifies daily, every head is pinned off the Machine, every ledger is
copied to a bucket locked for seven years, and a falsifier gates the run so it
cannot go quietly green. The one thing nobody had ever watched was a finding
travelling from the watch script to the console's alert sink. An alarm nobody has
heard is the exact defect class this control exists to end.

The dependencies arrive as CALLABLES because the caller is a dash-named script
that cannot be imported by name; injecting them also lets the tests drive the
real ``evaluate_export`` while stubbing the sink.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from bin.lib.console_d1 import REHEARSAL_DRIVER_PREFIX


class _Console(Protocol):  # pragma: no cover - typing only
    def newest_pin(self, slug: str) -> Optional[dict]: ...


#: A rehearsal is neither clean nor a finding about a client's records. Its own
#: exit code keeps it from ever being read as either — a drill that can be
#: mistaken for the real alarm is worse than no drill.
EXIT_REHEARSAL_OK = 3
EXIT_REHEARSAL_FAILED = 4

#: The head a rehearsal pins. Not random and not a real digest: a value that is
#: the right SHAPE (64 hex) so it passes the malformed-pin guard and reaches the
#: real absent-head branch, while being one no chain can ever produce, so the
#: rehearsal cannot accidentally match a genuine head and report clean.
REHEARSAL_HEAD = "de" * 32


def rehearse_mismatch(
    slug: str,
    console: _Console,
    *,
    seam_client_from_env: Callable[[str], Any],
    evaluate_export: Callable[..., Any],
    emit_alert: Callable[..., Optional[str]],
) -> tuple[int, list[str]]:
    """Prove the head-mismatch ALARM fires, not just that the detector detects.

    ss#2500's last criterion. Everything under it was proven months ago -- chains
    verified daily, heads pinned, copies locked for seven years -- and the one
    thing nobody had watched was a finding travelling from this script to the
    console's alert sink. An alarm nobody has heard is exactly the class of
    defect this control exists to end (Law 12), so it gets a repeatable drill
    rather than a one-time poke somebody remembers doing.

    WHAT IS REAL AND WHAT IS SYNTHETIC. The seat, the export, the seam pull, the
    pin check, the alert write and the sink are all real. The only fabricated
    input is the pinned head: :data:`REHEARSAL_HEAD` replaces whatever D1 holds,
    in memory, for this call only. ``audit_head_history`` is never written.

    THREE THINGS IT MUST NOT DO, each enforced here rather than remembered:

    * **Never overwrite a real alert.** The row goes in under
      :data:`REHEARSAL_DRIVER_PREFIX`, so the (entity_id, alert_date, driver)
      upsert cannot land on a genuine finding for the same seat the same day.
    * **Never write to the archive.** The ``audit/`` prefix is object-locked for
      seven years; a drill has no business writing there. This path does not
      archive at all.
    * **Never leave its own alarm standing.** The row is cleared before the
      function returns, and the clear is reported. A drill that leaves a fake
      fire on the dashboard has replaced one problem with another.

    Returns (exit code, report lines). A rehearsal that does NOT produce a
    finding is a FAILURE: it means the detector no longer detects.
    """
    lines: list[str] = [f"REHEARSAL  {slug}: synthetic head mismatch (ss#2500)"]

    client = seam_client_from_env(slug)
    if client is None:
        lines.append(f"  FAILED  the runtime-read seam is not configured for {slug}.")
        return EXIT_REHEARSAL_FAILED, lines
    try:
        rows = client.read_all("audit_export")
    except Exception as exc:  # noqa: BLE001 - any transport failure pulling the export is the rehearsal's FAILED line, reported not raised
        lines.append(f"  FAILED  the audit export could not be pulled ({exc}).")
        return EXIT_REHEARSAL_FAILED, lines

    real_pin = None
    try:
        real_pin = console.newest_pin(slug)
    except Exception:  # noqa: BLE001 -- the real pin is context, not the input
        pass

    synthetic = {
        "audit_head": REHEARSAL_HEAD,
        "first_seen_heartbeat_ts": None,
        "last_seen_heartbeat_ts": None,
    }
    outcome = evaluate_export(slug, rows, synthetic)
    lines.append(f"  export  {len(rows)} rows, real head {(outcome.details.get('head') or '')[:12]}")
    lines.append(f"  real pin {(real_pin or {}).get('audit_head', '(none)')[:12]} (untouched)")
    lines.append(f"  verdict {outcome.state}: {outcome.headline}")

    if not outcome.is_finding:
        # The drill's own falsifier. A pinned head that is not in the export MUST
        # be a finding; anything else means the detector stopped detecting and
        # the daily clean runs have been meaningless.
        lines.append(
            "  FAILED  a head that is not in the export did not produce a finding. "
            "The detector is broken, and every clean run since is unproven."
        )
        return EXIT_REHEARSAL_FAILED, lines

    outcome.details["rehearsal"] = True
    outcome.details["rehearsal_note"] = (
        "SYNTHETIC. ss#2500 alarm drill: the pinned head was replaced in memory to "
        "force a finding. This seat's real ledger was not touched and its real pin "
        "was not changed."
    )
    outcome.headline = f"[REHEARSAL — not a real finding] {outcome.headline}"

    problem = emit_alert(console, outcome, driver_prefix=REHEARSAL_DRIVER_PREFIX)
    if problem:
        lines.append(f"  FAILED  the alert row could not be written: {problem}")
        return EXIT_REHEARSAL_FAILED, lines
    lines.append(f"  alert   written under driver {REHEARSAL_DRIVER_PREFIX}{slug} — the alarm fired")

    try:
        console.clear_rehearsal_alerts(slug=slug)
    except Exception as exc:  # noqa: BLE001 - the alert was written; a failed clear is reported as FAILED so a rehearsal row is never left in D1 silently
        lines.append(
            f"  FAILED  the rehearsal alert was written but could NOT be cleared ({exc}). "
            f"Delete it by hand: driver = '{REHEARSAL_DRIVER_PREFIX}{slug}'."
        )
        return EXIT_REHEARSAL_FAILED, lines
    lines.append("  cleared the rehearsal row; the dashboard is back to real findings only")
    return EXIT_REHEARSAL_OK, lines
