"""The update block of a chronology envelope (2026-10-13), split out of
``medchron_ledger`` so that module stays under the size ceiling.

An update INTO the delivered chronology: the named documents go into the
delivered chronology, which comes back as one complete document
(``operator/runners/medchron/medchron/update/``). The runner's
``medchron.job.parse_update`` checks the same shape again on the other side.
"""

from __future__ import annotations

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
