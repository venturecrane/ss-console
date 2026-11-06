"""The accepted-file ledger: which library files a firm has said yes to.

WHY (design 2026-11-06). The newest file of a name in a firm's library cannot
be trusted: on the one live client seat every class template is the Operator's
own starter, three kinds hold several files under one name, and the case
system's history names the same user for a file a person saved and a file the
Operator wrote. So a file is used only once it is ACCEPTED, and the seat keeps
the list. One entry per (kind, attorney or none) slot:

    kind         a document kind (operator/contracts/document-kinds.yaml)
    attorney     the attorney key, or null for the firm's own file of the kind
    file_id      the file in the library
    version      the accepted saved version of that file
    sha256       the digest of exactly that version's bytes
    contract     "styles": page setup, header, footer and named styles, the body
                 written fresh (ADR 0083, 2026-08-19 amendment); "marked": a firm
                 document whose words were replaced by marked places
                 (``[[name]]``, the firm-base code); "prototypes": a firm document
                 holding one tagged prototype paragraph or table of each kind
                 (``PROTO:<kind>``), which the demand writer clones
                 (``jobs/demand/house.py``)
    accepted_by  who said yes
    accepted_at  when, ISO 8601 UTC
    replaces     the slot's previous accepted file (file_id, version, sha256),
                 or null

A replaced entry moves to ``superseded``, so the ledger answers "what did we use
before" without a second store.

WHERE IT LIVES ON A SEAT, and what writes it. In the seat's root-owned spec
store, beside ``layout.json``: ``<SMD_SPEC_DIR>/library/accepted.json``
(``/var/lib/smd-config/specs/library/accepted.json``), its sha256 recorded by
root in ``manifest.json`` under ``library_ledger``. The writer goes through the
existing root-owned spec install path: the root-side establishment intake
(``establish_intake/library_accept.py``) writes the new ledger (the output of
:func:`accept`) to the firm's own vault object ``vaults/<slug>/library/accepted.json``,
each accepted file's bytes beside it at ``vaults/<slug>/library/files/<sha256>.docx``,
and the spec applier (``spec_applier/library.py``) installs both and records
their digests in the manifest. Nothing on the agent side writes it. This module
holds the format, a pure parser and validator, the pure acceptance function, and
the one read-only reader of an installed ledger. The overlay keeps a byte-equal
copy at ``operator/hermes-smd-overlay/shared/library_ledger.py``, because the
overlay does not install this package; ``tests/test_library_seat_copy.py`` in
the overlay fails if the two differ.

"CANNOT LOOK" IS NEVER "NONE". :func:`read_installed` answers one of three:
``EMPTY`` (no ledger installed: an ordinary seat with nothing accepted yet),
``OK`` (a ledger its manifest vouches for), ``UNUSABLE`` (present but
unreadable, invalid or unvouched). A caller holds an UNUSABLE answer for SMD; it
never drafts on a plain base as though nothing were accepted.

STANDARD LIBRARY ONLY.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
#: The ledger's path inside the spec store, and its manifest key.
LEDGER_REL = "library/accepted.json"
MANIFEST = "manifest.json"
MANIFEST_KEY = "library_ledger"

STYLES = "styles"
MARKED = "marked"
PROTOTYPES = "prototypes"
CONTRACTS = (STYLES, MARKED, PROTOTYPES)

OK = "ok"
EMPTY = "empty"
UNUSABLE = "unusable"

_SHA = re.compile(r"^[0-9a-f]{64}$")
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_ENTRY_KEYS = frozenset(
    {
        "kind",
        "attorney",
        "file_id",
        "version",
        "sha256",
        "contract",
        "accepted_by",
        "accepted_at",
        "replaces",
    }
)
_REPLACED_KEYS = frozenset({"file_id", "version", "sha256"})
_TOP_KEYS = frozenset({"schema_version", "entries", "superseded"})


class LedgerInvalid(ValueError):
    """The bytes are not a ledger this code reads. ``problems`` names why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:300])
        self.problems = problems


class AcceptanceInvalid(ValueError):
    """An acceptance that cannot go in the ledger; nothing changed."""


@dataclass(frozen=True)
class Replaced:
    file_id: str
    version: str
    sha256: str


@dataclass(frozen=True)
class Entry:
    kind: str
    attorney: str | None
    file_id: str
    version: str
    sha256: str
    contract: str
    accepted_by: str
    accepted_at: str
    replaces: Replaced | None = None

    @property
    def slot(self) -> tuple[str, str | None]:
        return (self.kind, self.attorney)


#: An acceptance is an entry before it is placed: ``replaces`` is the ledger's
#: to fill, so an acceptance carries none.
Acceptance = Entry


@dataclass(frozen=True)
class Ledger:
    entries: tuple[Entry, ...] = ()
    superseded: tuple[Entry, ...] = ()

    def entry_for(self, kind: str, attorney: str | None) -> Entry | None:
        for entry in self.entries:
            if entry.slot == (kind, attorney):
                return entry
        return None


@dataclass(frozen=True)
class LedgerRead:
    """What the seat's store holds. ``ledger`` is set only when ``status`` is
    OK or EMPTY; an UNUSABLE read carries the reason and no ledger at all, so
    no caller can read it as empty by accident."""

    status: str
    ledger: Ledger | None
    reason: str = ""


# ---- validation -------------------------------------------------------------------


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _replaced_problems(where: str, raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, dict) or set(raw) != _REPLACED_KEYS:
        return [f"{where}.replaces must be null or {{file_id, version, sha256}}"]
    out = [f"{where}.replaces.{k} is empty" for k in ("file_id", "version") if not _text(raw[k])]
    if not isinstance(raw["sha256"], str) or not _SHA.match(raw["sha256"]):
        out.append(f"{where}.replaces.sha256 is not a sha256")
    return out


def _entry_problems(where: str, raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return [f"{where} is not an object"]
    if set(raw) != _ENTRY_KEYS:
        missing, extra = sorted(_ENTRY_KEYS - set(raw)), sorted(set(raw) - _ENTRY_KEYS)
        return [f"{where} has missing keys {missing} or unknown keys {extra}"]
    out = [
        f"{where}.{k} is empty"
        for k in ("kind", "file_id", "version", "accepted_by")
        if not _text(raw[k])
    ]
    if raw["attorney"] is not None and not _text(raw["attorney"]):
        out.append(f"{where}.attorney must be null or an attorney key")
    if not isinstance(raw["sha256"], str) or not _SHA.match(raw["sha256"]):
        out.append(f"{where}.sha256 is not a lowercase sha256")
    if raw["contract"] not in CONTRACTS:
        out.append(f"{where}.contract must be one of {list(CONTRACTS)}")
    if not isinstance(raw["accepted_at"], str) or not _WHEN.match(raw["accepted_at"]):
        out.append(f"{where}.accepted_at is not ISO 8601 UTC")
    return out + _replaced_problems(where, raw["replaces"])


def validate_ledger(doc: Any) -> list[str]:
    """Every reason ``doc`` is not a valid ledger; empty when it is one."""
    if not isinstance(doc, dict):
        return ["the ledger is not an object"]
    if set(doc) != _TOP_KEYS:
        return [f"the ledger's keys must be exactly {sorted(_TOP_KEYS)}"]
    if doc["schema_version"] != SCHEMA_VERSION:
        return [f"the ledger is schema {doc['schema_version']!r}, this code reads {SCHEMA_VERSION}"]
    out: list[str] = []
    for name in ("entries", "superseded"):
        if not isinstance(doc[name], list):
            out.append(f"{name} is not a list")
            continue
        for i, raw in enumerate(doc[name]):
            out += _entry_problems(f"{name}[{i}]", raw)
    if out:
        return out
    seen: set[tuple[Any, Any]] = set()
    for raw in doc["entries"]:
        slot = (raw["kind"], raw["attorney"])
        if slot in seen:
            out.append(f"two accepted files for kind {slot[0]!r}, attorney {slot[1]!r}")
        seen.add(slot)
    return out


# ---- the format ---------------------------------------------------------------------


def _entry(raw: dict[str, Any]) -> Entry:
    rep = raw["replaces"]
    return Entry(
        kind=raw["kind"],
        attorney=raw["attorney"],
        file_id=raw["file_id"],
        version=raw["version"],
        sha256=raw["sha256"],
        contract=raw["contract"],
        accepted_by=raw["accepted_by"],
        accepted_at=raw["accepted_at"],
        replaces=Replaced(rep["file_id"], rep["version"], rep["sha256"]) if rep else None,
    )


def _entry_json(entry: Entry) -> dict[str, Any]:
    rep = entry.replaces
    return {
        "kind": entry.kind,
        "attorney": entry.attorney,
        "file_id": entry.file_id,
        "version": entry.version,
        "sha256": entry.sha256,
        "contract": entry.contract,
        "accepted_by": entry.accepted_by,
        "accepted_at": entry.accepted_at,
        "replaces": {"file_id": rep.file_id, "version": rep.version, "sha256": rep.sha256}
        if rep
        else None,
    }


def parse_ledger(data: bytes) -> Ledger:
    """The ledger these bytes hold. Raises ``LedgerInvalid``; never guesses."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LedgerInvalid([f"the ledger is not JSON ({type(exc).__name__})"]) from exc
    problems = validate_ledger(doc)
    if problems:
        raise LedgerInvalid(problems)
    return Ledger(
        entries=tuple(_entry(e) for e in doc["entries"]),
        superseded=tuple(_entry(e) for e in doc["superseded"]),
    )


def dump_ledger(ledger: Ledger) -> bytes:
    """The ledger's bytes, deterministic, so root can digest what it wrote."""
    doc = {
        "schema_version": SCHEMA_VERSION,
        "entries": [_entry_json(e) for e in ledger.entries],
        "superseded": [_entry_json(e) for e in ledger.superseded],
    }
    return json.dumps(doc, indent=2, sort_keys=True).encode("utf-8") + b"\n"


# ---- acceptance -------------------------------------------------------------------


def accept(ledger: Ledger, acceptance: Acceptance) -> Ledger:
    """The ledger after ``acceptance``. Pure: no I/O, ``ledger`` is not changed.

    The acceptance takes its (kind, attorney) slot; the slot's previous entry,
    if any, becomes ``replaces`` and moves to ``superseded``. Accepting the very
    file, version and bytes already accepted returns the ledger unchanged.
    Raises ``AcceptanceInvalid`` for an acceptance the ledger cannot hold.
    """
    raw = _entry_json(replace(acceptance, replaces=None))
    problems = _entry_problems("acceptance", raw)
    if acceptance.replaces is not None:
        problems.append("acceptance.replaces is the ledger's to fill, not the caller's")
    if problems:
        raise AcceptanceInvalid("; ".join(problems))
    current = ledger.entry_for(acceptance.kind, acceptance.attorney)
    if current is not None and (
        current.file_id,
        current.version,
        current.sha256,
        current.contract,
    ) == (
        acceptance.file_id,
        acceptance.version,
        acceptance.sha256,
        acceptance.contract,
    ):
        return ledger
    placed = replace(
        acceptance,
        replaces=Replaced(current.file_id, current.version, current.sha256) if current else None,
    )
    kept = tuple(e for e in ledger.entries if e.slot != placed.slot)
    superseded = ledger.superseded + ((current,) if current else ())
    return Ledger(entries=kept + (placed,), superseded=superseded)


# ---- the installed ledger -----------------------------------------------------------


def _unusable(reason: str) -> LedgerRead:
    return LedgerRead(UNUSABLE, None, reason)


def _manifest_entry(
    root: Path, ledger_exists: bool
) -> tuple[dict[str, Any] | None, LedgerRead | None]:
    """The manifest's record of the ledger, or the read that ends the lookup."""
    try:
        manifest = json.loads((root / MANIFEST).read_text("utf-8"))
    except FileNotFoundError:
        if ledger_exists:
            return None, _unusable("the ledger has no manifest beside it")
        return None, LedgerRead(EMPTY, Ledger())
    except (OSError, ValueError) as exc:
        return None, _unusable(f"the manifest cannot be read: {type(exc).__name__}")
    if not isinstance(manifest, dict):
        return None, _unusable("the manifest is not an object")
    recorded = manifest.get(MANIFEST_KEY) or {}
    entry = recorded.get(LEDGER_REL) if isinstance(recorded, dict) else None
    return (entry if isinstance(entry, dict) else None), None


def read_installed(spec_dir: str | Path) -> LedgerRead:
    """The ledger the seat's spec store holds. Reads files; writes nothing; never raises."""
    root = Path(spec_dir)
    path = root / LEDGER_REL
    try:
        exists = path.exists()
    except OSError as exc:
        return _unusable(f"the ledger cannot be checked: {type(exc).__name__}")
    entry, done = _manifest_entry(root, exists)
    if done is not None:
        return done
    if not exists:
        return (
            _unusable("the manifest names the ledger and it is gone")
            if entry
            else LedgerRead(EMPTY, Ledger())
        )
    if entry is None:
        return _unusable("the ledger is present and the manifest does not name it")
    try:
        blob = path.read_bytes()
    except OSError as exc:
        return _unusable(f"the ledger cannot be read: {type(exc).__name__}")
    if entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return _unusable("the ledger does not match the digest in the manifest")
    try:
        return LedgerRead(OK, parse_ledger(blob))
    except LedgerInvalid as exc:
        return _unusable(f"the ledger is invalid: {exc}")


__all__ = [
    "CONTRACTS",
    "EMPTY",
    "LEDGER_REL",
    "MANIFEST",
    "MANIFEST_KEY",
    "MARKED",
    "OK",
    "PROTOTYPES",
    "SCHEMA_VERSION",
    "STYLES",
    "UNUSABLE",
    "Acceptance",
    "AcceptanceInvalid",
    "Entry",
    "Ledger",
    "LedgerInvalid",
    "LedgerRead",
    "Replaced",
    "accept",
    "dump_ledger",
    "parse_ledger",
    "read_installed",
    "validate_ledger",
]
