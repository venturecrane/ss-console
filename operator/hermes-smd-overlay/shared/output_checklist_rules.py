"""The output checklist's rules, written for the writer before the first draft.

WHY. On the A&P seat in November 2026, every reply the checklist held was
redrafted 8 to 24 seconds later: each hold cost a model turn and left a draft in
the Operator mailbox. The model met the rules only in the refusal. These lines
go into SOUL.md (``bootstrap/translate.py``) so the first draft already follows
them.

They are read from the rule set the gate enforces (``output_checklist.
surface_rules``), for the seat's checklist profile, so the guidance and the gate
cannot name different rules. This states the rules; it loosens none of them.
Every line passes the staff checklist itself (pinned by a test).
"""

from __future__ import annotations

from typing import Any

from shared.output_checklist import PROFILE_HOUSEHOLD, PROFILE_PARALEGAL, STAFF_SEND, surface_rules

_PLAIN_TEXT_LINE = (
    "Plain text only: no pound-sign headings, asterisks, underscores, backticks, "
    "tables or html tags."
)
_STAMP_LINE = "Never a time in another zone, such as Greenwich time, or a machine timestamp."

#: Each rule as the writer meets it. Rules that share a line are written once.
WRITER_LINES: dict[str, str] = {
    "em_dash": "No em or en dashes. Use a comma or a period.",
    "exclamation": "No exclamation marks.",
    "caps_emphasis": (
        "No words in capitals for emphasis. Write a folder or file name the firm spells "
        "in capitals in ordinary case, or quote it exactly inside double quotes "
        '("CLIENT DOCUMENTS").'
    ),
    "heading_marks": _PLAIN_TEXT_LINE,
    "emphasis_marks": _PLAIN_TEXT_LINE,
    "pipe_table": _PLAIN_TEXT_LINE,
    "html_tag": _PLAIN_TEXT_LINE,
    "bare_clock": "A clock time carries a.m. or p.m., in the firm's local time.",
    "utc_time": _STAMP_LINE,
    "iso_timestamp": _STAMP_LINE,
    "internal_id": (
        "No internal ids or codes. Name a matter by its number and client, an entry "
        "by its subject and date, a document by its file name."
    ),
}

#: The same rules for a profile that is not a firm (a couple's or a family's
#: own messages): no firm, no matter, no client in the wording.
_WRITER_LINES_BY_PROFILE: dict[str, dict[str, str]] = {
    PROFILE_HOUSEHOLD: {
        "bare_clock": "A clock time carries a.m. or p.m., in local time.",
        "internal_id": (
            "No internal ids or codes. Name an entry by its subject and date, "
            "a document by its file name."
        ),
    },
}


def writing_rules(surface: str = STAFF_SEND, profile: str = PROFILE_PARALEGAL) -> list[str]:
    """The lines a writer follows so ``surface`` holds nothing, for ``profile``."""
    rules, ceiling = surface_rules(surface, profile)
    words = {**WRITER_LINES, **_WRITER_LINES_BY_PROFILE.get(profile, {})}
    lines = [words.get(rule, "") for rule in rules]
    if ceiling is not None:
        lines.append(f"At most {ceiling} lines.")
    return [line for line in dict.fromkeys(lines) if line]


def soul_rules(customer: dict[str, Any], roles_dir: str | None = None) -> str:
    """The SOUL.md section stating the send checklist, closing the file.

    The profile is the seat's (``policy_profile.current``, reading the role
    package from ``roles_dir``, the root the role persona came from; it never
    raises, and a policy it cannot read is the strict set). A firm's seat is
    told about the firm's staff; a household seat is not."""
    from shared import policy_profile

    profile = policy_profile.current(customer, roles_dir_override=roles_dir).checklist_profile
    body = "\n".join(f"- {line}" for line in writing_rules(STAFF_SEND, profile))
    if profile == PROFILE_HOUSEHOLD:
        heading, lead = "Writing a message", "A message you send"
    else:
        heading, lead = "Writing a message to staff", "A message to the firm's staff"
    return (
        f"\n\n## {heading}\n\n"
        f"{lead} is checked before it goes, and one that breaks any of these is held "
        "and has to be written again. Write it this way the first time:\n"
        f"{body}\n"
    )


__all__ = ["WRITER_LINES", "soul_rules", "writing_rules"]
