"""An accepted-file ledger on a made-up seat, for the jobs' resolver tests."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from operator_connector_sdk.library import STYLES, Entry, Ledger, MemoryLibrary, accept, copies_dir, dump_ledger

WHEN = "2026-11-01T00:00:00Z"
#: Each entry's bytes by sha256, so ``install`` can put the seat's copy beside the ledger.
_BYTES: dict[str, bytes] = {}
STATES = ("absent", "manifest_only", "empty")


def entry(lib: MemoryLibrary, file_id: str, kind: str, data: bytes, *, attorney=None, contract=STYLES) -> Entry:
    version = lib.add(file_id, f"Template - {kind}.docx", kind, data, attorney=attorney)
    _BYTES[hashlib.sha256(data).hexdigest()] = data
    return Entry(
        kind, attorney, file_id, version, hashlib.sha256(data).hexdigest(), contract, "admin@firm.example", WHEN
    )


def install(spec: Path, *entries: Entry, copies: bool = True) -> None:
    """The ledger, and (unless ``copies`` is False) the seat's copy of each
    entry's file, where root installs them (``copies.copies_dir``)."""
    if copies:
        for e in entries:
            copy = copies_dir(spec) / f"{e.sha256}.docx"
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(_BYTES[e.sha256])
    ledger = Ledger()
    for e in entries:
        ledger = accept(ledger, e)
    blob = dump_ledger(ledger)
    (spec / "library").mkdir(parents=True, exist_ok=True)
    (spec / "library" / "accepted.json").write_bytes(blob)
    manifest = {"library_ledger": {"library/accepted.json": {"sha256": hashlib.sha256(blob).hexdigest()}}}
    (spec / "manifest.json").write_text(json.dumps(manifest))


def state(spec: Path, name: str) -> None:
    """A seat with nothing accepted, three ways: no ledger, a manifest that
    names none, an empty ledger."""
    if name == "manifest_only":
        (spec / "manifest.json").write_text(json.dumps({"layouts": {}}))
    elif name == "empty":
        install(spec)


def members(data: bytes) -> dict[str, bytes]:
    """A .docx compared by its members: its zip headers carry the save time."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def record(data_dir: Path) -> dict:
    return json.loads((data_dir / "library-resolution.json").read_text(encoding="utf-8"))


def spoil_copy(spec: Path, data: bytes, other: bytes) -> None:
    """The seat's copy of ``data`` holds ``other`` instead."""
    (copies_dir(spec) / f"{hashlib.sha256(data).hexdigest()}.docx").write_bytes(other)
