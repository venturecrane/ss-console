"""Advertising gate rules D to F (ADR 0098): imagery, solicitation, paid reviews,
third parties, client confidentiality and pending matters.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any


def _sibling(name: str) -> Any:
    """Load a sibling module of the advertising gate by path, once per directory.

    The gate is run as a script (``python3 advertising_gate_check.py``) and
    loaded by path from the overlay, so its siblings cannot rely on sys.path.
    """
    here = Path(__file__).resolve().parent
    key = "_smd_adgate_" + re.sub(r"\W", "_", str(here)) + "_" + name
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, here / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return module


_core = _sibling("advertising_gate_core")
BLOCK = _core.BLOCK
FLAG = _core.FLAG
AD_CHANNELS = _core.AD_CHANNELS
ALL_CHANNELS = _core.ALL_CHANNELS
CHANNELS = _core.CHANNELS
Finding = _core.Finding
_I = _core._I
_FIRM_REF = _core._FIRM_REF
_NAME = _core._NAME
_f = _core._f
_each = _core._each
_clip = _core._clip
_contains = _core._contains
_norm = _core._norm
_loose_pattern = _core._loose_pattern
_digits = _core._digits
_paragraphs = _core._paragraphs
_sentences = _core._sentences
_plain = _core._plain


# ---------------------------------------------------------------------------
# D. Dramatization and imagery (image spec, alt text and caption lines)
# ---------------------------------------------------------------------------

_IMAGE_LINE = re.compile(
    r"^[ \t]*(?:image|photo|picture|alt(?: text)?|caption|imagen|foto|texto alternativo|pie de "
    r"foto)[ \t]*:[ \t]*(?P<body>.+)$",
    _I | re.MULTILINE,
)
_FAKE_LAWYER = re.compile(
    r"(?:stock|generated|ai[- ]generated|illustration|ilustraci\w+|generad\w+).*(?:attorney|"
    r"lawyer|abogad\w*)|(?:attorney|lawyer|abogad\w*).*(?:stock|generated|ilustraci\w+|generad\w+)",
    _I,
)
_REAL_CASE = re.compile(
    r"\b(?:our client|this client|a client|client|cliente|claimant|victim|v[ií]ctima|actual "
    r"accident|real accident|accidente real)\b",
    _I,
)
_INJURY_IMAGE = re.compile(
    r"\b(?:crash\w*|wreck\w*|blood|injur\w*|ambulance|hospital bed|scars?|wheelchair|choque|"
    r"accidente|sangre|herid\w*)\b",
    _I,
)
_GORE = re.compile(r"\b(?:gore|graphic|mangled|severed|corpse|bloody|sangrient\w*)\b", _I)


def r19_r20(item: Any) -> list:
    out: list[Finding] = []
    for m in _IMAGE_LINE.finditer(item.scan):
        body = m.group("body")
        if _FAKE_LAWYER.search(body):
            out.append(
                _f(
                    "R-19",
                    BLOCK,
                    m.group(0),
                    "A stock or generated image may never be presented as one of the firm's "
                    "lawyers; no disclosure cures it.",
                )
            )
        elif _REAL_CASE.search(body):
            out.extend(
                item.disclosure("dramatization", m.group(0), "R-19", "An image presented as a real client or case")
            )
        if _GORE.search(body):
            out.append(_f("R-20", BLOCK, m.group(0), "Graphic injury imagery is not allowed (platform policy)."))
        elif _INJURY_IMAGE.search(body):
            out.append(
                _f(
                    "R-20",
                    FLAG,
                    m.group(0),
                    "Does this image depict an injury or accident scene? Such depictions are "
                    "presumptively misleading (B&P 6158.1(b)); confirm it is appropriate.",
                )
            )
    return out


# ---------------------------------------------------------------------------
# E. Solicitation, paid recommendations, third parties
# ---------------------------------------------------------------------------

_OFFER_SERVICES = re.compile(
    r"\bconsultation\b|\bconsulta\b|\brepresent you\b|\btake your (?:case|claim)\b|\bhelp "
    r"(?:you )?with your (?:claim|case)\b|\bhire us\b|\bsign up\b|\bte podemos ayudar con tu caso\b"
    r"|\ble podemos ayudar con su caso\b",
    _I,
)


def r21(item: Any) -> list:
    return _each(
        item,
        _OFFER_SERVICES,
        "R-21",
        BLOCK,
        "A reply to one person must not offer the firm's services; that is a targeted solicitation (Rule 7.3).",
    )


_PAID_REVIEW = re.compile(
    r"\b(?:leave|write|post) (?:us )?an? (?:\w+ )?review\b.{0,80}\b(?:discount|gift|free|enter|"
    r"win|raffle|\$)|\breferral (?:bonus|fee|reward)s?\b|\brefer a friend\b.{0,60}\b(?:\$|gift|"
    r"reward|bonus)|\bdeja\w*.{0,40}rese[nñ]a.{0,60}(?:descuento|regalo|gratis)",
    _I,
)


def r22(item: Any) -> list:
    return _each(
        item,
        _PAID_REVIEW,
        "R-22",
        BLOCK,
        "This offers something of value for a review or a referral, which Rule 7.2(b) and Google's policy forbid.",
    )


_THIRD_PARTY = re.compile(
    r"\bchiropract\w*|\bclinics?\b|\bbody shops?\b|\btow(?:ing)? (?:company|service)\b"
    r"|\bmedical (?:center|group|provider)s?\b|\bfunding (?:company|companies)\b|\blenders?\b"
    r"|\breferral services?\b|\bquiropr\w*|\bcl[ií]nicas?\b",
    _I,
)


def r23(item: Any) -> list:
    return _each(
        item,
        _THIRD_PARTY,
        "R-23",
        FLAG,
        "This names another business. Did that business pay for or co-sponsor this, and is "
        "any relationship with it disclosed and lawful (B&P 6155, 6157.3)?",
    )


# ---------------------------------------------------------------------------
# F. Confidentiality and third parties in posts
# ---------------------------------------------------------------------------

_CLIENT_IDENT = re.compile(
    r"\b(?:case|claim|file|caso)\s*(?:no\.?|number|n[uú]mero|#)\s*:?\s*[A-Z0-9][\w-]{2,}"
    r"|\b\d{2}[A-Z]{2,5}\d{4,}\b"
    r"|\b(?i:our|a|my) (?i:client),?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"
    r"|\b(?i:client)\s+[A-Z]\.\s?[A-Z]\.",
)


def r24(item: Any) -> list:
    return _each(
        item,
        _CLIENT_IDENT,
        "R-24",
        BLOCK,
        "This identifies a client or a matter (a name, initials or a case number). Client "
        "information may not be published without the client's informed consent (B&P "
        "6068(e)(1)); remove it.",
    )


_PENDING = re.compile(
    r"\b(?:currently|still) (?:litigating|representing)\b|\bpending (?:case|lawsuit|litigation|"
    r"trial)\b|\bongoing (?:case|lawsuit|litigation)\b|\bwe (?:are|have) (?:just )?filed "
    r"(?:a|suit|the)\b",
    _I,
)
_NEGATIVE = re.compile(
    r"\b(?:lied|lying|liars?|cheat(?:ed|ing|s)?|fraud\w*|lowball\w*|bad faith|refused to pay|"
    r"denied (?:the|your|our|her|his|their))\b",
    _I,
)
_NAMED_ENTITY = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z&]+(?:\s+[A-Z][a-zA-Z&]+)*")


def r25_r26(item: Any) -> list:
    out: list[Finding] = []
    for m in item.find(_PENDING):
        sentence = item.sentence_of(m)
        if _NEGATIVE.search(sentence):
            out.append(
                _f(
                    "R-25",
                    BLOCK,
                    sentence,
                    "This characterizes the other side in a pending matter; remove it (Rule 3.6, Rule 1.6).",
                )
            )
        else:
            out.append(
                _f(
                    "R-25",
                    FLAG,
                    sentence,
                    "Is this matter still pending, and does this go beyond what the trial-publicity rule permits?",
                )
            )
    own = [_norm(n) for n in item.regs["firm"]["names"]] + [_norm(r["name"]) for r in item.regs["roster"]]
    for m in item.find(_NEGATIVE):
        sentence = item.sentence_of(m)
        words = sentence.split(maxsplit=1)
        rest = words[1] if len(words) > 1 else ""
        names = [
            n.group(0)
            for n in re.finditer(r"\b[A-Z][a-zA-Z&]+(?:\s+[A-Z][a-zA-Z&]+)*", rest)
            if _norm(n.group(0)) not in own and n.group(0) not in ("I", "California")
        ]
        if names:
            out.append(
                _f(
                    "R-26",
                    FLAG,
                    sentence,
                    f"Can the firm prove this about {names[0]}? A negative factual assertion "
                    "about a named third party needs evidence.",
                )
            )
    return out
