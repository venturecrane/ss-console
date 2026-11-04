"""Resolve a matter the Operator already holds by id: the matter id it read, and
the number it read beside it, must name the same live matter.

WHY A SECOND RESOLVER. ``resolve_invoice_matter`` answers "which matter does
this letter belong to?" from the facts a letter prints. Most gated writes do
not start at a letter: a task on the matter a staff member named, a form letter
for the matter a request was about, a workbook for a matter already open in the
turn. There the Operator already holds a ``matter_id`` from an earlier read, and
the risk is not ambiguity but a wrong id carried forward: a matter id copied
from the previous matter in the conversation, or one remembered rather than
read. The id alone cannot catch that, because every id names some matter.

So this read asks for TWO facts the model read together, the id and the
matter's own number, and mints only when the live matter at that id carries
exactly that number. Those are two independent facts read back from Smokeball
in this call (``matter_resolution._verdict`` holds every ``unique`` verdict to
the same two-fact floor). A model that has the wrong id has, in the ordinary
case, the right number for the matter it means, and the two disagree.

WHAT A REFUSAL DOES NOT SAY. On a mismatch the verdict is ``none`` and the live
matter's number is NOT returned. If it were, the model would learn the number
that opens the id it already had, and the second call would mint for the wrong
matter it started with. It must go back to what it read.

Verdicts, the same vocabulary as ``resolve_invoice_matter``:

    unique         the matter exists at that id and its number equals the number
                   given (case and spacing aside, as ``_matters_by_number``
                   compares). Carries ``matter_resolution``.
    none           no matter at that id, or its number differs, or either fact
                   was not given in a usable shape. Mints nothing.
    search_failed  the tenant could not be read. Never an answer about it.
"""

from __future__ import annotations

import re
from typing import Any

from .client import SmokeballApiError
from .library import _norm as norm
from .matter_resolution import FACT_NUMBER, VERDICT_NONE, VERDICT_SEARCH_FAILED, VERDICT_UNIQUE
from .resolution_token import mint

#: The fact the id itself contributes to ``matched_on``.
FACT_ID = "matter_id"

#: Smokeball matter ids are GUIDs. Anything else is refused before it is put in
#: a request path, so a crafted id cannot address another endpoint.
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def resolve_matter_by_id(client: Any, *, matter_id: Any, matter_number: Any) -> dict[str, Any]:
    """The closed verdict for one (id, number) pair. See the module docstring."""
    wanted_id = _clean(matter_id)
    wanted_number = _clean(matter_number)
    base: dict[str, Any] = {"searched": [FACT_ID, FACT_NUMBER], "ignored": []}
    if not _GUID.match(wanted_id) or not wanted_number:
        return {
            **base,
            "verdict": VERDICT_NONE,
            "reason": "both the matter id (as a read returned it) and the matter number are required",
        }
    try:
        matter = client.get(f"/matters/{wanted_id}")
    except SmokeballApiError as exc:
        if exc.status == 404:
            return {**base, "verdict": VERDICT_NONE, "reason": "no matter has that id"}
        return {**base, "verdict": VERDICT_SEARCH_FAILED, "reason": f"the matter could not be read (HTTP {exc.status})"}
    except Exception as exc:  # noqa: BLE001 - any failure to read is a failed read, never an answer
        return {
            **base,
            "verdict": VERDICT_SEARCH_FAILED,
            "reason": f"the matter could not be read ({exc.__class__.__name__})",
        }
    live_id = matter.get("id") if isinstance(matter, dict) else None
    live_number = matter.get("number") if isinstance(matter, dict) else None
    if not isinstance(live_id, str) or live_id.casefold() != wanted_id.casefold():
        return {**base, "verdict": VERDICT_NONE, "reason": "no matter has that id"}
    if not isinstance(live_number, str) or norm(live_number) != norm(wanted_number):
        # The live number is deliberately absent: see the module docstring.
        return {
            **base,
            "verdict": VERDICT_NONE,
            "reason": "the matter at that id does not carry that matter number; use the id and number "
            "one read returned together",
        }
    matched_on = [FACT_ID, FACT_NUMBER]
    return {
        **base,
        "verdict": VERDICT_UNIQUE,
        "matter_id": live_id,
        "matter_number": live_number,
        "matched_on": matched_on,
        "matter_resolution": mint(live_id, live_number, tuple(matched_on)),
    }


def _client() -> Any:
    from . import server

    return server._get_client()


def resolve_matter_by_id_tool(matter_id: str, matter_number: str) -> Any:
    """Confirm the matter you are about to write on, and get the
    ``matter_resolution`` every matter write requires. Classified ``read``:
    nothing is written.

    Pass the ``matter_id`` and the matter's own ``number`` exactly as ONE read
    of that matter returned them (``get_matter``, ``list_matters``, a task or
    event that names its matter). Never a number from memory, from an email, or
    from a different matter in this conversation.

    ``verdict`` is ``unique`` when the live matter at that id carries that
    number: ``matter_resolution`` is then the code the write needs. Pass it with
    the same ``matter_id``, unchanged, to ONE write: it is single use, short
    lived and bound to that matter. ``none`` means the two do not name the same
    matter (``reason`` says which fact failed; the number on record is not
    given back): read the matter again instead of writing. ``search_failed``
    means the matter could not be read, which says nothing about the firm's
    record. For a letter or invoice whose matter is not yet known, use
    ``resolve_invoice_matter`` instead."""
    return resolve_matter_by_id(_client(), matter_id=matter_id, matter_number=matter_number)


def register(server: Any) -> None:
    """Register the read onto the connector's server."""
    server.tool(name="resolve_matter_by_id")(resolve_matter_by_id_tool)


__all__ = ["FACT_ID", "register", "resolve_matter_by_id", "resolve_matter_by_id_tool"]
