"""Cross-run send invariants: recipient sets and ACK stability, two tiers.

Split out of ``send_verify.py`` when the proposal tier landed (module-size
ceiling); ``send_verify`` re-exports everything, so callers and tests read
unchanged. Stdlib only, imported by ``send_verify`` -- never the other way, so
there is no cycle.

TWO TIERS, and the difference is the whole design (PR #2651 review, finding 2):

* PROPOSAL -- a value observed where the committed expectation file
  (``operator/bin/send-invariants.json``) has NOTHING for it. First-seen values
  are REPORTED as proposals for a reviewed PR into the committed file, exactly
  the reconcile-sends baseline discipline. Never a finding, never exit 1: the
  day the render cluster's stamps land on live rows, every legitimate
  recipient and ACK code shows up here at once, and a control that pages on
  the fleet's first honest day is muted by its second.
* FINDING -- an observed value that CONFLICTS with a committed expectation:
  a dispatch recipient outside a routine's NON-EMPTY committed set (recipient
  flapping), or an item_key whose observed ack_code differs from the committed
  one (ACK instability). Committed means a human reviewed and merged it, so a
  conflict is a real deviation, not a cold-start artifact.

Everything either tier emits is hashed (sha256 of routine|value) or an opaque
ACK token; both repos are public and the committed file must not widen the
address surface the baseline already carries.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional

_OPERATOR_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INVARIANTS_PATH = os.path.join(_OPERATOR_DIR, "bin", "send-invariants.json")


@dataclass
class InvariantFinding:
    """One conflict with a COMMITTED expectation. Hashed keys only -- the
    committed expectation file is public, and so is anything this renders."""

    rule: str  # recipient_set | ack_stability
    skill_name: str
    hashed_key: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class InvariantProposal:
    """One first-seen value with no committed expectation: a row for a human
    to review into operator/bin/send-invariants.json. Never a finding."""

    rule: str  # recipient_set | ack_stability
    skill_name: str
    hashed_key: str
    value: Optional[str] = None  # the ack_code for ack rows; None for recipients


def load_invariants(path: str = DEFAULT_INVARIANTS_PATH) -> dict:
    """Committed expectations. Missing/corrupt => EMPTY, on purpose: an empty
    expectation set proposes everything it sees and asserts nothing, so the
    failure mode is a longer proposal list, never a silently satisfied
    invariant -- the load_baseline discipline."""
    try:
        with open(path, encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return {"recipients": {}, "ack_codes": {}}
    if not isinstance(parsed, dict):
        return {"recipients": {}, "ack_codes": {}}
    recipients = parsed.get("recipients")
    ack_codes = parsed.get("ack_codes")
    return {
        "recipients": recipients if isinstance(recipients, dict) else {},
        "ack_codes": ack_codes if isinstance(ack_codes, dict) else {},
    }


def _recipient_hash(routine: str, recipient: str) -> str:
    return hashlib.sha256(f"{routine}|{recipient.lower()}".encode("utf-8")).hexdigest()


def _item_hash(routine: str, item_key: str) -> str:
    return hashlib.sha256(f"{routine}|{item_key}".encode("utf-8")).hexdigest()


def _metadata(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


#: Metadata keys a dispatch row may carry its recipient(s) under. Probed
#: against the render cluster's transmit-metadata additions as they land; a
#: row carrying none of these is SKIPPED (counted, not graded) rather than
#: guessed at -- vendor/emitter shapes are probed, never assumed.
_RECIPIENT_KEYS = ("recipient", "recipients", "to", "routing_leg_recipient")


def _row_recipients(meta: dict) -> list[str]:
    for key in _RECIPIENT_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list):
            found = [v for v in value if isinstance(v, str) and v]
            if found:
                return found
    return []


def recipient_invariant(
    rows: list[dict], declares: dict, expectations: dict
) -> tuple[list[InvariantFinding], list[InvariantProposal]]:
    """Recipient-set grading for compositional routines.

    A routine with an EMPTY committed set is in cold start: every recipient
    hash observed is a PROPOSAL. A routine with a NON-EMPTY committed set has a
    reviewed allowlist, and a dispatch outside it is the recipient-flapping
    FINDING. Addresses never leave the process; hashes compare.
    """
    committed = expectations.get("recipients") or {}
    findings: list[InvariantFinding] = []
    proposals: list[InvariantProposal] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("action_type") != "CONFIRM_SEND_DISPATCHED":
            continue
        skill = str(row.get("skill_name") or "")
        decl = declares.get(skill)
        if decl is None or decl.hash_verified:
            continue
        meta = _metadata(row)
        if meta.get("outcome") != "sent":
            continue
        allowed = {str(v) for v in (committed.get(skill) or [])}
        for recipient in _row_recipients(meta):
            hashed = _recipient_hash(skill, recipient)
            if hashed in allowed or hashed in seen:
                continue
            seen.add(hashed)
            if not allowed:
                proposals.append(InvariantProposal(rule="recipient_set", skill_name=skill, hashed_key=hashed))
                continue
            findings.append(
                InvariantFinding(
                    rule="recipient_set",
                    skill_name=skill,
                    hashed_key=hashed,
                    detail="dispatch recipient hash outside the committed set (operator/bin/send-invariants.json)",
                )
            )
    return findings, proposals


def ack_invariant(
    wakes: list, declares: dict, expectations: dict
) -> tuple[list[InvariantFinding], list[InvariantProposal]]:
    """ACK stability: the same item_key must keep its committed ack_code.

    An item_key with NO committed code proposes every distinct code it was
    observed with (two proposed codes for one key is visible instability, put
    in front of the reviewer of the invariants PR rather than paged on). An
    item_key WITH a committed code that is observed under a different one is
    the unstable-ACK FINDING from the review week.
    """
    committed = expectations.get("ack_codes") or {}
    findings: list[InvariantFinding] = []
    proposals: list[InvariantProposal] = []
    proposed: set[tuple[str, str]] = set()
    for wake in wakes:
        if declares.get(wake.skill_name) is None:
            continue
        for item in wake.items:
            key = _item_hash(wake.skill_name, str(item["item_key"]))
            code = str(item["ack_code"])
            expected = committed.get(key)
            if expected is None:
                if (key, code) not in proposed:
                    proposed.add((key, code))
                    proposals.append(
                        InvariantProposal(
                            rule="ack_stability",
                            skill_name=wake.skill_name,
                            hashed_key=key,
                            value=code,
                        )
                    )
                continue
            if expected != code:
                findings.append(
                    InvariantFinding(
                        rule="ack_stability",
                        skill_name=wake.skill_name,
                        hashed_key=key,
                        expected=expected,
                        actual=code,
                        detail="item_key maps to a different ack_code than committed",
                    )
                )
    return findings, proposals


__all__ = [
    "DEFAULT_INVARIANTS_PATH",
    "InvariantFinding",
    "InvariantProposal",
    "ack_invariant",
    "load_invariants",
    "recipient_invariant",
]
