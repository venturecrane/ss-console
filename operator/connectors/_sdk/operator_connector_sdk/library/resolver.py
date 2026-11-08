"""Which Word file a document is made in, and whose name goes on it.

One answer, in code, for every path that makes a firm's document (design
2026-11-06, piece 2). The caller passes the library (``store.LibraryStore``),
the seat's ledger read (``ledger.read_installed``), the kind, the attorney on
the matter, any attorney the requester named, and the attorneys the firm has.
The answer is a plain record (:class:`Resolution`, ``to_json``) written beside
the document, so a later check reads what was used.

THE RULES, each a branch below and a test in ``tests/test_library_resolver.py``:

* Whose name: an attorney the requester named, only when that name or key is
  exactly one of the firm's known attorneys after case and spacing are
  folded; never a near name, and a name two attorneys share is refused as
  ambiguous. Otherwise the matter's attorney. Otherwise nobody.
* Which file: the attorney's accepted file of the kind, else the firm's, else
  none, with the reason. Only an ACCEPTED file is ever returned; a file in the
  library that is not in the ledger is never used, however new.
* A newer saved version than the accepted one is reported, both version ids,
  and NOT used. Deciding to trust a look-only edit is a later piece; this
  module never decides it.
* The accepted version's bytes must match the ledger's sha256; a mismatch is an
  integrity failure, never a silent fallback to another file.
* A ledger the seat cannot read is "cannot look", never "none accepted".
* With ``copies_dir`` (the seat's kept copies, ``copies.py``), the accepted
  bytes come from the seat's own copy, checked against the ledger's sha256
  there, and the library is asked only for the file's versions, never for
  bytes. A copy that is not installed (``COPY_MISSING``), one whose bytes do not
  match (``INTEGRITY_FAILURE``) and one the seat cannot read (``CANNOT_LOOK``)
  are three answers, and none falls back to the library. Without it, every rule
  above holds exactly as it did before the seat kept copies.

No model call, no case-system import. STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import copies
from .ledger import UNUSABLE, Entry, LedgerRead
from .store import LibraryStore, LibraryUnavailable, NotSupportedYet

SCHEMA_VERSION = 1

RESOLVED = "resolved"
NONE_ACCEPTED = "none_accepted"
REFUSED = "refused"
INTEGRITY_FAILURE = "integrity_failure"
CANNOT_LOOK = "cannot_look"
#: The accepted version exists but this store cannot fetch it (today's case
#: system fetches only the current version, so a file saved again after it was
#: accepted lands here). Named, so it is never read as "none accepted".
VERSION_UNAVAILABLE = "version_unavailable"
#: The ledger names an accepted file and the seat holds no copy of it. Only
#: when the caller passed ``copies_dir``; never answered by the library instead.
COPY_MISSING = "copy_missing"

FROM_MATTER = "matter"
FROM_REQUEST = "named"
ATTORNEY_FILE = "attorney"
FIRM_FILE = "firm"


@dataclass(frozen=True)
class Attorney:
    """An attorney the firm has. ``key`` is the stable id the ledger records;
    ``name`` is the name that goes on the document."""

    key: str
    name: str


@dataclass(frozen=True)
class Signer:
    key: str
    name: str
    source: str  # FROM_MATTER | FROM_REQUEST


@dataclass(frozen=True)
class UsedFile:
    file_id: str
    version: str
    sha256: str
    contract: str
    scope: str  # ATTORNEY_FILE | FIRM_FILE
    accepted_by: str
    accepted_at: str
    #: A memo whose tables take another file's look (``ledger.Entry.chart_source``), else None.
    chart_source: dict[str, str] | None = None


@dataclass(frozen=True)
class NewerVersion:
    accepted_version: str
    newest_version: str


@dataclass(frozen=True)
class Resolution:
    """The answer. ``data`` (the accepted version's bytes) is set only when
    ``status`` is RESOLVED and is never part of the JSON record."""

    status: str
    kind: str
    reason: str = ""
    signer: Signer | None = None
    file: UsedFile | None = None
    newer: NewerVersion | None = None
    data: bytes | None = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        def obj(value: Any) -> dict[str, Any] | None:
            return dict(value.__dict__) if value is not None else None

        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "kind": self.kind,
            "reason": self.reason,
            "signer": obj(self.signer),
            "file": obj(self.file),
            "newer_version": obj(self.newer),
        }


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _match_named(named: str, known: Sequence[Attorney]) -> tuple[Attorney | None, str]:
    """The one known attorney ``named`` is, or the reason there is none."""
    want = _fold(named)
    hits = {a.key: a for a in known if want and want in (_fold(a.key), _fold(a.name))}
    if len(hits) == 1:
        return next(iter(hits.values())), ""
    if not hits:
        return None, f"the attorney named in the request, {named!r}, is not one of the firm's attorneys"
    return None, f"the attorney named in the request, {named!r}, matches more than one of the firm's attorneys"


def choose_signer(
    matter_attorney: Attorney | None, named_attorney: str | None, known_attorneys: Sequence[Attorney]
) -> tuple[Signer | None, str]:
    """Whose name goes on the document, or the reason a named one is refused."""
    if named_attorney is not None and named_attorney.strip():
        found, why = _match_named(named_attorney, known_attorneys)
        if found is None:
            return None, why
        return Signer(found.key, found.name, FROM_REQUEST), ""
    if matter_attorney is not None:
        return Signer(matter_attorney.key, matter_attorney.name, FROM_MATTER), ""
    return None, ""


def _used(entry: Entry, scope: str) -> UsedFile:
    return UsedFile(
        file_id=entry.file_id,
        version=entry.version,
        sha256=entry.sha256,
        contract=entry.contract,
        scope=scope,
        accepted_by=entry.accepted_by,
        accepted_at=entry.accepted_at,
        chart_source=entry.chart_source,
    )


def _from_copy(base: Resolution, entry: Entry, copies_dir: str | Path) -> Resolution:
    """The accepted bytes from the seat's own copy, checked against the ledger."""
    read = copies.read_copy(copies_dir, entry.sha256)
    if read.status == copies.NOT_INSTALLED:
        return replace_status(base, COPY_MISSING, f"the accepted file: {read.reason}")
    if read.status == copies.MISMATCH:
        return replace_status(base, INTEGRITY_FAILURE, f"the accepted file: {read.reason}")
    if read.status != copies.PRESENT or read.data is None:
        return replace_status(base, CANNOT_LOOK, f"the accepted file: {read.reason}")
    return Resolution(
        status=RESOLVED, kind=base.kind, signer=base.signer, file=base.file, newer=base.newer, data=read.data
    )


def _check(
    store: LibraryStore,
    kind: str,
    signer: Signer | None,
    entry: Entry,
    scope: str,
    copies_dir: str | Path | None = None,
) -> Resolution:
    """The accepted entry, verified against the library as it stands now."""
    base = Resolution(status=RESOLVED, kind=kind, signer=signer, file=_used(entry, scope))
    try:
        listing = store.list_versions(entry.file_id)
    except LibraryUnavailable as exc:
        return replace_status(base, CANNOT_LOOK, f"the library could not list the accepted file's versions: {exc}")
    newest = listing.newest
    if newest is None:
        return replace_status(base, INTEGRITY_FAILURE, "the accepted file is no longer in the library")
    if listing.complete and entry.version not in {v.version for v in listing.versions}:
        return replace_status(base, INTEGRITY_FAILURE, "the accepted version is not in the file's history")
    newer = NewerVersion(entry.version, newest.version) if newest.version != entry.version else None
    base = Resolution(status=RESOLVED, kind=kind, signer=signer, file=base.file, newer=newer)
    if copies_dir is not None:
        return _from_copy(base, entry, copies_dir)
    try:
        blob = store.fetch_version(entry.file_id, entry.version)
    except NotSupportedYet as exc:
        return replace_status(base, VERSION_UNAVAILABLE, f"the accepted version cannot be fetched: {exc}")
    except LibraryUnavailable as exc:
        return replace_status(base, CANNOT_LOOK, f"the accepted version could not be fetched: {exc}")
    actual = hashlib.sha256(blob).hexdigest()
    if actual != entry.sha256:
        return replace_status(
            base,
            INTEGRITY_FAILURE,
            f"the accepted version's bytes do not match the ledger (expected {entry.sha256}, found {actual})",
        )
    return Resolution(status=RESOLVED, kind=kind, signer=signer, file=base.file, newer=newer, data=blob)


def replace_status(res: Resolution, status: str, reason: str) -> Resolution:
    """``res`` with another status and reason, and never any bytes."""
    return Resolution(status=status, kind=res.kind, reason=reason, signer=res.signer, file=res.file, newer=res.newer)


def resolve(
    store: LibraryStore,
    ledger_read: LedgerRead,
    kind: str,
    *,
    matter_attorney: Attorney | None,
    named_attorney: str | None = None,
    known_attorneys: Sequence[Attorney] = (),
    copies_dir: str | Path | None = None,
) -> Resolution:
    """The accepted file a document of ``kind`` is made in, and whose name goes on it.

    ``copies_dir``: the seat's kept copies (``copies.copies_dir(spec_dir)``).
    Given, the bytes come from there and the library is asked only for versions;
    omitted, the library is asked for the bytes, as before copies existed.
    """
    signer, refused = choose_signer(matter_attorney, named_attorney, known_attorneys)
    if refused:
        return Resolution(status=REFUSED, kind=kind, reason=refused)
    ledger = ledger_read.ledger
    if ledger_read.status == UNUSABLE or ledger is None:
        reason = ledger_read.reason or "the ledger could not be read"
        return Resolution(status=CANNOT_LOOK, kind=kind, reason=f"the accepted-file ledger: {reason}", signer=signer)
    if signer is not None:
        own = ledger.entry_for(kind, signer.key)
        if own is not None:
            return _check(store, kind, signer, own, ATTORNEY_FILE, copies_dir)
    firm = ledger.entry_for(kind, None)
    if firm is not None:
        return _check(store, kind, signer, firm, FIRM_FILE, copies_dir)
    return Resolution(status=NONE_ACCEPTED, kind=kind, reason=f"no file is accepted for {kind}", signer=signer)


__all__ = [
    "ATTORNEY_FILE",
    "CANNOT_LOOK",
    "COPY_MISSING",
    "FIRM_FILE",
    "FROM_MATTER",
    "FROM_REQUEST",
    "INTEGRITY_FAILURE",
    "NONE_ACCEPTED",
    "REFUSED",
    "RESOLVED",
    "SCHEMA_VERSION",
    "VERSION_UNAVAILABLE",
    "Attorney",
    "NewerVersion",
    "Resolution",
    "Signer",
    "UsedFile",
    "choose_signer",
    "replace_status",
    "resolve",
]
