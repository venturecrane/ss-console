"""How far a proposed firm rule reaches, decided in code from the requester's own words.

WHY (2026-11-05, the drafting answer key rows 60 and 131). An attorney gave a tone
rule for persuasive briefs; the Operator stored it for every document the firm
drafts and read it back that way, and his "yes" answered the wider sentence. On
another thread his "every mediation brief" became a rule for every document. The
model chose the reach both times, and nothing checked it against what he said.

THE RULE, IN CODE. A firm rule about the firm's documents (``work_product``)
names the kinds of document it covers, and optionally the attorney whose
documents it covers. The model proposes both; they stand only when:

* every proposed kind is NAMED in the requester's own message
  (:func:`kinds_named`, matched against a fixed list of kind names), or the
  message says the rule is for all of the firm's documents; and
* a proposed attorney is the requester, or is named in the requester's own
  message.

The kinds of a request are the kinds the requester's own words name. Code has no
other record of what a chat turn's request was about, so a rule stated without
naming a kind is not placed: the model is told to ask which documents it is for.

THE SWITCH. With the seat's ``logic_in_code.wording_rules`` on, a proposal that
reaches further is refused with the reach the words allow, so the model proposes
again, and the read-back states the reach (:func:`reach_sentence`). Off, nothing
is refused and the read-back is as it was; the reach recorded is cut to what the
words allow (:func:`narrowed`), never widened, so a seat switched on later finds
only reaches the firm said.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from shared import layout_rules, wording_rules_store

logger = logging.getLogger(__name__)

#: The class whose rules carry a document reach.
RULE_CLASS = wording_rules_store.RULE_CLASS

#: Each kind's names, matched against the requester's words lowered with quotes
#: and apostrophes dropped (:func:`shared.layout_rules.normalize`).
_KIND_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mediation_brief", re.compile(r"\b(?:mediation |persuasive )?briefs?\b")),
    (
        "discovery_response",
        re.compile(
            r"\bdiscovery responses?\b|\bresponses to (?:discovery|interrogatories|requests)\b"
        ),
    ),
    (
        "discovery_set",
        re.compile(
            r"\bdiscovery (?:sets?|requests?)\b|\binterrogatories\b|\bsets? of discovery\b"
            r"|\brequests? for (?:production|admissions?)\b"
        ),
    ),
    ("memo", re.compile(r"\bmemos?\b|\bmemoranda\b|\bmemorandums?\b")),
    ("depo_outline", re.compile(r"\bdepo(?:sition)? outlines?\b")),
    ("demand_letter", re.compile(r"\bdemand letters?\b|\bdemands\b")),
    ("letter", re.compile(r"(?<!demand )\bletters?\b")),
)

#: Each reader's names, matched the same way. A reader is recorded only when the
#: requester's words name one; absent, a rule covers every reader.
_READER_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("client", re.compile(r"\b(?:our |the )?clients?\b")),
    ("adjuster", re.compile(r"\badjusters?\b")),
    ("opposing_counsel", re.compile(r"\b(?:opposing|defense|other side s) counsel\b")),
    ("mediator", re.compile(r"\bmediators?\b")),
    ("vendor", re.compile(r"\bvendors?\b")),
    ("internal", re.compile(r"\binternal\b|\bin[- ]house\b|\bour (?:own )?staff\b")),
    ("court", re.compile(r"\b(?:the )?court\b|\bjudges?\b")),
)

#: The requester saying a rule is for every document the firm drafts.
_ALL_KINDS = re.compile(
    r"\b(?:all|every|each|any)(?: of)?(?: (?:our|the|your|my|firm s|the firm s))?"
    r" (?:documents?|drafts?|work product)\b"
)


@dataclass(frozen=True)
class Reach:
    """The verdict on one proposal. ``refusal`` is ``""`` when it stands."""

    kinds: tuple[str, ...]
    attorney: str | None
    refusal: str = ""
    reader: str | None = None


def kinds_named(text: Any) -> tuple[str, ...]:
    """The kinds the words name, in the registry's order; every kind for "all"."""
    words = layout_rules.normalize(str(text or ""))
    if _ALL_KINDS.search(words):
        return wording_rules_store.KINDS
    named = {kind for kind, pattern in _KIND_NAMES if pattern.search(words)}
    return tuple(k for k in wording_rules_store.KINDS if k in named)


def _said_by(name: str, own_text: str, requester_name: str | None) -> bool:
    """True iff the requester is ``name`` or wrote ``name`` (or one of its words)."""
    key = wording_rules_store.name_key(name)
    if requester_name and key == wording_rules_store.name_key(requester_name):
        return True
    words = layout_rules.normalize(own_text)
    parts = [p for p in layout_rules.normalize(name).split() if len(p) > 1]
    return bool(parts) and any(re.search(rf"\b{re.escape(p)}\b", words) for p in parts)


def readers_named(text: Any) -> tuple[str, ...]:
    """The readers the words name, in the registry's order."""
    words = layout_rules.normalize(str(text or ""))
    named = {reader for reader, pattern in _READER_NAMES if pattern.search(words)}
    return tuple(r for r in wording_rules_store.READERS if r in named)


def _proposed(subject: dict[str, Any]) -> tuple[list[str], str | None, str | None]:
    kinds = subject.get("doc_kinds")
    kinds = [k for k in kinds if isinstance(k, str)] if isinstance(kinds, list) else []
    attorney = subject.get("attorney")
    attorney = (
        " ".join(attorney.split()) if isinstance(attorney, str) and attorney.strip() else None
    )
    reader = subject.get("reader")
    reader = reader.strip() if isinstance(reader, str) and reader.strip() else None
    return kinds, attorney, reader


def _kinds_refusal(kinds: list[str], allowed: tuple[str, ...]) -> str:
    said = wording_rules_store.kinds_said(allowed)
    if not kinds:
        if allowed:
            why = f"Their own message names {said}: set subject.doc_kinds to {list(allowed)}."
        else:
            why = (
                "Their own message names no kind of document, so ask them which documents "
                "the rule is for before proposing it."
            )
        return f"Refused: a rule about the firm's documents names the kinds it covers. {why}"
    unknown = [k for k in kinds if k not in wording_rules_store.KINDS]
    if unknown:
        return f"Refused: {unknown} is not a kind of document. Kinds: {list(wording_rules_store.KINDS)}."
    if [k for k in kinds if k not in allowed]:
        named = f"names only {said}" if allowed else "names no kind of document"
        return (
            f"Refused: their own message {named}, so the rule covers only that. Propose it for "
            f"{list(allowed) or 'the kinds they name once you have asked'}; it reaches further only "
            "if they say so in their own words."
        )
    return ""


def check(subject: dict[str, Any], own_text: str, requester_name: str | None) -> Reach:
    """Does the proposed reach stand on the requester's own words?"""
    kinds, attorney, reader = _proposed(subject)
    refusal = _kinds_refusal(kinds, kinds_named(own_text))
    if refusal:
        return Reach((), None, refusal)
    if attorney and not _said_by(attorney, own_text, requester_name):
        return Reach(
            (),
            None,
            f"Refused: their own message does not name {attorney}, so the rule cannot be limited to "
            "that attorney's documents. Leave subject.attorney out, or ask them.",
        )
    if reader and reader not in readers_named(own_text):
        return Reach(
            (),
            None,
            f"Refused: their own message does not say the rule is for documents written to {reader}. "
            "Leave subject.reader out: a rule with no reader covers every reader.",
        )
    return Reach(tuple(kinds), attorney, reader=reader)


def narrowed(subject: dict[str, Any], own_text: str, requester_name: str | None) -> dict[str, Any]:
    """The reach to record when the seat is not refusing: what the words allow, or none.

    Kinds are cut to the ones named. An attorney or a reader the words do not
    name drops the WHOLE reach rather than itself, because dropping only that
    field would widen the rule from one attorney's (or one reader's) documents to
    everyone's.
    """
    kinds, attorney, reader = _proposed(subject)
    kept = [k for k in kinds if k in kinds_named(own_text)]
    if not kept:
        return {}
    if attorney is not None and not _said_by(attorney, own_text, requester_name):
        return {}
    if reader is not None and reader not in readers_named(own_text):
        return {}
    out: dict[str, Any] = {"doc_kinds": kept}
    if attorney is not None:
        out["attorney"] = attorney
    if reader is not None:
        out["reader"] = reader
    return out


def reach_sentence(kinds: Any, attorney: Any, reader: Any = None) -> str:
    """The read-back's statement of reach, rendered by code: exactly what is recorded."""
    kinds = [k for k in wording_rules_store.KINDS if k in (kinds or ())]
    if not kinds:
        return ""
    whose = f"in {attorney}'s name" if attorney else "for any attorney"
    words = wording_rules_store.READER_WORDS.get(str(reader or ""))
    to = f"written to {words}" if words else "for any reader"
    return (
        f"(This rule covers {wording_rules_store.kinds_said(kinds)}, {whose}, {to}, "
        "and nothing else.)"
    )


# ---- the establishment plugin's three call sites --------------------------------------
def _applies(args: dict[str, Any]) -> bool:
    subject = args.get("subject")
    return (
        args.get("scope") == "firm_adjust"
        and isinstance(subject, dict)
        and str(subject.get("output_class") or "").strip().lower() == RULE_CLASS
    )


def _requester_name(entry: dict[str, Any] | None, cfg: Any) -> str | None:
    sender = (entry or {}).get("sender")
    try:
        return cfg.authored_person_name(sender) if cfg is not None and sender else None
    except Exception:  # noqa: BLE001 - an unreadable roster names nobody; the words still can
        return None


def _own(entry: dict[str, Any] | None) -> str:
    own = (entry or {}).get("own_text")
    return own if isinstance(own, str) else ""


def gate(args: dict[str, Any], entry: dict[str, Any] | None, cfg: Any, on: bool) -> str | None:
    """The refusal for ``establish_propose`` when the seat is on and the reach
    exceeds the words, else ``None``. Off, a reach that would be refused is
    logged and the proposal goes on, cut by :func:`subject_for_broker`."""
    if not _applies(args):
        return None
    verdict = check(args["subject"], _own(entry), _requester_name(entry, cfg))
    if not verdict.refusal:
        return None
    if on:
        return verdict.refusal
    logger.info("rule_reach: observe; a proposal's reach exceeds the requester's words")
    return None


def subject_for_broker(
    args: dict[str, Any], entry: dict[str, Any] | None, cfg: Any, on: bool
) -> Any:
    """The ``subject`` the broker records. A reach only on a firm ``work_product``
    rule, and then exactly what :func:`gate` let through (on) or what the words
    allow (off); every other subject without the three fields."""
    subject = args.get("subject")
    if not isinstance(subject, dict):
        return subject
    base = {k: v for k, v in subject.items() if k not in ("doc_kinds", "attorney", "reader")}
    if not _applies(args):
        return base
    if on:
        kinds, attorney, reader = _proposed(subject)
        reach: dict[str, Any] = {}
        if kinds:
            reach = {"doc_kinds": kinds}
            reach.update({"attorney": attorney} if attorney else {})
            reach.update({"reader": reader} if reader else {})
    else:
        reach = narrowed(subject, _own(entry), _requester_name(entry, cfg))
    return {**base, **reach}


def readback_with_reach(readback: str, subject: Any, on: bool) -> str:
    """The broker's readback, with the recorded reach stated after it when on."""
    if not on or not readback or not isinstance(subject, dict):
        return readback
    sentence = reach_sentence(
        subject.get("doc_kinds"), subject.get("attorney"), subject.get("reader")
    )
    return f"{readback} {sentence}" if sentence else readback


__all__ = [
    "RULE_CLASS",
    "Reach",
    "check",
    "gate",
    "kinds_named",
    "narrowed",
    "reach_sentence",
    "readback_with_reach",
    "readers_named",
    "subject_for_broker",
]
