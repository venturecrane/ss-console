"""The firm's Document Library in Smokeball, as an ``operator_connector_sdk``
``LibraryStore`` (design 2026-11-06, piece 2).

Built on the lookups ``library.py`` already makes (the library matter by number,
the folder by name, the paged file listing, the name match) and the client's
file calls (``GET /matters/{m}/documents/files/{f}``, ``download_file``,
``add_file_version``). New code lives here so ``library.py`` does not grow.

WHAT THIS CASE SYSTEM CAN AND CANNOT SAY ABOUT A FILE'S VERSIONS, as the client
stands today (``client.py``):

* the CURRENT version id, from the file's metadata (``versionId``), and when it
  was last saved (``lastUpdated``);
* the current version's bytes (``download_file``), and a new version sent only
  when the file is still at an expected version id (``add_file_version``).
* NOT the file's earlier versions: the client has no call that lists a file's
  history or downloads a version other than the current one. So
  ``list_versions`` answers the current version with ``complete=False``, and
  ``fetch_version`` of any other version raises ``NotSupportedYet``.
* NOT who saved a version: ``saved_by`` is always None. The vendor's history
  names the same user for a file a person saved and a file the Operator wrote,
  so it is not evidence of who made a file and is not read.

AN ATTORNEY'S OWN FILE is named ``Template - <Kind> - <label>.docx`` beside the
firm's ``Template - <Kind>.docx``, where ``label`` is the name the store is
given for that attorney key. The name only finds candidates; which file a
document is made in is the ledger's answer, never the name's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from operator_connector_sdk.library import (
    FiledVersion,
    FileVersion,
    LibraryFile,
    LibraryUnavailable,
    NotSupportedYet,
    VersionListing,
)

from .client import SmokeballApiError, SmokeballWriteError
from .library import (
    CLASS_TITLES,
    LibraryConfig,
    ListingFailed,
    _entry_folder_id,
    find_folder_id,
    list_matter_files,
    lookup_matter,
    name_matches,
)


def attorney_file_name(kind: str, label: str) -> str:
    """The library file name for an attorney's own file of ``kind``."""
    return f"Template - {CLASS_TITLES.get(kind, kind)} - {label}.docx"


class SmokeballLibrary:
    """A ``LibraryStore`` over the seat's authored library location."""

    def __init__(self, client: Any, cfg: LibraryConfig, attorney_labels: Mapping[str, str] | None = None) -> None:
        self._client = client
        self._cfg = cfg
        self._labels = dict(attorney_labels or {})
        self._matter_id: str | None = None

    # ---- where the library is ---------------------------------------------------

    def _matter(self) -> str:
        if self._matter_id:
            return self._matter_id
        if not self._cfg.authored:
            raise LibraryUnavailable(f"the document library location is not authored ({self._cfg.source})")
        found = lookup_matter(self._client, number=self._cfg.matter_number or "")
        if found.failed:
            raise LibraryUnavailable(f"the library matter could not be looked up just now ({found.reason})")
        if not found.found or not found.matter_id:
            raise LibraryUnavailable(f"the library matter {self._cfg.matter_number!r} was not found")
        self._matter_id = found.matter_id
        return found.matter_id

    def _meta(self, file_id: str) -> dict[str, Any] | None:
        """The file's metadata, or None when it is not in the library (404 or deleted)."""
        try:
            meta = self._client.get(f"/matters/{self._matter()}/documents/files/{file_id}")
        except SmokeballApiError as exc:
            if exc.status == 404:
                return None
            raise LibraryUnavailable(f"file {file_id!r} could not be read (HTTP {exc.status})") from exc
        except Exception as exc:  # any transport failure is "could not look", never "none"
            raise LibraryUnavailable(f"file {file_id!r} could not be read ({type(exc).__name__})") from exc
        if not isinstance(meta, dict) or meta.get("isDeleted") is True:
            return None
        return meta

    # ---- LibraryStore -----------------------------------------------------------

    def find_candidates(self, kind: str, attorney: str | None = None) -> list[LibraryFile]:
        matter_id = self._matter()
        want = (
            self._cfg.template_name(kind)
            if attorney is None
            else attorney_file_name(kind, self._labels.get(attorney, attorney))
        )
        try:
            listed = list_matter_files(self._client, matter_id)
        except ListingFailed as exc:
            raise LibraryUnavailable(f"the library's files could not be listed just now ({exc})") from exc
        hits = [e for e in listed if name_matches(e, want) and e.get("isDeleted") is not True]
        folder_id = find_folder_id(self._client, matter_id, self._cfg.folder_name or "")
        in_folder = [e for e in hits if folder_id and _entry_folder_id(e) == folder_id]
        return [
            LibraryFile(
                file_id=str(e.get("id") or e.get("fileId")),
                title=f"{e.get('name') or ''}{e.get('fileExtension') or ''}",
                kind=kind,
                attorney=attorney,
                version=str(e.get("versionId") or ""),
                saved_at=str(e.get("lastUpdated") or e.get("dateCreated") or ""),
            )
            for e in (in_folder or hits)
        ]

    def list_versions(self, file_id: str) -> VersionListing:
        meta = self._meta(file_id)
        if meta is None or not meta.get("versionId"):
            return VersionListing(file_id, (), complete=False)
        current = FileVersion(file_id, str(meta["versionId"]), str(meta.get("lastUpdated") or ""), None)
        return VersionListing(file_id, (current,), complete=False)

    def fetch_version(self, file_id: str, version: str) -> bytes:
        before = self._meta(file_id)
        if before is None:
            raise LibraryUnavailable(f"file {file_id!r} is not in the library")
        if str(before.get("versionId") or "") != version:
            raise NotSupportedYet(
                f"this case system fetches only a file's current version; file {file_id!r} is at "
                f"{before.get('versionId')!r}, not {version!r}"
            )
        try:
            _info, blob = self._client.download_file(self._matter(), file_id)
        except Exception as exc:  # a failed download is "could not look"
            raise LibraryUnavailable(f"file {file_id!r} could not be downloaded ({type(exc).__name__})") from exc
        after = self._meta(file_id)
        if after is None or str(after.get("versionId") or "") != version:
            raise LibraryUnavailable(f"file {file_id!r} was saved again while it was being fetched")
        return blob

    def file_new_version(self, file_id: str, data: bytes, *, expected_version: str) -> FiledVersion:
        try:
            sent = self._client.add_file_version(self._matter(), file_id, data, expected_version=expected_version)
        except (SmokeballWriteError, SmokeballApiError) as exc:
            raise LibraryUnavailable(str(exc)) from exc
        return FiledVersion(
            file_id=file_id,
            version_before=str(sent.get("versionBefore") or ""),
            version_after="",
            sha256=hashlib.sha256(data).hexdigest(),
        )


__all__ = ["SmokeballLibrary", "attorney_file_name"]
