"""Which email conversation a drafting request is in, read from the seat's
own Graph mailbox. Split from ``drafting_verbs`` (the module-size ceiling);
``drafting_verbs`` re-exports both names."""

from __future__ import annotations

from typing import Any, Callable


class ThreadUnknown(RuntimeError):
    """The mailbox could not say which conversation a request email is in."""


def graph_thread_of(msgraph: Any) -> Callable[[str], str] | None:
    """The conversation id of an email by its internetMessageId, read from the
    seat's own mailbox (``msgraph_lookup.find_received``), or None on a seat
    with no Graph mailbox (no thread to compare by). Raises ``ThreadUnknown``
    when it could not look: "could not tell" must never read as "not a reply"."""
    if msgraph is None:
        return None

    def thread_of(internet_message_id: str) -> str:
        from . import msgraph_lookup

        try:
            found = msgraph_lookup.find_received(msgraph, internet_message_id)
        except Exception as exc:  # noqa: BLE001 - any failure to look is "unknown", never "none"
            raise ThreadUnknown(str(exc)[:200]) from None
        return str((found or {}).get("conversation_id") or "")

    return thread_of
