"""Filing the made library file in the firm's Document Library, and nothing else.

WHERE. The firm's authored library matter and folder
(``self_initiation.document_library``, ``smokeball_connector.library``), under
the kind's library name: ``Template - <Kind>.docx`` for the firm's own file,
``Template - <Kind> - <attorney>.docx`` for an attorney's.

HOW. When the library already holds a file of that name, the made file is a NEW
VERSION of it (``add_file_version``, sent only while the file is still at the
version just read). Otherwise it is a new file (``add_file``). Then the file is
read back by id: its version id must have moved and its bytes must be the made
file's, or the filing is reported unproven and the job holds for SMD.

THE ONLY TWO WRITES. ``WriteLimited`` wraps the case system's client so the job
can call ``get``, ``download_file``, ``request`` (for the reads the library
calls make), ``add_file`` and ``add_file_version``, and at most ONE write in
all. Anything else raises before it reaches the case system.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

WRITES = frozenset({"add_file", "add_file_version"})
READS = frozenset({"get", "download_file"})


class WriteRefused(PermissionError):
    pass


class WriteLimited:
    """The client with every write but one ``add_file`` or ``add_file_version`` refused."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.writes: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name in READS:
            return getattr(self._client, name)
        if name in WRITES:
            if self.writes:
                raise WriteRefused("this job files one library file; a second write is refused")

            def write(*args: Any, **kwargs: Any) -> Any:
                self.writes.append(name)
                return getattr(self._client, name)(*args, **kwargs)

            return write
        if name == "request":
            return self._read_request
        raise WriteRefused(f"the library setup job may not call {name}")

    def _read_request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() != "GET":
            raise WriteRefused(f"the library setup job may not {method} {path}")
        return self._client.request(method, path, **kwargs)


@dataclass(frozen=True)
class Filed:
    file_id: str
    version: str
    sha256: str
    new_file: bool
    name: str


class FilingUnproven(RuntimeError):
    pass


def _library(client: Any, label: str | None) -> Any:
    from smokeball_connector.library import load_library_config
    from smokeball_connector.library_store import SmokeballLibrary

    labels = {label: label} if label else {}
    return SmokeballLibrary(client, load_library_config(), labels), load_library_config()


def _name(cfg: Any, kind: str, label: str | None) -> str:
    from smokeball_connector.library_store import attorney_file_name

    return attorney_file_name(kind, label) if label else cfg.template_name(kind)


#: A new file is readable once the firm's document system has taken it in.
READ_BACK_TRIES = 10
READ_BACK_PAUSE = 3.0


def _version(client: Any, matter_id: str, file_id: str) -> str:
    try:
        meta = client.get(f"/matters/{matter_id}/documents/files/{file_id}")
    except Exception:  # noqa: BLE001 - not readable yet is retried, then reported unproven
        return ""
    return str(meta.get("versionId") or "") if isinstance(meta, dict) else ""


def _read_back(client: Any, matter_id: str, file_id: str, before: str, data: bytes, pause: Any = None) -> str:
    import time

    version = ""
    for _ in range(READ_BACK_TRIES):
        version = _version(client, matter_id, file_id)
        if version and version != before:
            break
        (pause or time.sleep)(READ_BACK_PAUSE)
    if not version or version == before:
        raise FilingUnproven("the filed file's version did not move")
    blob = client.download_file(matter_id, file_id)[1]
    if hashlib.sha256(blob).hexdigest() != hashlib.sha256(data).hexdigest():
        raise FilingUnproven("the filed file's bytes are not the made file's")
    return version


def file_library(client: WriteLimited, kind: str, label: str | None, data: bytes, pause: Any = None) -> Filed:
    """File ``data`` as the library's file of ``kind`` (``label``: the attorney's
    case-system name, or None for the firm's). Raises ``FilingUnproven`` or the
    library's ``LibraryUnavailable``."""
    from smokeball_connector.library import find_folder_id

    store, cfg = _library(client, label)
    matter_id = store._matter()
    name = _name(cfg, kind, label)
    found = store.find_candidates(kind, label)
    if len(found) > 1:
        raise FilingUnproven("the library holds more than one file of that name")
    if found:
        current = found[0]
        store.file_new_version(current.file_id, data, expected_version=current.version)
        version = _read_back(client, matter_id, current.file_id, current.version, data, pause)
        return Filed(current.file_id, version, hashlib.sha256(data).hexdigest(), False, name)
    folder = find_folder_id(client, matter_id, cfg.folder_name or "")
    if not folder:
        raise FilingUnproven("the library folder was not found")
    out = client.add_file(matter_id, name, data, folder_id=folder)
    file_id = str((out or {}).get("fileId") or "")
    if not file_id:
        raise FilingUnproven("the new file has no id")
    version = _read_back(client, matter_id, file_id, "", data, pause)
    return Filed(file_id, version, hashlib.sha256(data).hexdigest(), True, name)


__all__ = ["Filed", "FilingUnproven", "WriteLimited", "WriteRefused", "file_library"]
