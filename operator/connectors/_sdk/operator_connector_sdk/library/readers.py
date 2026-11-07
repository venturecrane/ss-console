"""Who a document is written for, and which existing profile carries that reader
(design 2026-11-06, piece 7: "reader as an input").

THE READER OF A KIND is the document-kind registry's (``operator/contracts/
document-kinds.yaml`` ``reader`` and ``variant_readers``), copied here and pinned
to it by ``tests/document-kinds-registry.test.ts``: an adjuster for a pre-suit
demand, defense counsel for a litigation demand, the mediator for a mediation
brief, opposing counsel for discovery, the firm's own attorney for a memo or a
deposition outline, and for a letter whoever it is addressed to
(:data:`ADDRESSEE`, decided by the caller from the matter's contact roles, or
unknown).

THE PROFILE OF A READER is one that ALREADY exists. None is made here, and a
reader with no profile of its own is carried by the firm's base way of writing,
said as such (:data:`FIRM_BASE`). Where each lives today, per path:

* the demand job: the adjuster profile is the demand inputs' voice files
  (``demand-firm.yaml`` ``inputs``: ``voice_profile``, ``voice_fixed_strings``,
  ``voice_adjustments``), which the demand composer sends. It is the demand
  lane's only voice, so a litigation demand (defense counsel) is carried by it
  as that lane's base;
* the drafting job: the court register (a mediation brief; served discovery,
  "the plain register the rules call for") and the internal register (a memo, a
  deposition outline) are written in each class's own compose prompt in the
  drafting inputs (``prompts/compose-<class>.md``); the drafting lane's base is
  its ``style`` file;
* the chat turn's document tool: the installed ``work_product`` voice spec
  (``specs/classes/work_product/``) is the only voice it reads, so every reader
  is carried by that base.

This module decides and names; it sends nothing. :func:`record` is what each
path writes beside its document (``caller.build_record`` ``reader``).

STANDARD LIBRARY ONLY, no case-system import.
"""

from __future__ import annotations

from typing import Any

#: The readers a document or a rule may name (``wording_rules_store.READERS``,
#: pinned equal by ``tests/document-kinds-registry.test.ts``).
READERS: tuple[str, ...] = (
    "client",
    "adjuster",
    "opposing_counsel",
    "mediator",
    "vendor",
    "internal",
    "court",
)
#: A letter's reader is its addressee's, decided from the matter's roles.
ADDRESSEE = "addressee"

#: Each kind's reader, the registry's ``reader``.
KIND_READERS: dict[str, str] = {
    "discovery_set": "opposing_counsel",
    "discovery_response": "opposing_counsel",
    "demand_letter": "adjuster",
    "mediation_brief": "mediator",
    "memo": "internal",
    "depo_outline": "internal",
    "letter": "addressee",
}
#: A variant whose reader is not its kind's, the registry's ``variant_readers``.
VARIANT_READERS: dict[str, dict[str, str]] = {
    "demand_letter": {"litigation": "opposing_counsel"},
}

DEMAND_JOB = "demand_job"
DRAFTING_JOB = "drafting_job"
CHAT_TOOL = "chat_tool"

#: The profile names a record may carry.
ADJUSTER = "adjuster"
COURT = "court"
INTERNAL = "internal"
FIRM_BASE = "firm_base"
PROFILES = (ADJUSTER, COURT, INTERNAL, FIRM_BASE)

_DEMAND_VOICE = "the demand inputs' voice files (voice_profile, voice_fixed_strings, voice_adjustments)"
_CLASS_PROMPT = "the drafting inputs' compose prompt for the class (prompts/compose-{kind})"
_INSTALLED = "the installed work_product voice spec (specs/classes/work_product)"

#: (path, kind, reader) -> (profile, where it lives). Only the profiles that exist.
_HAS: dict[tuple[str, str, str], tuple[str, str]] = {
    (DEMAND_JOB, "demand_letter", "adjuster"): (ADJUSTER, _DEMAND_VOICE),
    (DRAFTING_JOB, "mediation_brief", "mediator"): (COURT, _CLASS_PROMPT),
    (DRAFTING_JOB, "discovery_set", "opposing_counsel"): (COURT, _CLASS_PROMPT),
    (DRAFTING_JOB, "discovery_response", "opposing_counsel"): (COURT, _CLASS_PROMPT),
    (DRAFTING_JOB, "memo", "internal"): (INTERNAL, _CLASS_PROMPT),
    (DRAFTING_JOB, "depo_outline", "internal"): (INTERNAL, _CLASS_PROMPT),
}
#: Each path's base: what carries a reader with no profile of its own.
_BASE: dict[str, str] = {
    DEMAND_JOB: _DEMAND_VOICE + ", the demand lane's only voice",
    DRAFTING_JOB: "the drafting inputs' style file",
    CHAT_TOOL: _INSTALLED,
}


def reader_for(kind: str, variant: str | None = None) -> str | None:
    """The reader of a document of ``kind`` (and ``variant``), per the registry.
    :data:`ADDRESSEE` for a letter (the caller decides it); None for an unknown kind."""
    by_variant = VARIANT_READERS.get(kind, {})
    if variant is not None and variant in by_variant:
        return by_variant[variant]
    return KIND_READERS.get(kind)


def record(path: str, kind: str, variant: str | None = None, addressee: str | None = None) -> dict[str, Any]:
    """What a document's record says about its reader and the profile in effect.

    ``reader`` is None when it cannot be named (a letter whose addressee's role
    is unknown); ``reader_from`` says how it was decided; ``profile`` and
    ``profile_lives_in`` name the profile that carried it, the path's base when
    the reader has none of its own."""
    if path not in _BASE:
        raise ValueError(f"unknown path {path!r}")
    reader = reader_for(kind, variant)
    reader_from = "variant" if variant in VARIANT_READERS.get(kind, {}) else "registry"
    if reader == ADDRESSEE:
        reader, reader_from = (addressee, "addressee_role") if addressee in READERS else (None, "addressee_unknown")
    found = _HAS.get((path, kind, reader or ""))
    profile, lives_in = found if found else (FIRM_BASE, _BASE[path])
    return {
        "reader": reader,
        "reader_from": reader_from,
        "profile": profile,
        "profile_lives_in": lives_in.format(kind=kind.replace("_", "-") + ".md"),
    }


_RECORD_KEYS = frozenset({"reader", "reader_from", "profile", "profile_lives_in"})
_FROM = frozenset({"registry", "variant", "addressee_role", "addressee_unknown"})


def record_problems(doc: Any) -> list[str]:
    """Every reason ``doc`` is not a reader record."""
    if not isinstance(doc, dict) or set(doc) != _RECORD_KEYS:
        return [f"a reader record's keys are exactly {sorted(_RECORD_KEYS)}"]
    out = []
    if doc["reader"] is not None and doc["reader"] not in READERS:
        out.append(f"reader {doc['reader']!r}")
    if doc["reader_from"] not in _FROM:
        out.append(f"reader_from {doc['reader_from']!r}")
    if doc["profile"] not in PROFILES:
        out.append(f"profile {doc['profile']!r}")
    if not isinstance(doc["profile_lives_in"], str) or not doc["profile_lives_in"]:
        out.append("profile_lives_in")
    return out


__all__ = [
    "ADDRESSEE",
    "ADJUSTER",
    "CHAT_TOOL",
    "COURT",
    "DEMAND_JOB",
    "DRAFTING_JOB",
    "FIRM_BASE",
    "INTERNAL",
    "KIND_READERS",
    "PROFILES",
    "READERS",
    "VARIANT_READERS",
    "reader_for",
    "record",
    "record_problems",
]
