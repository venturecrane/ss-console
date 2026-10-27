"""The California attorney-advertising gate at the outbound seam (ADR 0098).

A law-firm marketing seat drafts public content (blog posts, social posts,
Business Profile posts, review replies, press releases) and emails it to the
firm for approval. Every one of those drafts is an advertisement under Bus. &
Prof. Code 6157(b) (SB 37, effective 2026-01-01), and the scope the firm
accepted promised a California advertising check on every draft. This module is
that check's exit (a): every send, draft and relayed reply the seat makes is
scanned by the same checker the skills and the WordPress connector run,
``operator/templates/drafting/advertising_gate_check.py``.

INERT BY DEFAULT. It runs only when the seat's role policy sets
``advertising_gate: ca-law-firm`` (``shared/policy_profile``). Every other seat
returns before reading anything.

FAIL CLOSED WHEN ON. The checker is loaded from the image (root-owned, so the
agent cannot rewrite the check it is graded by) and the firm's registers from
the lane input staged at :data:`DEFAULT_REGISTERS`. A checker that cannot load,
registers that are missing or unparseable, or a checker fault each produce a
BLOCK, never a pass: a gate that silently did not run reads exactly like a gate
that passed.

THE DRAFT-BLOCK CONVENTION. One email to the firm usually carries a cover note
and several drafts. The skills wrap each draft on lines of their own::

    --- DRAFT <channel> ---
    ...the draft...
    --- END DRAFT ---

where ``<channel>`` is one of ``blog``, ``social``, ``youtube``, ``gbp``,
``review-reply``, ``press``. Each block is checked with its channel's rules; the
text outside the blocks (the cover note, the subject) with the ``note`` rules
only, the general ones that hold in any text (guarantees, quick cash,
client identifiers, paid reviews, litigation funding, notario, the firm's
blocklist). So a required-disclosure rule never misfires on a cover note,
and a disclosure in the cover never cures a draft. Text with no markers is one
``note`` item.

DISPOSITION. A BLOCK refuses the call exactly as an output-checklist refusal
does, with each finding's rule, quoted span and plain reason, so the model can
correct it. FLAGs are the reviewer's questions: they never refuse, and their
rule ids go to the audit row (rule ids only, never text).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from shared import policy_profile

logger = logging.getLogger(__name__)

#: Where the image carries the checker (Dockerfile ``COPY operator/templates/drafting/``,
#: root-owned by the chown that covers that tree).
DEFAULT_GATE_PATH = "/app/operator/templates/drafting/advertising_gate_check.py"
GATE_PATH_ENV = "SMD_ADVERTISING_GATE_PATH"

#: The firm's authored registers, staged as a root-owned lane input. Defined
#: once, in policy_profile, because their presence is also the marketing mark a
#: faulted policy read falls back on.
DEFAULT_REGISTERS = policy_profile.MARKETING_REGISTERS_DEFAULT
REGISTERS_ENV = policy_profile.MARKETING_REGISTERS_ENV

#: The audit reason and the reply-hold reason.
REASON = "advertising_gate"

DEFAULT_CHANNEL = "note"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    span: str
    message: str


@dataclass(frozen=True)
class Outcome:
    findings: tuple[Finding, ...]

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "BLOCK")

    @property
    def flags(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity != "BLOCK")

    def rule_summary(self) -> str:
        """``R-01:BLOCK,R-17:FLAG`` for the audit row. Ids only, never text."""
        seen: list[str] = []
        for f in self.findings:
            tag = f"{f.rule}:{f.severity}"
            if tag not in seen:
                seen.append(tag)
        return ",".join(seen)

    def describe(self) -> str:
        """Model-facing: each BLOCK with its rule, the quoted fragment and the reason."""
        parts = []
        for f in self.blocking:
            quote = f" ('{f.span}')" if f.span else ""
            parts.append(f"{f.rule}{quote}: {f.message}")
        return " ".join(parts)


_LOAD_LOCK = threading.Lock()
_LOADED: dict[tuple[str, float], ModuleType] = {}


def enabled(policy: policy_profile.Policy | None = None) -> bool:
    """True when this seat's role turns the gate on. False on every other seat."""
    try:
        p = policy if policy is not None else policy_profile.current()
    except Exception:  # noqa: BLE001 - current() never raises; it keeps a faulted marketing seat on
        return False
    return p.advertising_gate is not None


def _gate_path() -> str:
    return os.environ.get(GATE_PATH_ENV) or DEFAULT_GATE_PATH


def registers_path() -> str:
    return policy_profile.marketing_registers_path()


def _load_checker() -> ModuleType:
    path = _gate_path()
    mtime = Path(path).stat().st_mtime
    key = (path, mtime)
    with _LOAD_LOCK:
        module = _LOADED.get(key)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location("smd_advertising_gate_check", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED.clear()
        _LOADED[key] = module
        return module


def _closed(message: str) -> Outcome:
    return Outcome((Finding("CHECKER", "BLOCK", "", message),))


def scan(texts: list[str], channel: str = DEFAULT_CHANNEL) -> Outcome:
    """Check each text with the draft-block convention. Never raises; fails closed."""
    try:
        checker = _load_checker()
    except Exception as exc:  # noqa: BLE001 - no checker is a refusal, not a pass
        logger.error("advertising gate: checker unavailable (%s)", exc)
        return _closed(
            "the California advertising check could not run on this seat, so nothing that "
            "needs it can go out. Say so in your run output; SMD has to restore the check."
        )
    registers: Any
    registers, error = checker.load_registers(registers_path())
    if error:
        logger.error("advertising gate: registers unavailable (%s)", error)
        registers = None
    found: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    try:
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                continue
            for f in checker.check_document(text, channel, registers):
                key = (f.rule, f.severity, f.span)
                if key in seen:
                    continue
                seen.add(key)
                found.append(Finding(f.rule, f.severity, f.span, f.message))
    except Exception as exc:  # noqa: BLE001 - a crashing check must not read as a pass
        logger.error("advertising gate: checker raised (%s)", exc)
        return _closed("the California advertising check failed while reading this message.")
    return Outcome(tuple(found))


__all__ = [
    "DEFAULT_GATE_PATH",
    "DEFAULT_REGISTERS",
    "GATE_PATH_ENV",
    "REASON",
    "REGISTERS_ENV",
    "Finding",
    "Outcome",
    "enabled",
    "registers_path",
    "scan",
]
