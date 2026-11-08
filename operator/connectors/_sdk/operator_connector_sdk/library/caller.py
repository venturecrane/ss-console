"""What every caller of the resolver shares: the attorney key, the slot of a
variant kind, a store opened only when it is needed, and THE RECORD written
beside the document (design 2026-11-06, piece 2).

THE THREE CALLERS. Exactly three adapters call ``resolver.resolve``, one in
each path that makes a firm's Word document, and nothing else does
(``runners/jobs/tests/test_library_callers.py`` fails otherwise):

* the chat-turn document tool: ``smokeball_connector/library_pick.py``;
* the drafting job: ``jobs/drafting/library_pick.py``;
* the demand job: ``jobs/demand/library_pick.py``.

THE RECORD. Each caller writes :func:`build_record` beside what it made: a file
in a job's data folder (:data:`RECORD_NAME`), a field of the chat tool's result.
It says what the resolver answered (``Resolution.to_json``) and what the
document actually started from (``base``). :func:`check_used` is the rule the
two must keep, and :func:`build_record` refuses to build a record that breaks
it, so a caller that asks the resolver and then starts from something else
fails loudly instead of filing a document whose record says otherwise:

* ``resolved``: the base is the accepted file, by its sha256;
* ``none_accepted``: the base is one of today's fallbacks, named;
* any other status: nothing was started (``not_started``).

A FIRM'S OWN EDIT (design piece 4). When a job finds the accepted file saved
again in the library, it judges that version in code (``jobs/library_edit``)
and the record says so in one more key, ``firm_edit``, present only then: the
version considered, the one it replaces, whether it was used, and why not by
code. A used edit is the resolution's file, ``accepted_by`` :data:`FIRM_MADE`,
with the case system's version id and the sha256 of the cleaned bytes kept. A
record with no newer version is exactly the record it was before this key.

STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import readers as readers_mod, roster as roster_mod
from .ledger import LedgerRead
from .resolver import NONE_ACCEPTED, RESOLVED, Attorney, Resolution
from .store import FiledVersion, LibraryFile, LibraryStore, LibraryUnavailable, VersionListing

RECORD_SCHEMA_VERSION = 1
#: The record's file name in a job's data folder.
RECORD_NAME = "library-resolution.json"

CHAT_TOOL = "chat_tool"
DRAFTING_JOB = "drafting_job"
DEMAND_JOB = "demand_job"
PATHS = (CHAT_TOOL, DRAFTING_JOB, DEMAND_JOB)

#: Where the document's base came from.
ACCEPTED = "accepted_library_file"
PINNED = "pinned_firm_input"
BY_NAME = "library_file_by_name"
STARTER = "starter"
NOT_STARTED = "not_started"
FALLBACKS = (PINNED, BY_NAME, STARTER)
SOURCES = (ACCEPTED, *FALLBACKS, NOT_STARTED)

#: Who said yes to a firm's own edit of its accepted file: no person; the code.
FIRM_MADE = "firm_made"
#: The optional record key a considered firm edit adds, and its own keys.
FIRM_EDIT = "firm_edit"
#: The optional record key naming the document's reader and the profile in
#: effect (``readers.record``, design piece 7).
READER = "reader"
_RECORD_KEYS = frozenset({"schema_version", "path", "kind", "resolution", "base", "overridden"})
_EDIT_KEYS = frozenset({"status", "code", "version", "replaces_version", "sha256", "accepted_at", "install"})
EDIT_USED = "used"
EDIT_REFUSED = "refused"
_BASE_KEYS = frozenset({"source", "sha256", "name", "contract"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


def attorney_key(name: str | None) -> str:
    """The key an attorney is filed under in the ledger: the name with case and
    spacing folded. "" for no name."""
    return re.sub(r"\s+", " ", name or "").strip().casefold()


#: The matter's responsible attorney is someone the firm's roster does not list as
#: an attorney: the document holds and says so (design 2026-11-06: an attorney not
#: on the roster cannot be signed for; identity, not style).
NOT_ROSTERED = "attorney_not_rostered"
#: A roster is installed and cannot be read: "could not look", never "nobody".
ROSTER_UNREADABLE = "roster_unreadable"
NOT_ROSTERED_REASON = (
    "the matter's responsible attorney is not an attorney on the firm's roster, "
    "and a document is never signed for someone who is not"
)


@dataclass(frozen=True)
class MatterAttorney:
    """Who the resolver is told the matter's attorney is, or why nobody may be.
    ``hold`` is "" (``attorney`` may still be None: no attorney on the matter),
    :data:`NOT_ROSTERED` or :data:`ROSTER_UNREADABLE`."""

    attorney: Attorney | None
    hold: str = ""
    reason: str = ""


def matter_attorney(name: str | None, display: str, roster: roster_mod.Roster | None) -> MatterAttorney:
    """The matter's responsible attorney (``name``, as the staff record gives it;
    ``display``, the name that goes on the document) checked against the roster.
    With no roster installed (``absent``, or None) the answer is exactly today's:
    the matter's name, unchecked."""
    if roster is not None and roster.status == roster_mod.UNUSABLE:
        return MatterAttorney(None, ROSTER_UNREADABLE, roster.reason)
    if not (name or "").strip():
        return MatterAttorney(None)
    if roster is not None and roster.status == roster_mod.OK and not roster.is_attorney(name):
        return MatterAttorney(None, NOT_ROSTERED, NOT_ROSTERED_REASON)
    return MatterAttorney(Attorney(attorney_key(name), display))


def known_attorneys(roster: roster_mod.Roster | None, today: list[Attorney]) -> list[Attorney]:
    """The attorneys a requester may name: the roster's when one is installed,
    else ``today`` (the caller's own list)."""
    if roster is None or roster.status != roster_mod.OK:
        return today
    return [Attorney(attorney_key(p.name), p.name) for p in roster.attorneys]


def needs_matter_attorney(ledger_read: LedgerRead, kind: str) -> bool:
    """Whether the matter's attorney can change the answer for ``kind``: only
    when the ledger accepts an attorney's own file of that kind. A caller that
    would have to look the attorney up asks this first, so a seat with nothing
    accepted makes no lookup it does not make today."""
    ledger = ledger_read.ledger
    if ledger is None:
        return False
    return any(e.kind == kind and e.attorney is not None for e in ledger.entries)


class LazyStore:
    """A ``LibraryStore`` opened on its first call. The resolver touches the
    store only when the ledger names an accepted file, so a seat with nothing
    accepted never opens one. A factory that fails is "could not look"."""

    def __init__(self, factory: Callable[[], LibraryStore]) -> None:
        self._factory = factory
        self._store: LibraryStore | None = None

    def _open(self) -> LibraryStore:
        if self._store is None:
            try:
                self._store = self._factory()
            except LibraryUnavailable:
                raise
            except Exception as exc:  # any failure to open is "could not look", never "none"
                raise LibraryUnavailable(f"the library could not be opened ({type(exc).__name__})") from exc
        return self._store

    @property
    def opened(self) -> bool:
        return self._store is not None

    def find_candidates(self, kind: str, attorney: str | None = None) -> list[LibraryFile]:
        return self._open().find_candidates(kind, attorney)

    def list_versions(self, file_id: str) -> VersionListing:
        return self._open().list_versions(file_id)

    def fetch_version(self, file_id: str, version: str) -> bytes:
        return self._open().fetch_version(file_id, version)

    def file_new_version(self, file_id: str, data: bytes, *, expected_version: str) -> FiledVersion:
        return self._open().file_new_version(file_id, data, expected_version=expected_version)


@dataclass(frozen=True)
class BaseUsed:
    """What a document started from. ``sha256`` is None only for the starter
    (built in code, no file) and for ``not_started``."""

    source: str
    sha256: str | None = None
    name: str = ""
    contract: str = ""

    @classmethod
    def of(cls, source: str, data: bytes | None, name: str = "", contract: str = "") -> BaseUsed:
        return cls(source, hashlib.sha256(data).hexdigest() if data is not None else None, name, contract)

    def to_json(self) -> dict[str, Any]:
        return {"source": self.source, "sha256": self.sha256, "name": self.name, "contract": self.contract}


def check_used(resolution: Resolution, base: BaseUsed) -> list[str]:
    """Every way ``base`` is not what the resolver's answer allows; empty when it is."""
    if base.source not in SOURCES:
        return [f"base source {base.source!r} is not one of {list(SOURCES)}"]
    if resolution.status == RESOLVED:
        want = resolution.file.sha256 if resolution.file else None
        if base.source != ACCEPTED or base.sha256 != want:
            return [f"the resolver returned the accepted file {want}, and the document started from {base.to_json()}"]
        return []
    if resolution.status == NONE_ACCEPTED:
        if base.source not in FALLBACKS:
            return [f"nothing is accepted, so the base must be one of today's fallbacks, not {base.source!r}"]
        if base.source != STARTER and (base.sha256 is None or not _SHA.match(base.sha256)):
            return [f"a {base.source} base must name its sha256"]
        return []
    if base.source != NOT_STARTED:
        return [f"the resolver answered {resolution.status}, so nothing may be started, not {base.source!r}"]
    return []


def edit_problems(edit: Any) -> list[str]:
    """Every reason ``edit`` is not a record's ``firm_edit`` block."""
    if not isinstance(edit, dict) or set(edit) != _EDIT_KEYS:
        return [f"a firm_edit block's keys are exactly {sorted(_EDIT_KEYS)}"]
    if edit["status"] not in (EDIT_USED, EDIT_REFUSED):
        return [f"firm_edit status {edit['status']!r}"]
    if edit["status"] == EDIT_USED and (not isinstance(edit["sha256"], str) or not _SHA.match(edit["sha256"])):
        return ["a used firm edit names the sha256 of the bytes kept"]
    if edit["status"] == EDIT_REFUSED and not edit["code"]:
        return ["a refused firm edit names its code"]
    return []


def build_record(
    path: str,
    resolution: Resolution,
    base: BaseUsed,
    overridden: BaseUsed | None = None,
    firm_edit: dict[str, Any] | None = None,
    reader: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The record beside the document. ``overridden``: the pinned input the
    accepted file replaced, while both exist (the migration window).
    ``firm_edit``: the firm's own saved version considered, only when there was one.
    ``reader``: who the document is written for and the profile in effect
    (``readers.record``).
    Raises ``ValueError`` when ``base`` is not what the answer allows."""
    if path not in PATHS:
        raise ValueError(f"unknown path {path!r}")
    problems = check_used(resolution, base)
    if overridden is not None and resolution.status != RESOLVED:
        problems.append("only an accepted file overrides a pinned input")
    if firm_edit is not None:
        problems += edit_problems(firm_edit)
    if reader is not None:
        problems += readers_mod.record_problems(reader)
    if problems:
        raise ValueError("; ".join(problems))
    out = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "path": path,
        "kind": resolution.kind,
        "resolution": resolution.to_json(),
        "base": base.to_json(),
        "overridden": overridden.to_json() if overridden is not None else None,
    }
    if firm_edit is not None:
        out[FIRM_EDIT] = dict(firm_edit)
    if reader is not None:
        out[READER] = dict(reader)
    return out


def record_problems(doc: Any) -> list[str]:
    """Every reason ``doc`` is not a record a later check can read."""
    if not isinstance(doc, dict) or set(doc) - {FIRM_EDIT, READER} != _RECORD_KEYS:
        return [
            f"a record's keys are exactly {sorted(_RECORD_KEYS)}, and {FIRM_EDIT} when an edit was "
            f"considered, and {READER} when the reader was named"
        ]
    out = edit_problems(doc[FIRM_EDIT]) if FIRM_EDIT in doc else []
    out += readers_mod.record_problems(doc[READER]) if READER in doc else []
    if doc["schema_version"] != RECORD_SCHEMA_VERSION:
        out.append(f"schema {doc['schema_version']!r}")
    if doc["path"] not in PATHS:
        out.append(f"path {doc['path']!r}")
    res = doc["resolution"]
    if not isinstance(res, dict) or res.get("kind") != doc["kind"]:
        out.append("the resolution's kind is not the record's")
    for name in ("base", "overridden"):
        value = doc[name]
        if value is None and name == "overridden":
            continue
        if not isinstance(value, dict) or set(value) != _BASE_KEYS or value["source"] not in SOURCES:
            out.append(f"{name} is not a base")
    return out


def write_record(directory: str | Path, record: dict[str, Any]) -> Path:
    """The record, written whole or not at all, beside a job's other records."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / RECORD_NAME
    tmp = folder / f".{RECORD_NAME}.tmp"
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    return dest


__all__ = [
    "ACCEPTED",
    "BY_NAME",
    "CHAT_TOOL",
    "DEMAND_JOB",
    "DRAFTING_JOB",
    "EDIT_REFUSED",
    "EDIT_USED",
    "FALLBACKS",
    "FIRM_EDIT",
    "FIRM_MADE",
    "NOT_STARTED",
    "PATHS",
    "PINNED",
    "READER",
    "RECORD_NAME",
    "RECORD_SCHEMA_VERSION",
    "SOURCES",
    "STARTER",
    "BaseUsed",
    "LazyStore",
    "MatterAttorney",
    "NOT_ROSTERED",
    "ROSTER_UNREADABLE",
    "attorney_key",
    "build_record",
    "check_used",
    "edit_problems",
    "known_attorneys",
    "matter_attorney",
    "needs_matter_attorney",
    "record_problems",
    "write_record",
]
