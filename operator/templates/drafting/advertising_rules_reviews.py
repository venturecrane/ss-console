"""Advertising gate rules G (ADR 0098): owner replies to Google reviews."""

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
# G. Owner replies to Google reviews
# ---------------------------------------------------------------------------

_CONFIRM_CLIENT = re.compile(
    r"\b(?:your|su|tu) (?:case|matter|claim|accident|injury|injuries|settlement|caso|reclamo|"
    r"accidente|lesi[oó]n|lesiones)\b|\bwe (?:represented|handled|worked on|fought for) "
    r"(?:you|your)\b|\bwhen you (?:hired|retained|came to) us\b|\bthank you for (?:trusting|"
    r"choosing) us\b|\bas your (?:attorney|lawyer|attorneys|lawyers)\b|\bduring your "
    r"(?:case|representation)\b|\bnos eligi[oó]\b|\bsu abogad[oa]\b|\btu abogad[oa]\b",
    _I,
)


def r27(item: Any) -> list:
    return _each(
        item,
        _CONFIRM_CLIENT,
        "R-27",
        BLOCK,
        "A review reply must not confirm or imply that the reviewer is or was a client, or "
        "repeat anything about their matter (B&P 6068(e)(1), ABA 496).",
    )


_DENY_CLIENT = re.compile(
    r"\bnot (?:a|our) (?:client|former client)\b|\bnever (?:represented|hired)\b|\bno record "
    r"of\b|\bnunca (?:represent\w*|fue cliente)\b",
    _I,
)


def r28(item: Any) -> list:
    return _each(
        item,
        _DENY_CLIENT,
        "R-28",
        FLAG,
        "Has someone at the firm verified, through a conflicts search, that this person was "
        "never a client? This sentence holds until a named person confirms it.",
    )


_MATTER_FACTS = re.compile(
    r"\bwe (?:called|emailed|kept you|updated you|returned your|told you|advised you)\b"
    r"|\b(?:fees?|invoices?|billing|billed)\b|\bwe (?:believe|feel) we (?:acted|handled|did)\b"
    r"|\bhonorarios\b|\bfactura\w*\b",
    _I,
)


def r29(item: Any) -> list:
    return _each(
        item,
        _MATTER_FACTS,
        "R-29",
        BLOCK,
        "A review reply must not discuss what happened in a representation, fees, or why "
        "the firm acted properly (SF 2014-1, ABA 496).",
    )


_NEGATIVE_CUE = re.compile(r"\b(?:sorry|regret|unhappy|disappointed|frustrat\w*|lament\w*|sentimos)\b", _I)
_SAFE_THANKS = re.compile(r"^(?:thank(?:s| you)\b|gracias\b)", _I)
_SAFE_REGRET = re.compile(r"\b(?:we(?:'re| are) sorry|we regret|sorry to (?:hear|learn)|lamentamos|sentimos)\b", _I)
_SAFE_OBLIGATIONS = re.compile(
    r"professional obligations (?:do not|don't) allow us to respond as we would wish"
    r"|obligaciones profesionales no nos permiten responder como quisi[eé]ramos",
    _I,
)
_SAFE_DISAGREE = re.compile(r"(?:disagree|no estamos de acuerdo).*(?:not at liberty|confidential|confidencial)", _I)
_SAFE_STANDARDS = re.compile(r"^we (?:take|hold ourselves|strive|aim)\b", _I)


def r30_r32(item: Any) -> list:
    if not _NEGATIVE_CUE.search(item.scan):
        return []
    out: list[Finding] = []
    contact = item.regs["offline_contact"]
    for sentence in item.sentences:
        s = sentence.strip()
        if _SAFE_DISAGREE.search(s):
            out.append(
                _f(
                    "R-32",
                    FLAG,
                    s,
                    "This implies the reviewer was a client. Is the matter concluded, and does "
                    "the firm accept implying representation here?",
                )
            )
            continue
        if _SAFE_THANKS.search(s) or _SAFE_REGRET.search(s) or _SAFE_OBLIGATIONS.search(s) or _SAFE_STANDARDS.search(s):
            continue
        if (
            contact["name"]
            and _contains(s, contact["name"])
            and re.search(r"\b(?:contact|call|reach|comun[ií]que\w*|llame)\b", s, _I)
        ):
            continue
        out.append(
            _f(
                "R-30",
                FLAG,
                s,
                "This sentence is outside the safe-harbor phrasings for a negative review "
                "(thanks, regret, professional obligations, an offline contact). Should it stay?",
            )
        )
    return out


_HOSTILE = re.compile(
    r"\bliars?\b|\blying\b|\bdishonest\b|\bfake review\b|\bdefamat\w*|\bwe will (?:sue|report)\b"
    r"|\blegal action\b|\bslander\w*|\blibel\w*|\bmentiros\w*|\bdifamaci[oó]n\b|\bdemandar\w*",
    _I,
)


def r31(item: Any) -> list:
    return _each(
        item,
        _HOSTILE,
        "R-31",
        BLOCK,
        "A review reply must be proportionate and restrained: no accusation, threat or "
        "character statement about the reviewer (LACBA 525).",
    )


_URL = re.compile(r"https?://\S+|\bwww\.\S+", _I)
_PROMO = re.compile(
    r"\bfree consultation\b|\bcall (?:us )?today\b|\bno fee unless\b|\bdiscounts?\b|\d+\s?% off\b"
    r"|\bconsulta gratis\b",
    _I,
)
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


def r34_r35(item: Any) -> list:
    out = _each(
        item,
        _URL,
        "R-34",
        BLOCK,
        "A review reply may not carry a link (Google's advertising and solicitation policy).",
    )
    out += _each(
        item,
        _PROMO,
        "R-34",
        BLOCK,
        "A review reply may not carry promotional content or offers.",
    )
    contact = item.regs["offline_contact"]
    own_phones = {_digits(p) for p in item.regs["firm"]["phones"]} | {_digits(contact["phone"] or "")}
    own_phones.discard("")
    for m in item.find(_PHONE):
        if _digits(m.group(0)) not in own_phones:
            out.append(
                _f(
                    "R-35",
                    BLOCK,
                    m.group(0),
                    "A review reply may only carry the firm's own contact number.",
                )
            )
    own_email = (contact["email"] or "").lower()
    for m in item.find(_EMAIL):
        if m.group(0).lower() != own_email:
            out.append(
                _f(
                    "R-35",
                    BLOCK,
                    m.group(0),
                    "A review reply may only carry the firm's own contact address.",
                )
            )
    out += _each(
        item,
        re.compile(
            r"\b(?:surgery|surgeries|diagnos\w*|fractur\w*|concussion|whiplash|herniat\w*|"
            r"cirug[ií]a|fractura\w*)\b",
            _I,
        ),
        "R-35",
        BLOCK,
        "A review reply must not mention anyone's medical details.",
    )
    return out


_CUSTOMER_VOICE = re.compile(
    r"\bI (?:highly )?recommend\b|\bmy (?:lawyer|attorney|case|settlement)\b|\b(?:five|5)[- ]stars?\b"
    r"|\bI hired\b|\bthey (?:helped me|got me)\b|\bmi abogad[oa]\b|\bles recomiendo\b",
    _I,
)


def r36(item: Any) -> list:
    return _each(
        item,
        _CUSTOMER_VOICE,
        "R-36",
        BLOCK,
        "This is written in a customer's voice. The seat never drafts a review of the firm or "
        "a reply that sounds like a customer.",
    )
