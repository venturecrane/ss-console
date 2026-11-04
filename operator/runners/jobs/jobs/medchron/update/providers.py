"""A new entry names a clinician spelled near, but not as, the delivered
chronology spells one: the spelling is AUDITED, never guessed. $0.

A scan reads "Angell" for "Angeli", and the firm then reads two names for
one person. But two real clinicians can be one letter apart ("Peterson" and
"Pederson", both M.D.), and respelling one into the other merges two people
in a filed chronology, which no reader can see was done.

The known names are everything the delivered chronology spells: its provider
lines, the clinicians its entries' text names, and its Exhibit List titles
(the correct spelling is often only in the text or a title). A name an added
entry states, on its provider line or in its text, that is near a known name
under the safe rule (one name word one or two edits from a known one with
the same first letter, every other word shared, the same credential) becomes
an audited CLAIM: the auditor reads the name off the cited page, and a repair
writes the spelling the page shows. Only an audited spelling is applied, to
the provider line and the text, and the exhibit title follows the provider
line. Every near name is listed in the collect and assemble reports.
"""

from __future__ import annotations

import re
from typing import Any

from . import body as B

MIN_LEN, MAX_EDITS = 8, 2
#: The shortest name word the safe rule respells ("Moe" and "Poe" are two people).
MIN_WORD = 5


def edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _parts(name: str) -> tuple[str, str]:
    """(name part, credential part), both lowercased and trimmed."""
    head, _, cred = name.partition(",")
    return head.strip().lower(), cred.replace(".", "").replace(" ", "").lower()


#: The audited claim a near provider name becomes: the auditor reads the name
#: off the cited page image, and a repair writes the spelling the page shows.
CLAIM = re.compile(r"^Provider named on the record: (.+?)\. \((Exhibit \d+(?: - p\. [0-9,\s\-]+)?)\)\s*$")

#: A clinician named in text, either order ("Given [Middle] Surname, DO" or
#: "Surname, Given [Middle], DO"), nurse and doctorate degrees included.
_CRED = (
    r"M\.?D|D\.?O|D\.?C|P\.?T|N\.?P|PA-?C|LCSW|Au\.?D|R\.?N|L\.?P\.?N|M\.?S\.?N|B\.?S\.?N|F?NP-?C|"
    r"D\.?P\.?M|O\.?D|Ph\.?D|D\.?P\.?T"
)
_WORD = r"[A-Z][A-Za-z'-]+"
SPAN = re.compile(rf"\b((?:{_WORD},?[ \t]+(?:[A-Z]\.[ \t]+)?){{1,3}}{_WORD}),?[ \t]+((?:{_CRED})\.?)(?![A-Za-z])")
#: An Exhibit List title's provider: what precedes its dates or "(".
_TITLE_HEAD = re.compile(r" - \d{2}-\d{2}-\d{4}| -\s+\(| \(")


def _words(name: str) -> list[str]:
    """The name's words, its trailing credential set aside."""
    bare = re.sub(rf"[\s,]+(?:{_CRED})\.?\s*$", "", name or "")
    return re.findall(r"[A-Za-z][A-Za-z'-]+", bare)


def _cred(name: str) -> str:
    m = re.search(rf"(?:^|[\s,])((?:{_CRED})\.?)\s*$", name)
    return m.group(1).replace(".", "").lower() if m else ""


def names_in_text(text: str) -> set[str]:
    """Every clinician name with a credential the text states, as written."""
    return {f"{m.group(1)}, {m.group(2)}" for m in SPAN.finditer(text or "")}


def _near_word(a: str, b: str) -> bool:
    return a != b and len(a) >= MIN_WORD and len(b) >= MIN_WORD and a[0] == b[0] and edits(a, b) <= MAX_EDITS


def word_change(new: str, old: str) -> dict[str, str] | None:
    """{word in `new`: word in `old`} when `new` is `old` with one or two of
    its name words misspelled under the safe rule (each one or two edits,
    same first letter, at least five letters; every other word shared; the
    same credential), else None."""
    nw, ow = _words(new), _words(old)
    if not nw or len(nw) != len(ow) or _cred(new) != _cred(old):
        return None
    pairs = [(a, b) for a, b in zip(nw, ow) if a != b]
    if not pairs or len(pairs) == len(nw) or not all(_near_word(a, b) for a, b in pairs):
        return None
    return dict(pairs)


def _qualifies(new: str, old: str) -> bool:
    """`new` may be `old` misread: the same credential, a word shared, and
    each word `new` alone writes, where `old` writes a near word, near it
    under the safe rule (words in either order; a word only one side writes,
    such as a facility or a sentence's first word, decides nothing). The
    audit settles it; this only decides that it is audited."""
    if _cred(new) != _cred(old):
        return False
    nw, ow = set(_words(new)), set(_words(old))
    only_new, only_old = nw - ow, ow - nw
    pairs = [(a, b) for a in only_new for b in only_old if _near_word(a, b)]
    return bool(pairs) and bool(nw & ow)


def known_names(base_ents: list[str], titles: list[str] | None = None) -> list[str]:
    """Every provider the delivered chronology spells: its provider lines, the
    clinicians its entries' text names, and its Exhibit List titles. The
    correct spelling is often only in the text or a title (2026-10-13: a
    misread given name shipped in 16 added entries and an exhibit title while
    the delivered text spelled it right)."""
    got = {B.provider_of(b) for b in base_ents if B.provider_of(b)}
    for t in [*base_ents, *(titles or [])]:
        got |= names_in_text(t)
    for t in titles or []:
        head = _TITLE_HEAD.split(t)[0].strip()
        if head:
            got.add(head)
    return sorted(got)


def _names_in(e: str, name: str) -> bool:
    return B.provider_of(e) == name or name in names_in_text(e)


def claims(entries: list[str], log: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """(claim lines, {citation: composed name}) for each flagged name the
    safe rule marks likely the same person as a delivered one: its spelling
    is settled by the audit against the page, never by edit distance. A name
    an entry's text states is cited to the first page that entry cites."""
    lines: list[str] = []
    composed: dict[str, str] = {}
    for row in log:
        if not row.get("likely_same_person"):
            continue
        name = row["composed"]
        cite = next((m.group(0) for e in entries if _names_in(e, name) for m in B.CITE.finditer(e)), None)
        if cite is None or cite[1:-1] in composed:
            continue
        lines.append(f"Provider named on the record: {name}. {cite}")
        composed[cite[1:-1]] = name
    return lines, composed


def split_claims(pre: str) -> tuple[str, list[tuple[str, str]]]:
    """(the preamble without provider claims, [(audited name, ref)])."""
    keep: list[str] = []
    got: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", pre or ""):
        m = CLAIM.match(para.strip())
        if m:
            got.append((m.group(1).strip(), m.group(2)))
        elif para.strip().startswith("Provider named on the record"):
            continue  # a name claim a repair broke is dropped: the composed spelling stands
        elif para.strip():
            keep.append(para.strip("\n"))
    return "\n\n".join(keep), got


def swap(text: str, old: str, new: str) -> str:
    """`text` with the name `old` spelled `new` wherever a clinician name
    carrying one of its other words is written (provider line and text), and
    after "Dr.". Nothing else is touched: a word near a name is not a name."""
    change = word_change(old, new) or {}
    if not change:
        return text
    others = set(_words(old)) - set(change)

    def fix(span: str) -> str:
        if not (set(re.findall(r"[A-Za-z][A-Za-z'-]+", span)) & others):
            return span
        for a, b in change.items():
            span = re.sub(rf"\b{re.escape(a)}\b", b, span)
        return span

    text = SPAN.sub(lambda m: fix(m.group(0)), text)
    for a, b in change.items():
        text = re.sub(rf"\b(Dr\.?\s+){re.escape(a)}\b", lambda m, b=b: m.group(1) + b, text)
    return text


def respell(entries: list[str], audited: list[tuple[str, str]], composed: dict[str, str]) -> tuple[list[str], dict]:
    """New entries with each audited spelling applied, on the provider line
    and in the text, where the audit changed the composed one; {composed:
    confirmed} of what changed. Only a spelling fix under the safe rule is
    applied: an audited name that is another name is never applied."""
    change = {
        composed[ref]: name
        for name, ref in audited
        if ref in composed and composed[ref] != name and word_change(composed[ref], name)
    }
    out = []
    for e in entries:
        date, nl, rest = e.partition("\n")
        for old, new in change.items():
            rest = swap(rest, old, new)
        out.append(date + nl + rest)
    return out, change


def unspell(text: str, change: dict[str, str]) -> str:
    """`text` as the audit read it: each applied respelling undone."""
    for old, new in change.items():
        text = swap(text, new, old)
    return text


def _near(p: str, delivered: list[str]) -> tuple[list[str], list[str]]:
    near = [d for d in delivered if edits(_parts(p)[0], _parts(d)[0]) <= MAX_EDITS]
    likely = [d for d in delivered if _qualifies(p, d)]
    return sorted(set(near) | set(likely)), likely


def snap(
    entries: list[str], base_ents: list[str], titles: list[str] | None = None
) -> tuple[list[str], list[dict[str, Any]]]:
    """(entries, unchanged; a log row per name an added entry spells near a
    delivered one: its provider, or a clinician its text names)."""
    delivered = known_names(base_ents, titles)
    log: list[dict[str, Any]] = []
    for e in entries:
        seen: set[str] = set()
        for p in [B.provider_of(e), *sorted(names_in_text(e))]:
            if not p or p in delivered or p in seen:
                continue
            seen.add(p)
            near, likely = _near(p, delivered)
            if near:
                log.append(
                    {"date": B.iso(e), "composed": p, "near": near, "likely_same_person": likely, "action": "flagged"}
                )
    return list(entries), log
