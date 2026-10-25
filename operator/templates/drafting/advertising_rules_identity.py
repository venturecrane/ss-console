"""Advertising gate rules B and C (ADR 0098): contingency fees, name and office,
firm names, certification, expertise and lawyer identity.
"""

from __future__ import annotations

import datetime
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
# B. Contingency fees
# ---------------------------------------------------------------------------

_NO_FEE = re.compile(
    r"\bno (?:fees?|costs?|charges?) (?:unless|until|if)\b|\byou (?:don't|do not|won't|will not) "
    r"pay (?:unless|until|anything unless)\b|\bcontingen(?:cy|t) (?:fee|basis|representation)\b"
    r"|\bpay nothing\b|\bfree unless\b|\bnothing out of pocket\b|\bsin costo (?:a menos|hasta)\b"
    r"|\bno paga(?:s|r)? (?:a menos|hasta|si no|nada)\b|\bhonorarios? de contingencia\b"
    r"|\bno cobramos (?:si no|a menos|hasta)\b",
    _I,
)


def r12(item: Any) -> list:
    hits = item.find(_NO_FEE)
    if not hits:
        return []
    span = hits[0].group(0)
    if not item.regs["fee_terms"]["no_fee_phrases_allowed"]:
        return [
            _f(
                "R-12",
                BLOCK,
                span,
                "The firm has not confirmed that it may advertise no-fee or contingency terms "
                "(fee_terms.no_fee_phrases_allowed); remove this.",
            )
        ]
    return item.disclosure("costs", span, "R-12", "A no-fee or contingency statement")


_PERCENT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s?%|(\d{1,2}(?:\.\d+)?) percent\b|(\d{1,2}(?:\.\d+)?) por ciento\b", _I)
_FEE_WORD = re.compile(r"\bfees?\b|\bhonorarios?\b|\bcontingen\w*|\bcobramos\b|\bcharge\w*\b", _I)


def r13(item: Any) -> list:
    out: list[Finding] = []
    allowed = item.regs["fee_terms"]["percentages"]
    for m in item.find(_PERCENT):
        if not _FEE_WORD.search(item.sentence_of(m)):
            continue
        value = float(next(g for g in m.groups() if g))
        if not any(abs(value - p) < 1e-9 for p in allowed):
            out.append(
                _f(
                    "R-13",
                    BLOCK,
                    item.sentence_of(m),
                    "This fee percentage is not one of the firm's authored fee terms.",
                )
            )
    return out


# ---------------------------------------------------------------------------
# C. Identification, names, specialization
# ---------------------------------------------------------------------------


def r14(item: Any) -> list:
    if item.regs["channels"].get(item.channel, {}).get("identity_in_template"):
        return []
    firm = item.regs["firm"]
    has_name = any(_contains(item.raw, n) for n in firm["names"])
    has_city = any(_contains(item.raw, c) for c in firm["office_cities"])
    if has_name and has_city:
        return []
    missing = " and ".join(
        part for part, ok in (("the firm's name", has_name), ("its office city", has_city)) if not ok
    )
    first = next((line for line in item.raw.splitlines() if line.strip()), "")
    return [
        _f(
            "R-14",
            BLOCK,
            first,
            f"Every advertisement must show {missing} (B&P 6157.2(b)). Add them to this "
            f"{item.channel} item, or record that the {item.channel} template carries them "
            f"(channels.{item.channel}.identity_in_template).",
        )
    ]


_DESIGNATOR = re.compile(
    r"(?:[A-ZÁÉÍÓÚÑ][\w'.-]*\s+(?:(?:&|and|of|y|de)\s+)?)+?(?:&\s+Associates|and Associates|"
    r"Law Group|Law Firm|Law Offices?|Legal Group|Legal Services|Attorneys at Law|Injury "
    r"Lawyers|Injury Attorneys|LLP|APC|A\.P\.C\.|P\.C\.)(?!\w)"
    r"|\b(?:The )?Law Offices? of (?:[A-ZÁÉÍÓÚÑ][\w'.-]*\s?){1,4}"
)
_FIRM_FLAG = re.compile(
    r"\blegal aid\b|\bpublic defender\b|\bnon-?profit\b|\bstate[- ]approved\b|\bstate bar "
    r"approved\b|\bgovernment\b|\basistencia legal gratuita\b",
    _I,
)


def r15(item: Any) -> list:
    out: list[Finding] = []
    names = item.regs["firm"]["names"]
    for m in _DESIGNATOR.finditer(item.scan):
        span = m.group(0).strip()
        covered = False
        for name in names:
            for occ in _loose_pattern(name).finditer(item.scan):
                if (
                    occ.start() <= m.end()
                    and occ.end() >= m.end() - 1
                    or (occ.start() <= m.start() + len(span) and occ.end() >= m.start() + len(span))
                ):
                    covered = True
        if not covered:
            out.append(
                _f(
                    "R-15",
                    BLOCK,
                    span,
                    "This names a firm that is not one of the firm's authored names (Rule 7.5); "
                    "use the firm's exact name.",
                )
            )
    out.extend(
        _each(
            item,
            _FIRM_FLAG,
            "R-15",
            FLAG,
            "Could this wording imply a government, public or charitable affiliation the firm does not have?",
        )
    )
    return out


_CERTIFIED = re.compile(
    r"\bcertified specialists?\b|\bboard[- ]certified\b|\bcertified (?:in|by)\b"
    r"|\bespecialista certificad[oa]\b",
    _I,
)


def r16(item: Any) -> list:
    out: list[Finding] = []
    today = datetime.date.today().isoformat()
    for m in item.find(_CERTIFIED):
        ok = any(
            c["lawyer"]
            and c["body"]
            and _contains(item.raw, c["lawyer"])
            and _contains(item.raw, c["body"])
            and c["expires"] >= today
            for c in item.regs["certifications"]
        )
        if not ok:
            out.append(
                _f(
                    "R-16",
                    BLOCK,
                    item.sentence_of(m),
                    "A certified-specialist claim must match a current certification in the "
                    "firm's register, and the item must name the lawyer and the certifying "
                    "body (Rule 7.4(a)).",
                )
            )
    return out


_EXPERT = re.compile(
    r"\b(?:specialists?|speciali[sz](?:e|es|ed|ing) in|experts?|expertise|especialistas?|"
    r"expert[oa]s?|especializad[oa]s?)\b(?! witness)",
    _I,
)


def r17(item: Any) -> list:
    out = []
    for m in item.find(_EXPERT):
        sentence = item.sentence_of(m)
        if _CERTIFIED.search(sentence) or re.search(r"\bperitos?\b", sentence, _I):
            continue
        out.append(
            _f(
                "R-17",
                FLAG,
                m.group(0),
                "Can the firm substantiate this claim of specialty or expertise?",
            )
        )
    return out


_LAWYER_NAMED = re.compile(
    rf"\b(?i:attorneys?|lawyers?|abogad[oa]s?|licenciad[oa])\s+(?P<a>{_NAME})"
    rf"|(?P<b>{_NAME}),?\s+(?:Esq\.?|(?i:attorney at law))"
    rf"|(?P<c>{_NAME}),\s+(?i:an?\s+|our\s+|nuestr[oa]\s+)?(?i:attorney|lawyer|abogad[oa])\b"
)


def r18(item: Any) -> list:
    out: list[Finding] = []
    roster = item.regs["roster"]
    for m in _LAWYER_NAMED.finditer(item.scan):
        name = (m.group("a") or m.group("b") or m.group("c") or "").strip()
        if name.split()[0] in ("General",):
            continue
        person = next(
            (r for r in roster if _norm(name) == _norm(r["name"]) or _norm(name).startswith(_norm(r["name"]) + " ")),
            None,
        )
        if person is None:
            out.append(
                _f(
                    "R-18",
                    BLOCK,
                    m.group(0),
                    f"{name} is presented as a lawyer but is not on the firm's roster.",
                )
            )
        elif not person["lawyer"]:
            out.append(
                _f(
                    "R-18",
                    BLOCK,
                    m.group(0),
                    f"{name} is not a lawyer on the firm's roster and must not be presented as one (B&P 6157.2(a)(3)).",
                )
            )
    return out
