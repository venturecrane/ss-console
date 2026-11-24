"""Does a reply to firm staff ask them something?

WHY (A&P, 2026-11-23). An attorney wrote "Reduce special rogs to the most
important 120125", a typo for 120 to 125. The in-turn reply asked him how many
each set should be cut to, "Reply with the number". Six minutes later, after
SMD had done the trim and told him so in the same thread, his "120-125" got a
second reply that said nothing had changed and offered him two options. The
standing rule (the Captain, 2026-11-23): any message we cause in an attorney's
thread asks nothing. The next thing he gets is the work, or a statement of what
will be done.

The rule was skill prose until now, and prose did not hold. This reads the
reply's own words, once, before it goes: :func:`question_in` returns the first
sentence that asks the reader something, or ``""``. The relay
(``plugins/hermes-smd-reply/relay_checks.question_held``) holds such a reply and
tells the model to redraft it as statements, or as :data:`LOOK_INTO` alone,
which pages SMD when it goes (``shared.reply_could_not``).

A FALSE HOLD IS WORSE THAN A MISSED QUESTION: a held delivery to an attorney is
silence where the work should be. So every ask pattern is narrow, and each has a
test showing a statement with the same words that still passes.

WHAT COUNTS AS ASKING (few, clear signals; a missed ask is cheaper than a
held delivery, and the turn's [thread-so-far] context and the router skill
cover the rest)
* a sentence that ends with a question mark;
* a small ask set (:data:`_ASK_RES`): "Reply with the number" (no subject
  before it), sentence-initial "Reply "yes" to proceed", "let me know which /
  whether", "please / can you confirm whether", "which would you prefer",
  "would you like me to".

WHAT DOES NOT
* quoted history: lines starting ``>`` and everything from an "On ... wrote:",
  "-----Original Message-----" or "From: " line down; in HTML, every
  ``<blockquote>`` and everything from a ``gmail_quote`` block on;
* quoted discovery: a line or sentence starting with a discovery label and a
  number ("Request No. 4:", "SROG 14:", "Discovery Request 4:", "RFP Set Two,
  No. 8:", "No. 14.", "Form Interrogatory 2.5:") and the lines under it to the
  next blank line, and "Q:" / "A:" lines;
* a one-line label ("FAQ: ...?"), words inside quotation marks (single or
  double, across lines), web addresses, reported speech ("You asked whether",
  "The letter says: ...?");
* every line of a required block the turn's tools or hooks wrote
  (``shared.turn_says``), and an owed pending-act confirmation line.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from shared.reply_could_not import LOOK_INTO

_QUOTE_START_RE = re.compile(
    r"^\s*(?:on\b.{0,200}\bwrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
#: Quoted words: double quotes (straight or curly, across lines) and single
#: quotes that are not an apostrophe inside a word.
_DOUBLE_QUOTED_RE = re.compile(r"\"[^\"]*\"|“[^”]*”")
_SINGLE_QUOTED_RE = re.compile(r"(?<![\w'])'[^'\n]+'(?![\w'])|‘[^’\n]+’")
_REPORTED_RE = re.compile(
    r"\b(?:asked|asks|wondered|wonders|says|said|reads|states|writes|wrote|wants\s+to\s+know)\b",
    re.IGNORECASE,
)
#: A discovery label and its number: "Request No. 4:", "SROG 14:", "Special
#: Interrogatory No. 7:", "Discovery Request 4:", "RFP Set Two, No. 8:",
#: "Form Interrogatory 2.5:", "- No. 2:", "No. 14.".
_DISCOVERY_RE = re.compile(
    r"^\s*(?:[-*\u2022]\s*)?(?:discovery\s+)?(?:(?:special|form)\s+)?"
    r"(?:request(?:s)?(?:\s+for\s+(?:admission|production)s?)?|interrogator(?:y|ies)|s?rogs?|frogs?|rfas?|rfps?"
    r"|no\.?|number)"
    r"\s*(?:set\s+\w+,\s*)?(?:no\.?\s*|number\s*)?#?\s*\d+(?:\.\d+)?\s*[:\-\u2013(.]",
    re.IGNORECASE,
)
_QA_RE = re.compile(r"^\s*[QA]\s*:\s", re.IGNORECASE)
#: A one-line label: one to three words and a colon opening the line.
_LABEL_RE = re.compile(r"^\s*[A-Za-z][\w&/-]*(?:\s+[A-Za-z][\w&/-]*){0,2}\s*:\s")
#: Sentence ends: punctuation then whitespace, never inside "No. 4" or "2.5".
_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")
_ABBREV_RE = re.compile(
    r"\b(?:no|nos|mr|mrs|ms|dr|vs|v|st|inc|e\.g|i\.e|etc|jr|sr|p|pp)\.$", re.IGNORECASE
)

_ASK_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "Reply with the number, for example 35": a choice is asked for.
        # "Reply with any edits and I'll revise" and "We'll reply with the
        # final draft" are not.
        r"^(?:please\s+)?(?:reply|respond)\s+(?:back\s+)?with\s+(?:the|a|your|one)\s+"
        r"(?:number|count|choice|option|answer|one|matter|figure|date)\b",
        # Sentence-initial "Reply yes to proceed" (checked before quotes are stripped).
        r"^(?:please\s+)?(?:reply|respond)\s+[\"\u201c']?(?:yes|no|ok|approve|confirm)\b",
        r"\blet\s+(?:me|us)\s+know\s+(?:which|whether)\b",
        r"\b(?:please|can\s+you|could\s+you)\s+confirm\s+whether\b",
        r"\bwhich\s+would\s+you\s+prefer\b",
        r"\bwould\s+you\s+like\s+(?:me|us)\s+to\b",
    )
)


def _norm(line: str) -> str:
    text = line.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).lower()


def _own_text(text: str) -> str:
    out: list[str] = []
    for line in (text or "").splitlines():
        if _QUOTE_START_RE.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out)


def _sentences(line: str) -> list[str]:
    out: list[str] = []
    for piece in _SPLIT_RE.split(line):
        if out and _ABBREV_RE.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return [s.strip() for s in out if s.strip()]


def _asks(sentence: str) -> bool:
    return any(p.search(sentence) for p in _ASK_RES)


_REPLY_FIRST_RE = re.compile(r"^(?:please\s+)?(?:reply|respond)\b", re.IGNORECASE)


def _ask_in_line(line: str) -> str:
    """The first asking sentence of one line (quotes already stripped), or ``""``."""
    bare = _SINGLE_QUOTED_RE.sub("''", line)
    for sentence in _sentences(bare):
        if _REPORTED_RE.search(sentence) or _DISCOVERY_RE.match(sentence) or _QA_RE.match(sentence):
            continue
        if sentence.endswith("?") or _asks(sentence):
            return sentence
    return ""


def _readable(lines: list[str], fixed: set[str]) -> list[str]:
    """The lines a question is looked for in: no block line, no label line, no
    Q:/A: line, and no discovery block (a labelled line to the next blank line)."""
    out: list[str] = []
    discovery = False
    for line in lines:
        if not line.strip():
            discovery = False
            continue
        if _norm(line) in fixed:
            continue
        if _DISCOVERY_RE.match(line):
            discovery = True
            continue
        if discovery or _QA_RE.match(line):
            continue
        if _LABEL_RE.match(line):
            continue
        out.append(line)
    return out


def question_in(text: str, blocks: Iterable[str] = ()) -> str:
    """The first sentence of ``text`` that asks its reader something, or ``""``.

    ``blocks`` are the turn's required blocks (code's words); their lines are
    never read. Never raises on text."""
    if not isinstance(text, str) or not text.strip():
        return ""
    fixed = {
        _norm(line)
        for block in blocks
        if isinstance(block, str)
        for line in block.splitlines()
        if line.strip()
    }
    own = _URL_RE.sub("", _own_text(text))
    # "Reply "yes" to proceed" is read before quoted words are stripped.
    for line in _readable(own.splitlines(), fixed):
        for sentence in _sentences(line):
            if _REPLY_FIRST_RE.match(sentence) and _asks(sentence):
                return sentence[:200]
    lines = _readable(_DOUBLE_QUOTED_RE.sub('""', own).splitlines(), fixed)
    for line in lines:
        found = _ask_in_line(line)
        if found:
            return found[:200]
    return ""


#: The seat verticals whose staff are never asked a question.
FIRM_VERTICALS = frozenset({"law-firm"})
#: Redrafts a hold quotes the question for; after them the model is told to send
#: :data:`LOOK_INTO` and nothing else.
MAX_REDRAFTS = 2
#: What the model reads on a hold under the cap.
REDRAFT = (
    'firm_question: nothing was sent and the one reply is still yours. The reply asks the firm a question ("{q}"). '
    "A reply to the firm never asks them anything and never offers them choices. Draft it again as statements: "
    "say plainly what you did or will do. Read the earlier messages in this thread first and never contradict "
    "them; if what they asked is already done, say so in one line. Never guess which matter is meant: if the "
    "matter is unclear, or no reading of the request is plain, the whole reply is exactly: "
    + LOOK_INTO
)
#: Added when the turn has required sentences.
KEEP_BLOCKS = (
    " Keep every required sentence of this turn exactly as written, each as its own paragraph."
)
#: What the model reads past the cap: one line, and nothing else, so the person
#: is never left with silence.
AT_CAP = (
    "firm_question: nothing was sent. This reply has been held too many times for asking the firm a question. "
    "Draft it once more with exactly this line as the whole body, nothing else: " + LOOK_INTO
)


_BLOCKQUOTE_RE = re.compile(r"<blockquote\b.*?</blockquote\s*>", re.IGNORECASE | re.DOTALL)
_GMAIL_QUOTE_RE = re.compile(r"<div\b[^>]*\bclass=[\"'][^\"']*\bgmail_quote\b", re.IGNORECASE)


def _own_html(markup: str) -> str:
    """An HTML part without its quoted history: every ``<blockquote>``, and
    everything from a ``gmail_quote`` block on."""
    cut = _GMAIL_QUOTE_RE.search(markup)
    return _BLOCKQUOTE_RE.sub("", markup[: cut.start()] if cut else markup)


def _drop_sentence(text: str, sentence: str) -> str:
    """``text`` without ``sentence`` where it stands as its own sentence or line."""
    pattern = rf"(^|(?<=[.!?])[ \t]+){re.escape(sentence)}(?=\s|$)"
    return re.sub(pattern, r"\1", text, flags=re.MULTILINE)


def _owed_act_line(session_id: str) -> str:
    """The confirmation line of an act this session proposed and still owes
    (``shared.pending_acts``), or "": code's words that a Named Administrator
    must answer, never a question the model asked."""
    if not session_id:
        return ""
    try:
        from shared.pending_acts import PENDING_ACTS

        pending = PENDING_ACTS.peek(session_id)
    except Exception:  # noqa: BLE001 - no act read is no exemption
        return ""
    line = getattr(pending, "readback", "") or "" if pending is not None else ""
    return line if isinstance(line, str) else ""


def hold(
    vertical: object,
    bound: bool,
    reply_to: object,
    text: str,
    html: str,
    say: object,
    count: Callable[[], int],
    stop_detail: str,
    session_id: str = "",
) -> tuple[str, str] | None:
    """The relay's step-5b decision for one reply: ``(rule, detail)`` to hold
    it, or None. Only an in-turn reply (not ``bound``) on a firm seat, to anyone
    but SMD's own address, is read; ``say`` is the turn's required blocks (a
    ``turn_says.FORBID`` entry is not a block). ``count`` notes this email's hold
    and returns how many it has had; past :data:`MAX_REDRAFTS` the detail is
    ``stop_detail``, which tells the model to send :data:`LOOK_INTO` alone."""
    from shared.heartbeat import _is_smd_address
    from shared.turn_says import FORBID, html_lines

    if bound or vertical not in FIRM_VERTICALS or _is_smd_address(reply_to):
        return None
    said = (
        (say,)
        if isinstance(say, str)
        else tuple(say or ())
        if isinstance(say, (tuple, list))
        else ()
    )
    blocks = tuple(b for b in said if isinstance(b, str) and not b.startswith(FORBID))
    act = _owed_act_line(session_id)
    read_blocks = blocks + ((act,) if act else ())
    if act:
        # The act's own ask ("Reply "yes, delete them" to proceed.") is code's,
        # even on a line the model edited around it.
        ask = re.split(r"(?<=[.?!])\s+", act.strip())[-1]
        text, html = _drop_sentence(text, ask), _drop_sentence(html or "", ask)
    asked = question_in(text, read_blocks) or question_in(
        html_lines(_own_html(html or "")), read_blocks
    )
    if not asked:
        return None
    if count() > MAX_REDRAFTS:
        return "firm_question_cap", stop_detail
    detail = REDRAFT.format(q=asked.replace('"', "'"))
    return "firm_question", detail + (KEEP_BLOCKS if blocks else "")


__all__ = [
    "AT_CAP",
    "FIRM_VERTICALS",
    "LOOK_INTO",
    "MAX_REDRAFTS",
    "REDRAFT",
    "hold",
    "question_in",
]
