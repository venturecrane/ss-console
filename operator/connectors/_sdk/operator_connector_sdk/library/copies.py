"""The seat's own copy of each accepted library file, read by its sha256.

WHY THE SEAT KEEPS THE BYTES (design 2026-11-06). The case system hands back a
file's CURRENT version only (``smokeball_connector/client.py``
``download_file``; ``library_store.list_versions`` returns that one version
marked incomplete), and its history names the same user for a person's save and
the Operator's. So once a firm saves an accepted file again, the accepted bytes
cannot be fetched back from the library at all. The seat therefore keeps its own
copy of every accepted file, installed by root beside the ledger:

    <SMD_SPEC_DIR>/library/files/<sha256>.docx

named by the digest of its bytes, root-owned and world-readable, recorded in the
root-computed ``manifest.json`` under ``library_files``. The spec applier
(``spec_applier/library.py`` in the overlay) installs and prunes it from the
firm's vault (``vaults/<slug>/library/files/<sha256>.docx``); nothing on the
agent side writes it.

THREE ANSWERS, NEVER A FOURTH. :func:`read_copy` returns ``PRESENT`` with the
bytes, ``NOT_INSTALLED`` (no copy of that name is on the seat), ``CANNOT_LOOK``
(the store could not be read: a permission fault, a path that is not a
directory) or ``MISMATCH`` (a file of that name whose bytes do not hash to the
name). A caller never reads ``CANNOT_LOOK`` as ``NOT_INSTALLED`` and never uses
the bytes of a ``MISMATCH``: neither carries any.

STANDARD LIBRARY ONLY, like its siblings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Where the copies sit inside the spec store, and their suffix.
COPIES_REL = "library/files"
SUFFIX = ".docx"
#: The manifest key the applier records the copies under.
MANIFEST_FILES_KEY = "library_files"

PRESENT = "present"
NOT_INSTALLED = "not_installed"
CANNOT_LOOK = "cannot_look"
MISMATCH = "mismatch"

_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CopyRead:
    """One copy as the seat holds it. ``data`` is set only when PRESENT."""

    status: str
    sha256: str
    reason: str = ""
    data: bytes | None = field(default=None, repr=False, compare=False)


def copy_rel(sha256: str) -> str:
    """The copy's path inside the spec store. Raises ValueError for a non-sha."""
    if not isinstance(sha256, str) or not _SHA.match(sha256):
        raise ValueError(f"not a lowercase sha256: {sha256!r}")
    return f"{COPIES_REL}/{sha256}{SUFFIX}"


def copies_dir(spec_dir: str | Path) -> Path:
    """The directory of copies inside a spec store."""
    return Path(spec_dir) / COPIES_REL


def read_copy(directory: str | Path, sha256: str) -> CopyRead:
    """The copy named ``sha256`` in ``directory``. Reads; writes nothing; never
    raises for a seat fault (ValueError only for a ``sha256`` that is not one)."""
    name = copy_rel(sha256).rsplit("/", 1)[-1]
    path = Path(directory) / name
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return CopyRead(NOT_INSTALLED, sha256, "no copy of the accepted file is installed on the seat")
    except OSError as exc:
        return CopyRead(CANNOT_LOOK, sha256, f"the seat's copy cannot be read: {type(exc).__name__}")
    actual = hashlib.sha256(blob).hexdigest()
    if actual != sha256:
        return CopyRead(
            MISMATCH,
            sha256,
            f"the seat's copy does not match its name (expected {sha256}, found {actual})",
        )
    return CopyRead(PRESENT, sha256, data=blob)


__all__ = [
    "CANNOT_LOOK",
    "COPIES_REL",
    "MANIFEST_FILES_KEY",
    "MISMATCH",
    "NOT_INSTALLED",
    "PRESENT",
    "SUFFIX",
    "CopyRead",
    "copies_dir",
    "copy_rel",
    "read_copy",
]
