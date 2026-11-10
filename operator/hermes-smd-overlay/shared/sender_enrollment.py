"""Invite-code enrollment, the gateway's half (ss-console ADR 0106).

A guide seat (``seat.role: guide``) or SMD's demo seat (``seat.kind: demo``,
amendment 2026-11-10) that authors ``sender_enrollment: {enabled: true}`` lets
a person holding an invite code SMD issued email the seat from an address
nobody listed and be added to the people the seat answers. The store, the codes and the decision belong to the
workspace broker (ss-console ``workspace_broker/sender_enrollment.py``); this
module is what the agent's process needs:

* :func:`enabled_in`: whether this seat switches enrollment on (the same test
  the broker applies to its own copy of the config);
* :func:`enrolled`: the enrolled list, read-only, from the file the broker
  writes (``enrolled.json``, 0640 broker-owned, readable through the
  ``audit-readers`` group). :class:`shared.customer_config.CustomerConfig`
  adds these addresses to :attr:`inbound_roster` on such a seat, which is how
  every consumer of the roster (the router's sender check, the reply relay,
  trust, initiation) treats an enrolled sender exactly as a rostered one;
* :func:`find_code`: the router's detector, run before any model turn;
* :func:`redeem` / :func:`issue`: the two broker verbs;
* the fixed wording: the welcome, SMD's notice, the name rule.

THE CODE NEVER LEAVES CODE. The router hands it to the broker and drops the
delivery (``{"action": "skip"}``), so no model turn starts, and nothing here
logs, audits or returns it. An administrator's request for a code
(:func:`issue`) returns only the code's id: the broker composes and sends the
message that carries it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENROLLED_ENV = "SMD_SENDER_ENROLLMENT_ENROLLED"
DEFAULT_ENROLLED = "/opt/data/sender-enrollment/enrolled.json"
BLOCK = "sender_enrollment"
ROLE = "guide"
#: SMD's demo seat (ss-console ADR 0107 purpose demo): sales representatives
#: enroll on it the way they do on the guide. Read from ``seat.kind``, which
#: the seat carries; ``seat.yaml``'s purpose never reaches the seat.
KIND = "demo"

#: Mirrors ss-console ``workspace_broker/sender_enrollment.py`` CODE_PATTERN;
#: ``tests/sender-enrollment.test.ts`` pins the two literals equal.
CODE_PATTERN = r"\bSMD-[A-HJKMNP-TV-Z2-9]{4}-[A-HJKMNP-TV-Z2-9]{4}-[A-HJKMNP-TV-Z2-9]{4}\b"
_CODE_RE = re.compile(CODE_PATTERN, re.IGNORECASE)

#: A person's name as an administrator gives it. Mirrors the broker's
#: NAME_PATTERN (same test pins both): letters (any script), spaces,
#: apostrophes, hyphens and periods, 1-60 characters, starting with a letter.
NAME_MAX = 60
NAME_PATTERN = r"^[^\W\d_](?:[^\W\d_]|[ '.\-])*$"
_NAME_RE = re.compile(NAME_PATTERN)
NAME_REFUSAL = (
    "Please give the person's name as plain text: letters, spaces, apostrophes, "
    "hyphens and periods only, on one line, no links or addresses, at most 60 characters."
)

WELCOME_SUBJECT = "You are connected to the SMD Services product guide"
DEMO_WELCOME_SUBJECT = "You are connected to the SMD Services demo Operator"
_WELCOME_BODY = (
    "{greeting}\n\n"
    "This address is now connected to the SMD Services product guide. Reply here, or "
    "write a new email to this address, with any question about the Operator. Each "
    "answer comes from the guide's product library and names the document and section "
    "it came from. You can also ask to practice a pitch.\n\n"
    "If you did not expect this message, tell SMD Services and we will disconnect this "
    "address.\n"
)

#: The welcome on the demo seat. It says what the seat is (a real Operator
#: working for a fictional firm) so nobody mistakes a demo answer for a client's.
_DEMO_WELCOME_BODY = (
    "{greeting}\n\n"
    "This address is now connected to the SMD Services demo Operator. It works for a "
    "fictional law firm on fictional matters, and every reply comes from the product "
    "itself. Email it here the way a member of the firm would.\n\n"
    "If you did not expect this message, tell SMD Services and we will disconnect this "
    "address.\n"
)

#: On a guide seat that authors representative training (``shared.training``),
#: the welcome introduces the guide in its own voice and says what the person
#: can try. The Captain's approved text (2026-11-10), fixed, no model turn. It
#: greets the person by the first name their access was issued for.
_TRAINING_WELCOME_BODY = (
    "{greeting}\n\n"
    "I'm the SMD Services guide. I'm here to help you understand how the Operator works, "
    "and to be useful to you while you're out talking to people about it.\n\n"
    "There's nothing to learn about using me. Just talk to me the way you'd talk to a "
    "colleague who knows the product well.\n\n"
    "A few things you can try:\n\n"
    "- Ask me anything about the Operator. What it does, how it fits into a business, how "
    "to explain it to someone.\n"
    '- Let me walk you through it. Say "start training" and I\'ll take you through it a '
    "piece at a time, with a little practice along the way.\n"
    "- Practice a conversation. I'll play a business owner with questions and doubts, then "
    "tell you how it went.\n"
    "- Keep your notes and leads with me. Tell me who you met and what they said, and ask "
    "for it back whenever you need it.\n"
    '- Have me remind you. "Remind me Thursday at 4 to call them back" is all it takes.\n'
    "- Get ready for a meeting, or follow up after one. I'll pull together what you've told "
    "me, or draft a follow-up for you to send.\n\n"
    "Start wherever you like. If you're not sure, just say hello and tell me what you're "
    "working on.\n"
)


def first_name(name: str | None) -> str | None:
    """The first word of the name an invite was issued for, when it is valid."""
    return name.split()[0] if valid_name(name) else None


NOTICE_SUBJECT = "Guide seat: a new person connected with an invite code"
DEMO_NOTICE_SUBJECT = "Demo seat: a new person connected with an invite code"
_NOTICE_BODY = (
    "{address} connected to the {what} seat {slug} with invite code {code_id}"
    "{named}.\n\n"
    "They are now answered like a listed person on this seat. To disconnect them, run "
    "sender_enrollment_cli remove {address} on the seat.\n"
)


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFC", value).strip().lower() if isinstance(value, str) else ""


def seat_allows(seat: Any) -> bool:
    """Whether this seat descriptor may switch enrollment on at all: a guide
    seat, or SMD's demo seat. Every other seat's roster is its client's call."""
    return isinstance(seat, dict) and (seat.get("role") == ROLE or seat.get("kind") == KIND)


def is_demo(data: Any) -> bool:
    """True on SMD's demo seat (``seat.kind: demo``); selects the demo wording."""
    seat = data.get("seat") if isinstance(data, dict) else None
    return isinstance(seat, dict) and seat.get("kind") == KIND and seat.get("role") != ROLE


def enabled_in(data: Any) -> bool:
    """True only on a guide or demo seat whose config says ``enabled: true``."""
    if not isinstance(data, dict):
        return False
    seat, block = data.get("seat"), data.get(BLOCK)
    return seat_allows(seat) and isinstance(block, dict) and block.get("enabled") is True


def _path() -> Path:
    return Path(os.environ.get(ENROLLED_ENV) or DEFAULT_ENROLLED)


def enrolled() -> list[dict[str, str]]:
    """Every enrolled entry. Missing or unreadable is nobody (the closed
    direction: fewer people answered, never more)."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001 - unreadable lists nobody, and says so
        logger.warning("sender_enrollment: enrolled list unreadable; nobody enrolled is answered")
        return []
    entries = raw.get("enrolled") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and _norm(e.get("address"))]


def enrolled_addresses() -> list[str]:
    return sorted({_norm(e["address"]) for e in enrolled()})


def enrolled_name(address: Any) -> str | None:
    """The name SMD issued this enrolled person's code for, re-validated, or None."""
    wanted = _norm(address)
    for entry in enrolled():
        if _norm(entry.get("address")) == wanted:
            name = entry.get("name")
            return name if valid_name(name) else None
    return None


def find_code(*texts: Any) -> str | None:
    for text in texts:
        if isinstance(text, str):
            found = _CODE_RE.search(text)
            if found:
                return found.group(0).upper()
    return None


def valid_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= NAME_MAX
        and value == value.strip()
        and "  " not in value
        and _NAME_RE.match(value) is not None
    )


def welcome_text(name: str | None, *, demo: bool = False, training: bool = False) -> str:
    """The fixed welcome. ``demo`` is the demo seat's; ``training`` (a guide
    seat that authors representative training) is the guide's own introduction,
    greeting the person by first name. Every other seat's welcome is unchanged."""
    if demo:
        return _DEMO_WELCOME_BODY.format(
            greeting=f"Hello {name}," if valid_name(name) else "Hello,"
        )
    if training:
        first = first_name(name)
        return _TRAINING_WELCOME_BODY.format(greeting=f"Hi {first}," if first else "Hi,")
    return _WELCOME_BODY.format(greeting=f"Hello {name}," if valid_name(name) else "Hello,")


def notice_subject(*, demo: bool = False) -> str:
    return DEMO_NOTICE_SUBJECT if demo else NOTICE_SUBJECT


def notice_text(
    *, address: str, slug: str, code_id: str, name: str | None, demo: bool = False
) -> str:
    named = f", issued for {name}" if valid_name(name) else ""
    return _NOTICE_BODY.format(
        address=address,
        what="demo" if demo else "guide",
        slug=slug,
        code_id=code_id,
        named=named,
    )


def redeem(
    *, message_id: str, sender: str, code: str, authenticated: bool, auth_reason: str
) -> dict:
    """Ask the broker to redeem one code. Raises on a transport fault."""
    from shared.workspace_broker import request

    return request(
        {
            "action": "sender_enrollment_redeem",
            "payload": {
                "message_id": message_id,
                "sender": sender,
                "code": code,
                "authenticated": bool(authenticated),
                "auth_reason": auth_reason,
            },
        },
        timeout=25.0,
    )


def issue(*, message_id: str, admin: str, name: str, days: int, max_uses: int) -> dict:
    """Ask the broker to issue a code for ``name`` and send it to ``admin``,
    in the thread of ``message_id``. Returns ``{code_id, sent}``; never the code."""
    from shared.workspace_broker import request

    return request(
        {
            "action": "sender_enrollment_issue",
            "payload": {
                "message_id": message_id,
                "admin": admin,
                "name": name,
                "days": int(days),
                "max_uses": int(max_uses),
            },
        },
        timeout=25.0,
    )


__all__ = [
    "CODE_PATTERN",
    "NAME_PATTERN",
    "NAME_REFUSAL",
    "enabled_in",
    "enrolled",
    "enrolled_addresses",
    "enrolled_name",
    "first_name",
    "find_code",
    "issue",
    "notice_text",
    "redeem",
    "valid_name",
    "welcome_text",
]
