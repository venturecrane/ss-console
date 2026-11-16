"""Which pages of a firm's PDF can be read from their text layer, per lane.

Every "no usable text" rule the lanes had, moved here verbatim under its own
name (wave 5). They are NOT one rule: each lane's row in ``policy`` names its
family, and :func:`plan` applies that family and nothing else. Calibration
notes stay with each rule, because each threshold was measured on a lane's own
delivered runs.

* ``litigation`` (and the case file manager): a page under the 25-character
  floor, or a garbled layer (control bytes, or a long run with no common
  word); on a long paper only the first 3 and last 2 such pages are
  transcribed at extract, the rest marked for ``view_page``.
* ``negotiation``: the floor alone, with the same 3 + 2 deferral.
* ``chronology``: the whole file is a scan when its layer is absent, glyph
  indices or a cipher; otherwise a page is sent to vision when it is an image
  its thin or garbled layer does not carry, or its words are drawn as shapes
  (:func:`vision_pages`).
* ``demand``: a page under the floor that carries an image, or a layer of
  control, private-use or replacement characters, glyph indices or a cipher.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy import DEMAND, LITIGATION, NEGOTIATION, Policy

# ---- litigation (and the case file manager) -----------------------------------------------
LITIGATION_FLOOR = int(LITIGATION.floor or 0)  # characters: a page with less carries no text layer
#: Common words a real text layer carries; a long page with none is garbled.
_REAL_WORD = re.compile(r"(?i)\b(the|and|of|to|for|in|on|by|or|is|at|with|this|that|from|be|was|date|court|case)\b")


def litigation_garbled(t: str) -> bool:
    """A text layer that is not the page's words: a Type 3 font's glyph codes
    read out as control bytes, or a long run with no common word (2026-10-29:
    process servers' job sheets carried ~1,700 such characters a page, so the
    date of service never reached a read and seven values held a list)."""
    s = t.strip()
    if len(s) < LITIGATION_FLOOR:
        return False
    ctrl = sum(1 for c in s if ord(c) < 32 and c not in "\n\r\t")
    return ctrl > 0.10 * len(s) or (len(s) >= 300 and not _REAL_WORD.search(s))


def litigation_no_text(t: str) -> bool:
    return len(t.strip()) < LITIGATION_FLOOR or litigation_garbled(t)


def litigation_ocr_pages(
    texts: list[str], head: int = LITIGATION.head or 0, tail: int = LITIGATION.tail or 0
) -> list[int]:
    """The scanned pages transcribed at extract: every one on a short
    document; on a long one only those among its first ``HEAD`` and last
    ``TAIL`` pages (the caption, the filing stamp, the signature and the
    proof of service). The rest are read on demand: ``view_page``."""
    n = len(texts)
    keep = set(range(n)) if n <= head + tail else set(range(head)) | set(range(n - tail, n))
    return [i for i in sorted(keep) if litigation_no_text(texts[i]) and not texts[i].startswith("[scanned page")]


# ---- negotiation ----------------------------------------------------------------------------
def negotiation_ocr_pages(
    texts: list[str],
    floor: int = NEGOTIATION.floor or 0,
    head: int = NEGOTIATION.head or 0,
    tail: int = NEGOTIATION.tail or 0,
) -> list[int]:
    """The scanned pages to transcribe: all of them on a short document, only
    those among the first ``HEAD`` and last ``TAIL`` pages on a long one."""
    n = len(texts)
    keep = set(range(n)) if n <= head + tail else set(range(head)) | set(range(n - tail, n))
    return [i for i in sorted(keep) if len(texts[i].strip()) < floor]


# ---- the chronology -------------------------------------------------------------------------
GLYPH_RUN = re.compile(r"(?:/\d+ ?){4,}")
GLYPH_THRESHOLD = 0.08  # junk files score 0.21-0.31; the highest clean file 0.031
ENGLISH_FLOOR = 0.02  # ciphered files 0.0000; lowest legitimate (a bill) 0.0317

# A short, deliberately boring list: words that appear in ANY page of English
# prose or clinical narrative, including tables and forms.
_STOP = frozenset(
    "the and of to in for with was were is are on at by a an patient this that "
    "no not his her he she had has been from as or be date name page report".split()
)


def not_english(txt: str) -> bool:
    """A text layer that decodes to something that is not language: a
    constant character offset ("6HH$GGHQGXP%HORZ" for "See Addendum Below")
    contains no slashes and passes every structural check, and its digits
    decode to punctuation, so a composed entry reports the study as "not
    legible" while the page states it plainly."""
    toks = re.findall(r"[A-Za-z]{2,}", txt.lower())
    if len(toks) < 50:
        return False
    return sum(1 for w in toks if w in _STOP) / len(toks) < ENGLISH_FLOOR


def glyph_junk(txt: str) -> bool:
    """A text layer of glyph INDICES ("/0/1/2/3"): a subsetted font with no
    ToUnicode map. Passes every has-a-text-layer test; unreadable to the model."""
    if not txt:
        return False
    covered = sum(len(m.group()) for m in GLYPH_RUN.finditer(txt))
    return covered / len(txt) > GLYPH_THRESHOLD


def text_layer_absent(total: int, pages: int) -> bool:
    return total < 40 * max(1, pages) * 0.05 and total < 400


#: A page's own text layer is THIN below this many non-space characters (fax
#: and print header lines removed) or without a run of three words.
THIN_CHARS = 200
_HEADER_LINE = re.compile(r"(?im)^.*\b(?:fax|printed|print date|page \d+ of \d+|sent|received)\b.*$")
_WORD_RUN = re.compile(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){2,}")


def thin(text: str) -> bool:
    body = _HEADER_LINE.sub("", text or "")
    return len(re.sub(r"\s", "", body)) < THIN_CHARS or not _WORD_RUN.search(body)


#: A date whose digits a bad OCR layer read as letters: "0l/2S/198O".
_MISREAD_DATE = re.compile(r"(?<![\w/])[\dOolIS]{1,2}/[\dOolIS]{1,2}/[\dOolIS]{2,4}(?![\w/])")
#: Letters and digits interleaved inside one word ("pat1ent", "l0mg"), the
#: OCR confusion that also turns Z94.0 into 294.0. Codes ("M54.2", "99213",
#: "S13.4XXA") and units ("10mg") have their own shapes and are not counted.
_MIXED = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]+$")
_CODE = re.compile(r"^(?:[A-Z]\d{2}[A-Z\d]{0,5}|\d+(?:mg|mcg|ml|cc|kg|lbs?|cm|mm)|\d+(?:st|nd|rd|th))$", re.I)
_NONWORD = re.compile(r"[^aeiouy]{5,}", re.I)
GARBLE_MIXED, GARBLE_NONWORD = 0.06, 0.2


_DOB_TOKEN = re.compile(r"(?i)\b(?:D\.?O\.?B\.?|Date\s+of\s+Birth|Birth\s*Date)\s*[:#]?\s*(\d[\d/!'|\-.]{4,})")
_CLEAN_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4})[,.;]?$")
_LEAD_ICD9 = re.compile(r"(?m)^\s*\(?\d{3}\.\d{1,2}\)?\s+[A-Za-z]{3,}")
_ANY_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
ICD10_FROM_YEAR, FAR_YEAR = 2016, 2028


def ocr_tells(body: str) -> bool:
    """Signs measured on a real garbled OCR layer (2026-10-14, 111 pages) that
    the length and word-shape tests all scored clean:
    * a date of birth whose separators the OCR lost or misread ("1201/1980",
      "5/15!1983"): the client's own DOB then reads as another person's;
    * a diagnosis line that opens with an ICD-9-shaped numeric code ("(294.0)
      ...") on a page dated 2016 or later, when ICD-10 had replaced ICD-9: an
      ICD-10 code whose letter the OCR read as a digit (Z94.0 as 294.0);
    * a date whose year is impossible (later than FAR_YEAR or three digits)."""
    if any(not _CLEAN_DATE.match(t) for t in _DOB_TOKEN.findall(body)):
        return True
    years = [int(y) for _, _, y in _ANY_DATE.findall(body)]
    if any(y > FAR_YEAR for y in years) or re.search(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{3}(?![\d/])", body):
        return True
    return bool(_LEAD_ICD9.search(body)) and any(y >= ICD10_FROM_YEAR for y in years)


def garbled(text: str) -> bool:
    """A text layer too damaged to trust, though long enough: an OCR layer
    that confuses digits and letters. Three measurable signals, any one:
    a date carrying letters in its digit places; 6% or more of word tokens
    with letters and digits interleaved (codes and units aside); 20% or more
    of alphabetic words of four letters or more with five consonants in a
    row or no vowel at all. A clean typed page scores near zero on each."""
    body = _HEADER_LINE.sub("", text or "")
    if any(re.search(r"[OolIS]", d) for d in _MISREAD_DATE.findall(body)):
        return True
    if ocr_tells(body):
        return True
    toks = [t.strip(".,;:()[]") for t in body.split()]
    toks = [t for t in toks if len(t) >= 3]
    if not toks:
        return False
    mixed = sum(1 for t in toks if _MIXED.match(t) and not _CODE.match(t))
    alpha = [t for t in toks if t.isalpha() and len(t) >= 4]
    odd = sum(1 for t in alpha if _NONWORD.search(t) or not re.search(r"[aeiouy]", t, re.I))
    return mixed / len(toks) >= GARBLE_MIXED or (len(alpha) >= 10 and odd / len(alpha) >= GARBLE_NONWORD)


#: A page's text layer under this many non-space characters is short enough
#: that its words may be drawn as shapes instead (a full typed page carries
#: 2,000 or more).
DRAWN_TEXT_CHARS = 1000
#: Filled curve segments at which an image-free page carries text drawn as
#: glyph outlines (a print-to-PDF that outlined its fonts). Rules, boxes and
#: check boxes are straight lines and rectangles; a letterhead logo is tens
#: to a few hundred curves; a page of outlined words is thousands.
DRAWN_CURVES = 1500


def glyph_curves(page: Any) -> int:
    """Curve segments in the page's filled vector paths."""
    n = 0
    for path in page.get_drawings():
        if path.get("fill") is None:
            continue
        n += sum(1 for item in path.get("items", ()) if item and item[0] == "c")
    return n


def drawn_text(page: Any) -> bool:
    """True when an image-free page draws its words as shapes the text layer
    does not carry. WHY (2026-11-09): an orthopaedic office visit printed with
    outlined fonts reached an A&P update as two pages whose text layer was
    only the print header, with no image to send to vision, so the composer
    never read the visit and the chronology left it out."""
    return glyph_curves(page) >= DRAWN_CURVES


def vision_pages(path: Path, pages: list[tuple[int, str]]) -> list[int]:
    """Pages of a text-layer PDF that are IMAGES the text layer does not
    carry, or carries garbled: a thin or garbled (``garbled``) text layer over
    an embedded image. Decided per page, because one scanned page inside a
    typed record (an 834-page file held 64) is never read when the document as
    a whole has text, and a garbled OCR layer turned Z94.0 into 294.0 and a
    client's own date of birth into "another person". A thin page with no
    image is a short typed page or a blank one: vision would read nothing more,
    unless its words are drawn as shapes (``drawn_text``), which is checked on
    every image-free page whose text layer is short, thin or not: a print
    header and a URL line carried 285 characters over a drawn office visit
    (2026-11-09)."""
    text = dict(pages)
    short = {n for n, t in pages if len(re.sub(r"\s", "", t or "")) < DRAWN_TEXT_CHARS}
    cand = [n for n, t in pages if thin(t) or garbled(t) or n in short]
    if not cand:
        return []
    import pymupdf

    out: list[int] = []
    with pymupdf.open(str(path)) as doc:
        for n in cand:
            if not 1 <= n <= len(doc):
                continue
            page = doc[n - 1]
            if page.get_images():
                if thin(text[n]) or garbled(text[n]):
                    out.append(n)
            elif drawn_text(page):
                out.append(n)
    return out


# ---- demand ---------------------------------------------------------------------------------
CONTROL_THRESHOLD = 0.10  # share of a page's characters that are control/private-use/replacement
MIN_PAGE_CHARS = int(DEMAND.floor or 0)


def junk_page(text: str) -> bool:
    """A text layer that cannot be read: control characters, private-use
    glyphs and replacement marks (the 2026-09-24 bills), or glyph indices, or
    a cipher. The share is over non-whitespace characters."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    bad = sum(1 for c in chars if unicodedata.category(c) in ("Cc", "Co", "Cs") or c == "�")
    return bad / len(chars) > CONTROL_THRESHOLD or glyph_junk(text) or not_english(text)


def needs_reading(p: dict[str, Any]) -> bool:
    stripped = p["text"].strip()
    if len(stripped) < MIN_PAGE_CHARS:
        return p["has_image"]
    return junk_page(stripped)


# ---- the one entry -------------------------------------------------------------------------
@dataclass
class Plan:
    """What a lane does with each page of one PDF (0-based page indexes)."""

    #: Pages sent to paid reading at extract.
    read: list[int] = field(default_factory=list)
    #: Pages with no usable text that are NOT read now (marked with the lane's
    #: ``deferred`` text).
    deferred: list[int] = field(default_factory=list)
    #: The chronology only: the whole file goes to vision (layer absent or junk).
    scan: bool = False
    #: The chronology only: the layer is glyph indices or a cipher.
    junk: bool = False


def plan(pages: list[str], policy: Policy, images: list[bool] | None = None) -> Plan:
    """The lane's own decision over a PDF's page texts (``images``: whether each
    page carries an image, demand's rule). The chronology's per-page image
    test needs the open document and is :func:`vision_pages`."""
    family = policy.readable
    if family == "litigation":
        read = litigation_ocr_pages(pages, policy.head or 0, policy.tail or 0)
        return Plan(read=read, deferred=[i for i, t in enumerate(pages) if litigation_no_text(t) and i not in read])
    if family == "negotiation":
        floor = int(policy.floor or 0)
        read = negotiation_ocr_pages(pages, floor, policy.head or 0, policy.tail or 0)
        return Plan(read=read, deferred=[i for i, t in enumerate(pages) if len(t.strip()) < floor and i not in read])
    if family == "demand":
        flags = images if images is not None else [False] * len(pages)
        read = [i for i, (t, img) in enumerate(zip(pages, flags)) if needs_reading({"text": t, "has_image": img})]
        return Plan(read=read)
    if family == "chronology":
        txt = "\n".join(f"[p.{n}]\n{t}" for n, t in enumerate(pages, 1))
        junk = glyph_junk(txt) or not_english(txt)
        total = sum(len(t.strip()) for t in pages)
        return Plan(scan=text_layer_absent(total, len(pages)) or junk, junk=junk)
    raise ValueError(f"no readable family {family!r}")


__all__ = [
    "CONTROL_THRESHOLD",
    "DRAWN_CURVES",
    "DRAWN_TEXT_CHARS",
    "ENGLISH_FLOOR",
    "GARBLE_MIXED",
    "GARBLE_NONWORD",
    "GLYPH_RUN",
    "GLYPH_THRESHOLD",
    "ICD10_FROM_YEAR",
    "FAR_YEAR",
    "MIN_PAGE_CHARS",
    "THIN_CHARS",
    "Plan",
    "drawn_text",
    "garbled",
    "glyph_curves",
    "glyph_junk",
    "junk_page",
    "litigation_garbled",
    "litigation_no_text",
    "litigation_ocr_pages",
    "needs_reading",
    "negotiation_ocr_pages",
    "not_english",
    "ocr_tells",
    "plan",
    "text_layer_absent",
    "thin",
    "vision_pages",
]
