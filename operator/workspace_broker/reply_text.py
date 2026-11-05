"""Each job lane's status read carries the reply its job's bind will hand the
model, rendered by the same code (``fixed_wording.render``, through
``fixed_texts`` here, which the bind and the send call too), so the DELIVER turn reads one text from either
tool and never composes one from the counts beside it (mechanism 4, ADR 0104).

ONE RENDERER. The status tools in the overlay project what the broker returns
and render nothing themselves: rendering from projected counts in the overlay
would be a second renderer that could disagree with the one the send enforces.

THE KEYS. ``reply_text`` is the text when the job's reply has exactly one;
``reply_texts`` lists them when a held wording leaves a choice (a staff name
on a row that predates its recorded request names). Neither key appears when
the job has no reply in its state (a failed job, a running one, a held
chronology): the binding refuses those, and the status read says nothing it
would not send.

THE SWITCH (``logic_in_code.fixed_wording`` in the seat's customer.yaml; the
reading rule is ``logic_in_code``'s, and it is read fresh on every call).

* ``enforce``: every worded job's reply is its fixed wording; the status read
  carries it and the send refuses any other words.
* ``observe``: the firm's reply is what it is today. Neither the status read nor
  the bind carries any text (the model reads tool results, so a key there could
  change what it writes), and the send renders on the broker side only, sends the
  model's words and records whether they differed. The one exception is what refused before this
  switch existed: a held litigation or case file reply has been refused other
  words since the 2026-10-24 incident, and observing must not reopen that.
"""

from __future__ import annotations

import functools
import logging
import sqlite3
from typing import Any, Callable

from .broker_context import BrokerContext
from .logic_in_code import FIXED_WORDING, load_switch

logger = logging.getLogger(__name__)


Handler = Callable[[BrokerContext, str, dict[str, Any], int, "int | None"], dict[str, Any]]


def wording_reads(broker: BrokerContext, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """What a job's reply states that is not on its row, read by the broker
    now: a delivered chronology's allowance (what this delivery used, what
    remains for the period), exactly as ``medchron_allowance`` answers it. An
    unreadable read is left out, and its sentence is omitted, never guessed."""
    if kind != "medchron_job" or row.get("state") != "delivered" or broker.medchron is None:
        return {}
    try:
        return {
            "allowance": broker.medchron.allowance_state(),
            "job_pages_credited": broker.medchron.allowance_state(str(row.get("id") or "")).get("job_pages_credited"),
        }
    except (ValueError, OSError, sqlite3.Error):
        logger.exception("the allowance could not be read for the reply of %s", row.get("id"))
        return {}


def enforcing(broker: BrokerContext) -> bool:
    """Whether this seat has switched ``fixed_wording`` to ``enforce``, read now."""
    path = getattr(broker, "customer_path", None)
    return load_switch(FIXED_WORDING, str(path) if path is not None else None).enforcing


def refused_today(kind: str, row: dict[str, Any] | None) -> bool:
    """True for the replies the broker refused other words on before the switch
    existed: a held job on a lane with hold classes (2026-10-24, job 01M5MWGB).
    Observing never reopens these."""
    from . import fixed_wording

    lane = fixed_wording.KIND_LANE.get(kind)
    return bool(row) and row.get("state") == "held" and lane in fixed_wording.HELD_CLASS_LANES


def refuses(broker: BrokerContext, kind: str, row: dict[str, Any] | None) -> bool:
    """Whether this job's send refuses words other than its fixed wording."""
    return worded(kind, row) and (refused_today(kind, row) or enforcing(broker))


def worded(kind: str, row: dict[str, Any] | None) -> bool:
    """True for a job row whose reply this broker words (``fixed_wording``)."""
    from . import fixed_wording

    return kind in fixed_wording.KIND_LANE and bool(row)


def fixed_texts(broker: BrokerContext, kind: str, row: dict[str, Any]) -> list[str]:
    """The texts this job's reply may be, rendered now (``fixed_wording.render``):
    what the bind hands the model, the send enforces and the status read carries."""
    from . import fixed_wording

    return fixed_wording.render(kind, row, wording_reads(broker, kind, row))


def wording_class(kind: str, row: dict[str, Any]) -> str:
    """What the audit row names for a refused body: the hold class, or the
    lane and state. Never the body."""
    from . import fixed_wording, hold_class

    lane = fixed_wording.KIND_LANE[kind]
    if row.get("state") == "held" and lane in fixed_wording.HELD_CLASS_LANES:
        return hold_class.classify(lane, row.get("reason"))[0]
    return f"{lane} {row.get('state')}"


def bind_keys(broker: BrokerContext, kind: str, row: dict[str, Any] | None) -> dict[str, Any]:
    """The bind's wording keys. The model reads tool results, so a key here can
    change what it writes with no skill changing: a seat in ``observe`` gets none
    on a reply it does not refuse, and the send renders on the broker side only to
    record the difference. A held reply that already refused keeps ``held_wording``,
    the key and the behaviour it has had since 2026-10-24."""
    if row is None or not refuses(broker, kind, row):
        return {}
    texts = fixed_texts(broker, kind, row)
    held = {"held_wording": texts} if refused_today(kind, row) else {}
    return {**held, **({"fixed_wording": texts} if enforcing(broker) else {})}


def _observe_send(
    broker: BrokerContext, kind: str, row: dict[str, Any], comment: str, html: str, audit: Callable[..., None]
) -> None:
    """A seat in ``observe``: the model's words go out as they do today, and one
    row says whether they were the fixed wording. It names the lane and state,
    never the body. Nothing here may stop or change the send."""
    from . import fixed_wording

    try:
        what = wording_class(kind, row)
        try:
            fixed_wording.check(kind, row, comment, html, fixed_texts(broker, kind, row))
        except fixed_wording.FixedWordingRefused:
            outcome, why = "wording_differs", f"fixed_wording: the body is not the {what} wording"
        else:
            outcome, why = "wording_matches", f"fixed_wording: the body is the {what} wording"
        audit(outcome, reason=why)
    except Exception:  # observe never changes what a reply does
        logger.exception("the fixed wording of %s %s could not be compared", kind, row.get("id"))


def check_send(
    broker: BrokerContext, kind: str, row: dict[str, Any] | None, payload: dict[str, Any], audit: Callable[..., None]
) -> None:
    """A job's reply is its fixed wording, held (2026-10-24, job 01M5MWGB: the
    model sent its own words on a held job and nothing checked them) or
    delivered (2026-11-04: every delivered reply was the model's composition
    from counts). Called BEFORE the claim, so the retry with the quoted text
    still has its one reply. The audit row names the class, never the body. On a
    seat in ``observe`` only a reply that was already refused is refused; every
    other is recorded and goes out."""
    from . import fixed_wording

    if row is None or not worded(kind, row):
        return
    raw_html = payload.get("html")
    html = raw_html if isinstance(raw_html, str) else ""
    comment = str(payload.get("comment") or "")
    if not refuses(broker, kind, row):
        _observe_send(broker, kind, row, comment, html, audit)
        return
    try:
        fixed_wording.check(kind, row, comment, html, fixed_texts(broker, kind, row))
    except fixed_wording.FixedWordingRefused:
        audit("send_refused", reason=f"fixed_wording: the body is not the {wording_class(kind, row)} wording")
        raise


def texts_for(broker: BrokerContext, kind: str, job_id: str) -> list[str]:
    """The texts job ``job_id``'s reply may be, read from its ledger now."""
    from . import reply_binding

    if not broker.audit_db_path:
        # No audit ledger, so no job ledger to read and nothing the bind could
        # send either. Never open a ledger at a path derived from None.
        return []
    row = reply_binding.job_ledger(broker, kind).read(job_id)
    return fixed_texts(broker, kind, row) if row else []


def with_reply_text(kind: str, handler: Handler) -> Handler:
    """``handler`` (a lane's dispatch), with ``reply_text`` added to a
    single-job status read's ``job``. Every other action passes through.
    ``__wrapped__`` names the lane's dispatch (the verb table's parity checks)."""

    @functools.wraps(handler)
    def handle(
        broker: BrokerContext, action: str, request: dict[str, Any], pid: int, uid: int | None
    ) -> dict[str, Any]:
        out = handler(broker, action, request, pid, uid)
        job_id = str(request.get("job_id") or "").strip()
        job = out.get("job") if isinstance(out, dict) else None
        if not (action.endswith("_job_status") and job_id and isinstance(job, dict)):
            return out
        try:
            if not enforcing(broker):
                return out
            texts = texts_for(broker, kind, job_id)
        except (ValueError, OSError, sqlite3.Error):
            # The status read still answers; the bind renders (and the send
            # enforces) the same text, so nothing unchecked can go out.
            logger.exception("the reply text of %s %s could not be rendered", kind, job_id)
            return out
        if len(texts) == 1:
            job["reply_text"] = texts[0]
        elif texts:
            job["reply_texts"] = texts
        return out

    return handle


__all__ = [
    "bind_keys",
    "check_send",
    "enforcing",
    "fixed_texts",
    "refused_today",
    "refuses",
    "texts_for",
    "with_reply_text",
    "wording_class",
    "wording_reads",
    "worded",
]
