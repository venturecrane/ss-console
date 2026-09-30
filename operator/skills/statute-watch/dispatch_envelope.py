"""The statute report's pre-rendered dispatch envelope (WS-RENDER).

``pre_run.py`` renders the whole message and this module packs it into the
consume-once file the overlay's ``shared/prerendered_dispatch.py`` reads at
``pre_llm_call``: ONE dispatch, to the one authored recipient, full body and
identifier-free skeleton, each with its canonical body hash. The overlay sends
it out of turn through the full gate (full body first, skeleton if the full
body is refused, nothing if both are).

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


def build(
    *,
    recipient: str,
    subject: str,
    full_body: str,
    skeleton_body: str,
    started_at: str,
    body_hash,
) -> dict:
    """The envelope, ready to write. ``body_hash`` is the shared canonical hash
    (``skill_helpers.canonical_body_sha256``), passed in so this module stays
    free of imports."""
    return {
        "skill": SKILL_NAME,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [
            {
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
        ],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "statute_report_skeleton", "template": skeleton_body, "slots": {}}],
        "in_turn_enforce": True,
    }


def wake_stamps(envelope: dict) -> list[dict]:
    """The per-dispatch hash pair the EMITTED_WAKE row carries, so the daily
    send reconcile can match the sent body to what this run rendered."""
    return [
        {"body_sha256_full": d["body_sha256_full"], "body_sha256_skeleton": d["body_sha256_skeleton"]}
        for d in envelope.get("dispatches") or []
    ]
