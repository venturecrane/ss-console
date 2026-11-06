"""The seat's kept copies (copies.py) and the resolver reading from them.

With ``copies_dir`` the accepted bytes come from the seat's copy and the library
is asked only for versions: a recording store proves ``fetch_version`` is never
called. A missing copy and a corrupted copy are distinct failures, and neither
falls back to the library. Without the argument the resolver is as it was
(``test_library_resolver.py`` runs unchanged).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from operator_connector_sdk.library import (
    CANNOT_LOOK,
    COPY_MISSING,
    INTEGRITY_FAILURE,
    MARKED,
    RESOLVED,
    VERSION_UNAVAILABLE,
    Attorney,
    Entry,
    Ledger,
    LedgerRead,
    MemoryLibrary,
    NotSupportedYet,
    accept,
    copies,
    copies_dir,
    copy_rel,
    read_copy,
    resolve,
)
from operator_connector_sdk.library.ledger import OK

DANA = Attorney("atty-dana", "Dana Example")
FORM = b"PK accepted brief form bytes"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class RecordingStore:
    """The in-memory library, recording every call; it refuses to hand out bytes,
    as today's case system does for any version but the current one."""

    def __init__(self, lib: MemoryLibrary) -> None:
        self.lib = lib
        self.calls: list[str] = []

    def find_candidates(self, kind, attorney=None):
        self.calls.append("find_candidates")
        return self.lib.find_candidates(kind, attorney)

    def list_versions(self, file_id):
        self.calls.append("list_versions")
        return self.lib.list_versions(file_id)

    def fetch_version(self, file_id, version):
        self.calls.append("fetch_version")
        raise NotSupportedYet("only the current version can be fetched")

    def file_new_version(self, file_id, data, *, expected_version):
        self.calls.append("file_new_version")
        raise AssertionError("never called")


def _seat(tmp_path: Path, *, install: bytes | None = FORM) -> tuple[RecordingStore, LedgerRead, Path]:
    lib = MemoryLibrary()
    version = lib.add("f-brief", "Template - Mediation Brief.docx", "mediation_brief", FORM)
    entry = Entry(
        "mediation_brief", None, "f-brief", version, _sha(FORM), MARKED, "admin@firm.example", "2026-11-06T17:00:00Z"
    )
    spec_dir = tmp_path / "specs"
    if install is not None:
        target = spec_dir / copy_rel(_sha(FORM))
        target.parent.mkdir(parents=True)
        target.write_bytes(install)
    return RecordingStore(lib), LedgerRead(OK, accept(Ledger(), entry)), spec_dir


def _resolve(store, read, spec_dir: Path | None):
    kw = {} if spec_dir is None else {"copies_dir": copies_dir(spec_dir)}
    return resolve(store, read, "mediation_brief", matter_attorney=DANA, **kw)


def test_the_bytes_come_from_the_seat_copy_and_the_store_is_never_asked_for_bytes(tmp_path: Path) -> None:
    store, read, spec_dir = _seat(tmp_path)
    res = _resolve(store, read, spec_dir)
    assert res.status == RESOLVED and res.data == FORM
    assert store.calls == ["list_versions"]


def test_without_copies_the_same_store_cannot_produce_the_file(tmp_path: Path) -> None:
    store, read, _ = _seat(tmp_path)
    res = _resolve(store, read, None)
    assert res.status == VERSION_UNAVAILABLE and res.data is None
    assert store.calls == ["list_versions", "fetch_version"]


def test_a_newer_version_is_reported_and_the_accepted_copy_is_still_used(tmp_path: Path) -> None:
    store, read, spec_dir = _seat(tmp_path)
    newest = store.lib.save("f-brief", b"PK the firm saved it again")
    res = _resolve(store, read, spec_dir)
    assert res.status == RESOLVED and res.data == FORM
    assert res.newer is not None and res.newer.newest_version == newest
    assert res.to_json()["newer_version"]["accepted_version"] == read.ledger.entries[0].version
    assert "fetch_version" not in store.calls


def test_a_missing_copy_and_a_corrupted_copy_are_distinct_failures_and_never_a_fallback(tmp_path: Path) -> None:
    store, read, spec_dir = _seat(tmp_path / "a", install=None)
    missing = _resolve(store, read, spec_dir)
    store2, read2, spec_dir2 = _seat(tmp_path / "b", install=FORM + b" torn")
    corrupt = _resolve(store2, read2, spec_dir2)
    assert missing.status == COPY_MISSING and corrupt.status == INTEGRITY_FAILURE
    assert missing.status != corrupt.status
    assert missing.data is None and corrupt.data is None
    assert "fetch_version" not in store.calls + store2.calls
    assert "does not match its name" in corrupt.reason


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through any mode")
def test_a_copy_store_the_seat_cannot_read_is_cannot_look(tmp_path: Path) -> None:
    store, read, spec_dir = _seat(tmp_path)
    store_dir = copies_dir(spec_dir)
    store_dir.chmod(0o000)
    try:
        res = _resolve(store, read, spec_dir)
        assert res.status == CANNOT_LOOK and res.data is None
        assert read_copy(store_dir, _sha(FORM)).status == copies.CANNOT_LOOK
    finally:
        store_dir.chmod(0o755)


def test_the_reader_names_its_three_answers(tmp_path: Path) -> None:
    d = tmp_path / "files"
    d.mkdir()
    (d / f"{_sha(FORM)}.docx").write_bytes(FORM)
    (d / f"{_sha(b'x')}.docx").write_bytes(b"y")
    assert read_copy(d, _sha(FORM)).status == copies.PRESENT
    assert read_copy(d, _sha(b"z")).status == copies.NOT_INSTALLED
    assert read_copy(d, _sha(b"x")).status == copies.MISMATCH
    assert read_copy(d, _sha(b"x")).data is None
    assert read_copy(tmp_path / "absent", _sha(FORM)).status == copies.NOT_INSTALLED
    (tmp_path / "not-a-dir").write_bytes(b"")
    assert read_copy(tmp_path / "not-a-dir", _sha(FORM)).status == copies.CANNOT_LOOK
    with pytest.raises(ValueError):
        read_copy(d, "../etc/passwd")
