"""A propounded set's Definitions, read and added to by code (``propounded.py``
holds the other ops).

A set's Definitions are titled by a heading ("## DEFINITIONS") or, in a set
filed from the firm's own file, by a bold paragraph ("**DEFINITIONS**"); one
region finder (``defs_region``) serves both reading a definition and putting
the set's uses of a new term in capitals, so the two never disagree about
where the Definitions end. A definition is a paragraph opening with its term
in capitals in quotes, numbered ("1. "INCIDENT" means") or not ("“IDENTIFY,”
means", the comma inside the closing quote): a new one takes its neighbours'
form, number and all, and goes in alphabetically.

The term is the model's spelling of the email's word: code takes it when it is
the email's own, or a correction within two letters of one of the email's
words that the set's own text uses (``term_ok``, ``spelled_in``), so a
misspelled "ACCOMODATION(S)" is defined as "ACCOMMODATION(S)" and never as a
word no one wrote.
"""

from __future__ import annotations

import re

from .. import sets

#: One definition's opening: indent, number, the markup and quotes around its
#: term, the comma or period inside the closing quote.
_DEF_LINE = re.compile(
    r"(?m)^([ \t]*)(?:(\d+)(\.[ \t]+))?(\*\*|__)?([\"“])([A-Z][A-Z0-9&'./() -]*[A-Z0-9)])([,.]?)([\"”])(\*\*|__)?"
)
_BOLD_DEFS = re.compile(r"(?im)^\*\*\s*DEFINITIONS\b[^\n]*\*\*[ \t]*$")
#: The end of a bold-titled Definitions: the next bold-only paragraph (a title or a label) or heading.
_BOLD_END = re.compile(r"(?m)^(?:\*\*[^\n]*\*\*[ \t]*$|#{1,6}\s)")
_PLURAL = re.compile(r"\((?:E?S)\)$")
#: A case name ("Example v. Sample") or an italic span: never put in capitals.
_CASE = re.compile(
    r"\b[A-Z][\w.'&-]*(?:\s+[A-Z][\w.'&-]*)*\s+v\.?\s+[A-Z][\w.'&-]*(?:\s+[A-Z][\w.'&-]*)*"
    r"|(?<![*\w])\*(?!\*)[^*\n]+(?<!\*)\*(?![*\w])|(?<!\w)_[^_\n]+_(?!\w)"
)
_SIGNATURE = re.compile(r"(?m)^\W*Dated:")


def _bold_defs(md: str) -> tuple[int, str] | None:
    m = _BOLD_DEFS.search(md)
    if not m:
        return None
    end = _BOLD_END.search(md, m.end())
    return m.end(), md[m.end() : end.start() if end else len(md)]


def defs_region(md: str) -> tuple[int, int] | None:
    """The Definitions body (start, end), heading- or bold-titled; None for none."""
    got = sets._defs_section(md) or _bold_defs(md)
    return None if got is None else (got[0], got[0] + len(got[1]))


def _key(term: str) -> str:
    return " ".join(term.split())


def defs(md: str) -> tuple[int, int, list[re.Match[str]]] | None:
    """The Definitions body (start, end) and each definition's opening, in
    order (match offsets are into ``md``)."""
    span = defs_region(md)
    if span is None:
        return None
    return span[0], span[1], list(_DEF_LINE.finditer(md, span[0], span[1]))


def defined(md: str) -> list[str]:
    got = defs(md)
    return [_key(m.group(6)) for m in got[2]] if got else []


def base(term: str) -> str:
    """The term with its "(S)" plural mark off: what its uses are matched on."""
    return _PLURAL.sub("", _key(term).upper()).strip()


def _words(s: str) -> list[str]:
    return [w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", re.sub(r"\((?:e?s)\)", "", s, flags=re.I))]


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def term_ok(term: str, email: str) -> bool:
    """The term is the email's own word, or each of its words is within two
    letters of one of the email's (case aside, a plural "(S)" allowed)."""
    have = set(_words(email))
    mine = _words(term)
    return bool(mine) and all(w in have or (len(w) >= 5 and any(_distance(w, h) <= 2 for h in have)) for w in mine)


def spelled_in(term: str, md: str) -> bool:
    """The term's words are used in the set's own text outside its Definitions:
    the correction is a real word of this set, not one the model made up."""
    span = defs_region(md)
    text = md if span is None else md[: span[0]] + md[span[1] :]
    have = set(_words(text))
    return all(w in have or w + "s" in have for w in _words(base(term)))


def definition_ok(term: str, text: str) -> str | None:
    """The definition as code writes it (one paragraph opening with the quoted
    term in capitals); None when the model's text does not open with it."""
    t = " ".join((text or "").split())
    m = re.match(r"^[\"“]\s*([^\"”]+?)\s*[,.]?\s*[\"”]\s*[,.]?\s*(\S.*)$", t)
    if not m or _key(m.group(1)).rstrip(",.").upper() != _key(term).upper() or len(t) > 1500:
        return None
    return f'"{_key(term).upper()}" {m.group(2)}'


def _opening(row: re.Match[str], term: str, number: int | None) -> str:
    """A new definition's opening in its neighbour's form."""
    num = f"{number}{row.group(3)}" if number is not None else ""
    mark, close = row.group(4) or "", row.group(9) or ""
    return f"{row.group(1)}{num}{mark}{row.group(5)}{term}{row.group(7)}{row.group(8)}{close}"


def define(md: str, term: str, text: str) -> tuple[str, str | None]:
    """The definition placed alphabetically among the set's definitions, in
    their form (numbered, and the ones after it numbered on, only when they
    are numbered): (the document, None) or (the document unchanged, why)."""
    term = _key(term).upper()
    got = defs(md)
    if got is None or not got[2]:
        return md, f'the definition of "{term}" was not added: the set has no Definitions code can read'
    _start, end, rows = got
    if any(_key(r.group(6)) == term for r in rows):
        return md, f'"{term}" is already defined in it; left as written'
    numbered = {r.group(2) is not None for r in rows}
    if len(numbered) > 1:
        return md, f'the definition of "{term}" was not added: some of its definitions are numbered and some not'
    nums = [int(r.group(2)) for r in rows] if numbered == {True} else []
    if nums and nums != list(range(nums[0], nums[0] + len(nums))):
        return md, f'the definition of "{term}" was not added: its definitions are not numbered in order'
    rest = re.sub(r"^\"[^\"]*\"\s*", "", definition_ok(term, text) or text)
    order = term.replace("(", "").casefold()
    at = next((i for i, r in enumerate(rows) if _key(r.group(6)).replace("(", "").casefold() > order), len(rows))
    row = rows[min(at, len(rows) - 1)]
    out = md
    for i in range(len(rows) - 1, at - 1, -1) if nums else []:  # from the end, so earlier offsets hold
        out = out[: rows[i].start(2)] + str(nums[i] + 1) + out[rows[i].end(2) :]
    para = f"{_opening(row, term, nums[0] + at if nums else None).lstrip()} {rest}"
    if at < len(rows):
        p = rows[at].start()
        out = out[:p] + row.group(1) + para + "\n\n" + out[p:]
    else:  # after the whole last definition, its (a) and (b) included: the Definitions' end
        cut = len(out[:end].rstrip())
        out = out[:cut] + "\n\n" + row.group(1) + para + out[cut:]
    return out, None


def _protected(seg: str, others: list[str]) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _CASE.finditer(seg)]
    for t in others:
        spans += [(m.start(), m.end()) for m in sets._term_pattern(base(t)).finditer(seg)]
    return spans


def _caps(seg: str, terms: list[str], others: list[str]) -> str:
    keep = _protected(seg, others)

    def up(m: re.Match[str]) -> str:
        inside = any(a < m.end() and m.start() < b for a, b in keep)
        return m.group(0) if inside else m.group(0).upper()

    for t in terms:
        seg = sets._term_pattern(base(t)).sub(up, seg)
    return seg


def caps_text(text: str, md: str) -> str:
    """New words for the set (a request the model wrote) with the set's defined terms in capitals."""
    terms = sorted({base(t) for t in defined(md)}, key=len, reverse=True)
    return sets._outside(text, lambda s: _caps(s, terms, [])) if terms else text


def capitalize(md: str, terms: list[str]) -> str:
    """Every use of ``terms`` (and their plurals) after the Definitions, to the
    signature, in capitals: never inside a quotation, a case name or another
    defined term that holds the word."""
    span = defs_region(md)
    if span is None or not terms:
        return md
    at = span[1]
    sig = _SIGNATURE.search(md, at)
    end = sig.start() if sig else len(md)
    terms = sorted({base(t) for t in terms}, key=len, reverse=True)
    others = [t for t in defined(md) if base(t) not in terms and any(w in base(t) for w in terms)]
    return md[:at] + sets._outside(md[at:end], lambda s: _caps(s, terms, others)) + md[end:]


__all__ = [
    "base",
    "capitalize",
    "caps_text",
    "define",
    "defined",
    "definition_ok",
    "defs",
    "defs_region",
    "spelled_in",
    "term_ok",
]
