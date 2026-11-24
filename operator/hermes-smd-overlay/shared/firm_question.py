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
tells the model to redraft it as statements: the most reasonable reading, done
and stated, or :data:`LOOK_INTO` alone, which pages SMD when it goes
(``shared.reply_could_not``: ``cannot_complete``).

WHAT COUNTS AS ASKING
* a sentence that ends with a question mark;
* an ask phrase (:data:`_ASK_RE`): "reply with", "let me know which", "please
  confirm", "which would you prefer", "would you like me to", "should I" ...;
* a line that offers an option ("Option A:", "Choice 2 -").

WHAT DOES NOT
* quoted history: lines starting ``>`` and everything from an "On ... wrote:",
  "-----Original Message-----" or "From: " line down;
* words inside quotation marks (the attorney's own question, quoted back);
* reported speech: a sentence with "asked" or "wondered" in it ("You asked
  whether the set is filed: it is.");
* every line of a required block the turn's tools or hooks wrote
  (``shared.turn_says``): code's sentences are code's to word.

RISK, stated. False positives cost one redraft; a sentence like "Let me know if
you need anything else" is NOT an ask (it is not in :data:`_ASK_RE`). False
negatives: an ask phrased as a plain statement ("I need the number before I can
start") does not fire.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

#: The one sentence a reply may be when no reading of the email is reasonable.
#: It pages SMD when it goes (``shared.reply_could_not`` reads it as
#: ``cannot_complete``), so the Operator owns the follow-up in the thread.
LOOK_INTO = "Let me look into that. I'll reply in this thread."

_QUOTE_START_RE = re.compile(
    r"^\s*(?:on\b.{0,200}\bwrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s)",
    re.IGNORECASE,
)
#: Text inside double quotation marks, straight or curly.
_QUOTED_RE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
_REPORTED_RE = re.compile(r"\b(?:asked|wondered|wonders|asks)\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.?!\n]+[.?!]*")
_ASK_RE = re.compile(
    r"\b(?:"
    r"reply\s+(?:with|yes|no|back\s+with|in\s+this\s+thread\s+with)"
    r"|respond\s+with"
    r"|let\s+(?:me|us)\s+know\s+(?:which|whether|how\s+(?:many|much)|what|when|if\s+you(?:'d|\s+would)?\s+(?:like|prefer|want))"
    r"|(?:please|can\s+you|could\s+you|kindly)\s+(?:confirm|advise|choose|select|clarify|specify|indicate)"
    r"|which\s+(?:would|do)\s+you\s+prefer"
    r"|which\s+(?:one|option)\s+(?:would|do|should)"
    r"|(?:would|do)\s+you\s+(?:like|want)\s+(?:me|us)\s+to"
    r"|(?:should|shall)\s+(?:i|we)\b"
    r")",
    re.IGNORECASE,
)
_OPTION_LINE_RE = re.compile(r"^\s*(?:option|choice)\s+(?:[a-e]|[1-9])\b", re.IGNORECASE)


def _norm(line: str) -> str:
    text = line.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).lower()


def _own_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").splitlines():
        if _QUOTE_START_RE.match(line):
            break
        if line.lstrip().startswith(">"):
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
    for line in _own_lines(text):
        if not line.strip() or _norm(line) in fixed:
            continue
        if _OPTION_LINE_RE.match(line):
            return line.strip()[:200]
        bare = _QUOTED_RE.sub('""', line)
        for sentence in _SENTENCE_RE.findall(bare):
            said = sentence.strip()
            if not said or _REPORTED_RE.search(said):
                continue
            if said.endswith("?") or _ASK_RE.search(said):
                return said[:200]
    return ""


#: The seat verticals whose staff are never asked a question.
FIRM_VERTICALS = frozenset({"law-firm"})
#: Redrafts a hold quotes the question for; after them the model is told to stop.
MAX_REDRAFTS = 2
#: What the model reads on a hold under the cap.
REDRAFT = (
    'firm_question: nothing was sent and the one reply is still yours. The reply asks the firm a question ("{q}"). '
    "A reply to the firm never asks them anything and never offers them choices. Draft it again as statements: "
    "take the most reasonable reading of what they wrote, do it, and say plainly what you did or will do. "
    "Read the earlier messages in this thread first and never contradict them; if what they asked is already "
    "done, say so in one line. If no reading is reasonable, the whole reply is exactly: "
    + LOOK_INTO
)


def hold(
    vertical: object,
    bound: bool,
    reply_to: object,
    text: str,
    html: str,
    say: object,
    count: Callable[[], int],
    stop_detail: str,
) -> tuple[str, str] | None:
    """The relay's step-5b decision for one reply: ``(rule, detail)`` to hold
    it, or None. Only an in-turn reply (not ``bound``) on a firm seat, to anyone
    but SMD's own address, is read; ``say`` is the turn's required blocks (a
    ``turn_says.FORBID`` entry is not a block). ``count`` notes this email's hold
    and returns how many it has had."""
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
    asked = question_in(text, blocks) or question_in(html_lines(html or ""), blocks)
    if not asked:
        return None
    if count() > MAX_REDRAFTS:
        return "firm_question_cap", stop_detail
    return "firm_question", REDRAFT.format(q=asked.replace('"', "'"))


__all__ = ["FIRM_VERTICALS", "LOOK_INTO", "MAX_REDRAFTS", "REDRAFT", "hold", "question_in"]
