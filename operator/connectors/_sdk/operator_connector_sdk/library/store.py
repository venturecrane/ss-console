"""The storage interface a firm's document library is read through.

WHY AN INTERFACE (design 2026-11-06, piece 2). Every path that makes a firm's
document asks the same question first, "which Word file is this made in", and
the answer must not depend on which case system the firm keeps its files in.
The resolver (``resolver.py``) and the ledger (``ledger.py``) are plain code
that talk to a library only through the four calls below. Today's case-system
connector is the first implementation; a firm on another case system, or with
none, is a second implementation and no redesign.

THE FOUR CALLS:

* ``find_candidates(kind, attorney)``: the files in the library named for a
  kind (and, with an attorney, for that attorney's own file of the kind). For
  setup and for the later look-only acceptance; the resolver never picks a file
  from this list, because a file in the library is not an accepted file.
* ``list_versions(file_id)``: the file's saved versions, NEWEST FIRST, and
  whether the listing is the whole history (``complete``). A store that can
  name only the current version says so with ``complete=False``; it never
  passes one version off as the history.
* ``fetch_version(file_id, version)``: that version's bytes. A store that cannot
  fetch a version other than the current one raises ``NotSupportedYet``, a
  named outcome, never the current bytes in its place.
* ``file_new_version(file_id, data, expected_version)``: a new version of the
  file, sent only when the file is still at ``expected_version``.

"COULD NOT LOOK" IS NEVER "NONE". A store that cannot answer raises
``LibraryUnavailable`` with the reason. An empty list means the store looked and
found nothing. ``list_versions`` on a file that is not in the library (deleted,
or never there) returns an empty listing, which the resolver reports as an
accepted file gone missing, not as "nothing accepted".

STANDARD LIBRARY ONLY, and no import of any case-system connector:
``tests/test_library_stdlib_only.py`` fails otherwise. The jobs runner and the
connector server both install this package; the overlay does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class LibraryUnavailable(RuntimeError):
    """The store could not answer just now, or at all; the reason says which."""


class NotSupportedYet(LibraryUnavailable):
    """This store has no way to answer this call. A named outcome: the caller
    must never treat it as an empty answer or substitute another answer."""


@dataclass(frozen=True)
class LibraryFile:
    """One file in the library, as the store lists it.

    ``attorney`` is the attorney key the store read from the file's name, or
    None for the firm's own file of the kind. ``version`` is the newest saved
    version the listing carries ("" when the listing carries none).
    ``saved_at`` is ISO 8601 UTC, "" when the store cannot say."""

    file_id: str
    title: str
    kind: str
    attorney: str | None
    version: str
    saved_at: str = ""


@dataclass(frozen=True)
class FileVersion:
    """One saved version of a file. ``saved_by`` is None when the store cannot
    say who saved it, which is the ordinary state on today's case system: its
    history names the same user for a file a person saved and a file the
    Operator wrote, so it is not read at all."""

    file_id: str
    version: str
    saved_at: str = ""
    saved_by: str | None = None


@dataclass(frozen=True)
class VersionListing:
    """A file's versions, newest first. ``complete`` is False when the store can
    name only some of them (today's case system: the current version only)."""

    file_id: str
    versions: tuple[FileVersion, ...]
    complete: bool

    @property
    def newest(self) -> FileVersion | None:
        return self.versions[0] if self.versions else None


@dataclass(frozen=True)
class FiledVersion:
    """What ``file_new_version`` sent. ``version_after`` is "" when the store
    versions the file asynchronously and cannot name the new version yet; the
    caller reads it back by id."""

    file_id: str
    version_before: str
    version_after: str
    sha256: str


@runtime_checkable
class LibraryStore(Protocol):
    """A firm's document library, read and written by file id and version."""

    def find_candidates(self, kind: str, attorney: str | None = None) -> list[LibraryFile]: ...

    def list_versions(self, file_id: str) -> VersionListing: ...

    def fetch_version(self, file_id: str, version: str) -> bytes: ...

    def file_new_version(self, file_id: str, data: bytes, *, expected_version: str) -> FiledVersion: ...


__all__ = [
    "FileVersion",
    "FiledVersion",
    "LibraryFile",
    "LibraryStore",
    "LibraryUnavailable",
    "NotSupportedYet",
    "VersionListing",
]
