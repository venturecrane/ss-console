"""Report integration for the body-verify phase: lines, digests, --json.

Split out of ``send_verify.py`` when the pre-edge verdict landed (that module
sat three logical lines under the size ceiling, tests/operator-module-size
.test.ts); ``send_verify`` re-exports everything here, so callers and tests
read unchanged. Imports nothing from ``send_verify`` -- it reads the verdict
objects' own predicates (``is_finding`` / ``is_hold`` / ``is_degraded`` /
``is_pre_edge``) -- so there is no cycle.

LEAK SAFETY IS STRUCTURAL here as it is in the verdicts: every emitter below
walks a FIXED key allowlist or reads named hash/timestamp fields. Nothing in
this module can reach a message body, because no verdict carries one.

WHAT REDDENS AND WHAT DOES NOT. Four verdict classes leave this module:

* a FINDING (``BODY_DIVERGED``, an invariant conflict) -- indented under the
  inbox, joined into the finding fingerprint, files an issue;
* a HOLD -- a ``HOLD`` line in column 0 (the workflow greps ``^HOLD`` and
  reddens the run), filed nowhere: the control could not evaluate this send
  TODAY and someone can still make it evaluable (deploy the pin, fix the
  transport);
* PRE-EDGE -- an indented count, not a ``HOLD`` line: the send was dispatched
  before the inbox's first ``plain_body_sha256`` stamp, the seat now stamps,
  and no action anyone can take will ever make that row gradeable. Reddening
  the run on it daily measured nothing and taught the room to ignore red
  (2026-09-01..09-09: eleven consecutive red scheduled runs on ONE such row,
  seventeen unresolved critical notifications, nobody acting -- the exact
  "control that pages on its own blips gets muted" the workflow header warns
  about). Reported so the number is visible; never a signal;
* PROPOSAL -- paste-ready rows for a reviewed send-invariants.json PR;
* CHANNEL-UNSTAMPED -- an indented count (ss#3030): the mailbox stores a body
  representation no stamp hashes (msgraph keeps only the html part), so the
  channel check has no counterpart; the primary hash join still grades it;
* DISPOSITIONED -- a finding a reviewed ``verify_dispositioned`` entry names
  (lib/send_dispositions.py): printed indented with its issue, out of the
  finding set and the fingerprint.
"""

from __future__ import annotations

from typing import Optional

from .send_attribution import attribution_counts


def _live(invariants: list) -> list:
    """Invariant conflicts no reviewed disposition has quieted (send_dispositions)."""
    return [f for f in invariants if getattr(f, "dispositioned_in", None) is None]


def has_findings(verdicts: list, invariants: list) -> bool:
    return any(v.is_finding for v in verdicts) or bool(_live(invariants))


def has_holds(verdicts: list) -> bool:
    return any(v.is_hold for v in verdicts)


def render_lines(
    inbox: str,
    verdicts: list,
    invariants: list,
    proposals: Optional[list] = None,
) -> list[str]:
    """Report lines for one inbox. HOLD lines start in column 0 with `HOLD`
    (the workflow greps ``^HOLD`` and reddens the run on them); finding,
    degraded and pre-edge lines are indented under the inbox like the
    reconciler's own; PROPOSAL lines are the human's paste-ready rows for a
    send-invariants.json PR and never redden anything."""
    lines: list[str] = []
    for verdict in verdicts:
        if verdict.is_finding:
            lines.append(
                f"        BODY_DIVERGED {verdict.skill_name} "
                f"dispatch={verdict.dispatch_ts or '-'} "
                f"expected={verdict.expected_sha256 or '-'} actual={verdict.actual_sha256 or '-'}"
            )
        elif verdict.is_degraded:
            lines.append(
                f"        degraded {verdict.skill_name} "
                f"dispatch={verdict.dispatch_ts or '-'} ({verdict.detail or 'skeleton fallback delivered'})"
            )
    for finding in _live(invariants):
        lines.append(
            f"        INVARIANT {finding.rule} {finding.skill_name} key={finding.hashed_key} "
            f"expected={finding.expected or '-'} actual={finding.actual or '-'}"
        )
    for proposal in proposals or []:
        if proposal.rule == "recipient_set":
            lines.append(
                f"        PROPOSAL recipient_set {proposal.skill_name}: add "
                f'"{proposal.hashed_key}" to recipients["{proposal.skill_name}"] '
                "in operator/bin/send-invariants.json (reviewed PR)"
            )
        else:
            lines.append(
                f"        PROPOSAL ack_stability {proposal.skill_name}: add "
                f'ack_codes["{proposal.hashed_key}"] = "{proposal.value}" '
                "in operator/bin/send-invariants.json (reviewed PR)"
            )
    pre_edge = _count_by_skill(v for v in verdicts if v.is_pre_edge)
    for skill, count in sorted(pre_edge.items()):
        # Indented, not `HOLD`: unverifiable by construction (see module
        # docstring), so the workflow's ^HOLD grep must not see it.
        lines.append(
            f"        pre-edge {count} send(s) unverifiable by construction [{skill}] "
            "(dispatched before the inbox's first plain_body_sha256 stamp)"
        )
    unstamped = _count_by_skill(v for v in verdicts if getattr(v, "is_unstamped_repr", False))
    for skill, count in sorted(unstamped.items()):
        # Indented, not `HOLD` (ss#3030): the mailbox stores a representation no
        # stamp hashes (msgraph keeps only the html part), so no comparison exists.
        lines.append(
            f"        channel-unstamped {count} send(s) [{skill}] (mailbox stores a body representation "
            "no stamp hashes; primary hash join still grades these)"
        )
    quieted = [
        f"{v.skill_name} dispatch={v.dispatch_ts or v.wake_ts} (#{v.dispositioned_in})"
        for v in verdicts
        if getattr(v, "is_dispositioned", False)
    ] + [
        f"{f.rule} {f.skill_name} key={f.hashed_key} (#{f.dispositioned_in})"
        for f in invariants
        if getattr(f, "dispositioned_in", None) is not None
    ]
    for item in quieted:
        lines.append(f"        dispositioned {item} (reconcile-sends-baseline.json verify_dispositioned)")
    holds = _count_by_skill((v for v in verdicts if v.is_hold), with_verdict=True)
    for reason, count in sorted(holds.items()):
        lines.append(f"HOLD  {inbox}: body-verify {count} send(s) {reason}")
    # The two attribution metrics, printed whenever anything was paired: a seat
    # whose column stopped being written shows up as by_hash climbing and
    # by_skill falling, which is a number moving rather than silence.
    counts = attribution_counts(verdicts)
    if any(counts.values()):
        lines.append(
            f"        attributed_by_skill={counts['attributed_by_skill']} attributed_by_hash={counts['attributed_by_hash']}"
        )
    return lines


def _count_by_skill(verdicts, *, with_verdict: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in verdicts:
        key = f"{verdict.verdict} [{verdict.skill_name}]" if with_verdict else verdict.skill_name
        counts[key] = counts.get(key, 0) + 1
    return counts


def digest_keys(inbox: str, verdicts: list, invariants: list) -> list[str]:
    """Stable keys for the reconciler's finding fingerprint, so the existing
    issue-dedupe machinery covers the new classes with zero workflow changes."""
    keys = [
        f"{inbox}|body:{v.skill_name}|{v.dispatch_ts or v.wake_ts}|{v.actual_sha256}" for v in verdicts if v.is_finding
    ]
    keys += [f"{inbox}|inv:{f.rule}|{f.hashed_key}|{f.actual or ''}" for f in _live(invariants)]
    return keys


#: The COMPLETE emission surfaces for --json. Fixed key allowlists on purpose
#: (never dataclasses.asdict of something that might grow a field): what is
#: listed here is ALL that can ever leave the process, so a regressed verdict
#: that grew a body field still emits nothing new.
_VERDICT_EMIT_KEYS = (
    "skill_name",
    "verdict",
    "wake_ts",
    "dispatch_ts",
    "message_id",
    "expected_sha256",
    "actual_sha256",
    "detail",
    "attribution",
    "dispositioned_in",
)
_INVARIANT_EMIT_KEYS = (
    "rule",
    "skill_name",
    "hashed_key",
    "expected",
    "actual",
    "detail",
    "dispositioned_in",
)
_PROPOSAL_EMIT_KEYS = ("rule", "skill_name", "hashed_key", "value")


def as_dicts(
    verdicts: list,
    invariants: list,
    proposals: Optional[list] = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """--json emission through the fixed allowlists above."""
    return (
        [{key: getattr(v, key) for key in _VERDICT_EMIT_KEYS} for v in verdicts],
        [{key: getattr(f, key) for key in _INVARIANT_EMIT_KEYS} for f in invariants],
        [{key: getattr(p, key) for key in _PROPOSAL_EMIT_KEYS} for p in proposals or []],
    )


__all__ = [
    "as_dicts",
    "digest_keys",
    "has_findings",
    "has_holds",
    "render_lines",
]
