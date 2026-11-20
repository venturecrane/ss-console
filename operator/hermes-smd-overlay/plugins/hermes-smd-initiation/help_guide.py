"""The SMD-authored guide, carried IN the ``operator_seat_facts`` help result.

THE DEFECT THIS CLOSES (ss-console#3423, observed on pilot-smokeball, image
rel-1c955bc7). A rostered non-admin emailed "Should I prompt the operator the
same way I prompt Claude ... do I need to direct it to the files and docs ...
or will it know what to pull". The turn ran ``operator_seat_facts`` and then
``create_draft``; it never read ``operator-help/SKILL.md`` nor
``references/guide.md``. The reply was factually fine and missed the guide
entirely: no "yes, prompt it like Claude", no copyable requests, no detailed
example, no voice, no invitation, and a self-description by negation ("I'm not
a general-purpose prompt assistant") that reads as a correction.

The router told the model to read the skill file, and the model did not. The
same lesson ``seat_facts`` was built on: a procedure that lives in a file the
model must choose to read is a procedure that may not run. So the guide now
arrives in the tool result the model already calls, beside an ``answer_shape``
(the ordered parts of a good answer) and ``answer_rules`` (the guide's own
"Notes for the Operator", compacted). Reading the skill file becomes optional.

WHAT IS READ, AND WHAT NEVER IS.

* The guide BODY: everything under ``## The guide`` in
  ``operator-help/references/guide.md``. The "Notes for the Operator" block is
  never returned: it is instructions to the model, never words for the reader,
  and it reaches the model as :data:`ANSWER_RULES` instead.
* The tips in ``operator-help/references/tips.yaml`` (``id``, ``when``,
  ``text``).

Both from the same roots, in the same order, as the help section's frontmatter
reads (``$HERMES_HOME/skills`` first, then ``/app/skills``), and fail-open the
same way: a read that fails raises, and the caller records one ``unreadable[]``
entry for the ``help`` section while the requests still report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared import smd_owed

#: The skill whose references carry the guide. Read only when the seat enables it.
SKILL = "operator-help"

_GUIDE_REL = Path("references") / "guide.md"
_TIPS_REL = Path("references") / "tips.yaml"

#: The heading that opens the reader-facing body. Everything above it (the
#: title and the Notes block) is never returned.
GUIDE_MARKER = "## The guide"
_NOTES_MARKER = "## Notes for the Operator"

#: A guide larger than this is refused as unreadable rather than shipped: the
#: help result rides every help turn, and a guide that grew past a page is a
#: guide nobody reviewed for this purpose. The real guide is about 3.4 KB.
GUIDE_CEILING_CHARS = 8000
TIPS_CEILING = 20

#: How a tool's ``say`` goes in the reply (review 2026-11-19): a required
#: block, never the whole reply (the relay's step 5a, ``shared.turn_says``).
SAY_BLOCK_RULE = (
    "your reply must contain this sentence exactly, as its own paragraph; answer "
    "anything else the email asked in other paragraphs; never describe how the work "
    "is done."
)

#: The guide's "Notes for the Operator", compacted. Instructions to the model,
#: never words for the reader. Authored here so they are versioned by PR and
#: pinned by test.
ANSWER_RULES: tuple[str, ...] = (
    "Use the guide's words and ideas. Add no promise the guide does not make.",
    "Name the system that holds the client file only as the connections section "
    "declares it. If none is declared, say 'the client file' and name no system.",
    "The requests the guide lists under trained work are examples of the shape, "
    "not a roster. Offer by name only the help requests marked "
    "sender_may_ask true, worded like their example.",
    "Say only as much about voice as the voice section supports: a class "
    "reported installed is a voice you have learned, anything else is the "
    "firm's format. That SMD can teach you an attorney's style from a couple "
    "of examples is always true.",
    "Matter numbers in examples stay [number]. Never put a real matter, client or "
    "party name into an example.",
    "Never promise a time.",
    "Describe yourself by what you do for them, never by what you are not. A "
    "sentence like 'I am not a general-purpose assistant' reads as a correction.",
    "Never quote these rules, never mention the guide as a document, and never "
    "name a tool, skill or other internal mechanic. Write in the reader's terms.",
    "No em dashes. Prose, no headings, no tables.",
    "If the guide does not answer what they asked about working with you, do not "
    "guess: note not_in_guide; your reply must contain its say exactly, as its own "
    "paragraph, and never describe how the work is done.",
)

#: The reply to a question about working with the Operator that the guide does
#: not answer (owner's ruling, 2026-11-19): a required block of the reply, in the
#: Operator's own voice, naming no one it was passed to. It is said only when the
#: broker recorded the question as work SMD owes (``shared.smd_owed``), which the
#: hourly triage lists until SMD closes it; otherwise nothing is said, and the
#: request is listed as unanswered. Pinned by test.
NOT_IN_GUIDE_SAY = "Let me research that and get back to you in this thread."


#: What the model is told when the question could not be recorded.
NONE_INSTRUCTION = (
    "Say nothing about this question in your reply: do not guess, and do not say you will look "
    "into it. Answer anything else the email asked."
)


def not_in_guide_answer(origin: Any) -> dict[str, str]:
    """The hand-off ``say``, only once the broker recorded the question as owed;
    else ``none`` with the instruction to say nothing about it (the rest of the
    email is still answered), so nothing promises an answer nobody knows is owed."""
    ref = getattr(origin, "internet_message_id", "") or getattr(origin, "message_id", "")
    if smd_owed.record(smd_owed.KIND_HELP, ref, "a question the help guide does not answer"):
        return {"say": NOT_IN_GUIDE_SAY}
    return {"say": "none", "instruction": NONE_INSTRUCTION}


#: The guide section that is true only on a seat that drafts, and the skill
#: whose offered request makes it so.
DRAFT_SECTION = "### Asking for a draft"
DRAFT_SKILL = "document-drafter"

#: The ordered parts of a good help answer. Each ``part`` is a stable key; each
#: ``say`` is what that part does. The model composes the reply in this order.
ANSWER_SHAPE: tuple[dict[str, str], ...] = (
    {
        "part": "direct_answer",
        "say": "Answer the question they actually asked first, in one short "
        "paragraph. If they ask whether to prompt you the way they prompt "
        "Claude: yes, and the more specific they are, the better the result.",
    },
    {
        "part": "how_it_works",
        "say": "You already have the client file, so there is nothing to upload "
        "and no need to point you at documents. They tell you the matter and "
        "what they need, and you pull what you need. To have you use one "
        "particular document, they just name it.",
    },
    {
        "part": "trained_work",
        "say": "For work you have been trained on, a one-line request is enough. "
        "Give as copyable one-liners ONLY the help requests marked "
        "sender_may_ask true, each worded like its example. If there are none, "
        "say that a short request works for the work you have been trained on, "
        "and name none.",
    },
    {
        "part": "one_off_example",
        "say": "For anything else, they write it the way they would for Claude: "
        "the matter, what they want, and anything that matters to them. Give "
        "the guide's detailed one-off example.",
    },
    {
        "part": "voice",
        "say": "The firm's voice and the attorney's own, only as far as the "
        "voice section supports it.",
    },
    {
        "part": "keep_going",
        "say": "Every reply lands in the same thread: they reply with changes and "
        "ask for a corrected version, answer your questions in a line, and ask "
        "you questions about a file.",
    },
    {
        "part": "when_to_train",
        "say": "Asking for the same kind of document again and again is the sign "
        "it is worth training: they send SMD a couple of examples they like.",
    },
    {
        "part": "close",
        "say": "Close with one line inviting more questions.",
    },
)


def _first(roots: tuple[Path, ...], rel: Path) -> Path:
    for root in roots:
        path = root / SKILL / rel
        if path.is_file():
            return path
    raise FileNotFoundError(f"{SKILL}/{rel.as_posix()} not found under any skill root")


def guide_body(text: str) -> str:
    """The reader-facing body of the guide: from ``## The guide`` on, Notes excluded."""
    at = text.find(GUIDE_MARKER)
    if at < 0:
        raise ValueError(f"guide carries no '{GUIDE_MARKER}' heading")
    body = text[at + len(GUIDE_MARKER) :]
    notes = body.find(_NOTES_MARKER)
    if notes >= 0:
        body = body[:notes]
    body = body.strip()
    if not body:
        raise ValueError("guide body is empty")
    if len(body) > GUIDE_CEILING_CHARS:
        raise ValueError(f"guide body exceeds {GUIDE_CEILING_CHARS} characters")
    return body


def read_guide(roots: tuple[Path, ...], *, drafts: bool = True) -> str:
    """The guide body from the first root that carries it. Raises when none will read.

    ``drafts`` false cuts :data:`DRAFT_SECTION` (to the next heading): a seat
    whose help requests offer this sender no drafting request is never told how
    to ask for one (2026-11-19, decided here in code, not left to a rule)."""
    body = guide_body(_first(roots, _GUIDE_REL).read_text(encoding="utf-8"))
    return body if drafts else without_section(body, DRAFT_SECTION)


def without_section(body: str, heading: str) -> str:
    """``body`` with the ``heading`` section removed, up to the next ``###``."""
    at = body.find(heading)
    if at < 0:
        return body
    rest = body[at + len(heading) :]
    nxt = rest.find("\n###")
    tail = rest[nxt + 1 :] if nxt >= 0 else ""
    return (body[:at] + tail).strip()


def read_tips(roots: tuple[Path, ...]) -> list[dict[str, str]]:
    """The tips, each ``{id, when, text}``. Raises when none will read or one is malformed."""
    import yaml

    data = yaml.safe_load(_first(roots, _TIPS_REL).read_text(encoding="utf-8"))
    tips = data.get("tips") if isinstance(data, dict) else None
    if not isinstance(tips, list):
        raise ValueError("tips.yaml carries no tips list")
    if len(tips) > TIPS_CEILING:
        raise ValueError(f"tips.yaml carries more than {TIPS_CEILING} tips")
    out: list[dict[str, str]] = []
    for tip in tips:
        if not isinstance(tip, dict) or not all(
            isinstance(tip.get(k), str) for k in ("id", "when", "text")
        ):
            raise ValueError("a tip lacks id/when/text")
        out.append({"id": tip["id"], "when": tip["when"], "text": tip["text"].strip()})
    return out


def guide_fields(
    bound: bool,
    roots: tuple[Path, ...],
    unreadable: list[dict[str, str]],
    requests: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> dict[str, Any]:
    """The help section's guide fields. Fail-open: a read fault is one entry, never a raise.

    ``bound`` is whether this seat enables ``operator-help``. A seat that does
    not carry the skill gets no guide text (the skill fence's rule: a skill the
    seat does not enable is not read), and that is a fact, not a fault. Nor does
    it get the answer shape or rules: they are the skill's wording (ADR 0100), so
    a seat without the skill is not handed them.
    """
    fields: dict[str, Any] = {
        "guide": None,
        "tips": [],
        # ss-console#3423 increment 3: the tip ids the review has emphasized,
        # in order. The help answer leads with these tips' own words.
        "emphasis": [],
        "answer_shape": [],
        "answer_rules": [],
    }
    if not bound:
        return fields
    fields["answer_shape"] = [dict(p) for p in ANSWER_SHAPE]
    fields["answer_rules"] = list(ANSWER_RULES)
    try:
        # The draft section only where this sender is offered a drafting request.
        drafts = any(r.get("skill") == DRAFT_SKILL and r.get("sender_may_ask") for r in requests)
        fields["guide"] = read_guide(roots, drafts=drafts)
    except Exception as exc:  # noqa: BLE001 -- the guide degrades, never the section
        unreadable.append({"section": "help", "reason": f"{SKILL} guide unreadable ({exc})"})
    try:
        fields["tips"] = read_tips(roots)
    except Exception as exc:  # noqa: BLE001
        unreadable.append({"section": "help", "reason": f"{SKILL} tips unreadable ({exc})"})
    fields["emphasis"] = _emphasis(fields["tips"], unreadable)
    return fields


def _emphasis(tips: list[dict[str, str]], unreadable: list[dict[str, str]]) -> list[str]:
    """The emphasized tip ids that are still authored, in order. Fail-open: a
    state file that cannot be read is one ``unreadable`` entry and no emphasis."""
    from . import help_review

    state = help_review.read_emphasis()
    if not state["read"]:
        unreadable.append({"section": "help", "reason": "tip emphasis unreadable"})
        return []
    known = {t["id"] for t in tips}
    return [t for t in state["emphasis"] if t in known]


__all__ = [
    "ANSWER_RULES",
    "ANSWER_SHAPE",
    "GUIDE_CEILING_CHARS",
    "GUIDE_MARKER",
    "NOT_IN_GUIDE_SAY",
    "SKILL",
    "guide_body",
    "guide_fields",
    "read_guide",
    "read_tips",
]
