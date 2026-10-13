"""Every write proves the far end: the shared read-back for files, folders and
deletes, and the result shape every write tool returns when the read-back
disagrees.

WHY (2026-10-13). A census of the connector's 26 write tools found 8 that read
back and compared, 5 that checked only that something existed, and 13 that
reported success on the vendor's 2xx/202 alone. Smokeball accepts a write and
materializes it asynchronously, so a 202 says the request was queued, not that
the firm's record now holds what we sent. Even where a tool did read back, the
verdict lived in a key the audit plugin never reads (memo ``confirmed: False``,
form ``filed_not_visible``, vendor invoice ``staged_unverified``), so a write
that never landed scored ``ok`` and nobody was paged. Three deliveries that
week were marked delivered while wrong, with no alert.

THE CONTRACT, one shape for every write tool:

* A clean read-back adds ``readback: "confirmed"`` to the tool's own result.
  Every existing key stays: agents and skills read them.
* A read-back that disagrees sets a TOP-LEVEL ``status`` the overlay's audit
  plugin treats as a shortfall (``readback_mismatch``: the record is there and
  differs; ``link_not_visible``: it never appeared), which emails SMD. The
  result carries ``readback_detail`` (what differed) and ``readback_note``, a
  plain sentence telling the agent the write may have landed and must NOT be
  retried: a retry can duplicate a calendar event, task, file or expense.
* A read-back that could not run is a failure, never a success. "I could not
  look" and "it is there" are different facts and only one is a delivery.

Files are proven by id, at the uploaded byte count, and then by the sha256 of
the bytes downloaded through the minted URL (files over 25 MB, the download
guard, are proven by size alone and say so). Name and size first because they
are cheap and the vendor's index lags an upload; the hash last because only
bytes prove bytes. ``WAITS`` and ``SLEEP`` are module-level so tests run it
without sleeping.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from .library import _listing, _walk_folders, list_matter_files, name_matches

#: Pauses between reads, in seconds. The forms' measured backoff (2, 3, 5, 8),
#: one step longer: an upload has been seen taking ~10 s to materialize.
WAITS: tuple[float, ...] = (2, 3, 5, 8, 13)
SLEEP: Callable[[float], None] = time.sleep

#: Files up to this size are downloaded and hashed; the client refuses larger
#: downloads outright, so bigger files are proven by id and size alone.
HASH_LIMIT_BYTES = 25 * 1024 * 1024

MISMATCH = "readback_mismatch"
NOT_VISIBLE = "link_not_visible"

_NOTES = {
    MISMATCH: (
        "The write may have landed, but reading it back from Smokeball did not match what was "
        "written ({detail}). Do NOT retry it: a retry can duplicate a calendar event, task, file "
        "or expense. SMD has been alerted and will check the record."
    ),
    NOT_VISIBLE: (
        "The write was accepted, but it could not be found when read back from Smokeball "
        "({detail}). It may still land. Do NOT retry it: a retry can duplicate a calendar event, "
        "task, file or expense. SMD has been alerted and will check the record."
    ),
}


def _as_dict(result: Any) -> dict[str, Any]:
    return dict(result) if isinstance(result, dict) else {"result": result}


def confirmed(result: Any, detail: str = "", *, key: str = "readback") -> dict[str, Any]:
    """``result`` with ``readback: "confirmed"`` added. ``key`` names another
    key for the two tools whose ``readback`` already carries the record read
    (``create_matter``, ``stage_vendor_invoice``): those use ``readback_verdict``
    so an existing key keeps its meaning."""
    out = _as_dict(result)
    out[key] = "confirmed"
    if detail:
        out["readback_detail"] = detail
    return out


def shortfall(result: Any, status: str, detail: str, *, key: str = "readback") -> dict[str, Any]:
    """``result`` with the top-level shortfall ``status`` that pages SMD, the
    detail, and the do-not-retry sentence for the agent. An existing ``status``
    (a form's ``filed``) is kept as ``write_status`` so nothing is lost."""
    out = _as_dict(result)
    if "status" in out and out["status"] not in (MISMATCH, NOT_VISIBLE):
        out["write_status"] = out["status"]
    out["status"] = status
    out[key] = "mismatch" if status == MISMATCH else "not_visible"
    out["readback_detail"] = detail
    out["readback_note"] = _NOTES[status].format(detail=detail)
    return out


def verdict(result: Any, proof: dict[str, Any]) -> dict[str, Any]:
    """Apply a proof (``{"status": ..., "detail": ...}`` or ``{"ok": True}``)."""
    if proof.get("ok"):
        out = confirmed(result, proof.get("detail", ""))
        out.update(proof.get("extra") or {})
        return out
    return shortfall(result, proof["status"], proof["detail"])


def is_shortfall(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") in (MISMATCH, NOT_VISIBLE)


def _ok(detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "detail": detail, "extra": extra}


def _bad(status: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "status": status, "detail": detail}


def poll(read: Callable[[], Any], done: Callable[[Any], bool]) -> tuple[Any, bool, str]:
    """Read immediately, then after each of ``WAITS``, until ``done(value)``.

    Returns ``(last value read, done, last error)``. ``read`` raising is "not
    yet" (a 404 while materializing); the error text is kept so a read-back
    that never completed says why. The last value is None if no read ever
    succeeded."""
    last: Any = None
    error = ""
    for wait in (0.0, *WAITS):
        if wait:
            SLEEP(wait)
        try:
            value = read()
        except Exception as exc:  # noqa: BLE001 - a failed read is "not yet"; its absence after every poll is reported
            error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
            continue
        last = value
        if done(value):
            return value, True, error
    return last, False, error


# ---- files -------------------------------------------------------------------
def _size(record: Any) -> int | None:
    if not isinstance(record, dict):
        return None
    for key in ("sizeBytes", "size", "fileSize"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _live(record: Any) -> bool:
    return isinstance(record, dict) and bool(record) and not record.get("isDeleted")


def _locate(client: Any, matter_id: str, file_name: str, folder_id: str | None, size: int) -> dict[str, Any] | None:
    """The file named ``file_name`` (in ``folder_id`` when given), preferring
    one at ``size``. Pages past the vendor's 500-row cap."""
    hits = []
    for row in list_matter_files(client, matter_id):
        if not _live(row) or not name_matches(row, file_name):
            continue
        folder = row.get("folder") if isinstance(row.get("folder"), dict) else {}
        if folder_id and str(folder.get("id") or row.get("folderId") or "") != str(folder_id):
            continue
        hits.append(row)
    at_size = [r for r in hits if _size(r) == size]
    return (at_size or hits or [None])[-1]


def prove_file(
    client: Any,
    matter_id: str,
    file_id: Any,
    data: bytes,
    *,
    file_name: str | None = None,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """Prove one uploaded file: visible by id (or by name in its folder when
    the upload returned no id), at ``len(data)`` bytes, and with the sha256 of
    ``data`` when downloaded. Returns a proof for ``verdict``."""
    want = len(data)
    fid = str(file_id) if file_id else ""

    def read() -> Any:
        if fid:
            return client.get(f"/matters/{matter_id}/documents/files/{fid}")
        if file_name:
            return _locate(client, matter_id, file_name, folder_id, want)
        return None

    record, at_size, error = poll(read, lambda r: _live(r) and _size(r) in (None, want))
    if not _live(record):
        why = f"; last read error {error}" if error else ""
        return _bad(NOT_VISIBLE, f"the file never appeared on the matter after {len(WAITS) + 1} reads{why}")
    fid = fid or str(record.get("id") or record.get("fileId") or "")
    seen = _size(record)
    if not at_size:
        return _bad(MISMATCH, f"the stored file is {seen} bytes; {want} bytes were uploaded")
    if want > HASH_LIMIT_BYTES:
        return _ok(f"read back by id at {want} bytes (over the download limit, not hashed)", readbackFileId=fid)
    return _prove_bytes(client, matter_id, fid, data)


def _prove_bytes(client: Any, matter_id: str, fid: str, data: bytes) -> dict[str, Any]:
    blob, got, error = poll(lambda: client.download_file(matter_id, fid)[1], lambda b: isinstance(b, bytes | bytearray))
    if not got:
        why = f" ({error})" if error else ""
        return _bad(NOT_VISIBLE, f"the file is listed but its bytes could not be downloaded to compare{why}")
    sent, stored = hashlib.sha256(data).hexdigest(), hashlib.sha256(bytes(blob)).hexdigest()
    if sent != stored:
        return _bad(MISMATCH, f"the downloaded bytes differ from the bytes uploaded ({len(blob)} vs {len(data)} bytes)")
    return _ok("read back by id, size and sha256", readbackFileId=fid, readbackSha256=stored)


def file_and_prove(
    client: Any, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None
) -> dict[str, Any]:
    """``client.add_file`` and then ``prove_file``: the one filing path every
    document-writing tool uses. The upload result's keys are kept."""
    result = client.add_file(matter_id, file_name, data, folder_id=folder_id)
    file_id = result.get("fileId") if isinstance(result, dict) else None
    proof = prove_file(client, matter_id, file_id, data, file_name=file_name, folder_id=folder_id)
    return verdict(result, proof)


# ---- deletes -------------------------------------------------------------------
def _gone(read: Callable[[], Any]) -> tuple[bool, str]:
    """Poll until ``read`` 404s or returns a deleted/empty record."""
    state = {"gone": False}

    def probe() -> Any:
        try:
            record = read()
        except Exception as exc:  # only a 404 is "gone"; any other failure is "could not look" and re-raises
            if getattr(exc, "status", None) == 404:
                state["gone"] = True
                return None
            raise
        state["gone"] = not _live(record)
        return record

    _value, done, error = poll(probe, lambda _r: state["gone"])
    return done, error


def prove_file_deleted(client: Any, matter_id: str, file_id: str, result: Any) -> dict[str, Any]:
    """``delete_file``'s read-back: the file reads deleted (or 404) by id."""
    done, error = _gone(lambda: client.get(f"/matters/{matter_id}/documents/files/{file_id}"))
    if done:
        return confirmed(result, "the file reads deleted by id")
    why = f" (last read error {error})" if error else ""
    return shortfall(result, MISMATCH, f"the file still reads present after {len(WAITS) + 1} reads{why}")


# ---- folders -------------------------------------------------------------------
def prove_folder(client: Any, matter_id: str, name: str, parent_folder_id: str | None, result: Any) -> dict[str, Any]:
    """``create_folder``'s read-back: the parent's folder tree holds ``name``
    (and the returned id, when the create returned one)."""
    want_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    path = f"/matters/{matter_id}/documents/folders" + (f"/{parent_folder_id}" if parent_folder_id else "")

    def present(resp: Any) -> bool:
        for folder in _walk_folders(_listing(resp)):
            same_name = str(folder.get("name") or "").strip().casefold() == name.strip().casefold()
            if same_name and (not want_id or str(folder.get("id")) == want_id):
                return True
        return False

    _resp, done, error = poll(lambda: client.get(path, Limit=500, Offset=0), present)
    if done:
        return confirmed(result, "the folder reads back in its parent's tree")
    why = f" (last read error {error})" if error else ""
    return shortfall(result, NOT_VISIBLE, f"no folder named {name!r} appeared in the folder tree{why}")


__all__ = [
    "HASH_LIMIT_BYTES",
    "MISMATCH",
    "NOT_VISIBLE",
    "SLEEP",
    "WAITS",
    "confirmed",
    "file_and_prove",
    "is_shortfall",
    "poll",
    "prove_file",
    "prove_file_deleted",
    "prove_folder",
    "shortfall",
    "verdict",
]
