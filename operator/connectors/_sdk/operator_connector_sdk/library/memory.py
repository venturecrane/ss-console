"""A library held in memory: the reference implementation of ``LibraryStore``.

It keeps every version of every file, so it answers all four calls completely.
The resolver's and the ledger's tests run against it, and a jobs-runner test
can stand it in for a case system. Nothing on a seat uses it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .store import FiledVersion, FileVersion, LibraryFile, LibraryUnavailable, VersionListing


@dataclass
class _File:
    title: str
    kind: str
    attorney: str | None
    versions: list[tuple[FileVersion, bytes]] = field(default_factory=list)  # oldest first


class MemoryLibrary:
    def __init__(self) -> None:
        self._files: dict[str, _File] = {}
        self._clock = 0
        #: Set to make every call raise, as a store that cannot be reached does.
        self.unavailable: str = ""

    def _tick(self) -> str:
        self._clock += 1
        return f"2026-01-01T00:00:{self._clock:02d}Z"

    def _guard(self) -> None:
        if self.unavailable:
            raise LibraryUnavailable(self.unavailable)

    def add(self, file_id: str, title: str, kind: str, data: bytes, *, attorney: str | None = None) -> str:
        """A new file with one version; returns that version's id."""
        self._files[file_id] = _File(title, kind, attorney)
        return self._save(file_id, data)

    def _save(self, file_id: str, data: bytes, saved_by: str | None = None) -> str:
        f = self._files[file_id]
        version = f"{file_id}-v{len(f.versions) + 1}"
        f.versions.append((FileVersion(file_id, version, self._tick(), saved_by), bytes(data)))
        return version

    def save(self, file_id: str, data: bytes, saved_by: str | None = None) -> str:
        """A person saves the file again (a new version); returns its id."""
        return self._save(file_id, data, saved_by)

    def tamper(self, file_id: str, version: str, data: bytes) -> None:
        """Change a stored version's bytes in place, as no real save does."""
        f = self._files[file_id]
        f.versions = [(v, data if v.version == version else b) for v, b in f.versions]

    def remove(self, file_id: str) -> None:
        del self._files[file_id]

    # ---- LibraryStore -------------------------------------------------------------

    def find_candidates(self, kind: str, attorney: str | None = None) -> list[LibraryFile]:
        self._guard()
        out = []
        for fid, f in self._files.items():
            if f.kind == kind and f.attorney == attorney:
                newest = f.versions[-1][0]
                out.append(LibraryFile(fid, f.title, f.kind, f.attorney, newest.version, newest.saved_at))
        return out

    def list_versions(self, file_id: str) -> VersionListing:
        self._guard()
        f = self._files.get(file_id)
        versions = tuple(v for v, _ in reversed(f.versions)) if f else ()
        return VersionListing(file_id, versions, complete=True)

    def fetch_version(self, file_id: str, version: str) -> bytes:
        self._guard()
        f = self._files.get(file_id)
        for v, data in f.versions if f else []:
            if v.version == version:
                return data
        raise LibraryUnavailable(f"file {file_id!r} has no version {version!r}")

    def file_new_version(self, file_id: str, data: bytes, *, expected_version: str) -> FiledVersion:
        self._guard()
        f = self._files.get(file_id)
        if f is None:
            raise LibraryUnavailable(f"file {file_id!r} is not in the library; nothing sent")
        before = f.versions[-1][0].version
        if before != expected_version:
            raise LibraryUnavailable(f"file {file_id!r} is at {before!r}, not {expected_version!r}; nothing sent")
        after = self._save(file_id, data)
        return FiledVersion(file_id, before, after, hashlib.sha256(data).hexdigest())


__all__ = ["MemoryLibrary"]
