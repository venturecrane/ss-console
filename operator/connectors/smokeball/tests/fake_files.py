"""A fake Smokeball document store for the write read-back (readback.py).

Every document-filing tool now reads its upload back by id, at its byte size,
and by the sha256 of the downloaded bytes. A fake client that only accepted the
upload would read as "never appeared" (correctly: the read-back fails closed),
so fakes that file documents hold a ``FileStore`` and serve the file read and
the download from it.

Planting a far-end disagreement: ``hide`` (the file never appears),
``wrong_size`` (metadata reports another byte count), ``wrong_bytes`` (the
download serves other bytes), ``deleted`` (the file reads deleted).
"""

from __future__ import annotations

import re
from typing import Any

_FILE_PATH = re.compile(r"^/matters/([^/]+)/documents/files/([^/]+)$")


class FileStore:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.hide: set[str] = set()
        self.wrong_size: dict[str, int] = {}
        self.wrong_bytes: dict[str, bytes] = {}
        self.deleted: set[str] = set()
        self._n = 0

    def add(
        self, matter_id: str, file_name: str, data: bytes, folder_id: str | None = None, file_id: str | None = None
    ) -> str:
        self._n += 1
        fid = file_id or f"file-{self._n}"
        self.files[fid] = {"matter": matter_id, "name": file_name, "data": bytes(data), "folder": folder_id}
        return fid

    def record(self, fid: str) -> dict[str, Any]:
        f = self.files[fid]
        return {
            "id": fid,
            "name": f["name"],
            "sizeBytes": self.wrong_size.get(fid, len(f["data"])),
            "folder": {"id": f["folder"]} if f["folder"] else None,
            "isDeleted": fid in self.deleted,
        }

    def read(self, path: str) -> dict[str, Any] | None:
        """The file-by-id read for ``path``, or None when ``path`` is not one.
        A hidden or unknown file raises, as the vendor's 404 does."""
        m = _FILE_PATH.match(path)
        if not m:
            return None
        fid = m.group(2)
        if fid not in self.files or fid in self.hide:
            raise LookupError(f"404 file {fid}")
        return self.record(fid)

    def download(self, matter_id: str, fid: str) -> tuple[dict[str, Any], bytes]:
        if fid not in self.files or fid in self.hide:
            raise LookupError(f"404 file {fid}")
        return {"fileId": fid}, self.wrong_bytes.get(fid, self.files[fid]["data"])


class FilesMixin:
    """``add_file`` / ``download_file`` over a ``FileStore``; a fake's ``get``
    calls ``self.store.read(path)`` first."""

    store: FileStore

    def add_file(self, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None) -> dict:
        fid = self.store.add(matter_id, file_name, data, folder_id)
        return {"fileId": fid, "matterId": matter_id, "fileName": file_name, "uploaded": True}

    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        return self.store.download(matter_id, file_id)
