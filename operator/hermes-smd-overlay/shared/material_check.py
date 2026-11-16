"""The material check on a guide seat: what a representative's draft is, decided by code.

A representative asks the guide to look over a draft before it goes out: a
post, deck text, a one-pager, a proposal draft, an email, their own or someone
else's (a colleague's draft, a competitor's post), pasted into their email or
forwarded in it. The ``material_check`` tool (``hermes-smd-training``) counts
the seat's tone misses (:func:`shared.training.tone_misses`, the rules authored
as ``rep-training/references/tone-rules.yaml``) over the text this module
decides is the material, and records NOTHING: checking a rough draft, or
someone else's wording, never moves anyone's readiness.

WHAT IS READ. The representative's own words in the verified email that opened
the turn (``InboundOrigin.reply_text``, the provider's quoted-history-free
text), always; the rest of the body (``InboundOrigin.body_text``) only when the
email is a forward, by its subject ("Fwd:", "Fw:") or by a mail client's
forwarded-message line in the body. The quoted history of an ordinary reply is
earlier conversation, not material, so it is never read. An attachment is never
read: the result says so, and says to paste the text. Nothing the model passes
is read; the tool takes no arguments.

WHAT IS RETURNED, so the reply can say exactly what was checked: the count of
characters read, whether forwarded text was included, and one sentence rendered
here that the reply opens with.

WHO. The person is :func:`shared.training.turn_person`: the strict per-turn
verified sender, on the seat's roster now. Anyone else gets
:data:`shared.training.NO_PERSON`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shared import training

#: A subject that says the email is a forward ("Fwd:", "Fw:", "FW:").
_FORWARD_SUBJECT = re.compile(r"^\s*(?:fwd?|fw)\s*:", re.IGNORECASE)
#: The lines mail clients write at the top of a forwarded message.
_FORWARD_MARKER = re.compile(
    r"^\s*(?:-{2,}\s*forwarded message\s*-{2,}|begin forwarded message:"
    r"|-{2,}\s*original message\s*-{2,})\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ATTACHMENTS = (
    "Attached files are not read; paste a deck or document's text into the email to have "
    "it checked."
)


@dataclass(frozen=True)
class Material:
    """What the check read: the own words, plus the forwarded part of a forward."""

    text: str
    own_chars: int
    forwarded_chars: int

    @property
    def forwarded_included(self) -> bool:
        return self.forwarded_chars > 0

    def checked_sentence(self) -> str:
        """One sentence, rendered by code, naming what was checked."""
        total = len(self.text)
        if self.forwarded_included and self.own_chars:
            what = f"Checked {total} characters: your own words and the forwarded message."
        elif self.forwarded_included:
            what = f"Checked {total} characters of the forwarded message."
        else:
            what = f"Checked {total} characters of your own words; nothing forwarded was included."
        return f"{what} {_ATTACHMENTS}"


def material_text(reply_text: str, body_text: str, subject: str) -> Material:
    """The material, from the verified email's own words, whole body and
    subject (see module docstring, WHAT IS READ). Pure and deterministic."""
    own = (reply_text or "").strip()
    body = (body_text or "").strip()
    forwarded = ""
    is_forward = bool(_FORWARD_SUBJECT.match(subject or "")) or bool(_FORWARD_MARKER.search(body))
    if is_forward and body:
        # The own words are part of the body; drop them once so nothing is
        # counted twice.
        at = body.find(own) if own else -1
        rest = (body[:at] + body[at + len(own) :]) if at >= 0 else body
        forwarded = rest.strip()
    text = "\n\n".join(part for part in (own, forwarded) if part)
    return Material(text=text, own_chars=len(own), forwarded_chars=len(forwarded))


def turn_material(session_id: Any) -> tuple[training.TurnPerson | None, Material | None]:
    """The verified sender of this turn and the material their email carries.
    ``(None, None)`` when the turn has no verified sender on the roster."""
    from shared.inbound import SESSION_INBOUND_ORIGIN

    person = training.turn_person(session_id)
    if person is None:
        return None, None
    origin = SESSION_INBOUND_ORIGIN.bound_this_turn(session_id)
    material = material_text(
        person.reply_text,
        getattr(origin, "body_text", "") or "",
        getattr(origin, "subject", "") or "",
    )
    return person, material


def check(session_id: Any) -> dict[str, Any]:
    """The tool's whole result: tone misses over the material, and what was
    read. Writes nothing. Raises :class:`shared.training.TrainingError` when
    the tone rules cannot be read."""
    person, material = turn_material(session_id)
    if person is None or material is None:
        return {"ok": False, "say": training.NO_PERSON}
    if not material.text:
        return {
            "ok": False,
            "say": "This email carries no text to check. Paste the draft into the email, or "
            "forward it; attached files are not read.",
        }
    misses = training.tone_misses(material.text, training.load_tone_rules())
    return {
        "ok": True,
        "misses": sum(m.count for m in misses),
        "rules_missed": [{"rule": m.rule, "times": m.count, "fix": m.fix} for m in misses],
        "chars_checked": len(material.text),
        "forwarded_included": material.forwarded_included,
        "checked": material.checked_sentence(),
    }


__all__ = ["Material", "check", "material_text", "turn_material"]
