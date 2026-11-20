"""Confirm-by-reply: what a plain yes, a pick and a binding mean (2026-11-19).

The half of :mod:`shared.rule_confirm` that R2.1 added, split out under the
module-size ratchet. It reads that module's primitives (the person's own text,
the affirmatives and defeaters, the verdict shapes) through ``rc`` at call
time; :mod:`shared.rule_confirm` imports this module at its end and forwards the
public names, so callers keep using ``rule_confirm.pure_affirmative`` and the
rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from shared import act_broker
from shared import rule_confirm as rc


@dataclass(frozen=True)
class ReadbackBinding:
    """The read-back a reply answers, as the broker matched it (2026-11-19).

    The broker records which of a person's OWN proposals each read-back it sent
    carried, keyed by the sent message's id, and matches an inbound reply's
    ``In-Reply-To`` against it. ``proposal_ids`` is that message's proposals in
    the order the message showed them, still open or not (the matcher keeps the
    open ones); ``listed`` is True when that message was the numbered list, so
    "1 and 3" resolves against it. The binding names WHICH rules; the person's
    own words still decide WHETHER.
    """

    proposal_ids: tuple[str, ...]
    listed: bool = False


#: Words that may sit beside an affirmative in a reply that is ONLY a yes:
#: "Yes, thanks." is a plain yes; "Yes, letters only" is not. Each is matched
#: as a whole clause or a run of whole words, never as a substring.
_COURTESIES: tuple[str, ...] = ("thanks", "thank you", "please", "great", "perfect")


#: The closing lines that start a signature block (a closed list, with or
#: without a trailing comma, alone or followed by the sender's name). Review
#: 2026-11-19: a real attorney signs "Cordially," then his name with a middle
#: initial, the firm, a street, a city line, a phone and a fax, and every one of
#: twelve real plain yeses failed the old three-line, short-list test.
_CLOSINGS: tuple[str, ...] = (
    "cordially", "sincerely", "regards", "best regards", "kind regards", "warm regards",
    "warmest regards", "respectfully", "best", "all the best", "thanks", "thank you",
    "thanks again", "thanks so much", "thank you so much", "many thanks", "cheers",
    "very truly yours", "yours truly", "truly yours", "thx", "ty",
)  # fmt: skip

#: A device or client footer, or legal confidentiality boilerplate: the scan
#: for the answer and its signature stops there.
_FOOTER = re.compile(
    r"\A(?:sent from my |sent from outlook|get outlook for |sent via |confidentiality notice"
    r"|this e-?mail and any attachments|this message and any attachments)"
)

#: Words in a signature block that would change the answer above it: the
#: block is ignored only when none of these is in it.
_SIGNATURE_QUALIFIERS: tuple[str, ...] = (
    "but", "only", "except", "unless", "not", "don't", "dont", "change", "instead", "however",
)  # fmt: skip
#: A numbered edit ("1. use 12 point") in the block is a qualifier too. A street
#: or phone line ("123 Main St", "602-555-0100") is not.
_NUMBERED_EDIT = re.compile(r"\A\d+[.)]\s+[a-z]")
_NAME_EXTRAS = frozenset({"esq", "jr", "sr", "ii", "iii", "iv"})

#: The longest signature block ignored for the purity test.
SIGNATURE_MAX_LINES = 12


def _own_lines(message: Any) -> list[str]:
    own = rc.strip_quoted(rc.email_body(message))
    return [rc._normalize(line) for line in own.splitlines() if rc._normalize(line)]


#: A greeting line above the answer ("Hi,", "Good morning Pat"), a closed list.
_GREETING = re.compile(
    r"\A(?:hi|hello|hey|good (?:morning|afternoon|evening))(?: [a-z]+){0,2}[\s,.!]*\Z"
)


def _display(message: Any, header: str = "from") -> tuple[list[str], str]:
    """(display-name words, address) from the envelope's ``header:`` line above
    the untrusted-body fence (code-rendered, never the body)."""
    if not isinstance(message, str):
        return [], ""
    cut = message.find(rc.UNTRUSTED_EMAIL_DELIMITER)
    head = message[:cut] if cut >= 0 else ""
    found = re.search(rf"(?im)^{header}:\s*(.+)$", head)
    if not found:
        return [], ""
    display, address = parseaddr(found.group(1).strip())
    return [w for w in re.split(r"[^a-z]+", display.lower()) if w], address.lower()


def sender_names(message: Any, header: str = "from") -> frozenset[str]:
    """The sender's own name words and initials, and the words of their
    address's local part. ``header="to"`` reads the name the email addressed
    (the Operator's), the same way."""
    words, address = _display(message, header)
    if not words and not address:
        return frozenset()
    local = [w for w in re.split(r"[^a-z]+", address.split("@")[0]) if len(w) > 1]
    initials = "".join(w[0] for w in words)
    return frozenset({*words, *local, *([initials] if len(initials) > 1 else [])})


def _words(line: str) -> list[str]:
    text = re.sub(r"\A/s/\s*", "", line)
    return [w for w in re.split(r"[^a-z]+", text) if w and w not in _NAME_EXTRAS]


def _is_name_line(line: str, names: frozenset[str], display: list[str]) -> bool:
    """The sender's name as they sign it: words all from their own names, or
    their display name's first and last tokens with a middle name or initial,
    a suffix, "Esq." and a leading "/s/" allowed ("/s/ Pat Q. Doe, Esq.")."""
    words = _words(line)
    if not words:
        return False
    if names and all(w in names for w in words):
        return True
    if len(display) < 2 or not 2 <= len(words) <= 4:
        return False
    first, last = display[0], display[-1]
    return words[0].startswith(first) and words[-1] == last


def _is_closing(line: str, names: frozenset[str], display: list[str]) -> bool:
    """A closing courtesy alone or before the sender's name, or the name alone."""
    text = re.sub(r"[,.!\-]+", " ", line).strip()
    for closing in sorted(_CLOSINGS, key=len, reverse=True):
        if text == closing:
            return True
        if text.startswith(closing + " "):
            return _is_name_line(text[len(closing) :].strip(), names, display)
    return _is_name_line(line, names, display)


def _clean_signature(block: list[str]) -> bool:
    """A signature block that changes nothing: short enough, and no qualifier."""
    if len(block) > SIGNATURE_MAX_LINES:
        return False
    for line in block:
        if _NUMBERED_EDIT.match(line):
            return False
        if any(rc._contains_phrase(line, q) for q in _SIGNATURE_QUALIFIERS):
            return False
    return True


def _consumes(clause: str, phrases: tuple[str, ...]) -> tuple[bool, bool]:
    """Does ``clause`` consist only of ``phrases`` (and courtesies)? And did it
    carry at least one of ``phrases`` (a courtesy alone is not an answer)?"""
    ordered = sorted({*phrases, *_COURTESIES}, key=len, reverse=True)
    rest, saw = clause.strip(), False
    while rest:
        for phrase in ordered:
            if rest == phrase or rest.startswith(phrase + " "):
                saw = saw or phrase in phrases
                rest = rest[len(phrase) :].strip()
                break
        else:
            return False, saw
    return True, saw


def _answer_and_signoff(message: Any) -> str | None:
    """The answer: the sender's own lines before the first closing line, with a
    leading greeting dropped and the signature block after it ignored. ``None``
    when the block could change the answer. Without a closing line the whole
    own text is the answer, as before."""
    lines = _own_lines(message)
    if len(lines) > 1 and _GREETING.match(lines[0]):
        lines = lines[1:]  # "Hi,\nYes": the greeting is not the answer
    names, (display, _address) = sender_names(message), _display(message)
    for i, line in enumerate(lines):
        if _FOOTER.match(line):
            lines = lines[:i]
            break
    cut = next((i for i, line in enumerate(lines) if i and _is_closing(line, names, display)), None)
    if cut is not None:
        if not _clean_signature(lines[cut:]):
            return None
        lines = lines[:cut]
    return ". ".join(lines) if lines else None


def _negated(answer: str) -> bool:
    """A defeater in the answer itself (``rule_confirm.DEFEATERS``)."""
    return any(rc._contains_phrase(answer, token) for token in rc.DEFEATERS)


def pure_affirmative(message: Any) -> bool:
    """Is the sender's own text ONLY a yes? (2026-11-19)

    The equality test of the send-as lane (:data:`_SEND_AS_BARE_SEND`) applied
    to a rule: the answer line, split at its punctuation, is nothing but
    affirmatives and courtesies ("Yes.", "Yes, thanks", "ok"), and anything under
    it is a sign-off (a name, "Thanks"). A defeater anywhere in their own text
    makes it not a plain yes, and so does any other word: "Yes for memos" is
    agreement with something narrower than the read-back, and only a plain yes
    may confirm every rule a read-back carried.
    """
    answer = _answer_and_signoff(message)
    if answer is None or _negated(answer):
        return False
    clauses = [c.strip() for c in re.split(r"[,.;:!]+|\s[-\u2013]+\s?", answer) if c.strip()]
    # "Yes, Chris", "Yes - Chris": the sender's own name, or the name the email
    # addressed, may sit in the answer (review 2026-11-19); nothing else may.
    names = sender_names(message) | sender_names(message, "to")
    saw_any = False
    for clause in clauses:
        if names and all(w in names for w in clause.split()):
            continue
        whole, saw = _consumes(clause, rc.BARE_AFFIRMATIVES)
        if not whole:
            return False
        saw_any = saw_any or saw
    return saw_any


#: Words a pick from the numbered list may carry around its numbers: "1 and 3",
#: "yes to 2", "all", "#1, #2 please". Anything else and it is not a pick.
_PICK_FILLER = frozenset(
    {
        "yes", "ok", "okay", "to", "please", "confirm", "confirmed", "and", "&", "number",
        "numbers", "rule", "rules", "item", "items", "of", "them", "the", "thanks",
        "thank", "you", "go", "ahead", "only", "just", "plus",
    }
)  # fmt: skip


_PICK_ALL = frozenset({"all", "both", "everything"})


#: The longest list code renders, and so the largest number a pick may name.
MAX_PICK = 20


def read_pick(message: Any, listed: int) -> tuple[int, ...] | None:
    """The numbers a reply picked from a list of ``listed`` rules, in order.

    ``None`` when the reply is not a pick: a word that is not a number, a
    filler word or "all" (so "not 2", "1 but change it" and a question are not
    picks), a defeater anywhere in their own text, or no number at all. "all"
    (and "both", on a list of two) pick every number. A number outside the list
    returns as given, so the caller can say the list again rather than guess.
    """
    answer = _answer_and_signoff(message)
    if answer is None or _negated(answer) or "?" in answer:
        return None
    tokens = re.sub(r"[,.;:!/]+", " ", answer.replace("#", " ")).split()
    picked: list[int] = []
    saw_all = False
    for token in tokens:
        if token.isdigit():
            number = int(token)
            if number not in picked:
                picked.append(number)
        elif token in _PICK_ALL:
            if token == "both" and listed != 2:
                return None
            saw_all = True
        elif token not in _PICK_FILLER:
            return None
    if saw_all and not picked:
        return tuple(range(1, listed + 1))
    if saw_all or not picked:
        return None
    return tuple(picked)


def is_own_rule(row: dict[str, Any], sender: Any) -> bool:
    """A RULE the sender stated about their own say: not an act, not an
    operations request, not one waiting on an administrator. These, and only
    these, are confirmed by the message replied to (2026-11-19); every other
    row keeps the tag path exactly."""
    kind = str(row.get("kind") or "rule")
    if kind in ("tool_call", rc.OPS_REQUEST_KIND) or bool(row.get("for_admin")):
        return False
    stated_by = str(row.get("instructed_by") or "").strip().lower()
    return bool(stated_by) and stated_by == str(sender or "").strip().lower()


def _ids(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(r["proposal_id"]) for r in rows)


def _confirmed(ids: tuple[str, ...], candidates: tuple[str, ...]) -> rc.Verdict:
    return rc.Verdict(
        kind=rc.CONFIRMED, proposal_id=ids[0], candidates=candidates, proposal_ids=ids
    )


def resolve_bound(
    message: Any,
    reading: rc.Reading,
    by_id: dict[str, dict[str, Any]],
    own_open: list[dict[str, Any]],
    binding: ReadbackBinding,
    address: str,
) -> rc.Verdict | None:
    """A reply to a read-back the broker recorded. ``None`` falls through.

    The binding says which rules the message replied to carried; the person's
    own words, read as strictly as ever, say whether. Only rules still open and
    still the sender's own are in play, so a binding can never reach a rule of
    somebody else's, a rule waiting on an administrator, or an act.
    """
    carried = binding.proposal_ids
    bound_open = [by_id[p] for p in carried if p in by_id and is_own_rule(by_id[p], address)]
    if not bound_open:
        return None
    open_ids = _ids(bound_open)
    if binding.listed:
        pick = read_pick(message, len(carried))
        if pick is None and pure_affirmative(message):
            # Every rule on the list is one the person stated, and the list asks
            # "Reply all to confirm every one": a plain yes to it means all of
            # them. Asking again was the loop this binding exists to end.
            pick = tuple(range(1, len(carried) + 1))
        if pick is not None:
            if any(n < 1 or n > len(carried) for n in pick):
                return rc.Verdict(kind=rc.ASK, reason=rc.ASK_PICK, candidates=_ids(own_open))
            chosen = tuple(carried[n - 1] for n in pick if carried[n - 1] in open_ids)
            if not chosen:
                return rc.Verdict(kind=rc.ASK, reason=rc.ASK_PICK, candidates=_ids(own_open))
            return _confirmed(chosen, open_ids)
    elif pure_affirmative(message):
        return _confirmed(open_ids, open_ids)
    if reading.negated:
        if reading.affirmative:
            return rc.Verdict(kind=rc.ASK, reason=rc.ASK_QUALIFIED, candidates=open_ids)
        return rc._declined_or_ask(bound_open, open_ids, reading)
    if reading.affirmative:
        # A yes with anything else in it ("Yes\nletters only") agrees with
        # something other than the read-back: restate and ask again.
        return rc.Verdict(kind=rc.ASK, reason=rc.ASK_QUALIFIED, candidates=open_ids)
    return None


#: The phrase an act's own read-back tells the firm to reply with
#: (``workspace_broker/act_records_order.order_readback``: 'Reply "yes, place
#: it" to proceed.').
_INSTRUCTED = re.compile(r'reply "([^"]{2,40})" to proceed', re.IGNORECASE)


def _bare(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def instructed_act(
    message: Any, rows: list[dict[str, Any]], address: str, binding: ReadbackBinding | None
) -> rc.Verdict | None:
    """A tagless reply that is exactly the phrase an act's read-back asked for
    (review 2026-11-19: "yes, place it" was refused as needing a tag). It
    confirms that act when the reply is bound to its read-back, or when it is
    the only act open for this sender; otherwise ``None`` (the tag path)."""
    acts = [r for r in rows if str(r.get("kind") or "rule") == act_broker.KIND_TOOL_CALL]
    if not acts:
        return None
    answer = _answer_and_signoff(message)
    if answer is None:
        return None
    bound = set(binding.proposal_ids) if binding is not None else set()
    chosen = [r for r in acts if str(r["proposal_id"]) in bound] or (acts if len(acts) == 1 else [])
    if len(chosen) != 1:
        return None
    phrase = _INSTRUCTED.search(str(chosen[0].get("text") or ""))
    if phrase is None or _bare(answer) != _bare(phrase.group(1)):
        return None
    act_id = str(chosen[0]["proposal_id"])
    return rc.Verdict(
        kind=rc.CONFIRMED, proposal_id=act_id, candidates=(act_id,), proposal_ids=(act_id,)
    )


def unbound_own(
    message: Any, rows: list[dict[str, Any]], address: str, binding: ReadbackBinding | None = None
) -> tuple[rc.Verdict | None, list[dict[str, Any]]]:
    """A tagless reply bound to no read-back, over the sender's own open rules:
    the numbered list when their whole text is a plain yes or a pick, else
    ``None`` and the rows without their own (which simply stay open). An act's
    own instructed phrase confirms that act first (:func:`instructed_act`)."""
    if (act := instructed_act(message, rows, address, binding)) is not None:
        return act, rows
    own = own_ids(rows, address)
    if not own:
        return None, rows
    if pure_affirmative(message) or read_pick(message, MAX_PICK):
        return rc.Verdict(kind=rc.ASK, reason=rc.ASK_PICK, candidates=own), rows
    return None, [r for r in rows if str(r["proposal_id"]) not in own]


def own_ids(rows: list[dict[str, Any]], address: str) -> tuple[str, ...]:
    """The ids of the sender's own open rules among ``rows``, in their order."""
    return _ids([r for r in rows if is_own_rule(r, address)])


def bound_verdict(
    message: Any,
    reading: rc.Reading,
    rows: list[dict[str, Any]],
    address: str,
    binding: ReadbackBinding | None,
) -> rc.Verdict | None:
    """:func:`resolve_bound` over ``rows``, or ``None`` with no binding."""
    if binding is None or not binding.proposal_ids:
        return None
    if rc.find_tags(message):
        # A reply naming rules by tag ("[rule A] yes and [rule B] yes", a read-back
        # the old release sent, its tags quoted back) is the tag path's to read,
        # exactly as without a binding (review 2026-11-19).
        return None
    by_id = {str(r["proposal_id"]): r for r in rows}
    own_open = [r for r in rows if is_own_rule(r, address)]
    return resolve_bound(message, reading, by_id, own_open, binding, address)


__all__ = [
    "MAX_PICK",
    "ReadbackBinding",
    "bound_verdict",
    "instructed_act",
    "is_own_rule",
    "own_ids",
    "pure_affirmative",
    "read_pick",
    "resolve_bound",
]
