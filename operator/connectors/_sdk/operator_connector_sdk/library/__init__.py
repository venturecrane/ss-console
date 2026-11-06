"""A firm's document library: the storage interface, the accepted-file ledger,
and the resolver that answers "which Word file is this document made in, and
whose name goes on it" (design 2026-11-06, piece 2).

Standard library only, and no case-system import (``tests/test_library_stdlib_only.py``).
Nothing on a production path calls it yet; the callers are wired in a later PR.
"""

from __future__ import annotations

from .ledger import (
    CONTRACTS,
    EMPTY,
    LEDGER_REL,
    MANIFEST_KEY,
    MARKED,
    OK,
    STYLES,
    UNUSABLE,
    Acceptance,
    AcceptanceInvalid,
    Entry,
    Ledger,
    LedgerInvalid,
    LedgerRead,
    Replaced,
    accept,
    dump_ledger,
    parse_ledger,
    read_installed,
    validate_ledger,
)
from .memory import MemoryLibrary
from .resolver import (
    CANNOT_LOOK,
    INTEGRITY_FAILURE,
    NONE_ACCEPTED,
    REFUSED,
    RESOLVED,
    VERSION_UNAVAILABLE,
    Attorney,
    NewerVersion,
    Resolution,
    Signer,
    UsedFile,
    resolve,
)
from .store import (
    FiledVersion,
    FileVersion,
    LibraryFile,
    LibraryStore,
    LibraryUnavailable,
    NotSupportedYet,
    VersionListing,
)

__all__ = [
    "CANNOT_LOOK",
    "CONTRACTS",
    "EMPTY",
    "INTEGRITY_FAILURE",
    "LEDGER_REL",
    "MANIFEST_KEY",
    "MARKED",
    "NONE_ACCEPTED",
    "OK",
    "REFUSED",
    "RESOLVED",
    "STYLES",
    "UNUSABLE",
    "VERSION_UNAVAILABLE",
    "Acceptance",
    "AcceptanceInvalid",
    "Attorney",
    "Entry",
    "FileVersion",
    "FiledVersion",
    "Ledger",
    "LedgerInvalid",
    "LedgerRead",
    "LibraryFile",
    "LibraryStore",
    "LibraryUnavailable",
    "MemoryLibrary",
    "NewerVersion",
    "NotSupportedYet",
    "Replaced",
    "Resolution",
    "Signer",
    "UsedFile",
    "VersionListing",
    "accept",
    "dump_ledger",
    "parse_ledger",
    "read_installed",
    "resolve",
    "validate_ledger",
]
