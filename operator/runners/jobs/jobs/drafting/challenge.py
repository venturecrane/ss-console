"""The other side's meet-and-confer letter, read into challenges code has verified (design A, feature 4).

WHY (2026-11-23). After our served responses the other side writes a letter
naming the responses it says are deficient, and the attorney asks for amended
responses. Until now that request stopped for SMD (``served_amended``). The
letters are in the matter file as text (measured on one real matter: three letters, 2
and 3 pages, Word-generated PDFs; every one names numbers, as lists and ranges,
"Nos. 1-9", "1, 2, 4-8, 10", and none says "all"; the hand-final amended sets
carry exactly the numbers the letters name). So:

1. **Letters, by code** (:func:`letters`): a document in the pull that is not a
   set, not a response, not ours, dated on or after our served response, whose
   name or text reads as a meet and confer and whose text names a request
   number.
2. **One model call per letter** returns items ``{set_type, set_number,
   numbers, quote, page}``. Code keeps an item only when the quote is on that
   page of the letter, the numbers code reads in the quote (ranges expanded by
   code) are exactly the numbers returned, and every number is in the served set
   our response answered (:func:`verify`).
3. **Nothing named is missed**: every request-number mention code finds in the
   letter must sit inside a kept quote (:func:`uncovered`). One retry, naming
   what was missed; then the job stops ``challenge_unread`` before the amended
   sets are paid for. SMD resumes it with a hand ``challenge.json`` in the job
   directory (the override, like ``served.json``).

The memo (``meetconfer/``) consumes :func:`load`; it never reads a letter a
second way.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import served_papers as papers
from .outcome import DraftingFailed
from .served_docs import Doc
from .served_page import classify

FILE = "challenge.json"
#: The ledger's stage name for the letter reads.
STAGE = "challenge"
#: More candidate letters than this is not a meet and confer; SMD looks.
MAX_LETTERS = 4
#: One letter's text sent to the read, at most.
LETTER_CHARS = 30_000
MAX_TOKENS = 2_000

#: ``ask(system, user, max_tokens) -> text`` through the job's doorway.
Ask = Callable[[str, str, int], str]


class Unread(DraftingFailed):
    """``challenge_unread``: a letter code could not confirm, before the amended sets are paid for."""

    def __init__(self, why: str) -> None:
        super().__init__(f"challenge_unread: {why}; SMD writes {FILE} in the job directory and resumes")


# ---- request numbers in prose, read by code ------------------------------------------------

_LABEL = (
    r"(?:(?:special(?:ly\s+prepared)?|form)\s+)?interrogator(?:y|ies)"
    r"|requests?(?:\s+for\s+(?:the\s+)?(?:production|admissions?|inspection)(?:\s+of\s+documents)?)?"
    r"|demands?\s+for\s+(?:production|inspection)|rf[ap]d?s?|s?rogs?|frogs?|responses?\s+to"
)
_NOWORD = r"(?:\bnos?\.|\bnos\b|\bnumbers?\b|#)"
_NUM = r"\d{1,3}(?:\.\d{1,2})?(?!\d)(?:\s*\([a-z0-9]{1,3}\))?"
_ITEM = rf"{_NUM}(?:\s*(?:-|through|thru|to)\s*{_NUM})?"
_JOIN = rf"\s*(?:,\s*(?:and\s+|&\s*)?|\band\s+|&\s*)(?:{_NOWORD}\s*)?"
_LIST = rf"{_ITEM}(?:{_JOIN}{_ITEM})*"
#: "Special Interrogatory Nos. 1-9", "Request No. 8", "Nos. 1, 2, 4-8, 10", "12.1 and 14.2".
MENTION = re.compile(rf"(?i)(?:\b(?:{_LABEL})\s*(?:{_NOWORD})?\s*|{_NOWORD}\s*)(?P<list>{_LIST})")
_PIECE = re.compile(
    r"(?i)(\d{1,3}(?:\.\d{1,2})?)(?!\d)(?:\s*\([a-z0-9]{1,3}\))?(?:\s*(?:-|through|thru|to)\s*(\d{1,3}(?:\.\d{1,2})?)(?!\d))?"
)
_ALL = re.compile(r"(?i)\ball\s+(?:of\s+)?(?:(?:your|the|plaintiff'?s|defendant'?s)\s+)?(?:responses?|answers?)\b")
#: The longest range code writes out; past it the letter means something else.
MAX_RANGE = 200


def flat(s: str) -> str:
    """Whitespace, curly quotes and dashes made plain, case folded: how a quote is found on a page."""
    s = re.sub(r"[‘’ʼ]", "'", str(s or ""))
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"[‐-―−]", "-", s)
    return " ".join(s.split()).casefold()


def canon(n: Any) -> str:
    """A request number as one string: "04" is "4", "12.10" stays "12.10"."""
    parts = str(n).strip().split(".")
    return ".".join(str(int(p)) if p.isdigit() else p for p in parts)


def _key(n: str) -> tuple[int, ...]:
    return tuple(int(p) for p in n.split(".") if p.isdigit())


def _range(a: str, b: str, universe: tuple[str, ...]) -> list[str]:
    if "." not in a and "." not in b:
        lo, hi = int(a), int(b)
        return [str(i) for i in range(lo, hi + 1)] if lo <= hi and hi - lo <= MAX_RANGE else [a, b]
    # A dotted range ("12.1-12.3") is the served numbers between its ends, in the served set.
    lo, hi = _key(a), _key(b)
    got = [n for n in universe if lo <= _key(n) <= hi]
    return got if got else [a, b]


def expand(text: str, universe: tuple[str, ...] = ()) -> list[str]:
    """Every request number ``text`` names, ranges written out, in order, each once."""
    out: list[str] = []
    for m in MENTION.finditer(flat(text)):
        for p in _PIECE.finditer(m.group("list")):
            a, b = canon(p.group(1)), canon(p.group(2)) if p.group(2) else ""
            out += _range(a, b, universe) if b else [a]
    return list(dict.fromkeys(out))


def sort_numbers(nums: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(dict.fromkeys(nums), key=_key)


# ---- the letters, by code --------------------------------------------------------------------

_NAME = re.compile(r"(?i)M\s*&\s*C|meet|confer|deficien")
_TEXT = re.compile(r"(?i)meet\s+and\s+confer")
_WANTS = re.compile(
    r"(?i)further\s+responses?|code[- ]compliant|motion\s+to\s+compel|deficien|evasive|incomplete"
    r"|2030\.300|2031\.310|2033\.290"
)


def letters(all_docs: list[Doc], after: str | None) -> list[Doc]:
    """The other side's meet-and-confer letters in the pull, by code: not a set,
    not a response paper, not a file SMD wrote; its name or text reads as a meet
    and confer, its text asks for more, it names a request number, and it is
    dated on or after ``after`` (our earliest served response) when both dates read."""
    out = []
    for d in all_docs:
        if papers.ours_written(d.name, d.folder) or classify(d)[0] in ("served", "response"):
            continue
        if not ((_NAME.search(d.name) or _TEXT.search(d.text)) and _WANTS.search(d.text)):
            continue
        if not MENTION.search(flat(d.text)):
            continue
        if after and d.listed and d.listed < after:
            continue
        out.append(d)
    return out


# ---- one read per letter, and code's checks of it --------------------------------------------


@dataclass(frozen=True)
class Target:
    """A served set our served response answered: what a letter may challenge."""

    set_type: str
    set_number: int
    numbers: tuple[str, ...]


@dataclass(frozen=True)
class Item:
    set_type: str
    set_number: int
    numbers: tuple[str, ...]
    quote: str
    letter: str
    page: int


@dataclass
class Challenges:
    letters: list[dict[str, Any]] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def numbers_for(self, set_type: str, set_number: int) -> list[str]:
        got = [n for i in self.items if (i.set_type, i.set_number) == (set_type, set_number) for n in i.numbers]
        return sort_numbers(got)

    def quotes_for(self, set_type: str, set_number: int, number: str) -> list[Item]:
        return [i for i in self.items if (i.set_type, i.set_number) == (set_type, set_number) and number in i.numbers]


def system(targets: list[Target]) -> str:
    sets = "; ".join(f"{t.set_type} set {t.set_number} (numbers {', '.join(t.numbers[:80])})" for t in targets)
    return (
        "You read a meet-and-confer letter from opposing counsel about our client's served discovery responses. "
        'Return ONLY a JSON object {"items": [...]}, one item per passage of the letter that names a request '
        "number, including the opening and closing summaries and any number named in passing. Each item: "
        '"set_type" (one of ' + ", ".join(sorted({t.set_type for t in targets})) + '), "set_number" (a whole '
        'number, or null when the letter does not say), "numbers" (every request number the quote names, as '
        'strings, ranges written out in full: "Nos. 4-6" is ["4", "5", "6"]; ["all"] only when the quote says '
        'all of the responses), "quote" (the letter\'s own words naming those numbers and what it says is wrong, '
        'copied exactly, at most two sentences), "page" (the [p.N] page the quote is on). Our served responses '
        f"answer: {sets}. No other text."
    )


def _target(raw: dict[str, Any], targets: list[Target]) -> Target | None:
    n = raw.get("set_number")
    hits = [t for t in targets if t.set_type == raw.get("set_type") and (n is None or t.set_number == n)]
    return hits[0] if len(hits) == 1 else None


def _items(raw: str) -> list[dict[str, Any]] | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    items = got.get("items") if isinstance(got, dict) else None
    if not isinstance(items, list):
        return None
    keys = {"set_type", "set_number", "numbers", "quote", "page"}
    ok = [
        i
        for i in items
        if isinstance(i, dict)
        and set(i) == keys
        and isinstance(i["quote"], str)
        and isinstance(i["numbers"], list)
        and isinstance(i["page"], int)
        and (i["set_number"] is None or isinstance(i["set_number"], int))
    ]
    return ok if len(ok) == len(items) else None


def verify(got: list[dict[str, Any]], d: Doc, targets: list[Target]) -> tuple[list[Item], list[str]]:
    """The items code confirms against the letter, and why each other was refused."""
    kept: list[Item] = []
    why: list[str] = []
    for raw in got:
        t, quote, page = _target(raw, targets), raw["quote"].strip(), d.pages.get(raw["page"], "")
        said = f"{d.name!r} p.{raw['page']} {quote[:80]!r}"
        if t is None:
            why.append(f"{said}: names no one served set our responses answer")
            continue
        if not quote or flat(quote) not in flat(page):
            why.append(f"{said}: the quote is not on that page of the letter")
            continue
        returned = [canon(x) for x in raw["numbers"]]
        nums = expand(quote, t.numbers)
        if not nums and returned == ["all"] and _ALL.search(quote):
            nums, returned = list(t.numbers), list(t.numbers)
        if not nums or set(nums) != set(returned):
            why.append(f"{said}: the read names {returned[:20]}; the quote names {nums[:20]}")
            continue
        outside = [n for n in nums if n not in t.numbers]
        if outside:
            why.append(f"{said}: {', '.join(outside[:10])} not in the served set our response answered")
            continue
        kept.append(Item(t.set_type, t.set_number, tuple(sort_numbers(nums)), quote, d.name, int(raw["page"])))
    return kept, why


def uncovered(d: Doc, kept: list[Item]) -> list[str]:
    """Every request-number mention code finds in the letter that no kept quote holds."""
    out = []
    for n, page in sorted(d.pages.items()):
        text = flat(page)
        spans = []
        for it in kept:
            q = flat(it.quote)
            spans += [(m.start(), m.start() + len(q)) for m in re.finditer(re.escape(q), text)] if q else []
        for m in MENTION.finditer(text):
            if not any(a <= m.start() and m.end() <= b for a, b in spans):
                out.append(f"p.{n} {m.group(0)!r}")
    return out


def _letter_text(d: Doc) -> str:
    text = "\n".join(f"[p.{n}]\n{d.pages[n]}" for n in sorted(d.pages))
    return text[:LETTER_CHARS]


def read_letter(d: Doc, ask: Ask, targets: list[Target]) -> tuple[list[Item], list[str]]:
    """One letter: the read, code's checks, one retry naming what was missed;
    ``Unread`` when a mention is still uncovered or the read keeps no item."""
    note, refused, missed = "", [], []
    for _attempt in range(2):
        got = _items(ask(system(targets), _letter_text(d) + note, MAX_TOKENS))
        if got is None:
            refused, missed = ["the read did not return items in the closed shape"], ["(no items)"]
        else:
            kept, refused = verify(got, d, targets)
            missed = uncovered(d, kept) + ([] if kept else ["(no item confirmed)"])
            if not missed:
                return kept, refused
        note = "\n\nYOUR LAST READ MISSED OR MISREAD: " + "; ".join([*missed[:12], *refused[:6]])
    raise Unread(f"{d.name!r}: {'; '.join([*missed[:6], *refused[:4]])[:400]}")


def read(found: list[Doc], ask: Ask, targets: list[Target]) -> Challenges:
    """Every candidate letter read and verified."""
    if not found:
        raise Unread("no letter from the other side challenging our responses is in the matter file")
    if len(found) > MAX_LETTERS:
        raise Unread(f"{len(found)} letters read as a meet and confer; at most {MAX_LETTERS} are read")
    out = Challenges()
    for d in found:
        kept, refused = read_letter(d, ask, targets)
        out.items += kept
        out.rejected += refused
        out.letters.append({"id": d.id, "name": d.name, "pages": len(d.pages), "listed": d.listed})
    return out


# ---- on disk: code's file in data/, SMD's hand file in the job directory ----------------------


def save(data: Path, ch: Challenges) -> None:
    body = {"letters": ch.letters, "items": [asdict(i) for i in ch.items], "rejected": ch.rejected}
    tmp = Path(data) / f".{FILE}.tmp"
    tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
    tmp.replace(Path(data) / FILE)


def _parse(doc: Any, where: str) -> Challenges:
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list) or not doc["items"]:
        raise Unread(f"{where}: {FILE} must hold {{'items': [one or more]}}")
    keys = {f for f in Item.__dataclass_fields__}
    items = []
    for i, raw in enumerate(doc["items"], 1):
        if not isinstance(raw, dict) or set(raw) != keys:
            raise Unread(f"{where}: item {i} must have exactly the keys {sorted(keys)}")
        nums = tuple(sort_numbers([canon(n) for n in raw["numbers"]]))
        items.append(Item(str(raw["set_type"]), int(raw["set_number"]), nums, str(raw["quote"]), str(raw["letter"]),
                          int(raw["page"])))  # fmt: skip
    return Challenges(list(doc.get("letters") or []), items, list(doc.get("rejected") or []))


def load(data: Path, job_dir: Path | None = None) -> Challenges | None:
    """SMD's hand file when there, else code's; None when neither is written."""
    for p, where in ((Path(job_dir) / FILE if job_dir else None, "the hand file"), (Path(data) / FILE, "data")):
        if p is not None and p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                raise Unread(f"{where}: {FILE} is not JSON") from None
            return _parse(doc, where)
    return None


__all__ = [
    "Challenges",
    "Item",
    "MENTION",
    "Target",
    "Unread",
    "expand",
    "letters",
    "load",
    "read",
    "save",
    "uncovered",
    "verify",
]
