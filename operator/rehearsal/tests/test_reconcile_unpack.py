"""The rehearsal driver's reconcile leg unpacks what the reconciler returns.

2026-09-04: the overlay#346 release gate (`run.py --drive` on the rig) crashed
at direct-api-send-bypass with ``ValueError: too many values to unpack
(expected 3)``. ``reconcile-sends.reconcile`` had returned a 4-tuple since
ss#2499 (#2519); ``drivers.unaccounted_sends`` still unpacked three, so the
shadow firm could not have completed that scenario for any bump in between.
This test drives the real reconciler through the driver's own loader so the
two cannot drift apart silently again.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rehearsal import drivers


def test_reconcile_unpack_matches_the_reconciler(monkeypatch) -> None:
    reconciler = drivers._load_bin_module("reconcile_sends_for_rehearsal_test", "reconcile-sends.py")
    sent = [{"message_id": "m-unaccounted", "timestamp": "2026-09-04T14:00:00Z"}]
    # A stub list_sent so the leg never touches AgentMail; reconcile() is real.
    monkeypatch.setattr(reconciler, "list_sent", lambda inbox, key, since=None: sent)
    monkeypatch.setattr(drivers, "_load_bin_module", lambda name, filename: reconciler)
    unmatched = drivers.unaccounted_sends(
        "rig@example.com",
        rows=[],
        since=None,
        key="k",  # type: ignore[arg-type]
    )
    assert unmatched is not None
    assert [m["message_id"] for m in unmatched] == ["m-unaccounted"]
