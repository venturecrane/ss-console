"""The statute report's pre-rendered dispatch envelope (WS-RENDER).

``pre_run.py`` renders the whole message and this module packs it into the
consume-once file the overlay's ``shared/prerendered_dispatch.py`` reads at
``pre_llm_call``: ONE dispatch, to the one authored recipient, full body and
identifier-free skeleton, each with its canonical body hash, and the workbook
as the one ``attachments`` entry with its ``body_without_attachment`` rung. The
overlay sends it out of turn through the full gate (full body with the
workbook first, the full body without it if the workbook is refused, the
skeleton if the body is refused, nothing if all are).

``appends`` is empty: a report raises nothing and acknowledges nothing, so a
successful send writes no ledger row.

``in_turn`` declares the skeleton as the ONLY body the woken turn may send in
this session, and ``in_turn_enforce`` makes the overlay's rendered-body check
block anything else. The turn has nothing to send; the declaration is what
stops it from composing a report of its own if it tried.

``started_at`` is the moment of writing, AFTER the pull. The overlay binds an
envelope only while it is younger than twenty minutes, and the pull alone
takes about twelve; a stamp taken when the script began would expire on a slow
morning before the turn that dispatches it ever started.
"""

from __future__ import annotations

SKILL_NAME = "statute-watch"

#: The authored-recipient leg: one address named in the skill's settings.
ROUTING_LEG = "central"

#: The one attachment type the overlay and the broker accept (pinned interface).
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def attachment_name(month: str, year: int) -> str:
    """``Statute watch October 2026.xlsx``: a month and a year, never a day."""
    return "Statute watch " + month + " " + str(year) + ".xlsx"


def build(
    *,
    recipient: str,
    subject: str,
    full_body: str,
    skeleton_body: str,
    started_at: str,
    body_hash,
    attachment: dict | None = None,
    body_without_attachment: str | None = None,
) -> dict:
    """The envelope, ready to write. ``body_hash`` is the shared canonical hash
    (``skill_helpers.canonical_body_sha256``), passed in so this module stays
    free of imports.

    ``attachment`` is the workbook (``{"name", "content_b64", "sha256"}``);
    with it the dispatch carries the pinned ``attachments`` list and the
    middle rung of the overlay's ladder: ``body_without_attachment``, the full
    body saying the workbook could not be attached, sent when the attachment
    is refused and the body is not. Without it (the workbook could not be
    built) the full body already says so and neither field is present."""
    dispatch = {
        "recipients": [recipient],
        "cc": [],
        "routing_leg": ROUTING_LEG,
        "subject": subject,
        "full_body": full_body,
        "skeleton_body": skeleton_body,
        "body_sha256_full": body_hash(full_body),
        "body_sha256_skeleton": body_hash(skeleton_body),
        "appends": [],
    }
    if attachment is not None and body_without_attachment is not None:
        dispatch["attachments"] = [
            {
                "name": attachment["name"],
                "content_type": XLSX_CONTENT_TYPE,
                "content_b64": attachment["content_b64"],
                "sha256": attachment["sha256"],
            }
        ]
        dispatch["body_without_attachment"] = body_without_attachment
        dispatch["body_sha256_without_attachment"] = body_hash(body_without_attachment)
    return {
        "skill": SKILL_NAME,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [dispatch],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "statute_report_skeleton", "template": skeleton_body, "slots": {}}],
        "in_turn_enforce": True,
    }


def wake_stamps(envelope: dict) -> list[dict]:
    """The per-dispatch hash pair the EMITTED_WAKE row carries, so the daily
    send reconcile can match the sent body to what this run rendered. A
    dispatch carrying the workbook adds its no-attachment body hash: the
    overlay's middle rung sends that body (``body_variant``
    ``full_no_attachment``), and the verifier grades it degraded, not
    diverged, only when the wake row names it."""
    out = []
    for d in envelope.get("dispatches") or []:
        stamp = {"body_sha256_full": d["body_sha256_full"], "body_sha256_skeleton": d["body_sha256_skeleton"]}
        if d.get("body_sha256_without_attachment"):
            stamp["body_sha256_without_attachment"] = d["body_sha256_without_attachment"]
        out.append(stamp)
    return out
