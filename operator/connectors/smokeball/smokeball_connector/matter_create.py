"""The body of ``create_matter`` (the Operator's OWN internal matter): the
fail-closed duplicate check, the POST, and the read-back poll. The MCP tool and
its contract live in server.py; this moved out on 2026-10-13 so the read-back
work could land there without growing the largest module past its ratchet.

OBSERVED WIRE SHAPE, probed live on the pilot's sandbox tenant 2026-08-21
(vfy_01M0K2CMQBTMZCXZWKBTESSV5A), and every number below is from that probe
rather than from the vendor's documentation:

  POST /matters {description, matterTypeId, clientIds:[id], number, status}
    -> 202 {id, href}, where ``id`` is the FINAL matter id
  ``status`` is REQUIRED. Without it the API answers 400 "Must provide a
    valid Status"; the published docs list it optional, and they are wrong.
  GET /matters/{id} answers 404 WHILE THE MATTER MATERIALIZES and the full
    record afterwards: 404 at 0.6s, 2.8s, 4.9s, 7.2s, then 200 at 9.6s.
  The supplied ``number`` is honored verbatim, and /matters?Search=<number>
    finds the matter immediately once it materializes.

So the poll treats a 404 as "not yet" and anything else as an answer. Running
out of polls was reported as a quiet success with ``pending: true`` until
2026-10-13; it now carries ``status: link_not_visible`` so SMD is paged, while
still saying the matter was ACCEPTED and must not be created again. A
created-but-slow matter that read as a failure would invite a second create,
which is the duplicate this tool exists to prevent.
"""

from __future__ import annotations

import time
from typing import Any

from .client import SmokeballApiError
from .library import LOOKUP_FAILED, lookup_matter
from .readback import NOT_VISIBLE, shortfall

_CREATE_MATTER_POLL_ATTEMPTS = 15
_CREATE_MATTER_POLL_SECONDS = 2.0
_CREATE_MATTER_STATUS = "Open"


def _require_arg(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"create_matter: {name} is required and must be a non-empty string")
    return value.strip()


def _refuse_existing(client: Any, number: str, description: str, client_contact_id: str) -> None:
    existing = lookup_matter(client, number=number, description=description, client_contact_id=client_contact_id)
    if existing.state == LOOKUP_FAILED:
        raise ValueError(
            "create_matter: refusing to create because the existing-matter check could "
            f"not complete ({existing.reason}). Nothing was created. Try again once the "
            "case system is reachable."
        )
    if existing.found:
        raise ValueError(
            f"create_matter: a matter matching this one already exists (matched on "
            f"{existing.matched_on}; id {existing.matter_id}). Nothing was created. "
            "Use that matter."
        )


def create_operator_matter(
    client: Any, body_of: Any, description: str, matter_type_id: str, client_contact_id: str, number: str
) -> Any:
    """Check, POST, poll. ``body_of`` is the server's ``_body`` (drops None)."""
    description = _require_arg(description, "description")
    matter_type_id = _require_arg(matter_type_id, "matter_type_id")
    client_contact_id = _require_arg(client_contact_id, "client_contact_id")
    number = _require_arg(number, "number")
    _refuse_existing(client, number, description, client_contact_id)
    accepted = client.request(
        "POST",
        "/matters",
        json=body_of(
            description=description,
            matterTypeId=matter_type_id,
            clientIds=[client_contact_id],
            number=number,
            status=_CREATE_MATTER_STATUS,  # REQUIRED by the API despite the docs
        ),
    )
    matter_id = accepted.get("id") or accepted.get("matterId") if isinstance(accepted, dict) else None
    if not matter_id:
        raise ValueError(
            "create_matter: Smokeball accepted the request but returned no matter id, so "
            f"the result cannot be read back or de-duplicated. Response: {accepted!r}"
        )
    matter_id = str(matter_id)
    for attempt in range(_CREATE_MATTER_POLL_ATTEMPTS):
        try:
            record = client.get(f"/matters/{matter_id}")
        except SmokeballApiError as exc:
            # A 404 is "still materializing". Anything else is an answer, and a
            # wrong answer here must not be swallowed into a pending result.
            if exc.status != 404:
                raise
        else:
            if record:
                return {"created": True, "readback": record, "accepted": accepted, "readback_verdict": "confirmed"}
        if attempt < _CREATE_MATTER_POLL_ATTEMPTS - 1:
            time.sleep(_CREATE_MATTER_POLL_SECONDS)
    # ``readback`` has always held the matter record (None: there is none yet),
    # so the verdict goes in ``readback_verdict``.
    pending = {"created": True, "pending": True, "matter_id": matter_id, "accepted": accepted, "readback": None}
    detail = f"matter {matter_id} was accepted but did not read back after {_CREATE_MATTER_POLL_ATTEMPTS} reads"
    return shortfall(pending, NOT_VISIBLE, detail + "; do not create it again", key="readback_verdict")


__all__ = ["create_operator_matter"]
