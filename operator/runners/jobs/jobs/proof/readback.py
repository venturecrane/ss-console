"""The far end, re-read: every filed file found by name in its folder, at its
byte count, then DOWNLOADED by its file id and its sha256 compared with the
bytes this job built.

One implementation for every lane. Before this the lanes read back at four
depths: litigation downloaded and hashed (``litigation/deliver.py``), the
chronology, demand and drafting compared name and size only
(``stages/upload.py``), so a file the vendor stored truncated or swapped at the
same length read as delivered. Name and size come first because they are cheap
and the vendor's index lags an upload by minutes (the listing is retried); the
hash comes last because only bytes prove bytes.

The listing is the seat's ``list_files``, which pages past the vendor's 500-row
cap (``seat.ClientSeat.list_files``); a single page would read a present file
as missing on a large matter.

Two different failures, kept apart on purpose:

* ``ReadbackShort``: a name never appeared at its size after the retries. The
  file may still be materializing; the job fails RESUMABLE and the resume reads
  again (it never resends: the lanes' intent rows forbid that).
* ``ReadbackMismatch``: the file is there and its bytes are not ours. That is
  wrong content on the firm's matter, never a lag; the job fails and SMD is
  alerted with the file named in the ledger row.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable

READBACK_TRIES = 8
READBACK_PAUSE_SECONDS = 15.0


class ReadbackShort(RuntimeError):
    """A filed name never appeared at its byte count."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(f"read-back short: {len(names)} file(s) not at their size: {', '.join(names)[:300]}")
        self.names = names


class ReadbackMismatch(RuntimeError):
    """A filed file's downloaded bytes differ from the bytes built."""

    def __init__(self, name: str, why: str) -> None:
        super().__init__(f"read-back mismatch: {name}: {why}"[:400])
        self.name = name
        self.why = why


def vendor_name(row: dict[str, Any]) -> str:
    """The full filename (the vendor splits name and extension; see
    ``stages/upload._vendor_name`` for the measured cases)."""
    name = str(row.get("name") or "")
    ext = str(row.get("ext") or "")
    if not ext or name.lower().endswith(ext.lower()):
        return name
    return name + ext


def rows_in(seat: Any, matter_id: str, folder_id: str) -> dict[str, dict[str, Any]]:
    """``{full name: vendor row}`` for the live files in one folder."""
    out: dict[str, dict[str, Any]] = {}
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == str(folder_id) and not f.get("deleted"):
            out[vendor_name(f)] = f
    return out


def remote_sha(seat: Any, matter_id: str, file_id: str, scratch: Path) -> str | None:
    """The sha256 of one file as the vendor serves it, or None when no download
    URL could be minted (a lag, not a verdict: the caller retries)."""
    minted = seat.mint(matter_id, [str(file_id)])
    url = (minted[0] if minted else {}).get("url")
    if not url:
        return None
    scratch.mkdir(parents=True, exist_ok=True)
    dest = scratch / f".readback-{file_id}"
    try:
        seat.fetch(url, dest, None)
        h = hashlib.sha256()
        with dest.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    finally:
        dest.unlink(missing_ok=True)


def confirm_files(
    seat: Any,
    matter_id: str,
    folder_id: str,
    expected: list[dict[str, Any]],
    scratch: Path,
    *,
    tries: int = READBACK_TRIES,
    pause: float = READBACK_PAUSE_SECONDS,
    log: Callable[[str], None] = lambda _m: None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Every ``{name, sha256, bytes}`` in ``expected`` found, at size, and equal
    by hash. Returns the proof block's read-back half:
    ``{"readback": "confirmed", "folder_id", "files": [{name, file_id, size, sha256}]}``.
    Raises ``ReadbackShort`` or ``ReadbackMismatch``."""
    if not expected:
        raise ReadbackShort(["(nothing was expected: an empty delivery is not a delivery)"])
    want = {str(e["name"]): e for e in expected}
    rows: dict[str, dict[str, Any]] = {}
    short = list(want)
    for attempt in range(tries):
        rows = rows_in(seat, matter_id, folder_id)
        short = [n for n, e in want.items() if int((rows.get(n) or {}).get("size") or -1) != int(e["bytes"])]
        if not short:
            break
        if attempt + 1 < tries:
            log(f"read-back: {len(short)} of {len(want)} not yet at size; waiting")
            sleep(pause)
    if short:
        raise ReadbackShort(short)
    proven: list[dict[str, Any]] = []
    for name, e in want.items():
        fid = str(rows[name].get("id") or "")
        if not fid:
            raise ReadbackMismatch(name, "the vendor row carries no file id")
        got = None
        for attempt in range(3):
            got = remote_sha(seat, matter_id, fid, scratch)
            if got is not None:
                break
            sleep(pause)
        if got is None:
            raise ReadbackShort([name])
        if got != str(e["sha256"]):
            raise ReadbackMismatch(name, "the downloaded bytes differ from the bytes built")
        proven.append({"name": name, "file_id": fid, "size": int(e["bytes"]), "sha256": got})
    log(f"read-back proven: {len(proven)} file(s) by id, size and sha256 in folder {folder_id}")
    return {"readback": "confirmed", "folder_id": str(folder_id), "files": proven}


__all__ = [
    "READBACK_PAUSE_SECONDS",
    "READBACK_TRIES",
    "ReadbackMismatch",
    "ReadbackShort",
    "confirm_files",
    "remote_sha",
    "rows_in",
    "vendor_name",
]
