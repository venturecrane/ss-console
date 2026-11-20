"""Is a person's own reply a plain yes, or a pick? A grammar over the WHOLE text.

THE RULE (2026-11-20, approved redesign). After quoted history is removed, the
sender's own text, line by line (blank lines dropped, whitespace, case and curly
quotes folded), must be exactly

    [G] A+ [C [N]] [B] [F]

and nothing else:

* G: one greeting line (:data:`GREETINGS`, optionally with a name);
* A: one or more answer lines, each wholly :data:`AFFIRMATIVES` and
  :data:`COURTESIES` (his roster first name or the Operator's persona name may
  sit in them); for a pick, each wholly pick tokens ("1 and 3", "all");
* C: one closing line (:data:`CLOSINGS`), optionally with his name on it;
* N: one line that is his name (roster first name, or first and last);
* B: HIS learned signature block, whole, in order, nothing between
  (``workspace_broker/sender_block``: the run of lines that ends his own emails
  identically, learned from his mailbox, never from the reply being judged);
* F: one device footer (:data:`DEVICE_FOOTERS`), last.

WHY A GRAMMAR AND NOT LINE SHAPES. The first build read each line by its shape
(a firm line, a street, a phone, a notice) and skipped the signature-shaped ones.
Every review found an instruction written in such a shape ("Only For Craig LLC",
"12 Point Font Avenue", "Confidentiality Notice: actually make it 12pt") and a
real signature the shapes missed. There are no shapes here: a line is part of a
plain yes only if it is one of the closed lists above or one of his own block's
lines, in its place, with the whole block present. Anything else is not a plain
yes, and the rule is read back again (at most once: ``readback_rounds``).

Pure functions; no I/O. :mod:`shared.rule_confirm_reply` is the caller.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from shared.inbound import UNTRUSTED_EMAIL_DELIMITER

#: Plain agreement (``shared.rule_confirm.BARE_AFFIRMATIVES``, copied so this
#: module imports nothing of that one; ``test_rule_reply_shapes`` pins the two).
AFFIRMATIVES: tuple[str, ...] = (
    "yes please", "yes", "yep", "yeah", "correct", "that's right", "thats right",
    "confirmed", "confirm", "go ahead", "do it", "okay", "ok", "sounds good", "that works",
)  # fmt: skip
#: Words that may sit beside an affirmative in an answer line and change nothing.
COURTESIES: tuple[str, ...] = (
    "thanks", "thank you", "please", "great", "perfect", "and", "thx", "ty", "cool",
)  # fmt: skip
#: A greeting that may open the reply.
GREETINGS: tuple[str, ...] = (
    "hi", "hello", "hey", "dear", "good morning", "good afternoon", "good evening",
)  # fmt: skip
#: A closing courtesy, alone on its line or followed by the sender's own name.
CLOSINGS: tuple[str, ...] = (
    "cordially", "sincerely", "sincerely yours", "regards", "best regards", "kind regards",
    "warm regards", "warmest regards", "respectfully", "respectfully yours", "best",
    "all the best", "best wishes", "thanks", "thank you", "thanks again", "thanks so much",
    "thank you so much", "many thanks", "cheers", "very truly yours", "yours truly",
    "truly yours", "thx", "ty",
)  # fmt: skip
#: Mail-client footers, each a whole line.
DEVICE_FOOTERS: tuple[str, ...] = (
    "sent from my iphone", "sent from my ipad", "sent from my android", "sent from outlook",
    "sent from outlook for ios", "sent from outlook for android", "get outlook for ios",
    "get outlook for android", "sent from mail for windows",
)  # fmt: skip
_NAME_SUFFIX = frozenset({"jr", "sr", "ii", "iii", "iv", "esq"})
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}  # fmt: skip
_PICK_FILLER = frozenset(
    {
        "yes", "ok", "okay", "to", "please", "confirm", "confirmed", "and", "&", "number",
        "numbers", "rule", "rules", "item", "items", "of", "them", "the", "thanks",
        "thank", "you", "go", "ahead", "only", "just", "plus", "one", "ones",
    }
)  # fmt: skip
_PICK_ALL = frozenset({"all", "both", "every", "everything"})
#: A clause that asks for confirmation rather than giving it.
_REQUEST = re.compile(r"\A(?:please|pls|kindly|can you|could you) (?:confirm|go ahead|do it)\b")
#: Gmail / Apple "On <date> ... wrote:" (an address, a year or a clock time in it,
#: so "On second thought as Craig wrote: ..." is the person's own words), and the
#: original/forwarded bars.
_QUOTE_OPEN = re.compile(
    r"\A\s*(?:on\b(?=.{0,240}(?:<[^>]*@|\b\d{4}\b|\b\d{1,2}:\d{2}\b)).{0,240}\bwrote:\s*\Z"
    r"|-{2,}\s*original message|-{2,}\s*forwarded message)",
    re.IGNORECASE,
)
_BAR = re.compile(r"\A\s*_{5,}\s*\Z")
_OUTLOOK_FROM = re.compile(r"\A\s*from:\s", re.IGNORECASE)
_OUTLOOK_META = re.compile(r"\A\s*(?:sent|date|to|cc|subject):\s", re.IGNORECASE)


def norm(line: str) -> str:
    """One line in the form blocks compare in (``workspace_broker.sender_block.norm_line``,
    the same rule; ``test_rule_reply_shapes`` pins the two)."""
    text = str(line).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().lower()


def _outlook_header(lines: list[str], i: int) -> bool:
    return bool(_OUTLOOK_FROM.match(lines[i])) and any(
        _OUTLOOK_META.match(n) for n in lines[i + 1 : i + 4]
    )


def _opens_quote(lines: list[str], i: int) -> bool:
    """Does quoted history begin at ``lines[i]``? A Gmail attribution wrapped
    over two lines counts ("On Tue ... Riley <" / "ops@x> wrote:")."""
    line = lines[i]
    if _QUOTE_OPEN.match(line) or _outlook_header(lines, i):
        return True
    if i + 1 < len(lines) and re.match(r"\A\s*on\b", line, re.IGNORECASE):
        if _QUOTE_OPEN.match(f"{line.rstrip()} {lines[i + 1].strip()}"):
            return True
    return bool(_BAR.match(line)) and any(
        _outlook_header(lines, j) for j in range(i + 1, min(i + 3, len(lines)))
    )


def own_lines(message: Any) -> list[str]:
    """The sender's own lines, original case: below the untrusted-body fence,
    above the quoted history, ``>`` lines dropped."""
    if not isinstance(message, str) or not message:
        return []
    cut = message.find(UNTRUSTED_EMAIL_DELIMITER)
    if cut >= 0:
        end = message.find("\n", cut)
        message = "" if end < 0 else message[end + 1 :]
    lines = message.splitlines()
    own: list[str] = []
    for i, line in enumerate(lines):
        if _opens_quote(lines, i):
            break
        if not line.lstrip().startswith(">"):
            own.append(line.rstrip())
    return own


def own_normal(message: Any) -> list[str]:
    """:func:`own_lines`, non-blank, in :func:`norm` form."""
    return [n for n in (norm(line) for line in own_lines(message)) if n]


@dataclass(frozen=True)
class People:
    """Whose names a reply may carry, and his learned signature block."""

    first: str = ""
    last: str = ""
    words: frozenset[str] = frozenset()
    addressed: frozenset[str] = frozenset()
    #: His learned block (``workspace_broker/sender_block``), in :func:`norm` form.
    block: tuple[str, ...] = ()


def people(message: Any, addressed: Any = (), block: Any = (), full_name: Any = "") -> People:
    """The sender's name: ``full_name`` (the firm's authored roster name for his
    address) when there is one, else the envelope's ``from:`` line above the fence
    (code-rendered, never the body; on the Graph lane the bare address, so only a
    first name). The names he may address the Operator by (``addressed``, and a
    ``to:`` line), and his learned ``block``."""
    fence = message.find(UNTRUSTED_EMAIL_DELIMITER) if isinstance(message, str) else -1
    head = message[:fence] if isinstance(message, str) and fence >= 0 else ""

    def _header(name: str) -> tuple[list[str], str]:
        found = re.search(rf"(?im)^{name}:\s*(.+)$", head)
        if not found:
            return [], ""
        display, address = parseaddr(found.group(1).strip())
        return [w for w in re.split(r"[^a-z]+", display.lower()) if w], address.lower()

    display, address = _header("from")
    authored = [
        w for w in re.split(r"[^a-z]+", str(full_name or "").lower()) if w and w not in _NAME_SUFFIX
    ]
    if authored:
        display = authored
    if not display and address:
        display = [w for w in re.split(r"[^a-z]+", address.split("@")[0]) if len(w) > 1]
    to_words, _to = _header("to")
    named = {
        w for n in addressed if isinstance(n, str) for w in re.split(r"[^a-z]+", n.lower()) if w
    }
    first = display[0] if display else ""
    last = display[-1] if len(display) > 1 else ""
    blocked = tuple(n for n in (norm(s) for s in block if isinstance(s, str)) if n)
    return People(first, last, frozenset(display), frozenset({*named, *to_words}), blocked)


def _consume(text: str, phrases: tuple[str, ...], names: frozenset[str]) -> tuple[bool, bool]:
    """``(whole, saw_affirmative)``: is ``text`` only these phrases and names?"""
    ordered = sorted({*phrases, *COURTESIES}, key=len, reverse=True)
    rest, saw = text.strip(), False
    while rest:
        word = rest.split(" ", 1)[0]
        if word in names:
            rest = rest[len(word) :].strip()
            continue
        for phrase in ordered:
            if rest == phrase or rest.startswith(phrase + " "):
                saw = saw or phrase in phrases
                rest = rest[len(phrase) :].strip()
                break
        else:
            return False, saw
    return True, saw


def _drop_greeting(text: str, names: frozenset[str]) -> str:
    for greeting in sorted(GREETINGS, key=len, reverse=True):
        if text == greeting or text.startswith(greeting + " ") or text.startswith(greeting + ","):
            rest = text[len(greeting) :].lstrip(" ,")
            while rest and rest.split(" ", 1)[0].strip(",.!") in names:
                rest = rest.split(" ", 1)[1] if " " in rest else ""
                rest = rest.lstrip(" ,")
            return rest
    return text


def _clauses(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,.;:!/]+|\s[-–—]+\s?|\A[-–—]+", line) if c.strip()]


def _count_phrases(count: int | None) -> tuple[str, ...]:
    if count == 2:
        return ("to both", "both", "both of them")
    if count == 3:
        return ("to all three", "all three", "to all", "all of them")
    if count is not None and count > 3:
        return ("to all", "all of them")
    return ()


def affirmative_line(line: str, who: People, count: int | None) -> bool | None:
    """True: an answer line (A). False: a greeting or a courtesy only. None:
    anything else. ``count`` is how many rules the read-back answered carried."""
    if "?" in line:
        return None
    phrases = AFFIRMATIVES + _count_phrases(count)
    names = who.words | who.addressed
    saw_any = False
    for index, clause in enumerate(_clauses(line)):
        if index == 0:
            clause = _drop_greeting(clause, names)
            if not clause:
                continue
        if _REQUEST.match(clause):
            return None  # "Please confirm." asks US to confirm: not his yes
        whole, saw = _consume(clause, phrases, names)
        if not whole:
            return None
        saw_any = saw_any or saw
    return saw_any


def _name_line(line: str, who: People) -> bool:
    """N: his name exactly (first, or first and last; one middle initial,
    "/s/", a suffix or "Esq." allowed)."""
    words = [
        w
        for w in re.split(r"[^a-z]+", re.sub(r"\A/s/\s*", "", line))
        if w and w not in _NAME_SUFFIX
    ]
    if not words or not who.first or words[0] != who.first:
        return False
    if len(words) == 1:
        return True
    return (
        bool(who.last)
        and words[-1] == who.last
        and (len(words) == 2 or (len(words) == 3 and len(words[1]) == 1))
    )


def _closing_line(line: str, who: People) -> bool:
    """C: a closing, optionally with his name on the same line."""
    text = " ".join(re.sub(r"[,.!\-]+", " ", line).split())
    for closing in sorted(CLOSINGS, key=len, reverse=True):
        if text == closing:
            return True
        if text.startswith(closing + " ") and _name_line(text[len(closing) :].strip(), who):
            return True
    return False


def _greeting_line(line: str, who: People) -> bool:
    text = " ".join(re.sub(r"[,.!]+", " ", line).split())
    return (
        bool(text)
        and any(text == g or text.startswith(g + " ") for g in GREETINGS)
        and _drop_greeting(text, who.words | who.addressed) == ""
    )


def _tail_ok(lines: list[str], who: People) -> bool:
    """``[C [N]] [B] [F]`` covers ``lines`` exactly. Every reading is tried, so
    a block that starts with his name after a closing is read as C B, not as
    C N and then a block missing its first line (review 2026-11-20)."""
    starts = [0]
    if lines and _closing_line(lines[0], who):
        starts.append(1)
        if len(lines) > 1 and _name_line(lines[1], who):
            starts.append(2)
    return any(_block_footer_ok(lines, i, list(who.block)) for i in starts)


def _block_footer_ok(lines: list[str], i: int, block: list[str]) -> bool:
    """``[B] [F]`` covers ``lines[i:]`` exactly."""
    ends = [i]
    if block and lines[i : i + len(block)] == block:
        ends.append(i + len(block))
    n = len(lines)
    return any(j == n or (j == n - 1 and lines[j] in DEVICE_FOOTERS) for j in ends)


def _parse(message: Any, who: People, head_ok: Callable[[list[str]], bool]) -> list[str] | None:
    """The A lines when the whole own text is ``[G] A+ [C [N]] [B] [F]`` with A
    judged by ``head_ok``; ``None`` otherwise. The shortest A that leaves a valid
    tail wins, so a closing is never read as an answer."""
    lines = own_normal(message)
    if lines and _greeting_line(lines[0], who):
        lines = lines[1:]
    for split in range(1, len(lines) + 1):
        head, tail = lines[:split], lines[split:]
        if _tail_ok(tail, who) and head_ok(head):
            return head
    return None


def strip_tags(message: Any, pattern: re.Pattern[str]) -> Any:
    """``message`` with every ``pattern`` match removed from it (the tag path
    reads "[rule A] yes and [rule B] yes" as "yes and yes")."""
    return pattern.sub(" ", message) if isinstance(message, str) else message


def is_pure_yes(message: Any, who: People, count: int | None = None) -> bool:
    """The whole own text is ``[G] A+ [C [N]] [B] [F]`` with every A an answer line."""

    def answers(head: list[str]) -> bool:
        return all(affirmative_line(a, who, count) for a in head)

    return _parse(message, who, answers) is not None


def _pick_numbers(head: list[str], who: People, listed: int) -> tuple[int, ...] | None:
    if any("?" in a for a in head):
        return None
    tokens = " ".join(head).replace("#", " ")
    # "1-3", "1 to 3", "1 through 3" are ranges: never "1 and 3".
    tokens = re.sub(r"(\d+)\s*(?:[-–]|\bto\b|\bthrough\b|\bthru\b)\s*(\d+)", r"\1to\2", tokens)
    picked: list[int] = []
    saw_all = saw_none = False
    for token in re.sub(r"[,.;:!/]+", " ", tokens).split():
        token = re.sub(r"(?<=\d)(?:st|nd|rd|th)\Z", "", token)
        span = re.fullmatch(r"(\d+)to(\d+)", token)
        if span and int(span.group(1)) <= int(span.group(2)) <= 50:
            numbers = range(int(span.group(1)), int(span.group(2)) + 1)
        elif token.isdigit():
            numbers = range(int(token), int(token) + 1)
        elif token in _ORDINALS:
            numbers = range(_ORDINALS[token], _ORDINALS[token] + 1)
        else:
            numbers = range(0)
        if numbers:
            picked.extend(n for n in numbers if n not in picked)
        elif token in _PICK_ALL:
            if token == "both" and listed != 2:
                return None
            saw_all = True
        elif token == "none":
            saw_none = True
        elif token not in _PICK_FILLER and token not in (who.words | who.addressed):
            return None
    if saw_none:
        return () if not picked and not saw_all else None
    if saw_all:
        return tuple(range(1, listed + 1)) if not picked else None
    return tuple(picked) if picked else None


def pick(message: Any, who: People, listed: int) -> tuple[int, ...] | None:
    """The numbers a reply picked from a list of ``listed``, in order; ``()`` for
    "none"; ``None`` when the whole own text is not ``[G] P+ [C [N]] [B] [F]``."""
    found: list[tuple[int, ...] | None] = [None]

    def picks(head: list[str]) -> bool:
        found[0] = _pick_numbers(head, who, listed)
        return found[0] is not None

    return found[0] if _parse(message, who, picks) is not None else None


def looks_like_pick(message: Any, who: People) -> bool:
    """Did the reply TRY to pick ("both" on a list of three, "one", "1 only for
    Craig")? Such a reply gets the list again (within the read-back cap)."""
    tokens = re.sub(r"[,.;:!/#?]+", " ", " ".join(own_normal(message))).split()
    pick_words = {*_ORDINALS, *_PICK_ALL, "one", "none", "number", "numbers"}
    return any(re.match(r"\d{1,2}(?:\D|\Z)", t) or t in pick_words for t in tokens)


__all__ = [
    "AFFIRMATIVES",
    "CLOSINGS",
    "COURTESIES",
    "DEVICE_FOOTERS",
    "GREETINGS",
    "People",
    "affirmative_line",
    "is_pure_yes",
    "looks_like_pick",
    "norm",
    "own_lines",
    "own_normal",
    "people",
    "pick",
    "strip_tags",
]
