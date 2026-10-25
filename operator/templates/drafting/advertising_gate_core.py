"""Shared pieces of the California advertising gate (ADR 0098): constants,
the finding, text helpers, language detection and the item under check.

Loaded by ``advertising_gate_check.py`` and its rule modules; see that file's
docstring for what the gate decides.
"""

from __future__ import annotations

import re
from typing import NamedTuple

BLOCK = "BLOCK"
FLAG = "FLAG"

CHANNELS: tuple[str, ...] = ("blog", "social", "youtube", "gbp", "review-reply", "press", "note")

#: Channels whose items are public advertisements.
AD_CHANNELS = frozenset({"blog", "social", "youtube", "gbp", "review-reply", "press"})
ALL_CHANNELS = frozenset(CHANNELS)
#: Rules that hold in any text the seat writes, including a cover note.
_NOTE_AND_ADS = ALL_CHANNELS

_DISCLOSURE_KEYS = ("results", "testimonial", "costs", "blog", "dramatization")
_DISCLOSURE_LABEL = {
    "results": "results",
    "testimonial": "testimonial",
    "costs": "case-costs",
    "blog": "blog disclaimer",
    "dramatization": "dramatization",
}
_LANG_NAME = {"en": "English", "es": "Spanish"}

_SPAN_MAX = 160


class Finding(NamedTuple):
    rule: str
    severity: str
    span: str
    message: str


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_SMART = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
    "\u2013": "-",
    "\u2014": "-",
}
_SMART_RE = re.compile("|".join(map(re.escape, _SMART)))


def _plain(text: str) -> str:
    return _SMART_RE.sub(lambda m: _SMART[m.group(0)], text or "")


def _norm(text: str) -> str:
    """Lowercase, smart characters to ASCII, whitespace collapsed."""
    return re.sub(r"\s+", " ", _plain(text).lower()).strip()


def _clip(span: str) -> str:
    flat = " ".join(span.split())
    return flat if len(flat) <= _SPAN_MAX else flat[: _SPAN_MAX - 3] + "..."


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _loose_pattern(phrase: str) -> re.Pattern[str]:
    """A case-insensitive pattern for an authored phrase, whitespace-tolerant."""
    words = [re.escape(w) for w in _plain(phrase).split()]
    return re.compile(r"\s+".join(words), re.IGNORECASE)


def _contains(haystack: str, needle: str) -> bool:
    needle_n = _norm(needle).rstrip(" .")
    return bool(needle_n) and needle_n in _norm(haystack)


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

_ES_WORDS = frozenset(
    "el la los las del que y en por para con su sus es una se al como pero usted ustedes "
    "nuestro nuestra nuestros abogado abogada abogados caso accidente si tiene puede".split()
)
_EN_WORDS = frozenset("the and of to you is for with your are that this it we our if after can have was".split())


def detect_languages(text: str, lang: str = "auto") -> frozenset[str]:
    """``{"en"}``, ``{"es"}`` or both. ``lang`` other than ``auto`` overrides."""
    if lang in ("en", "es"):
        return frozenset({lang})
    words = re.findall(r"[a-záéíóúñü]+", _plain(text).lower())
    es = sum(1 for w in words if w in _ES_WORDS) + 2 * len(re.findall(r"[áéíóúñ¿¡]", text or ""))
    en = sum(1 for w in words if w in _EN_WORDS)
    langs: set[str] = set()
    top = max(es, en)
    if en >= 3 and en >= 0.25 * top:
        langs.add("en")
    if es >= 3 and es >= 0.25 * top:
        langs.add("es")
    return frozenset(langs or {"en"})


# ---------------------------------------------------------------------------
# The item under check
# ---------------------------------------------------------------------------


class Item:
    def __init__(self, text: str, channel: str, regs: dict, langs: frozenset[str]):
        self.raw = _plain(text)
        self.channel = channel
        self.regs = regs
        self.langs = langs
        scan = self.raw
        # Authored disclosure sentences are the firm's own words: they are
        # removed before the pattern rules run, so "does not constitute a
        # guarantee" never trips R-01.
        for entry in regs["disclosures"].values():
            for sentence in (entry or {}).values():
                if sentence:
                    scan = _loose_pattern(sentence).sub(" ", scan)
        self.scan = scan
        self.sentences = _sentences(scan)

    def find(self, pattern: re.Pattern[str]) -> list[re.Match[str]]:
        return list(pattern.finditer(self.scan))

    def disclosure(self, key: str, span: str, rule: str, why: str) -> list[Finding]:
        """BLOCK unless the authored ``key`` sentence is present in every language."""
        label = _DISCLOSURE_LABEL[key]
        entry = self.regs["disclosures"].get(key)
        if entry is None:
            return [
                Finding(
                    rule,
                    BLOCK,
                    _clip(span),
                    f"{why} needs the firm's {label} sentence, and the firm has not authored the {label} sentence yet.",
                )
            ]
        out: list[Finding] = []
        for lang in sorted(self.langs):
            sentence = entry.get(lang)
            if not sentence:
                out.append(
                    Finding(
                        "R-45" if lang == "es" else rule,
                        BLOCK,
                        _clip(span),
                        f"{why} needs the firm's {label} sentence in {_LANG_NAME[lang]}, and "
                        f"the firm has not authored the {label} sentence in "
                        f"{_LANG_NAME[lang]} yet.",
                    )
                )
            elif not _contains(self.raw, sentence):
                out.append(
                    Finding(
                        "R-45" if lang == "es" else rule,
                        BLOCK,
                        _clip(span),
                        f"{why} must carry the firm's {label} sentence in "
                        f"{_LANG_NAME[lang]}, word for word, in the item itself.",
                    )
                )
        return out

    def has_disclosure(self, key: str) -> bool:
        entry = self.regs["disclosures"].get(key)
        if entry is None:
            return False
        return all(entry.get(lang) and _contains(self.raw, entry[lang]) for lang in self.langs)

    def sentence_of(self, match: re.Match[str]) -> str:
        start = max(
            self.scan.rfind(".", 0, match.start()),
            self.scan.rfind("!", 0, match.start()),
            self.scan.rfind("?", 0, match.start()),
            self.scan.rfind("\n", 0, match.start()),
        )
        ends = [i for i in (self.scan.find(c, match.end()) for c in ".!?\n") if i != -1]
        end = min(ends) if ends else len(self.scan)
        return self.scan[start + 1 : end + 1].strip()

    def mentions_firm(self, sentence: str) -> bool:
        if _FIRM_REF.search(sentence):
            return True
        return any(_contains(sentence, n) for n in self.regs["firm"]["names"])

    def register_match(self, span: str) -> dict | None:
        """The results row whose ``text_match`` covers ``span``, if any."""
        want = re.sub(r"\s+", "", _norm(span))
        if not want:
            return None
        for row in self.regs["results"]:
            for tm in row["text_match"]:
                have = re.sub(r"\s+", "", _norm(tm))
                if have and (want == have or want in have or have in want):
                    return row
        return None


_I = re.IGNORECASE
_FIRM_REF = re.compile(
    r"\b(we|we're|we've|our|ours|us|firm|lawyers?|attorneys?|abogad\w*|nosotros|nuestr\w*|bufete)\b",
    _I,
)
_NAME = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ'.-]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ'.-]+){1,3}"


def _f(rule: str, sev: str, span: str, message: str) -> Finding:
    return Finding(rule, sev, _clip(span), message)


def _each(item: Item, pattern: re.Pattern[str], rule: str, sev: str, msg: str) -> list[Finding]:
    return [_f(rule, sev, m.group(0), msg) for m in item.find(pattern)]
