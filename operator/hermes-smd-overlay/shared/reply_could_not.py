"""Did a reply tell the person the Operator could not do what they asked?

WHY THIS EXISTS (2026-11-01, ashton-price). An attorney asked the Operator for
five records attached to a reply. It answered in one minute that it could not
find or attach any of them. Every tool call it made succeeded, so no shortfall
fired, and the request card read "replied in 1m, refused 0, failed 0", which
looks like a clean answer. The Captain learned of it a day later, by asking.

The shortfall classes until then were all read off STRUCTURED rows: a refused
call, a failed call, a held or failed reply, an unproven delivery. A decline the
model writes in words after a run of successful calls has no structured row:
nothing in the reply path records "this answer did not give them what they
asked for". The reply text is the only place that fact exists, so this reads
the text, once, at the moment the reply is handed to the transport, and records
a CLOSED CODE on the ``REPLY_SENT`` audit row (``could_not``). The text itself
never leaves the seat: the code is all the heartbeat, the shortfall ledger
(D1) and the request card's ledger row ever see (ADR 0052).

THE RULE, mechanically. :func:`classify` returns the first match of a closed
pattern set over the reply's own lines (quoted history removed), or ``None``:

* ``cannot_find`` / ``cannot_attach`` / ``cannot_access`` / ``cannot_complete``:
  a FIRST-PERSON inability (``I`` or ``we`` followed by one of
  :data:`_INABILITY`: "could not", "couldn't", "can't", "cannot", "was unable
  to", "wasn't able to", "have not been able to", ...), then at most two filler
  words from :data:`_FILLERS` ("still", "seem to", ...), then a delivery verb
  from :data:`_VERB_CODES`. The verb picks the code.
* ``cannot_access``: also "I don't have access to" and its spellings.
* ``not_permitted``: "I'm not permitted / allowed / authorized to".
* ``none_available``: "none of the/those/these ... (could be|were|are|was|is)
  (found|located|attached|available|accessible)", within one sentence.

Three exclusions keep ordinary answers from firing:

* a HYPOTHETICAL: the match's own sentence, before the match, contains "if",
  "in case", "unless", "whether", "in the event" or "let me know" ("if I can't
  find it I'll tell you" promises, it does not decline);
* a CLEAN NEGATIVE RESULT: "could not find any conflicts / issues / problems /
  errors / discrepancies / concerns / reason" is the answer to a check, not a
  failure to deliver;
* quoted history: lines starting ``>`` and everything from an "On ... wrote:",
  "-----Original Message-----" or a "From: " header line down.

RISK, stated (measured in ``tests/test_reply_could_not.py``). False negatives:
a decline phrased without a first-person inability or a "none of" sentence
("Those records are not in Smokeball.", "Unfortunately that isn't something
the system holds.") does not fire. False positives: a reply that declines
one small part of an otherwise complete answer fires (deliberately: the
person was told the Operator could not do something they asked), and a
negative search result phrased outside the exclusion list ("I couldn't find
any other matters for her" as the ANSWER to "does she have other matters?")
fires. The cost of a false positive is one SHORTFALL email SMD reads and
dismisses; the cost of a false negative is the 2026-11-01 miss. The pattern
set is closed and every change to it lands with its replay.
"""

from __future__ import annotations

import re

#: Every code this module can return. The heartbeat rides it as
#: ``reply_could_not:<code>``; anything else on a row rides as ``other``.
CODES = frozenset(
    {
        "cannot_find",
        "cannot_attach",
        "cannot_access",
        "cannot_complete",
        "not_permitted",
        "none_available",
        "product_limit",
        "looking_into",
    }
)

#: The one sentence the Operator says when it cannot do what was asked
#: (2026-11-24, the owner's rule): it never states a limit of its own to the
#: person; it owns the follow-up, and SMD does the work. A reply carrying it is
#: stamped ``looking_into``, so the request reads NOT FULFILLED and pages SMD
#: exactly as a decline did (``shared.heartbeat``, ``shared.request_cards``).
LOOK_INTO = "Let me look into that. I'll reply in this thread."

#: The codes the reply relay HOLDS a reply for (:func:`stated_limit`): a limit
#: of the Operator's own, or that it cannot attach, send or open something
#: (review of #4210). ``cannot_find``, ``none_available``, ``cannot_complete``
#: and ``not_permitted`` stay metadata only: "I can't find a file named X on
#: matter Y" and "we were unable to locate the defendant" are answers, and the
#: request card still reads them NOT FULFILLED.
HOLD_CODES = frozenset({"product_limit", "cannot_attach", "cannot_access"})
#: A sentence that reports what someone else said or wrote ("the clinic said
#: it cannot send the records", "Dr. Roe wrote: we cannot provide ...").
_REPORTED_RE = re.compile(
    r"\b(?:said|says|told|tells|wrote|writes|replied|replies|responded|stated|states|"
    r"according\s+to|per\s+(?:the|their|his|her))\b|[\"“]"
)

#: The audit metadata key the reply plugin writes the code under.
METADATA_KEY = "could_not"

#: How much of a reply is read. A decline that matters is said early; a cap
#: keeps a long attachment-free body from costing the send path anything.
_MAX_CHARS = 8000

#: First-person inability, after "i" or "we". A phrase that starts with an
#: apostrophe attaches to the subject ("i'm unable to"); the rest follow a space.
_INABILITY = (
    "could not",
    "couldn't",
    "cannot",
    "can not",
    "can't",
    "was unable to",
    "were unable to",
    "am unable to",
    "are unable to",
    "'m unable to",
    "'re unable to",
    "was not able to",
    "wasn't able to",
    "were not able to",
    "weren't able to",
    "am not able to",
    "'m not able to",
    "are not able to",
    "'re not able to",
    "aren't able to",
    "have not been able to",
    "haven't been able to",
    "'ve not been able to",
    "have been unable to",
    "'ve been unable to",
    "had no way to",
    "have no way to",
    "'ve no way to",
)

#: Words allowed between the inability and the verb ("I couldn't still find",
#: "I can't seem to locate", "we were not able to successfully attach").
_FILLERS = (
    "still",
    "yet",
    "currently",
    "actually",
    "seem",
    "appear",
    "to",
    "directly",
    "successfully",
    "now",
    "immediately",
    "fully",
)

#: Delivery verbs and the code each one names. A verb not listed never fires:
#: "I can't wait", "I couldn't agree more", "I can't thank you enough".
_VERB_CODES = {
    "find": "cannot_find",
    "locate": "cannot_find",
    "identify": "cannot_find",
    "retrieve": "cannot_find",
    "pull": "cannot_find",
    "obtain": "cannot_find",
    "download": "cannot_find",
    "attach": "cannot_attach",
    "send": "cannot_attach",
    "provide": "cannot_attach",
    "produce": "cannot_attach",
    "share": "cannot_attach",
    "deliver": "cannot_attach",
    "forward": "cannot_attach",
    "upload": "cannot_attach",
    "include": "cannot_attach",
    "access": "cannot_access",
    "open": "cannot_access",
    "read": "cannot_access",
    "reach": "cannot_access",
    "view": "cannot_access",
    "complete": "cannot_complete",
    "finish": "cannot_complete",
    "process": "cannot_complete",
    "generate": "cannot_complete",
    "create": "cannot_complete",
    "draft": "cannot_complete",
    "prepare": "cannot_complete",
    "confirm": "cannot_complete",
    "verify": "cannot_complete",
    "file": "cannot_complete",
    "save": "cannot_complete",
    "update": "cannot_complete",
    "change": "cannot_complete",
    "edit": "cannot_complete",
    "schedule": "cannot_complete",
    "book": "cannot_complete",
    "cancel": "cannot_complete",
    "help": "cannot_complete",
    "do": "cannot_complete",
}

_SUBJECT = r"\b(?:i|we)"
_INABILITY_RE = "|".join(
    (re.escape(p) if p.startswith("'") else r"\s+" + re.escape(p).replace(r"\ ", r"\s+"))
    for p in sorted(_INABILITY, key=len, reverse=True)
)
_FILLER_RE = "|".join(_FILLERS)
_VERB_RE = "|".join(sorted(_VERB_CODES, key=len, reverse=True))

_FIRST_PERSON_RE = re.compile(
    rf"{_SUBJECT}(?:{_INABILITY_RE})(?:\s+(?:{_FILLER_RE})){{0,2}}\s+(?P<verb>{_VERB_RE})\b"
    # "help but", "do anything but": idioms, not declines.
    r"(?!\s+but\b)"
)

_NO_ACCESS_RE = re.compile(
    rf"{_SUBJECT}(?:\s+do\s+not|\s+don't|\s+no\s+longer)(?:\s+currently)?"
    r"\s+have\s+(?:any\s+)?access\s+to\b"
    rf"|{_SUBJECT}(?:\s+have|'ve)\s+no\s+access\s+to\b"
)

_NOT_PERMITTED_RE = re.compile(
    rf"{_SUBJECT}(?:'m|'re|\s+am|\s+are)\s+not\s+(?:permitted|allowed|authorized|authorised)\s+to\b"
)

#: A limit of the Operator's own said as a product fact rather than in the
#: first person (2026-11-24, A&P: "my document reader cannot open that format
#: ... This is a limit of how I read files in Smokeball"), which the
#: first-person rule above does not see.
_PRODUCT_LIMIT_RE = re.compile(
    r"\b(?:my|our)\s+(?:[a-z-]+\s+){0,3}?"
    r"(?:tool|tools|reader|system|setup|access|email|mailbox|connection|software)\s+"
    r"(?:cannot|can't|can\s+not|could\s+not|couldn't|is\s+unable\s+to|isn't\s+able\s+to"
    r"|does\s+not|doesn't|won't|will\s+not)\s+"
    r"(?:open|read|attach|send|access|handle|support|carry|reach)\b"
    r"|\ba\s+limit(?:ation)?\s+(?:of|in)\s+(?:how|what)\s+(?:i|we)\b"
    r"|\b(?:that|this|the)\s+(?:file\s+)?format\s+(?:is\s+not|isn't)\s+(?:supported|readable)\b"
    rf"|{_SUBJECT}(?:'m|'re|\s+am|\s+are)\s+not\s+(?:set\s+up|equipped|built)\s+to\b"
    rf"|{_SUBJECT}\s+(?:do\s+not|don't)\s+have\s+(?:a\s+)?(?:way|tool|the\s+ability)\s+to\b"
)

#: The owner's sentence (:data:`LOOK_INTO`), or the same promise in its words.
_LOOKING_INTO_RE = re.compile(r"\blet\s+me\s+look\s+into\s+(?:that|this|it)\b")

_NONE_OF_RE = re.compile(
    r"\bnone\s+of\s+(?:the|those|these|them|your)\b[^.?!\n]{0,100}?"
    r"\b(?:could\s+be|can\s+be|were|are|was|is)\s+(?:found|located|attached|available|accessible)\b"
)

#: A hypothetical or an offer, in the same sentence before the match.
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|in\s+case|unless|whether|in\s+the\s+event|let\s+me\s+know)\b"
)

#: A check that came back clean: the answer, not a failure to deliver.
_CLEAN_RESULT_RE = re.compile(
    r"\s+(?:any|no)\s+(?:other\s+|further\s+|obvious\s+)?"
    r"(?:conflicts?|issues?|problems?|errors?|discrepanc(?:y|ies)|concerns?|reason)\b"
)

_QUOTE_START_RE = re.compile(
    r"^\s*(?:on\b.{0,200}\bwrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s)",
)

_SENTENCE_BREAK_RE = re.compile(r"[.?!\n]")


def _own_text(text: str) -> str:
    """The reply's own lines, lowercased, curly apostrophes made straight,
    quoted history dropped."""
    out: list[str] = []
    for line in text[:_MAX_CHARS].replace("’", "'").replace("‘", "'").splitlines():
        low = line.lower()
        if _QUOTE_START_RE.match(low):
            break
        if low.lstrip().startswith(">"):
            continue
        out.append(low)
    return "\n".join(out)


def _sentence_before(text: str, start: int) -> str:
    """The text of the match's own sentence before the match."""
    head = text[:start]
    breaks = list(_SENTENCE_BREAK_RE.finditer(head))
    return head[breaks[-1].end() :] if breaks else head


def _hits(text: object) -> list[tuple[int, str, str]]:
    """Every rule's first match in a reply's own lines, as ``(start, code,
    the words matched)``, in order of where it starts."""
    if not isinstance(text, str) or not text.strip():
        return []
    own = _own_text(text)
    hits: list[tuple[int, str, str]] = []
    for match in _FIRST_PERSON_RE.finditer(own):
        if _HYPOTHETICAL_RE.search(_sentence_before(own, match.start())):
            continue
        verb = match.group("verb")
        if verb == "find" and _CLEAN_RESULT_RE.match(own, match.end()):
            continue
        hits.append((match.start(), _VERB_CODES[verb], match.group(0)))
        break
    for pattern, code in (
        (_NO_ACCESS_RE, "cannot_access"),
        (_NOT_PERMITTED_RE, "not_permitted"),
        (_NONE_OF_RE, "none_available"),
        (_PRODUCT_LIMIT_RE, "product_limit"),
        (_LOOKING_INTO_RE, "looking_into"),
    ):
        for match in pattern.finditer(own):
            if code != "looking_into" and _HYPOTHETICAL_RE.search(
                _sentence_before(own, match.start())
            ):
                continue
            hits.append((match.start(), code, match.group(0)))
            break
    return sorted(hits)


def classify(text: object) -> str | None:
    """The closed code for a reply that told the person the Operator could not
    do what they asked, or ``None``. Never raises; non-text is ``None``."""
    hits = _hits(text)
    return hits[0][1] if hits else None


def stated_limit(text: object) -> str:
    """The words with which a reply STATES a limit of the Operator's own, or
    ``""``. The reply relay holds such a reply so the redraft says
    :data:`LOOK_INTO` instead. Only :data:`HOLD_CODES`, every match read (not
    only the first), and never:

    * a "we" that is the firm: a first-person "we cannot attach / open" is the
      firm's act as often as the Operator's ("we cannot produce those until the
      protective order issues"), so only "I", "my", "our <tool>" and a stated
      limit of how the Operator works hold;
    * a sentence that reports what someone else said or wrote."""
    if not isinstance(text, str) or not text.strip():
        return ""
    own = _own_text(text)
    for match in _FIRST_PERSON_RE.finditer(own):
        codes = {_VERB_CODES[match.group("verb")]}
        joined = _JOINED_VERB_RE.match(own, match.end())  # "find or attach"
        if joined:
            codes.add(_VERB_CODES[joined.group("verb")])
        if codes & HOLD_CODES and match.group(0).startswith("i") and _plain(own, match.start()):
            return (match.group(0) + (joined.group(0) if joined else "")).strip()
    for pattern, code in ((_NO_ACCESS_RE, "cannot_access"), (_PRODUCT_LIMIT_RE, "product_limit")):
        for match in pattern.finditer(own):
            if code == "cannot_access" and not match.group(0).startswith("i"):
                continue
            if _plain(own, match.start()):
                return match.group(0).strip()
    return ""


#: A second delivery verb joined to the first ("I couldn't find or attach").
_JOINED_VERB_RE = re.compile(rf"\s+(?:or|and|nor)\s+(?P<verb>{_VERB_RE})\b")


def _plain(own: str, start: int) -> bool:
    """The match's own sentence is the Operator speaking: no hypothetical, and
    nothing reported from someone else."""
    before = _sentence_before(own, start)
    after = own[start:]
    end = _SENTENCE_BREAK_RE.search(after)
    sentence = before + (after[: end.start()] if end else after)
    return not _HYPOTHETICAL_RE.search(before) and not _REPORTED_RE.search(sentence)


def metadata(text: object) -> dict[str, str]:
    """``{"could_not": code}`` for a decline, ``{}`` otherwise: spread into a
    ``REPLY_SENT`` row's metadata. Never raises."""
    try:
        code = classify(text)
    except Exception:  # noqa: BLE001 — a classifier fault never costs the send's audit row
        return {}
    return {METADATA_KEY: code} if code else {}
