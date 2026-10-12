"""Reviewed dispositions for body-verify and invariant FINDINGS (ss#3030).

THE GAP THIS CLOSES. ``reconcile-sends-baseline.json`` is the reconciler's
memory for UNACCOUNTED sends: a send named there by message id stops being a
finding. The body-verify phase had no equivalent, so a ``BODY_DIVERGED`` or an
invariant conflict on a send from last month reddened every scheduled run
forever. The scheduled run scans the whole mailbox with no window, and no action
anyone takes can change a historical send, so such a finding is a red that
cannot go green -- a check that cannot fail measured nothing (Law 12), and it
mutes the control for the day a real divergence arrives. Observed: 2026-10-02
onward, every scheduled run red on five 09-30/10-01 statute-watch sends on the
ashton-price inbox (#3018, #3030).

SAME PHILOSOPHY AS THE BASELINE, and the same file. The list lives under
``verify_dispositioned`` in ``operator/bin/reconcile-sends-baseline.json``:

* COMMITTED and PR-updated, so quieting a finding is a reviewed act, never a
  side effect of a scheduled job;
* keyed NARROWLY -- (seat, skill, dispatch timestamp, the ACTUAL hash) for a
  body finding, (seat, rule, hashed key, actual value) for an invariant
  conflict -- so the next divergent send from the same routine, to the same
  recipients, is still a finding: its timestamp and hash differ. ``seat`` is
  the owning seat's slug (the reconciler passes ``report.slug``), so a public
  file never has to name a client mailbox; an inbox no seat owns keys on its
  address under ``inbox`` instead;
* an entry is honoured only when it names the issue it was reviewed in
  (``reported_in``, an int) and says why (``why``, non-empty). An entry missing
  either is IGNORED, never guessed at, so a malformed row over-reports;
* a missing, unreadable or malformed file yields NO dispositions -- the failure
  mode is over-reporting, never silence.

A DISPOSITIONED FINDING IS STILL PRINTED. It leaves the finding set (no issue,
no exit 1, no fingerprint) but is counted indented under its inbox with the
issue that dispositioned it, so the number stays visible.

LEAK SAFETY. Keys are hashes, timestamps and rule names -- the same fields the
verdicts already emit. Nothing here can hold a body.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

#: The key under the baseline file's top-level mapping.
DISPOSITIONS_KEY = "verify_dispositioned"


def _norm_ts(value) -> str:
    """One timestamp spelling for both sides of the join: a hand-pasted ``Z``
    and a ``+00:00`` isoformat name the same instant. Unparseable input is kept
    verbatim, so it can only fail to match (over-report), never widen a match."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return str(value)


def body_key(inbox: str, skill: str, ts, actual: Optional[str]) -> str:
    return f"{inbox}|body:{skill}|{_norm_ts(ts)}|{actual or ''}"


def invariant_key(inbox: str, rule: str, hashed_key: str, actual: Optional[str]) -> str:
    return f"{inbox}|inv:{rule}|{hashed_key}|{actual or ''}"


def _entry_key(entry: dict) -> Optional[str]:
    inbox = entry.get("seat") or entry.get("inbox")
    if not isinstance(inbox, str) or not inbox:
        return None
    if entry.get("kind") == "body":
        fields = [entry.get(k) for k in ("skill_name", "dispatch_ts", "actual_sha256")]
        if all(isinstance(f, str) and f for f in fields):
            return body_key(inbox, *fields)
    elif entry.get("kind") == "invariant":
        fields = [entry.get(k) for k in ("rule", "hashed_key", "actual")]
        if all(isinstance(f, str) and f for f in fields):
            return invariant_key(inbox, *fields)
    return None


def load_dispositions(path: Optional[str]) -> dict[str, int]:
    """key -> the issue number that dispositioned it. EMPTY on any failure."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return {}
    entries = parsed.get(DISPOSITIONS_KEY) if isinstance(parsed, dict) else None
    out: dict[str, int] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        issue, why = entry.get("reported_in"), entry.get("why")
        if not isinstance(issue, int) or isinstance(issue, bool) or not isinstance(why, str) or not why.strip():
            continue  # unreviewed: ignored, so it over-reports
        key = _entry_key(entry)
        if key is not None:
            out[key] = issue
    return out


def apply_dispositions(seat: str, verdicts: list, findings: list, dispositions: dict[str, int]) -> None:
    """Mark (never drop) every finding a reviewed entry names. Only FINDINGS
    can be dispositioned: a hold is interim by definition and must stay red."""
    if not dispositions:
        return
    for verdict in verdicts:
        if not verdict.is_finding:
            continue
        key = body_key(seat, verdict.skill_name, verdict.dispatch_ts or verdict.wake_ts, verdict.actual_sha256)
        verdict.dispositioned_in = dispositions.get(key)
    for finding in findings:
        key = invariant_key(seat, finding.rule, finding.hashed_key, finding.actual)
        finding.dispositioned_in = dispositions.get(key)


__all__ = [
    "DISPOSITIONS_KEY",
    "apply_dispositions",
    "body_key",
    "invariant_key",
    "load_dispositions",
]
