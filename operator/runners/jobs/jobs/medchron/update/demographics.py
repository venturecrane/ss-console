"""A composed entry carries no street address and no telephone number. $0.

The chronology is not a demographics sheet: an update once added entries
stating the client's home street address and phone number, read off an
intake page. The compose prompt forbids it (prompts/map-system.md "NO
patient demographics"); this is the deterministic floor under that rule,
applied where every composed entry is read (``collect.composed``), so the
entries collect keeps and the entries proof 10 measures against are scrubbed
alike and a removed house number is never counted as a lost value.

The entry's two header lines (date, then provider line) are kept as composed:
the base lane writes the provider line as compose wrote it, and a facility's
number there is the base lane's shape. In the body, a US street address or
telephone number is removed with the words that introduce it ("resides at",
"phone number"); a sentence left with nothing else to say goes, and a
paragraph left empty goes with its citation.
"""

from __future__ import annotations

import re

_SUFFIX = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Circle|Cir|"
    r"Parkway|Pkwy|Highway|Hwy|Trail|Trl|Terrace|Ter|Loop"
)
_CITY_ZIP = r"(?:,?\s+[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*){0,2},?\s+[A-Z]{2}\.?\s+\d{5}(?:-\d{4})?)?"
_UNIT = r"(?:,?\s*(?:Apt|Apartment|Unit|Suite|Ste|#)\.?\s*[\w-]+)?"
STREET = re.compile(
    rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Z0-9][A-Za-z0-9'.]*\s+){{1,4}}(?:{_SUFFIX})\b\.?{_UNIT}{_CITY_ZIP}"
)
PHONE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[.-])\d{3}[.-]\d{4}\b(?:\s*(?:ext\.?|x)\s*\d{1,5})?")
_LEAD = re.compile(
    r"(?:\b(?:who\s+)?(?:resides|resided|lives|lived|living|residing)\s+at|"
    r"\b(?:home\s+|mailing\s+|street\s+)?address(?:\s+(?:of|is|was|listed\s+as))?:?|"
    r"\b(?:(?:home|cell|mobile|work|contact|primary)\s+)?(?:phone|telephone|tel)(?:\s+(?:number|no\.?))?"
    r"(?:\s+(?:of|is|was|listed\s+as))?:?|\breached\s+at|\bat)\s*$",
    re.I,
)
CITE = re.compile(r"\s*\((?:Exhibit|FILE:)[^()]*\)\s*$")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_FILLER = frozenset("the a an and or of at is was were her his their patient patient's with to by as on in".split())


def _strip(sentence: str) -> str:
    """`sentence` with every address and phone number removed, each with the
    words that introduce it."""
    for rx in (STREET, PHONE):
        while m := rx.search(sentence):
            head = _LEAD.sub("", sentence[: m.start()]).rstrip()
            rest = sentence[m.end() :].lstrip()
            if head.endswith(",") and rest.startswith(","):  # an aside: "The patient, who resides at X, reported"
                rest = rest[1:].lstrip()
            rest = re.sub(r"^(?:,\s*)?(?:and|or)\s+", "", rest)  # "lives at X and walks daily"
            sentence = (head.rstrip(" ,;:") + " " + rest).strip()
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", sentence)).strip()


def _empty(sentence: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z']+", sentence.lower()) if w not in _FILLER]
    return len(words) < 3


def scrub_line(line: str) -> str:
    """One body line without addresses or phone numbers; '' when nothing of
    substance is left (its citation goes with it)."""
    if not (STREET.search(line) or PHONE.search(line)):
        return line
    m = CITE.search(line)
    cite = m.group().strip() if m else ""
    body = line[: m.start()] if m else line
    kept = []
    for s in _SENT.split(body.strip()):
        t = _strip(s)
        if not _empty(t):
            kept.append(t if t.endswith((".", "!", "?")) else t + ".")
    if not kept:
        return ""
    return " ".join(kept) + (" " + cite if cite else "")


def scrub(entry: str) -> str:
    """`entry` with its body scrubbed; the date and provider lines as composed.
    A heading whose only paragraph went is dropped with it."""
    lines = entry.split("\n")
    head, body = lines[:2], lines[2:]
    if not any(STREET.search(ln) or PHONE.search(ln) for ln in body):
        return entry
    out: list[str] = []
    for i, ln in enumerate(body):
        new = scrub_line(ln)
        if new or not ln.strip():
            out.append(new)
            continue
        prev = next((k for k in range(len(out) - 1, -1, -1) if out[k].strip()), None)
        nxt = next((x for x in body[i + 1 :] if x.strip()), "")
        if prev is not None and not CITE.search(out[prev]) and not CITE.search(nxt):
            del out[prev]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(head + out)).rstrip("\n")
