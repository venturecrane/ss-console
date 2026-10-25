"""Advertising gate rules A (ADR 0098): guarantees, quick cash, money, results,
record claims, testimonials, superlatives, awards, free consultations.
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
# A. False, misleading, guarantees, results, money
# ---------------------------------------------------------------------------

_GUARANTEE = re.compile(
    r"\bguarantee(?:s|d|ing)?\b|\bwe will win\b|\bwe'll win\b|\byou will (?:win|recover|get "
    r"(?:paid|money|compensation|a settlement))\b|\bpromise[sd]? (?:to win|you (?:a|the|will))\b"
    r"|\bassured (?:result|outcome|recovery|win|compensation)\b|\bno risk of losing\b"
    r"|\balways win\b|\bnever lose\b|\bcan(?:no|')t lose\b|\bgarantiz\w*|\b(?:te|le|les) "
    r"aseguramos\b|\bvas? a ganar\b|\bganaremos su caso\b|\bseguro que (?:gan|recib|obten)\w*",
    _I,
)


def r01(item: Any) -> list:
    return _each(
        item,
        _GUARANTEE,
        "R-01",
        BLOCK,
        "This promises or predicts an outcome. Advertising may not guarantee a result "
        "(Rule 7.1, B&P 6157.2(a)(1)); remove it.",
    )


_QUICK_CASH = re.compile(
    r"\b(?:quick|fast) settlements?\b|\bimmediate cash\b|\bcash now\b|\bmoney "
    r"(?:fast|now|quickly)\b|\bget paid (?:fast|quickly|now)\b|\bsettle fast\b"
    r"|\bdinero r[aá]pido\b|\befectivo (?:ya|inmediato)\b|\bacuerdo r[aá]pido\b"
    r"|[\U0001F4B0\U0001F4B5\U0001F4B8\U0001F911]",
    _I,
)


def r02(item: Any) -> list:
    return _each(
        item,
        _QUICK_CASH,
        "R-02",
        BLOCK,
        "This suggests quick cash or a quick settlement, which B&P 6157.2(a)(2) forbids; remove it.",
    )


_MONEY_FIGURE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|million|thousand|billion)\b)?"
    r"|\b\d+(?:[.,]\d+)?\s?(?:million|millones|billion)\b",
    _I,
)
_MONEY_CHARACTERIZATION = re.compile(
    r"\b(?:six|seven|eight)[- ]figures?\b|\bmillions? (?:of dollars )?(?:recovered|won)\b"
    r"|\bmaximum (?:compensation|settlement|recovery)\b|\bfull compensation\b"
    r"|\bm[aá]xima compensaci[oó]n\b|\bmillones recuperados\b",
    _I,
)
_RECOVERY_CONTEXT = re.compile(
    r"\b(?:recover\w*|won|win|wins|settle\w*|verdict\w*|awarded|award|obtain\w*|secured|"
    r"compensation|worth|got|client\w*|case|cases|recuper\w*|ganamos|acuerdo|veredicto|"
    r"indemnizaci\w*|compensaci\w*|cliente\w*|caso|casos|obtuvimos)\b",
    _I,
)


def r03(item: Any) -> list:
    out = _each(
        item,
        _MONEY_CHARACTERIZATION,
        "R-03",
        BLOCK,
        "This characterizes a sum of money for a client or a reader, which is presumptively "
        "misleading in electronic media (B&P 6158.1(c)); remove it.",
    )
    for m in item.find(_MONEY_FIGURE):
        if not _RECOVERY_CONTEXT.search(item.sentence_of(m)):
            continue
        row = item.register_match(m.group(0))
        if row is None:
            out.append(
                _f(
                    "R-06",
                    BLOCK,
                    m.group(0),
                    "This dollar figure is not in the firm's results register, so it cannot be "
                    "published; a figure must trace to a result the firm authored.",
                )
            )
            continue
        if not row["consent"]:
            out.append(
                _f(
                    "R-24",
                    BLOCK,
                    m.group(0),
                    "The client behind this result has not consented to its use (results register consent is false).",
                )
            )
            continue
        context = _result_context_present(item, row)
        if not context and not item.has_disclosure("results"):
            out.extend(item.disclosure("results", m.group(0), "R-05", "A dollar figure"))
            continue
        out.append(
            _f(
                "R-03",
                FLAG,
                m.group(0),
                "Does the whole message present this figure fairly, with the facts that "
                "justify it? A money figure stays presumptively misleading even when disclosed.",
            )
        )
    return out


def _result_context_present(item: Any, row: dict) -> bool:
    basis, injury = row.get("liability_basis"), row.get("injury")
    return bool(basis and injury and _contains(item.raw, basis) and _contains(item.raw, injury))


_RESULT_STATEMENT = re.compile(
    r"\b(?:we|our (?:team|firm|attorneys?|lawyers?))\s+(?:won|secured|obtained|recovered|"
    r"negotiated|settled|got)\b|\bour client (?:received|recovered|was awarded|won|got)\b"
    r"|\b(?:verdict|settlement) (?:for|on behalf of) (?:our|a) client\b|\bganamos\b"
    r"|\bobtuvimos\b|\brecuperamos\b|\bnuestr[oa] cliente (?:recibi|obtuvo|gan)\w*",
    _I,
)
_RECORD_CLAIM = re.compile(
    r"\b\d[\d,]*\+?\s+(?:cases|verdicts|wins|trials|clients)\b|\b(?:hundreds|thousands) of "
    r"(?:cases|clients)\b|\bnever lost\b|\b(?:over |more than |nearly |almost )?\d+\+?\s+years "
    r"(?:of experience|in practice|practicing|serving)\b|\bm[aá]s de \d+ a[nñ]os\b",
    _I,
)
_SINCE_YEAR = re.compile(r"\bsince (?:19|20)\d\d\b|\bdesde (?:19|20)\d\d\b", _I)


def r04_r06(item: Any) -> list:
    out: list[Finding] = []
    for m in item.find(_RESULT_STATEMENT):
        sentence = item.sentence_of(m)
        row = None
        for candidate in item.regs["results"]:
            if candidate["liability_basis"] is None and candidate["injury"] is None:
                continue
            if any(_contains(sentence, tm) or _contains(item.raw, tm) for tm in candidate["text_match"]):
                row = candidate
                break
        if row is None:
            out.append(
                _f(
                    "R-06",
                    BLOCK,
                    sentence or m.group(0),
                    "This describes a case result that is not in the firm's results register; "
                    "only results the firm authored, with consent, may be described.",
                )
            )
            continue
        if not row["consent"]:
            out.append(
                _f(
                    "R-24",
                    BLOCK,
                    sentence,
                    "The client behind this result has not consented to its use.",
                )
            )
            continue
        if not _result_context_present(item, row) and not item.has_disclosure("results"):
            out.extend(item.disclosure("results", sentence, "R-05", "A case result"))
            continue
        out.append(
            _f(
                "R-04",
                FLAG,
                sentence,
                "Does this describe the result fairly, with the basis of liability and the "
                "injury, so a reader is not misled about what the firm can do for them?",
            )
        )
    claims = item.find(_RECORD_CLAIM) + [m for m in item.find(_SINCE_YEAR) if item.mentions_firm(item.sentence_of(m))]
    for m in claims:
        if item.register_match(m.group(0)) is None:
            out.append(
                _f(
                    "R-06",
                    BLOCK,
                    m.group(0),
                    "This record claim (experience, case counts or a win record) is not in the "
                    "firm's register; the firm must author it before it can be published.",
                )
            )
    return out


_TESTIMONIAL_CUE = re.compile(
    r"\bclients? (?:said|says|say|wrote|writes|told us|tell us)\b|\btestimonials?\b"
    r"|\breviews? (?:say|said)\b|\b(?:five|5)[- ]stars?\b|★|\bsaid about us\b"
    r"|\btestimonio\w*\b|\b(?:nuestro|un) cliente dijo\b|\brese[nñ]as? dice\w*\b",
    _I,
)
_QUOTED = re.compile(r'"([^"\n]{12,})"')


def r07_r08(item: Any) -> list:
    out: list[Finding] = []
    quotes = [q for q in item.find(_QUOTED) if len(q.group(1).split()) >= 4]
    registered = []
    for q in quotes:
        for row in item.regs["testimonials"]:
            if _norm(q.group(1)).strip(" .") == _norm(row["text"]).strip(" ."):
                registered.append((q, row))
                break
    cue = item.find(_TESTIMONIAL_CUE)
    if not cue and not registered:
        return out
    span = cue[0].group(0) if cue else registered[0][0].group(0)
    matched_spans = {q.span() for q, _ in registered}
    for q in quotes:
        if q.span() not in matched_spans and cue:
            out.append(
                _f(
                    "R-08",
                    BLOCK,
                    q.group(0),
                    "This quotation is not word for word in the firm's testimonial register; "
                    "client words may only be quoted verbatim from it.",
                )
            )
    for q, row in registered:
        if not row["consent"]:
            out.append(
                _f(
                    "R-08",
                    BLOCK,
                    q.group(0),
                    "The client has not consented to this testimonial's use.",
                )
            )
    if cue and not quotes:
        out.append(
            _f(
                "R-08",
                BLOCK,
                span,
                "This paraphrases what clients say about the firm. A testimonial may only be "
                "quoted verbatim from the firm's testimonial register.",
            )
        )
    out.extend(item.disclosure("testimonial", span, "R-07", "A testimonial"))
    return out


_SUPERLATIVE = re.compile(
    r"(?<![\w#])(?:best|leading|premier|unmatched|top[- ]rated|top \d+|top (?:lawyers?|"
    r"attorneys?|firms?)|most (?:experienced|successful|trusted)|highest[- ]rated|no one "
    r"(?:else )?fights|better than|los mejores|el mejor|la mejor|n[uú]mero uno|l[ií]deres?|"
    r"number one|#1)(?!\w)",
    _I,
)
_DO_OUR_BEST = re.compile(r"\b(?:do|doing|did|try|trying|tries) (?:our|your|my|their) best\b", _I)
_SOURCED = re.compile(r"\d.*\b(?:as of|reviews?|rating|rese[nñ]as)\b|\bas of\b.*\d", _I)


def r09(item: Any) -> list:
    out: list[Finding] = []
    for m in item.find(_SUPERLATIVE):
        sentence = item.sentence_of(m)
        if not item.mentions_firm(sentence):
            continue
        if _DO_OUR_BEST.search(sentence) and m.group(0).lower() == "best":
            continue
        if _SOURCED.search(sentence):
            out.append(
                _f(
                    "R-09",
                    FLAG,
                    m.group(0),
                    "Is this comparison tied to a verifiable, dated source the firm can show?",
                )
            )
        else:
            out.append(
                _f(
                    "R-09",
                    BLOCK,
                    m.group(0),
                    "This is an unsubstantiated superlative or comparison about the firm "
                    "(Rule 7.1, B&P 6157.2(a)(5)); remove it or tie it to a dated, "
                    "verifiable source.",
                )
            )
    return out


_AWARD = re.compile(
    r"\bawards?\b|\brecogni[sz]ed as\b|\bsuper lawyers\b|\brising stars?\b|\bbest lawyers\b"
    r"|\bavvo\b|\bmartindale\b|\bav[- ]?preeminent\b|\bpremios?\b|\breconocid[oa]s? como\b",
    _I,
)


def r10(item: Any) -> list:
    out: list[Finding] = []
    for m in item.find(_AWARD):
        sentence = item.sentence_of(m)
        listed = [a for a in item.regs["awards"] if _contains(sentence, a["name"])]
        if not listed:
            out.append(
                _f(
                    "R-10",
                    BLOCK,
                    sentence or m.group(0),
                    "This cites an award or recognition that is not on the firm's award list; "
                    "an award may be cited only once the firm attests it is not "
                    "membership-based and no fee was charged (B&P 6157.2(a)(6)).",
                )
            )
        elif not all(a["ok"] for a in listed):
            out.append(
                _f(
                    "R-10",
                    BLOCK,
                    sentence,
                    "The firm has not attested that this award is not membership-based and carried no fee.",
                )
            )
    return out


_FREE_CONSULT = re.compile(
    r"\bfree (?:consultation|case review|case evaluation)\b|\bconsulta gratis\b|\bconsulta gratuita\b", _I
)


def r11(item: Any) -> list:
    return _each(
        item,
        _FREE_CONSULT,
        "R-11",
        FLAG,
        "Does the firm offer this free consultation exactly as stated?",
    )
