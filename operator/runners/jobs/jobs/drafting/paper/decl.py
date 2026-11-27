"""A declaration for additional discovery, read: where it states the set's
count and which item numbers it lists under each statutory reason.

A declaration states the count more than once: in prose ("40 special
interrogatories", "forty (40)", "a total of 40") and beside the statute cite
("section 2030.050: 40"). A mention of the statutory limit itself ("exceed
the 35 permitted") is not a count of this set and is not read as one. Under
each reason letter "(A)" to "(E)" it may list the items that reason covers,
as ranges or written out number by number, after "No."/"Nos."/"Numbers"; a
list under no letter is a list of the whole set.
"""

from __future__ import annotations

import re

from .model import Block, CountMention, Declaration, ReasonList

_NOUN_SET = r"(?:additional\s+|more\s+)?(?:specially\s+prepared\s+|special\s+)?(?:interrogatories|requests)\b"
_COUNTS = (
    re.compile(rf"(?i)(?<![\d.])\(?(\d{{1,3}})\)?(?![\d.]?\d)\s+{_NOUN_SET}"),
    re.compile(r"(?i)\b(?:total(?:\s+number)?\s+of|propounding|consists\s+of|contains)\s+\(?(\d{1,3})\)?(?![\d.]?\d)"),
    re.compile(r"(?i)(?:section|§)\s*\d{4}\.\d{3}[^\n\d]{0,20}?(?<!\d)\(?(\d{2,3})\)?(?![.\d]*\d)"),
)
#: Words around a number that make it the statutory limit, not this set's count.
_LIMIT = re.compile(r"(?i)\b(exceed|limit|permit|allow|more\s+than|in\s+excess|maximum|up\s+to)")
_ITEM = r"\d+(?:\s*(?:-|–|—|through|thru|to)\s*\d+)?"
_SEP = r"(?:\s*,\s*(?:and\s+)?|\s+and\s+)"
_LIST = re.compile(rf"(?i)\b(Nos?\.|Numbers?|No\b)\s*({_ITEM}(?:{_SEP}{_ITEM})*)(?![A-Za-z0-9])")
_NOT_ITEMS = re.compile(r"(?i)\b(case|set|bar|state\s+bar|dept|department|suite|ste)\.?\s*$")
_REASON = re.compile(r"^\W*\(([A-E])\)")


def expand(body: str) -> list[int]:
    out: list[int] = []
    for part in re.split(_SEP, body):
        ends = [int(n) for n in re.findall(r"\d+", part)]
        out += list(range(ends[0], ends[-1] + 1)) if len(ends) == 2 else ends
    return out


def _is_limit(text: str, start: int, end: int) -> bool:
    return bool(_LIMIT.search(text[max(0, start - 40) : start]) or _LIMIT.search(text[end : end + 30]))


def counts(b: Block) -> list[CountMention]:
    found: dict[int, CountMention] = {}
    for pat in _COUNTS:
        for m in pat.finditer(b.text):
            if m.start(1) in found or _is_limit(b.text, m.start(1), m.end(1)):
                continue
            found[m.start(1)] = CountMention(int(m.group(1)), b.id)
    return [found[k] for k in sorted(found)]


def lists(b: Block, reason: str | None) -> list[ReasonList]:
    out = []
    for m in _LIST.finditer(b.text):
        if _NOT_ITEMS.search(b.text[: m.start()]):
            continue
        out.append(ReasonList(reason=reason, numbers=expand(m.group(2)), noun=m.group(1), id=b.id))
    return out


def read(region: list[Block]) -> Declaration:
    d = Declaration(ids=[b.id for b in region])
    reason: str | None = None
    for b in region:
        m = _REASON.match(b.numbered or "") or _REASON.match(b.text)
        if m:
            reason = m.group(1)
        d.count_mentions += counts(b)
        d.reason_lists += lists(b, reason)
    return d
