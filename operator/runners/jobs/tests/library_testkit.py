"""An accepted-file ledger on a made-up seat, for the jobs' resolver tests."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from operator_connector_sdk.library import STYLES, Entry, Ledger, MemoryLibrary, accept, dump_ledger

WHEN = "2026-11-01T00:00:00Z"
STATES = ("absent", "manifest_only", "empty")


def entry(lib: MemoryLibrary, file_id: str, kind: str, data: bytes, *, attorney=None, contract=STYLES) -> Entry:
    version = lib.add(file_id, f"Template - {kind}.docx", kind, data, attorney=attorney)
    return Entry(
        kind, attorney, file_id, version, hashlib.sha256(data).hexdigest(), contract, "admin@firm.example", WHEN
    )


def install(spec: Path, *entries: Entry) -> None:
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
