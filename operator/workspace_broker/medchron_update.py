"""The update block of a chronology envelope (2026-10-13), split out of
``medchron_ledger`` so that module stays under the size ceiling.

An update INTO the delivered chronology: the named documents go into the
delivered chronology, which comes back as one complete document
(``operator/runners/medchron/medchron/update/``). The runner's
``medchron.job.parse_update`` checks the same shape again on the other side.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The one update kind (medchron/job.py UPDATE_INTO): the named documents go
#: INTO the delivered chronology, which comes back as one complete document.
UPDATE_INTO = "delivered_chronology"
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def update_block(req: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    """``{"update": {...}}`` for an update into the delivered chronology, ``{}``
    when the request is not one. Checked the way ``medchron.job.parse_update``
    checks it again: ONE client (a delivered chronology is one client's), the
    documents to read named by id (``selection.include_file_ids``), and the
    delivered document's id when the skill read it off the matter (absent,
    the runner finds the newest one by the firm's own file name)."""
    raw = req.get("update")
    if raw is None:
        return {}
    if not isinstance(raw, dict) or raw.get("into") != UPDATE_INTO:
        raise _error(f"update.into must be {UPDATE_INTO!r}")
    if len(env["units"]) != 1:
        raise _error("an update into a delivered chronology names exactly one client")
    if not (env.get("selection") or {}).get("include_file_ids"):
        raise _error("an update names the documents to read in selection.include_file_ids")
    out: dict[str, Any] = {"into": UPDATE_INTO}
    fid = raw.get("chronology_file_id")
    if fid is not None:
        if not isinstance(fid, str) or not _FILE_ID_RE.match(fid):
            raise _error("update.chronology_file_id must be a document id")
        out["chronology_file_id"] = fid
    return {"update": out}


def _error(msg: str) -> Exception:
    from .medchron_ledger import EnvelopeError

    return EnvelopeError(msg)


#: The shape the agent submits instead, in the words a refusal relays. Shared by
#: the broker's refusal below and the runner's own guard (``stages/listing.py``
#: carries the same sentence), so the agent reads one instruction either way.
UPDATE_SHAPE = (
    'resubmit as an UPDATE: update: {"into": "delivered_chronology", "chronology_file_id": '
    '"<the delivered chronology document id, when read off the matter>"}, with '
    "selection.include_file_ids naming only the documents the delivery did not cover "
    "(medchron_job_status with this matter_id lists covered_document_ids)"
)


def client_keys(env: dict[str, Any]) -> list[str]:
    """The clients an envelope is for, as the runner tells them apart: by
    ``client_name``, which names each client's chronology file
    (``update/locate.name_pattern``, matched case-insensitively, read by
    ``stages/listing.py`` and the update's locator alike). Sorted, unique."""
    return sorted({" ".join(str(u.get("client_name") or "").split()).lower() for u in env.get("units") or []} - {""})


def delivered_refusal(env: dict[str, Any], delivered_count: Any) -> str | None:
    """A prose refusal when a RUN or a plain append (named ids, no ``update``
    block) is asked for a client who already holds a delivered chronology on
    the matter; None when the envelope may queue (2026-10-13: a "full
    chronology" ask on a client matter that already held one, queued as a
    full run).

    PER CLIENT, not per matter (2026-10-17). A joint matter holds one
    chronology per client, and the runner's own guard looks for THIS job's
    clients' files only, so client A's delivery must not refuse client B's
    first chronology. A ledger row that records no clients (written before
    the column, or a backfill) counts for every client: unknown is never none.

    Only the ledger's own delivery record is read here; the broker holds no
    practice-management session, so a chronology delivered before the ledger
    existed (and never backfilled) is caught by the runner's ``list_matter``
    guard, before any paid stage, from the matter's own file listing.

    ``delivered_count`` is the ledger's reader. If it raises, this refuses with
    its own reason: whether the matter holds a delivery could not be read, and
    "could not look" must never queue as "none"."""
    if "update" in env:
        return None
    matter_id = env["matter"]["id"]
    try:
        count = int(delivered_count(matter_id, client_keys(env)))
    except Exception as exc:  # noqa: BLE001 - any read failure refuses, named
        return (
            f"whether matter {env['matter']['number']} holds a delivered chronology could not be "
            f"read ({type(exc).__name__}), so nothing was queued: a run on a matter that already has one "
            "re-reads and bills the whole file. Ask again once the record reads; nothing is needed from the firm"
        )
    if count <= 0:
        return None
    kind = "an append of named documents" if (env.get("selection") or {}).get("include_file_ids") else "a full run"
    return (
        f"matter {env['matter']['number']} already holds a delivered chronology, so this request is an "
        f"update into it, not {kind}; nothing was queued and nothing was charged. To bring it current, " + UPDATE_SHAPE
    )


def delivered_count(ledger: Any, matter_id: str, clients: list[str]) -> int:
    """How many chronologies the ledger records as delivered on the matter for
    any of ``clients`` (``client_keys``), record or not (``delivered_coverage``
    needs a coverage record; this asks only whether a delivery happened). A
    row with no recorded clients counts. Here rather than on ``MedchronLedger``
    so that module stays under the size ceiling. A read failure RAISES (a
    corrupt ``client_keys`` included): the caller refuses on it, because
    "could not look" must never read as "none"."""
    conn = ledger._connect()
    try:
        rows = conn.execute(
            "SELECT client_keys FROM medchron_jobs WHERE matter_id=? AND state='delivered'", (str(matter_id),)
        ).fetchall()
    finally:
        conn.close()
    wanted = set(clients)
    return sum(1 for (raw,) in rows if raw is None or wanted & set(json.loads(raw)))
