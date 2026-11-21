"""REVISE OR DRAFT: ``drafting_job_submit``'s routing step (``drafting_verbs``
check 7), decided from the job store, never by the model.

A request is a revision when a draft of its class was DELIVERED on its matter
and the request email is in that delivery's email thread (the same Graph
conversation as the email that asked for the draft). Root may name the
delivered job (``revise_of``: the ops workspace's rehearsal). A thread that
could not be read refuses: queuing a fresh draft on a guess is the $56 re-read
of 2026-11-04.

A discovery response revises EVERY document its request filed, one unit per
file id (``drafting_revise_units``, 2026-11-21); any other class revises its
one filed draft, and a delivery of several drafts of another class drafts
afresh, as before.
"""

from __future__ import annotations

from typing import Any, Callable

from . import drafting_revise_units as revise_units
from .drafting_threads import ThreadUnknown


class RouteRefused(str):
    """A routing refusal: its sentence, and the closed code it carries."""

    code: str

    def __new__(cls, reason: str, code: str) -> RouteRefused:
        out = super().__new__(cls, reason)
        out.code = code
        return out


def _unanswered() -> RouteRefused:
    return RouteRefused(
        "this email could not be matched to the email thread of the draft already filed on the matter "
        "(the mailbox did not answer), so it is not known whether it asks to revise that draft; "
        "nothing was queued",
        "mailbox_unanswered",
    )


def route(
    db: Any,
    lookup: Callable[[], Callable[[str], str] | None],
    envelope: dict[str, Any],
    revise_of: str,
    peer_uid: int | None,
) -> dict[str, Any] | RouteRefused | None:
    """The runner's ``revise`` block, None for a fresh draft, or a refusal.
    ``lookup`` gives the mailbox's conversation-id lookup as it stands now."""
    whole_set = envelope["document_class"] == revise_units.SET_CLASS
    delivered = db.delivered_on_matter(
        envelope["matter"]["id"], envelope["document_class"], revise_units.MAX_JOBS if whole_set else 10
    )
    thread_of = lookup() if delivered else None
    if revise_of:
        if peer_uid != 0:
            return RouteRefused("revise_of is accepted from root only; nothing was queued", "revise_of_root_only")
        prior = next((r for r in delivered if r["id"] == revise_of), None)
        if prior is None:
            return RouteRefused(
                f"job {revise_of} is not a delivered draft of this class on this matter; nothing was queued",
                "revise_of_unknown",
            )
    else:
        if thread_of is None:
            return None
        try:
            here = thread_of(envelope["request_ref"])
            prior = next((r for r in delivered if here and thread_of(r["request_ref"]) == here), None)
        except ThreadUnknown:
            return _unanswered()
        if prior is None:
            return None
    if whole_set:
        return _route_set(db, delivered, prior, thread_of)
    files = db.proven_files(prior["id"])
    if not files or not prior.get("folder_id"):
        return RouteRefused(
            f"the draft filed as job {prior['id']} cannot be identified on the matter by file id, so it cannot be "
            "revised in place; nothing was queued",
            "prior_unidentified",
        )
    if sum(1 for f in files if f.get("role") == "draft") != 1:
        # This runner edits ONE filed draft (revise/run.py). Several drafts in one
        # delivery are a discovery response's sets, revised whole by _route_set.
        return None
    return {"prior_job_id": prior["id"], "folder_id": prior["folder_id"], "files": files}


def _route_set(
    db: Any, delivered: list[dict[str, Any]], prior: dict[str, Any], thread_of: Callable[[str], str] | None
) -> dict[str, Any] | RouteRefused:
    """Every document the request filed, by file id (``drafting_revise_units``)."""
    try:
        here = thread_of(prior["request_ref"]) if thread_of is not None else ""
        block, bad = revise_units.collect(
            db,
            delivered,
            lambda r: r["id"] == prior["id"] or bool(here and thread_of and thread_of(r["request_ref"]) == here),
        )
    except ThreadUnknown:
        return _unanswered()
    if block is None:
        return RouteRefused(
            f"the draft filed as job {bad} cannot be identified on the matter by file id, so the filed "
            "responses cannot be revised in place; nothing was queued",
            "prior_unidentified",
        )
    return block


__all__ = ["RouteRefused", "route"]
