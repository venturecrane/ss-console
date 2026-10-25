"""Advertising gate rules H to J (ADR 0098): Business Profile posts, blog posts,
language claims, notario, and the firm's blocklist.
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
_PHONE = _sibling("advertising_rules_reviews")._PHONE


# ---------------------------------------------------------------------------
# H. Business Profile posts, I. blog, J. language
# ---------------------------------------------------------------------------


def r37(item: Any) -> list:
    return _each(
        item,
        _PHONE,
        "R-37",
        FLAG,
        "Google may remove a Business Profile post that carries a phone number; keep it, or "
        "rely on the profile's call button?",
    )


_RESTRICTED = re.compile(
    r"\bpre[- ]?settlement (?:funding|loans?|advances?)\b|\blawsuit loans?\b|\bcash advances?\b"
    r"|\bfinanciamiento\b",
    _I,
)


def r39(item: Any) -> list:
    return _each(
        item,
        _RESTRICTED,
        "R-39",
        BLOCK,
        "This promotes litigation funding or a cash advance, which is restricted content and a "
        "quick-cash claim; remove it.",
    )


_SPECIFIC_READER = re.compile(
    r"\b(?i:you asked)\b|\b(?i:your question)\b|^@\w+|\b(?i:dear) [A-Z][a-z]+\b", re.MULTILINE
)
_YOU_SHOULD = re.compile(
    r"\byou should (?:not |never |always )?(?:file|sign|refuse|accept|settle|reject|talk|give)\b", _I
)
_GENERAL_FRAME = re.compile(r"\b(?:in general|generally|many|often|usually|depending|may|might|can|typically)\b", _I)


def r40(item: Any) -> list:
    out = _each(
        item,
        _SPECIFIC_READER,
        "R-40",
        BLOCK,
        "This answers a specific person's question in public. A post is general information, "
        "not advice to one reader (Rule 1.18).",
    )
    for m in item.find(_YOU_SHOULD):
        if not _GENERAL_FRAME.search(item.sentence_of(m)):
            out.append(
                _f(
                    "R-40",
                    FLAG,
                    item.sentence_of(m),
                    "Does this read as advice about a reader's own case rather than general information?",
                )
            )
    return out


def r41(item: Any) -> list:
    first = next((line for line in item.raw.splitlines() if line.strip()), "")
    return item.disclosure("blog", first, "R-41", "A blog post")


_DEADLINE = re.compile(
    r"\b(?:two|2|three|3|six|6|one|1) (?:years?|months?)\b|\bwithin \d+ days\b|\bstatute of "
    r"limitations\b|\bplazo\b|\bprescripci[oó]n\b",
    _I,
)
_QUALIFIER = re.compile(
    r"\bexception\w*|\bdepend\w*|\bmay be shorter\b|\bgovernment\w*|\bexcepci\w*|\bdepende\w*",
    _I,
)


def r42(item: Any) -> list:
    out: list[Finding] = []
    for para in _paragraphs(item.scan):
        m = _DEADLINE.search(para)
        if m and not _QUALIFIER.search(para):
            out.append(
                _f(
                    "R-42",
                    FLAG,
                    m.group(0),
                    "This states a deadline without saying exceptions exist and the dates "
                    "depend on the facts. Is the qualifier needed here?",
                )
            )
    return out


def r43(item: Any) -> list:
    first = next((line for line in item.raw.splitlines() if line.strip()), "")
    return [
        _f(
            "R-43",
            FLAG,
            first,
            "Which lawyer reviewed this post? A lawyer must be named as its reviewer before it "
            "is published (Rule 5.3).",
        )
    ]


_SPANISH_ABILITY = re.compile(
    r"\bhablamos espa[nñ]ol\b|\bse habla espa[nñ]ol\b|\bspanish[- ]speaking\b|\bbilingual\b"
    r"|\bwe speak spanish\b|\bhabla espa[nñ]ol\b|\bbiling[uü]e\b",
    _I,
)


def _speaks_spanish(row: dict) -> bool:
    return any(lang in ("es", "spanish", "español", "espanol") for lang in row["languages"])


def r44(item: Any) -> list:
    hits = item.find(_SPANISH_ABILITY)
    if not hits:
        return []
    speakers = [r for r in item.regs["roster"] if _speaks_spanish(r)]
    if any(r["lawyer"] for r in speakers):
        return []
    named = [r for r in speakers if _contains(item.raw, r["name"]) and r["title"] and _contains(item.raw, r["title"])]
    if named:
        return []
    return [
        _f(
            "R-44",
            BLOCK,
            hits[0].group(0),
            "No lawyer on the firm's roster speaks Spanish. A Spanish-language claim must name "
            "the person who speaks it and their title, in Spanish (Rule 7.1 cmt [5]).",
        )
    ]


def r46(item: Any) -> list:
    return _each(
        item,
        re.compile(r"\bnotari[oa]s?\b", _I),
        "R-46",
        BLOCK,
        "Never use 'notario' for the firm or its staff (B&P 6126.7).",
    )


def blocklist(item: Any) -> list:
    out = []
    for phrase in item.regs["blocklist"]:
        for m in _loose_pattern(phrase).finditer(item.scan):
            out.append(
                _f(
                    "BLOCKLIST",
                    BLOCK,
                    m.group(0),
                    "This phrase is on the firm's blocklist from a prior State Bar complaint "
                    "(B&P 6158.4); it may never be used again.",
                )
            )
    return out
