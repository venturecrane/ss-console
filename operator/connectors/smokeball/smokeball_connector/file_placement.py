"""Put documents already on a matter into one of its folders: copy or move.

WHY (2026-11-24). An attorney asked the Operator to "create a folder and place
these documents into it", naming six documents already on the matter. The
Operator created the folder, left it empty, and told him its connection "cannot
move or copy a document that is already on the matter". That was never true of
Smokeball: firm users move files every day, and the API's own "Patch a file"
call moves one (``folderId`` on ``PATCH /matters/{m}/documents/files/{f}``,
docs.smokeball.com, read 2026-11-24). The connector simply had no verb for it,
so the model reported the gap as a product limit to a client.

Two verbs, one module:

* ``copy_files_to_folder``: the originals stay where they are. Each file's
  bytes are downloaded and filed into the folder under the same name through
  ``add_file``'s two-stage upload, then proven by id, size and sha256
  (``readback.file_and_prove``). The copy's sha256 must equal the original's.
* ``move_files_to_folder``: the file leaves its old folder. One PATCH carrying
  the folder id and the file's own unchanged name; no bytes are sent. Proven
  by re-reading the file by id until it sits in the folder, under the same
  name, with the same sha256 it had before the move.

THE MATTER BINDING. Both refuse, before anything is written, unless every file
AND the folder belong to the matter the call names. The vendor does not check
the folder ("the destination is not validated for existence"), so a folder id
from another matter, or a typo, would otherwise move a document somewhere no
one can find it. A file id that the matter's own path cannot read (the vendor
answers 400 or 404 for a file of another matter) is refused the same way. A
deleted file is refused. All of this is checked for EVERY file first: one bad
id in six sends nothing at all.

RETRY-SAFE. A file already in the folder is reported ``already_in_folder``
and not written again; a copy whose same-named twin in the folder already
carries the same sha256 is reported ``already_copied``. So a second call after
a shortfall finishes the work instead of duplicating it.

Every write is recorded in the seat's audit ledger before it is sent
(``client.request`` / ``WriteRecord``), and a read-back that disagrees returns
the top-level shortfall ``status`` that pages SMD (``readback.py``).
Provenance: no text is composed, so there is nothing to stamp; a copy carries
the original's name exactly, because the firm named that document.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .client import SmokeballApiError
from .library import _entry_folder_id, _listing, _walk_folders, list_matter_files, name_matches
from .readback import (
    HASH_LIMIT_BYTES,
    MISMATCH,
    NOT_VISIBLE,
    _live,
    _size,
    confirmed,
    file_and_prove,
    poll,
    shortfall,
)

#: One request names a handful of documents; fifty is far past any real ask and
#: keeps a runaway list from turning into hundreds of writes in one turn.
MAX_FILES = 50

#: Smokeball ids are GUIDs; any id is refused before it reaches a request path
#: unless it is plain letters, digits and dashes.
_ID = re.compile(r"^[A-Za-z0-9-]{1,80}$")

_NOTHING_SENT = "Nothing was copied or moved."


class PlacementRefused(ValueError):
    """The call cannot be carried out as asked; nothing was written."""


def _check_ids(matter_id: Any, file_ids: Any, folder_id: Any) -> tuple[str, list[str], str]:
    if not isinstance(matter_id, str) or not _ID.match(matter_id):
        raise PlacementRefused(f"matter_id {matter_id!r} is not a matter id. {_NOTHING_SENT}")
    if not isinstance(folder_id, str) or not _ID.match(folder_id):
        raise PlacementRefused(
            f"folder_id {folder_id!r} is not a folder id: read it with list_folders (or create the folder "
            f"with create_folder, which returns its id). {_NOTHING_SENT}"
        )
    if isinstance(file_ids, str):
        file_ids = [file_ids]
    if not isinstance(file_ids, list) or not file_ids:
        raise PlacementRefused(f"file_ids must list at least one file id. {_NOTHING_SENT}")
    ids: list[str] = []
    for fid in file_ids:
        if not isinstance(fid, str) or not _ID.match(fid):
            raise PlacementRefused(
                f"{fid!r} is not a file id: find the documents with find_files_on_matter or "
                f"get_files_on_matter and pass their ids. {_NOTHING_SENT}"
            )
        if fid not in ids:
            ids.append(fid)
    if len(ids) > MAX_FILES:
        raise PlacementRefused(f"{len(ids)} files in one call; send at most {MAX_FILES} per call. {_NOTHING_SENT}")
    return matter_id, ids, folder_id


def _folder_on_matter(client: Any, matter_id: str, folder_id: str) -> dict[str, Any]:
    """The folder, found in the matter's own folder tree, or a refusal. A tree
    that cannot be read raises: "could not look" is never "not there"."""
    resp = client.get(f"/matters/{matter_id}/documents/folders", Limit=500, Offset=0)
    for folder in _walk_folders(_listing(resp)):
        if str(folder.get("id")) == folder_id:
            return folder
    raise PlacementRefused(
        f"folder {folder_id!r} is not a folder on matter {matter_id}. Read the matter's folders with "
        f"list_folders, or create the folder there with create_folder, then call again. {_NOTHING_SENT}"
    )


def _record_matter(meta: dict[str, Any]) -> str:
    matter = meta.get("matter")
    if isinstance(matter, dict) and matter.get("id"):
        return str(matter["id"])
    return str(meta.get("matterId") or "")


def _full_name(meta: dict[str, Any]) -> str:
    """The name as the firm sees it: Smokeball keeps the extension apart
    (``library.name_matches``)."""
    return f"{meta.get('name') or ''}{meta.get('fileExtension') or ''}"


def _source(client: Any, matter_id: str, file_id: str) -> dict[str, Any]:
    """One file, read through the matter's own path, or a refusal."""
    try:
        meta = client.get(f"/matters/{matter_id}/documents/files/{file_id}")
    except SmokeballApiError as exc:
        if exc.status in (400, 404):
            raise PlacementRefused(
                f"file {file_id!r} is not a document on matter {matter_id} (Smokeball answered {exc.status}). "
                f"A document is only placed in a folder of its own matter. {_NOTHING_SENT}"
            ) from exc
        raise
    meta = meta if isinstance(meta, dict) else {}
    owner = _record_matter(meta)
    if owner and owner != matter_id:
        raise PlacementRefused(
            f"file {file_id!r} belongs to another matter, not {matter_id}. A document is only placed in a "
            f"folder of its own matter. {_NOTHING_SENT}"
        )
    if not _live(meta):
        raise PlacementRefused(f"file {file_id!r} reads deleted on matter {matter_id}. {_NOTHING_SENT}")
    if not str(meta.get("name") or "").strip():
        raise PlacementRefused(f"file {file_id!r} carries no name to place it under. {_NOTHING_SENT}")
    return meta


def _preflight(client: Any, matter_id: Any, file_ids: Any, folder_id: Any) -> tuple[str, str, dict, list]:
    matter_id, ids, folder_id = _check_ids(matter_id, file_ids, folder_id)
    folder = _folder_on_matter(client, matter_id, folder_id)
    sources = [(fid, _source(client, matter_id, fid)) for fid in ids]
    return matter_id, folder_id, folder, sources


def _sha(blob: bytes) -> str:
    return hashlib.sha256(bytes(blob)).hexdigest()


def _row(file_id: str, meta: dict[str, Any], result: str, **extra: Any) -> dict[str, Any]:
    return {"fileId": file_id, "name": _full_name(meta), "result": result, **extra}


def _summarize(action: str, matter_id: str, folder_id: str, folder: dict, rows: list[dict]) -> dict[str, Any]:
    done = {"copy": ("copied", "already_copied"), "move": ("moved",)}[action]
    out: dict[str, Any] = {
        "action": action,
        "matterId": matter_id,
        "folderId": folder_id,
        "folderName": folder.get("name"),
        "files": rows,
        "placed": sum(1 for r in rows if r["result"] in done),
        "alreadyInFolder": sum(1 for r in rows if r["result"] == "already_in_folder"),
    }
    bad = [r for r in rows if r.get("status") in (MISMATCH, NOT_VISIBLE) or r["result"] == "failed"]
    if not bad:
        return confirmed(out, f"every file reads back in folder {folder.get('name')!r} by id, name and sha256")
    status = MISMATCH if any(r.get("status") != NOT_VISIBLE for r in bad) else NOT_VISIBLE
    detail = "; ".join(f"{r['name'] or r['fileId']}: {r.get('readback_detail') or r.get('error')}" for r in bad)
    return shortfall(out, status, detail)


# ---- copy ----------------------------------------------------------------------
def _twins_in_folder(client: Any, matter_id: str, folder_id: str) -> list[dict[str, Any]]:
    """Live files already in the folder. A listing that cannot be read raises
    before any copy is sent: a copy that cannot check for its twin could
    duplicate a document on a retry."""
    return [r for r in list_matter_files(client, matter_id) if _live(r) and _entry_folder_id(r) == folder_id]


def _copy_one(client: Any, matter_id: str, folder_id: str, fid: str, meta: dict, twins: list[dict]) -> dict:
    name = _full_name(meta)
    if _entry_folder_id(meta) == folder_id:
        return _row(fid, meta, "already_in_folder")
    _info, blob = client.download_file(matter_id, fid)
    want = _sha(blob)
    for twin in twins:
        if not name_matches(twin, name) or _size(twin) not in (None, len(blob)):
            continue
        twin_id = str(twin.get("id") or twin.get("fileId") or "")
        if twin_id and _sha(client.download_file(matter_id, twin_id)[1]) == want:
            return _row(fid, meta, "already_copied", newFileId=twin_id, sha256=want)
    filed = file_and_prove(client, matter_id, name, blob, folder_id=folder_id)
    row = _row(fid, meta, "copied", newFileId=filed.get("fileId"), sha256=want)
    for key in ("readback", "readback_detail", "status", "readbackSha256"):
        if key in filed:
            row[key] = filed[key]
    return row


def copy_files_to_folder(client: Any, matter_id: Any, file_ids: Any, folder_id: Any) -> dict[str, Any]:
    """Copy each file into the folder; the originals stay. See the module docstring."""
    matter_id, folder_id, folder, sources = _preflight(client, matter_id, file_ids, folder_id)
    twins = _twins_in_folder(client, matter_id, folder_id)
    rows = []
    for fid, meta in sources:
        try:
            rows.append(_copy_one(client, matter_id, folder_id, fid, meta, twins))
        except Exception as exc:  # noqa: BLE001 - one file's failure is reported on its row; the rest still run
            rows.append(_row(fid, meta, "failed", error=f"{type(exc).__name__}: {str(exc)[:200]}"))
    return _summarize("copy", matter_id, folder_id, folder, rows)


# ---- move ----------------------------------------------------------------------
def _prove_moved(client: Any, matter_id: str, fid: str, folder_id: str, name: str, before: str | None) -> dict:
    """Re-read the file by id until it sits in the folder; then its name and
    bytes must be the ones it had before the move."""
    path = f"/matters/{matter_id}/documents/files/{fid}"
    meta, moved, error = poll(lambda: client.get(path), lambda r: _live(r) and _entry_folder_id(r) == folder_id)
    if not moved:
        why = f"; last read error {error}" if error else ""
        return {"status": NOT_VISIBLE, "readback_detail": f"the file still does not read in the folder{why}"}
    if _full_name(meta) != name:
        return {"status": MISMATCH, "readback_detail": f"the file reads as {_full_name(meta)!r}, not {name!r}"}
    if before is None:
        return {"readback": "confirmed", "readback_detail": "read back in the folder by id (over the hash limit)"}
    _info, blob = client.download_file(matter_id, fid)
    after = _sha(blob)
    if after != before:
        return {"status": MISMATCH, "readback_detail": "the file's bytes changed across the move"}
    return {"readback": "confirmed", "readbackSha256": after}


def _move_one(client: Any, matter_id: str, folder_id: str, fid: str, meta: dict) -> dict:
    name = _full_name(meta)
    if _entry_folder_id(meta) == folder_id:
        return _row(fid, meta, "already_in_folder")
    size = _size(meta)
    before = None
    if size is None or size <= HASH_LIMIT_BYTES:
        before = _sha(client.download_file(matter_id, fid)[1])
    client.move_file(matter_id, fid, folder_id, file_name=name)
    proof = _prove_moved(client, matter_id, fid, folder_id, name, before)
    return _row(fid, meta, "moved", fromFolderId=_entry_folder_id(meta), **proof)


def move_files_to_folder(client: Any, matter_id: Any, file_ids: Any, folder_id: Any) -> dict[str, Any]:
    """Move each file into the folder; it leaves its old folder. See the module docstring."""
    matter_id, folder_id, folder, sources = _preflight(client, matter_id, file_ids, folder_id)
    rows = []
    for fid, meta in sources:
        try:
            rows.append(_move_one(client, matter_id, folder_id, fid, meta))
        except Exception as exc:  # noqa: BLE001 - one file's failure is reported on its row; the rest still run
            rows.append(_row(fid, meta, "failed", error=f"{type(exc).__name__}: {str(exc)[:200]}"))
    return _summarize("move", matter_id, folder_id, folder, rows)


# ---- the MCP tools -------------------------------------------------------------
_SHARED = """
    ``file_ids`` are the documents' ids on THIS matter (find them with
    ``find_files_on_matter`` or ``get_files_on_matter``); ``folder_id`` is a
    folder on the same matter (``list_folders``, or the id ``create_folder``
    returned). Before anything is written, every file and the folder are read
    through the matter: a file or folder of another matter, a deleted file, or
    a folder that is not there refuses the whole call and nothing is written.
    A refusal names what to read or create; do that and call again. Never tell
    the person a document cannot be copied or moved.

    Each file comes back on its own row in ``files`` with ``result``
    (``copied`` / ``moved``, ``already_in_folder``, ``already_copied``, or
    ``failed``). The call returns ``readback: "confirmed"`` when every file
    reads back in the folder by id, name and sha256; otherwise the top-level
    ``status`` is ``readback_mismatch`` or ``link_not_visible``, SMD is alerted,
    and the rows say which file. Calling again is safe: a file already in the
    folder is never written twice.

    Classified INTERNAL_WRITE: the firm's own record is reorganized; nothing
    is sent outside the firm."""


def _client() -> Any:
    from . import server

    return server._get_client()


def copy_files_to_folder_tool(matter_id: str, file_ids: list[str], folder_id: str) -> Any:
    return copy_files_to_folder(_client(), matter_id, file_ids, folder_id)


def move_files_to_folder_tool(matter_id: str, file_ids: list[str], folder_id: str) -> Any:
    return move_files_to_folder(_client(), matter_id, file_ids, folder_id)


copy_files_to_folder_tool.__doc__ = (
    """COPY documents already on a matter into one of its folders; the originals
    stay where they are. Use it whenever someone asks to put, place, add, file or
    copy existing documents into a folder ("create a folder and place these
    documents into it"), unless they ask to MOVE them. Each copy is the original's
    exact bytes under the original's name, filed into the folder and proven by
    sha256 against the original."""
    + _SHARED
)
move_files_to_folder_tool.__doc__ = (
    """MOVE documents already on a matter into one of its folders; each leaves the
    folder it was in. Use it when someone asks to move, relocate or reorganize
    documents into a folder, or to take them out of where they are. One call per
    request, any number of files up to 50. The file keeps its id, name and bytes;
    only its folder changes, and the read-back proves all three."""
    + _SHARED
)


def register(server: Any) -> None:
    """Register both verbs onto the connector's server."""
    server.tool(name="copy_files_to_folder")(copy_files_to_folder_tool)
    server.tool(name="move_files_to_folder")(move_files_to_folder_tool)


__all__ = [
    "MAX_FILES",
    "PlacementRefused",
    "copy_files_to_folder",
    "move_files_to_folder",
    "register",
]
