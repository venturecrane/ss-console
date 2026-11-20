"""What shape a person's own reply has: a plain yes, a pick, or anything else.

WHY THIS IS A WHITELIST (2026-11-20). The earlier reading found the answer line,
then cut the text at the first closing line ("Thanks,", "Best,") or device
footer and ignored everything under it unless it carried a word from a list of
qualifiers. Every review found a line that list did not name:
"Yes / Thanks, / Actually make that 12pt", "Yes / Best, / Chris / P.S. just
Craig's cases", "Yes / Sent from my iPhone / Make it 50". Each confirmed a rule
the person had just changed. A list of words that change an answer can never be
complete, so this module has none.

Instead EVERY line of the sender's own text (quoted history removed, nothing
else removed) must be one of a closed set of shapes, each defined by what it
IS, never by what it lacks:

* an ANSWER line: a plain yes (:data:`AFFIRMATIVES`, :data:`COURTESIES`, the
  connective "and", the sender's own name or the name the email addressed, a
  leading greeting), or, for a numbered list, a pick ("1 and 3", "all");
* a SAFE line, from closed lists: a GREETING or a CLOSING courtesy
  (:data:`GREETINGS`, :data:`CLOSINGS`), the sender's own name exactly (first,
  or first and last, with at most a single middle initial, "/s/", a suffix or
  "Esq."), a device footer (:data:`DEVICE_FOOTERS`), the RFC 3676 separator;
* an ANCHORED signature line: signature-SHAPED (a fuller name, a job title
  from :data:`TITLES`, a firm-name line, a street, suite or city-state-zip
  line, a labelled phone or fax line, an email or web address, a line of a
  confidentiality notice) AND found verbatim, whitespace and case aside, in
  the quoted history of this same reply or in the signature this seat already
  recorded for this sender from an earlier email of his (``People.anchors``).

WHY ANCHORED (review 2026-11-20). A shape alone admits an instruction written
to look like one: "Only For Craig LLC", "12 Point Font Avenue", "Confidentiality
Notice: actually make it 12pt". A signature is not a shape, it is the same lines
the person sends every time (the firm's attorney: 34 emails, one identical
8-line block), so a line is skipped as signature only when it is one of those
lines. A first email from an unknown sender is simply read back once more.

Any other line, anywhere, means the reply is not a plain yes and not a pick.
Unknown content is never ignored; the cost is one more question, and the rule is
read back again.

Pure functions; no I/O. :mod:`shared.rule_confirm_reply` is the caller.
"""

from __future__ import annotations

import re
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
COURTESIES: tuple[str, ...] = ("thanks", "thank you", "please", "great", "perfect", "and")
#: A greeting that may open the answer, or stand on its own line.
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
#: Job titles a signature carries.
TITLES: tuple[str, ...] = (
    "attorney", "attorney at law", "partner", "associate", "paralegal", "legal assistant",
    "of counsel", "managing partner", "office manager",
)  # fmt: skip
#: Mail-client footers, each a whole line.
DEVICE_FOOTERS: tuple[str, ...] = (
    "sent from my iphone", "sent from my ipad", "sent from my android", "sent from outlook",
    "sent from outlook for ios", "sent from outlook for android", "get outlook for ios",
    "get outlook for android", "sent from mail for windows",
)  # fmt: skip
#: How a confidentiality notice opens. Its lines are skipped only while each is
#: anchored (see the module docstring); an unanchored one is content.
_NOTICE_OPEN = re.compile(
    r"\A(?:confidentiality notice|confidential(?:ity)? and privilege(?:d| notice)"
    r"|privileged (?:and|&) confidential|privileged communication)\b"
)
#: Legal-entity endings of a firm-name line.
_FIRM_SUFFIX = re.compile(
    r"(?:,?\s(?:llp|l\.l\.p\.|llc|l\.l\.c\.|pllc|p\.l\.l\.c\.|pc|p\.c\.|apc|a\.p\.c\.|plc"
    r"|p\.l\.c\.|lp|l\.p\.|pa|p\.a\.|ltd|chartered)\.?"
    r"|\slaw group|\slaw firm|\slaw offices?|\s(?:&|and) associates)\Z"
)
_FIRM_JOINERS = frozenset({"&", "and", "of", "the"})
_STREET_WORDS = (
    "st", "street", "ave", "avenue", "rd", "road", "blvd", "boulevard", "dr", "drive", "ln",
    "lane", "way", "ct", "court", "pl", "place", "pkwy", "parkway", "hwy", "highway", "cir",
    "circle", "ter", "terrace", "trl", "trail", "sq", "square", "plaza", "center", "centre",
)  # fmt: skip
_STREET = re.compile(
    r"\A\d{1,6}[a-z]?(?: (?:[nsew]|north|south|east|west)\.?)?((?: [a-z0-9.']+){1,5})"
    r" (?:" + "|".join(_STREET_WORDS) + r")\.?"
    r"(?:,? (?:suite|ste\.?|unit|floor|fl\.?|#) ?#?[a-z0-9-]{1,6})?\Z"
)
_SUITE = re.compile(
    r"\A(?:(?:suite|ste\.?|unit|floor|fl\.?) ?#?[a-z0-9-]{1,6}|\d{1,3}(?:st|nd|rd|th) floor)\Z"
)
_CITY = re.compile(
    r"\A[a-z][a-z .']{1,40}, ?(?:[a-z]{2}|[a-z]{4,14}(?: [a-z]{4,10})?) \d{5}(?:-\d{4})?\Z"
)
_PHONE_LABEL = (
    r"(?:office|phone|tel|telephone|t|o|direct|direct dial|d|cell|mobile|m|c|fax|f|main|ph|p)"
)
_PHONE = re.compile(
    rf"\A(?:{_PHONE_LABEL}\.?:? ?)?\+?[\d().\- ]{{7,20}}(?: (?:x|ext\.?) ?\d{{1,6}})?\Z"
)
_ADDRESS = re.compile(
    r"\A(?:(?:e-?mail|e|web|w|website)\.?:? ?)?"
    r"(?:[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"
    r"|(?:https?://|www\.)[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?"
    r"|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|law|legal|lawyer|attorney|us|biz|info|io|co|example))\Z"
)
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
#: The Gmail / Apple header that opens quoted history: "On <date> ... wrote:",
#: carrying an address or a year or a clock time ("On second thought as Craig
#: wrote: make it 14pt" is the person's own words), and the bars clients draw.
_QUOTE_OPEN = re.compile(
    r"\A\s*(?:on\b(?=.{0,200}(?:<[^>]*@|\b\d{4}\b|\b\d{1,2}:\d{2}\b)).{0,200}\bwrote:\s*\Z"
    r"|-{2,}\s*original message|-{2,}\s*forwarded message)",
    re.IGNORECASE,
)
#: Outlook's bar above its header block: a quote opener only when a From:
#: header follows it within three lines.
_BAR = re.compile(r"\A\s*_{5,}\s*\Z")
#: An Outlook header block: ``From:`` then, within a few lines, another header.
_OUTLOOK_FROM = re.compile(r"\A\s*from:\s", re.IGNORECASE)
_OUTLOOK_META = re.compile(r"\A\s*(?:sent|date|to|cc|subject):\s", re.IGNORECASE)


def _norm(line: str) -> str:
    text = line.replace("’", "'").replace("‘", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _outlook_header(lines: list[str], i: int) -> bool:
    """``lines[i]`` opens an Outlook header block (``From:`` then another header)."""
    return bool(_OUTLOOK_FROM.match(lines[i])) and any(
        _OUTLOOK_META.match(n) for n in lines[i + 1 : i + 4]
    )


def _split(message: Any) -> tuple[list[str], list[str]]:
    """``(own lines, quoted lines)`` of the body below the untrusted-body fence.

    Quoted history begins only at a line that IS a quote header: Gmail's
    "On <date> ... wrote:", a forwarded or original-message bar, an Outlook
    ``From:`` line followed within three lines by another header, or the bar
    Outlook draws right above one. A bare "From: now on ..." is the person's own
    words, and stays. ``>`` lines anywhere are quoted."""
    if not isinstance(message, str) or not message:
        return [], []
    cut = message.find(UNTRUSTED_EMAIL_DELIMITER)
    if cut >= 0:
        end = message.find("\n", cut)
        message = "" if end < 0 else message[end + 1 :]
    lines = message.splitlines()
    own: list[str] = []
    quoted: list[str] = []
    for i, line in enumerate(lines):
        bar = _BAR.match(line) and any(
            _outlook_header(lines, j) for j in range(i + 1, i + 3) if j < len(lines)
        )
        if _QUOTE_OPEN.match(line) or bar or _outlook_header(lines, i):
            quoted.extend(lines[i:])
            break
        if line.lstrip().startswith(">"):
            quoted.append(line)
            continue
        own.append(line.rstrip())
    return own, quoted


def own_lines(message: Any) -> list[str]:
    """The sender's own lines, original case, in order (:func:`_split`)."""
    return _split(message)[0]


def _anchor_form(line: str) -> str:
    """A line as an anchor compares it: quote marks off, whitespace and case folded."""
    return _norm(re.sub(r"\A[\s>]+", "", line))


def quoted_anchors(message: Any) -> frozenset[str]:
    """Every non-blank line of this reply's quoted history, in anchor form."""
    return frozenset(a for a in (_anchor_form(q) for q in _split(message)[1]) if a)


@dataclass(frozen=True)
class People:
    """Whose names a reply may carry: the sender's, and the one it addressed."""

    first: str = ""
    last: str = ""
    words: frozenset[str] = frozenset()
    addressed: frozenset[str] = frozenset()
    firm: tuple[str, ...] = ()
    #: The signature lines this seat recorded for the sender from an earlier
    #: email of his, in anchor form (``readback_bindings`` keeps them).
    anchors: frozenset[str] = frozenset()


def people(message: Any, addressed: Any = (), firm: Any = (), signature: Any = ()) -> People:
    """The sender's name from the envelope's ``from:`` line above the fence
    (code-rendered, never the body), the names the person may address the
    Operator by (``addressed``, plus a ``to:`` line when the prompt has one), the
    seat's firm names (``firm``), and the signature lines recorded for him
    (``signature``)."""
    fence = message.find(UNTRUSTED_EMAIL_DELIMITER) if isinstance(message, str) else -1
    head = message[:fence] if isinstance(message, str) and fence >= 0 else ""

    def _header(name: str) -> tuple[list[str], str]:
        found = re.search(rf"(?im)^{name}:\s*(.+)$", head)
        if not found:
            return [], ""
        display, address = parseaddr(found.group(1).strip())
        return [w for w in re.split(r"[^a-z]+", display.lower()) if w], address.lower()

    display, address = _header("from")
    if not display and address:
        display = [w for w in re.split(r"[^a-z]+", address.split("@")[0]) if len(w) > 1]
    to_words, _to = _header("to")
    named = {
        w for n in addressed if isinstance(n, str) for w in re.split(r"[^a-z]+", n.lower()) if w
    }
    first = display[0] if display else ""
    last = display[-1] if len(display) > 1 else ""
    initials = "".join(w[0] for w in display) if len(display) > 1 else ""
    words = frozenset({*display, *([initials] if initials else [])})
    firms = tuple(_norm(f) for f in firm if isinstance(f, str) and f.strip())
    anchors = frozenset(_anchor_form(s) for s in signature if isinstance(s, str) and s.strip())
    return People(first, last, words, frozenset({*named, *to_words}), firms, anchors)


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


def affirmative_line(line: str, who: People, count: int | None) -> bool | None:
    """True: a plain-yes line. False: a courtesy or greeting only (no answer).
    None: anything else. ``count`` is how many rules the read-back answered
    carried, so "to both" agrees only with a read-back of two."""
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


#: A clause that asks for confirmation rather than giving it.
_REQUEST = re.compile(r"\A(?:please|pls|kindly|can you|could you) (?:confirm|go ahead|do it)\b")


def _count_phrases(count: int | None) -> tuple[str, ...]:
    if count == 2:
        return ("to both", "both", "both of them")
    if count == 3:
        return ("to all three", "all three", "to all", "all of them")
    if count is not None and count > 3:
        return ("to all", "all of them")
    return ()


def _name_words(line: str) -> list[str]:
    words = [w for w in re.split(r"[^a-z]+", re.sub(r"\A/s/\s*", "", line)) if w]
    return [w for w in words if w not in _NAME_SUFFIX]


def _name_line(line: str, who: People) -> bool:
    """The sender's own name EXACTLY, safe without an anchor: the display name's
    first token, or its first and last with at most one middle initial (a single
    letter, with or without a period), "/s/", a suffix or "Esq.". Initials of the
    display name ("PQD") too. Anything fuller is an anchored line."""
    words = _name_words(line)
    if not words or not who.first:
        return False
    if len(words) == 1:
        initials = (
            len(words[0]) > 1 and words[0] in who.words and words[0] not in (who.first, who.last)
        )
        return words[0] == who.first or initials
    if words[0] != who.first or not who.last or words[-1] != who.last:
        return False
    return len(words) == 2 or (len(words) == 3 and len(words[1]) == 1)


def _fuller_name_line(line: str, who: People) -> bool:
    """A name SHAPE that needs its anchor: "Patrick Q. Doe" for a display name of
    "Pat Doe" (a nickname either way, one middle name or initial)."""
    words = _name_words(line)
    if not who.first or not 1 <= len(words) <= 4:
        return False
    short, long_ = sorted((words[0], who.first), key=len)
    first_ok = len(short) >= 3 and long_.startswith(short)
    return first_ok and (len(words) == 1 or (bool(who.last) and words[-1] == who.last))


def _closing_line(line: str, who: People) -> bool:
    text = re.sub(r"[,.!\-]+", " ", line).strip()
    text = " ".join(text.split())
    for closing in sorted(CLOSINGS, key=len, reverse=True):
        if text == closing:
            return True
        if text.startswith(closing + " "):
            return _name_line(text[len(closing) :].strip(), who)
    return False


def _greeting_line(line: str, who: People) -> bool:
    text = " ".join(re.sub(r"[,.!]+", " ", line).split())
    return any(text == g for g in GREETINGS) or (
        _drop_greeting(text, who.words | who.addressed) == "" and text.split(" ", 1)[0] in GREETINGS
    )


def _title_line(line: str) -> bool:
    parts = [" ".join(p.split()) for p in re.split(r"[|,;]", re.sub(r"[.]", "", line))]
    parts = [p for p in parts if p]
    return bool(parts) and all(p in TITLES for p in parts)


def _firm_line(raw: str, line: str, who: People) -> bool:
    if line.rstrip(".") in who.firm or line in who.firm:
        return True
    found = _FIRM_SUFFIX.search(line)
    if not found or any(c.isdigit() for c in line):
        return False
    written = re.sub(r"\s+", " ", raw.replace(" ", " ")).strip()
    words = written[: found.start()].replace(",", " ").split()
    if not 1 <= len(words) <= 6:
        return False
    return all(w in _FIRM_JOINERS or w.lower() in _FIRM_JOINERS or w[0].isupper() for w in words)


def _street_line(raw: str, line: str) -> bool:
    found = _STREET.match(line)
    if not found:
        return bool(_SUITE.match(line))
    # The words between the number and the street word, as written: a street
    # name is capitalised ("123 Camelback Rd"); "12 point to the court" is not.
    middle = raw.strip().split()[1 : 1 + len(found.group(1).split())]
    return all(w[0].isupper() or w[0].isdigit() for w in middle)


def _phone_line(line: str) -> bool:
    parts = [p.strip() for p in re.split(r"\s[|•]\s|\s{2,}", line) if p.strip()]
    return bool(parts) and all(
        _PHONE.match(p) and 7 <= sum(c.isdigit() for c in p) <= 15 for p in parts
    )


def safe_line(raw: str, who: People) -> bool:
    """A line from the closed lists, skipped without an anchor: blank, the RFC
    separator, a device footer, a greeting, a closing (with the sender's exact
    name), or the sender's exact name."""
    line = _norm(raw)
    if not line or re.fullmatch(r"--\s*", line) or line in DEVICE_FOOTERS:
        return True
    return _closing_line(line, who) or _greeting_line(line, who) or _name_line(line, who)


def shaped_line(raw: str, who: People) -> bool:
    """Is this line SHAPED like a signature line (and so skippable when anchored)?"""
    line = _norm(raw)
    if " | " in raw:
        # "Pat Q. Doe | Partner", "Office: 602 555 0100 | Fax: 602 555 0101".
        return all(safe_line(p, who) or shaped_line(p, who) for p in raw.split(" | ") if p.strip())
    return (
        _fuller_name_line(line, who)
        or _title_line(line)
        or _firm_line(raw, line, who)
        or _street_line(raw, line)
        or bool(_CITY.match(line))
        or _phone_line(line)
        or bool(_ADDRESS.match(line))
    )


def signature_line(raw: str, who: People, anchors: frozenset[str] = frozenset()) -> bool:
    """Is this line skipped as signature? Safe, or shaped AND anchored: the same
    line is in ``anchors`` (this reply's quoted history) or in ``who.anchors``
    (the signature recorded for the sender)."""
    if safe_line(raw, who):
        return True
    form = _anchor_form(raw)
    return (form in anchors or form in who.anchors) and shaped_line(raw, who)


def answer_lines(message: Any, who: People) -> list[str] | None:
    """The sender's own lines that are not skipped as signature, normalised.

    A confidentiality notice is skipped line by line while each line of it is
    anchored; an unanchored line of it is content. ``None`` when there is
    nothing of the sender's own at all."""
    lines = own_lines(message)
    if not any(line.strip() for line in lines):
        return None
    anchors = quoted_anchors(message)
    answers: list[str] = []
    in_notice = False
    for raw in lines:
        line = _norm(raw)
        form = _anchor_form(raw)
        anchored = form in anchors or form in who.anchors
        if _NOTICE_OPEN.match(line) and anchored:
            in_notice = True
        if in_notice and (anchored or not line):
            continue
        in_notice = False
        if affirmative_line(line, who, None) is True:
            answers.append(line)
            continue
        if signature_line(raw, who, anchors):
            continue
        answers.append(line)
    return answers


def trailing_signature(message: Any, who: People) -> list[str]:
    """The signature block that closes the sender's own text, in anchor form:
    the longest run of signature-shaped (or safe) lines at its end, a notice
    included. ``[]`` when it is under two lines. What the seat records for the
    sender, from an email of his that no rule decision rides on."""
    lines = own_lines(message)
    opener = next((i for i, raw in enumerate(lines) if _NOTICE_OPEN.match(_norm(raw))), len(lines))
    block = list(reversed(lines[opener:]))  # a notice runs to the end
    for raw in reversed(lines[:opener]):
        if safe_line(raw, who) or shaped_line(raw, who):
            block.append(raw)
            continue
        break
    kept = [_anchor_form(b) for b in reversed(block) if b.strip()]
    return kept if len(kept) >= 2 else []


def strip_tags(message: Any, pattern: re.Pattern[str]) -> Any:
    """``message`` with every ``pattern`` match removed from it (the tag path
    reads "[rule A] yes and [rule B] yes" as "yes and yes")."""
    return pattern.sub(" ", message) if isinstance(message, str) else message


def is_pure_yes(message: Any, who: People, count: int | None = None) -> bool:
    """The whole of the sender's own text is a plain yes and a signature."""
    answers = answer_lines(message, who)
    if not answers:
        return False
    verdicts = [affirmative_line(a, who, count) for a in answers]
    return all(v is not None for v in verdicts) and any(verdicts)


def pick(message: Any, who: People, listed: int) -> tuple[int, ...] | None:
    """The numbers a reply picked from a list of ``listed``, in order.

    ``()`` for "none"; ``None`` when the reply is not a pick (a word that is not a
    number, an ordinal, "all"/"both", or :data:`_PICK_FILLER`, on any answer
    line). "all", and "both" on a list of two, pick every number; "1-3" is a
    range. A number outside the list is returned as given, for the caller to
    ask again."""
    answers = answer_lines(message, who)
    if not answers or any("?" in a for a in answers):
        return None
    tokens = " ".join(answers).replace("#", " ")
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


def looks_like_pick(message: Any, who: People) -> bool:
    """Did the reply TRY to pick ("both" on a list of three, "one", "1 only for
    Craig")? Such a reply gets the list again, never silence."""
    answers = answer_lines(message, who) or []
    tokens = re.sub(r"[,.;:!/#?]+", " ", " ".join(answers)).split()
    pick_words = {*_ORDINALS, *_PICK_ALL, "one", "none", "number", "numbers"}
    return any(re.match(r"\d{1,2}(?:\D|\Z)", t) or t in pick_words for t in tokens)


__all__ = [
    "AFFIRMATIVES",
    "CLOSINGS",
    "COURTESIES",
    "DEVICE_FOOTERS",
    "GREETINGS",
    "People",
    "TITLES",
    "affirmative_line",
    "answer_lines",
    "is_pure_yes",
    "looks_like_pick",
    "own_lines",
    "quoted_anchors",
    "safe_line",
    "shaped_line",
    "people",
    "pick",
    "signature_line",
    "strip_tags",
    "trailing_signature",
]
