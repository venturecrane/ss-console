"""Deterministic renderer for the verification tracker's internal alert.

WS-RENDER: the chase's internal escalations (hold surfaces, ceiling hand-offs,
the config-missing note, and the degraded chase-due line) are rendered HERE, in
code, from the pre_run's own plans — the model composes nothing. Every phrase
is authored client-facing content under the no-fabrication policy: the words
come from this file's closed situation map, the values come from the pre_run's
plans and its connector pull, and an unknown situation renders NOTHING.

THE CHASE FAIL-CLOSED RULE (build fork 2, probed 2026-08-31): the client
reminder template (references/verification-request.md Draft 2) needs a
``{return_link}`` and NO authored source for one exists on any seat. Until the
firm authors ``settings.return_link`` on this skill, a due chase degrades to
an internal surface line — a person sends the reminder — and no ``chased``
event is recorded (nothing was chased). ``render_chase`` exists, tested, for
the day the setting is authored; authoring a value is a Captain decision, not
this module's.

No em dashes; matter by number, never caption, never a GUID; the floor-clean
substitutions of verification-request.md hold everywhere.

Stdlib only; path-loaded sibling of ``pre_run.py``.
"""

from __future__ import annotations

import hashlib

# ---------------------------------------------------------------------------
# canonical_body_sha256 — the ONE hash function (cross-workstream contract).
# Same definition as the escalator's render.py and the console verifier;
# arbiter fixture: operator/contracts/fixtures/body-canon-vectors.json.
# ---------------------------------------------------------------------------


def canonical_body_sha256(text: str) -> str:
    """CRLF->LF, per-line trailing whitespace stripped, trailing newlines
    stripped, sha256 over utf-8."""
    normalized = text.replace("\r\n", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    canonical = "\n".join(lines).rstrip("\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The situation map — CLOSED, authored. Keyed by the pre_run plan's action
# (plus its reason qualifier). Anything else renders nothing.
# ---------------------------------------------------------------------------

_SITUATION_HOLD = "held for a person; the block recorded on this matter is unresolved"
_SITUATION_HOLD_STALE = "the facts an earlier release rested on have changed; a person decides before any chase resumes"
_SITUATION_HANDOFF = (
    "chase attempts reached the authored ceiling; the client chase is stopped "
    "and the open item needs the responsible person"
)
_SITUATION_CHASE_UNROUTABLE = (
    "a client reminder is due, and the reminder's return destination is not authored; a person should send it"
)
_SITUATION_CONFIG = (
    "chase cadence or escalation attempt-count is not authored; the client "
    "verification chase is held until it is authored"
)


def situation_line(plan: dict) -> str | None:
    """The authored situation phrase for one plan entry, or None (render
    nothing for an action this map does not know)."""
    action = plan.get("action")
    if action == "surface_hold":
        if plan.get("reason") == "determination_stale":
            return _SITUATION_HOLD_STALE
        return _SITUATION_HOLD
    if action == "handoff":
        return _SITUATION_HANDOFF
    if action == "chase":
        # Reaching the renderer at all means the chase could not fully render
        # (return_link unauthored) — the fail-closed degradation.
        return _SITUATION_CHASE_UNROUTABLE
    if action == "surface_config_missing":
        return _SITUATION_CONFIG
    return None


def _is_seat_level(entry: dict) -> bool:
    """A seat-level entry is about the seat's authored config, not a matter,
    so its line carries no matter head. Two shapes today: the config-missing
    surface, and the one throttled line every due chase collapses to when
    ``return_link`` is unauthored (``dispatch_envelope`` emits it with
    ``matter_id: ""``). Giving that line a matter head reads as a matter
    whose number could not be resolved, a failure that did not happen."""
    return entry.get("action") == "surface_config_missing" or (
        entry.get("action") == "chase" and entry.get("reason") == "return_link_unauthored"
    )


def _matter_head(entry: dict) -> str:
    number = entry.get("matter_number")
    if isinstance(number, str) and number:
        return f"matter {number}"
    if entry.get("matter_number_absent") == "no_number_on_record":
        return "no number on record"
    return "matter number unavailable"


def render_alert(entries: list[dict], *, today_iso: str) -> tuple[str, str]:
    """Render (subject, body) for one recipient set's internal alert.

    ``entries`` are the pre_run plans for this recipient's matters, each
    already carrying ``matter_number`` / ``matter_number_absent`` from the
    pull's code join (never composed). Entries whose action the situation map
    does not know are dropped by the CALLER (an unknown signal renders
    nothing); counts here are list lengths by construction.
    """
    lines: list[str] = []
    lines.append(f"## Needs a person ({len(entries)})")
    lines.append("")
    for index, entry in enumerate(entries, start=1):
        phrase = situation_line(entry) or ""
        attempt = entry.get("attempt")
        ceiling = entry.get("ceiling")
        tail = ""
        if entry.get("action") in ("chase", "handoff") and attempt and ceiling:
            tail = f" (nudge {attempt} of {ceiling})"
        if _is_seat_level(entry):
            lines.append(f"{index}. {phrase}.")
        else:
            lines.append(f"{index}. {_matter_head(entry)}, verification: {phrase}{tail}.")
    lines.append("")
    lines.append("This is an internal alert to a person at the firm; no client message has been sent.")
    subject = f"[Verifications] {len(entries)} need attention, {today_iso}"
    return subject, "\n".join(lines) + "\n"


def render_skeleton(count: int) -> str:
    """Authored minimal fallback body: counts only, zero identifiers, zero
    dates."""
    noun = "item" if count == 1 else "items"
    return (
        "## Verification tracker (details unavailable)\n"
        "\n"
        f"{count} {noun} need a person, but the detailed alert could not be "
        "delivered this run. Open Smokeball or the tracker view for the "
        "items; the next run will retry.\n"
        "\n"
        "This is an internal alert to a person at the firm; no client message "
        "has been sent.\n"
    )


# ---------------------------------------------------------------------------
# The client reminder (Shape B), for the day settings.return_link is authored.
# verification-request.md Draft 2 VERBATIM, with exactly two substitutions.
# ---------------------------------------------------------------------------

_CHASE_TEMPLATE = (
    "Hi {signer_first_name}, following up on the verification for your "
    "discovery responses: it is still open and we have not received it back "
    "yet. When you have a few minutes, add your name and the date where the "
    "form shows and return it here: {return_link}. There is a due date on "
    "this one, so getting it back soon keeps your case on track. Happy to "
    "answer any questions with the team."
)


def render_chase(*, signer_first_name: str, return_link: str) -> str | None:
    """Draft 2 with the two slots substituted from AUTHORED/READ values, or
    None when either is missing — a chase that cannot fully render is not a
    chase (fail-closed to the surface path, never a model-composed reminder).
    """
    if not (isinstance(signer_first_name, str) and signer_first_name.strip()):
        return None
    if not (isinstance(return_link, str) and return_link.strip()):
        return None
    return _CHASE_TEMPLATE.format(signer_first_name=signer_first_name.strip(), return_link=return_link.strip()) + "\n"


def authored_return_link(customer_yaml: dict) -> str | None:
    """The optional authored ``settings.return_link`` on THIS skill's per-skill
    settings block. Unset (the live state on every seat) -> None -> every due
    chase degrades to a surface. Reading it here defines the seam; authoring a
    value is the Captain's follow-up."""
    if not isinstance(customer_yaml, dict):
        return None
    for persona in customer_yaml.get("personas") or []:
        if not isinstance(persona, dict):
            continue
        for entry in persona.get("skills") or []:
            if not isinstance(entry, dict) or entry.get("name") != "client-verification-tracker":
                continue
            settings = entry.get("settings")
            if isinstance(settings, dict):
                link = settings.get("return_link")
                if isinstance(link, str) and link.strip():
                    return link.strip()
            return None
    return None


#: The one-line failure note for the deploy-skew window / delivery fault
#: (SKILL.md; the in-turn rendered-body check accepts exactly this text).
FAILURE_NOTE = (
    "The verification tracker run failed and needs attention; no alert was "
    "delivered this run. The items are in Smokeball and the tracker view."
)

#: Subject for the failure note when the GATE dispatches it out of turn rather
#: than instructing the turn to send it (2026-09-02). No date and no counts:
#: the run that sends this could read nothing, so every number in it would be
#: invented.
FAILURE_NOTE_SUBJECT = "[Verifications] run failed, no alert delivered"
