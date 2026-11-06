"""The resolver: which accepted file a document is made in, and whose name goes on it.

Every rule in resolver.py has a test here that fails if the rule is broken. The
library is the in-memory reference store; attorneys and files are made up.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from operator_connector_sdk.library import (
    CANNOT_LOOK,
    EMPTY,
    INTEGRITY_FAILURE,
    NONE_ACCEPTED,
    REFUSED,
    RESOLVED,
    STYLES,
    MARKED,
    UNUSABLE,
    VERSION_UNAVAILABLE,
    Attorney,
    Entry,
    FileVersion,
    Ledger,
    LedgerRead,
    LibraryStore,
    MemoryLibrary,
    NotSupportedYet,
    VersionListing,
    accept,
    resolve,
)
from operator_connector_sdk.library.ledger import OK

DANA = Attorney("atty-dana", "Dana Example")
LEE = Attorney("atty-lee", "Lee Sample")
ROSTER = (DANA, LEE)
FIRM_BYTES = b"firm brief file"
DANA_BYTES = b"dana brief file"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _entry(file_id: str, version: str, data: bytes, attorney: str | None = None, contract: str = STYLES) -> Entry:
    return Entry(
        kind="mediation_brief",
        attorney=attorney,
        file_id=file_id,
        version=version,
        sha256=_sha(data),
        contract=contract,
        accepted_by="admin@firm.example",
        accepted_at="2026-11-06T17:00:00Z",
    )


def _setup(*, with_dana: bool = True) -> tuple[MemoryLibrary, LedgerRead]:
    lib = MemoryLibrary()
    fv = lib.add("f-firm", "Template - Mediation Brief.docx", "mediation_brief", FIRM_BYTES)
    led = accept(Ledger(), _entry("f-firm", fv, FIRM_BYTES))
    if with_dana:
        dv = lib.add(
            "f-dana", "Template - Mediation Brief - Dana.docx", "mediation_brief", DANA_BYTES, attorney=DANA.key
        )
        led = accept(led, _entry("f-dana", dv, DANA_BYTES, attorney=DANA.key, contract=MARKED))
    return lib, LedgerRead(OK, led)


def test_the_memory_library_is_a_library_store() -> None:
    assert isinstance(MemoryLibrary(), LibraryStore)


def test_the_attorneys_accepted_file_beats_the_firms() -> None:
    lib, read = _setup()
    res = resolve(lib, read, "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    assert res.status == RESOLVED
    assert res.file is not None and res.file.file_id == "f-dana" and res.file.scope == "attorney"
    assert res.file.contract == MARKED
    assert res.data == DANA_BYTES
    assert res.signer is not None and (res.signer.name, res.signer.source) == ("Dana Example", "matter")


def test_an_attorney_with_no_file_of_their_own_gets_the_firms() -> None:
    lib, read = _setup()
    res = resolve(lib, read, "mediation_brief", matter_attorney=LEE, known_attorneys=ROSTER)
    assert res.status == RESOLVED and res.file is not None and res.file.file_id == "f-firm"
    assert res.file.scope == "firm" and res.signer is not None and res.signer.key == LEE.key


def test_a_named_attorney_on_the_roster_wins_over_the_matters() -> None:
    lib, read = _setup()
    res = resolve(
        lib, read, "mediation_brief", matter_attorney=LEE, named_attorney="  dana   EXAMPLE ", known_attorneys=ROSTER
    )
    assert res.status == RESOLVED and res.file is not None and res.file.file_id == "f-dana"
    assert res.signer is not None and (res.signer.key, res.signer.source) == (DANA.key, "named")


@pytest.mark.parametrize("named", ["Dana Exampel", "Dana", "Dana Example Esq.", "atty-dan"])
def test_a_named_attorney_not_on_the_roster_is_refused_not_guessed(named) -> None:
    lib, read = _setup()
    res = resolve(lib, read, "mediation_brief", matter_attorney=LEE, named_attorney=named, known_attorneys=ROSTER)
    assert res.status == REFUSED
    assert res.file is None and res.signer is None and res.data is None
    assert named in res.reason


def test_a_name_two_attorneys_share_is_refused_as_ambiguous() -> None:
    lib, read = _setup()
    twins = (Attorney("atty-1", "Sam Doe"), Attorney("atty-2", "Sam Doe"))
    res = resolve(lib, read, "mediation_brief", matter_attorney=None, named_attorney="Sam Doe", known_attorneys=twins)
    assert res.status == REFUSED and "more than one" in res.reason


def test_a_newer_unaccepted_version_is_reported_and_not_used() -> None:
    lib, read = _setup(with_dana=False)
    accepted = read.ledger.entry_for("mediation_brief", None).version  # type: ignore[union-attr]
    newest = lib.save("f-firm", b"someone saved it again", saved_by="staff")
    res = resolve(lib, read, "mediation_brief", matter_attorney=None)
    assert res.status == RESOLVED
    assert res.data == FIRM_BYTES, "the accepted version's bytes, never the newer save's"
    assert res.file is not None and res.file.version == accepted
    assert res.newer is not None and (res.newer.accepted_version, res.newer.newest_version) == (accepted, newest)
    assert res.to_json()["newer_version"] == {"accepted_version": accepted, "newest_version": newest}


def test_a_file_in_the_library_that_is_not_in_the_ledger_is_never_used() -> None:
    lib = MemoryLibrary()
    lib.add("f-new", "Template - Mediation Brief.docx", "mediation_brief", b"newest by name")
    res = resolve(lib, LedgerRead(EMPTY, Ledger()), "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    assert res.status == NONE_ACCEPTED and res.file is None and res.data is None
    assert "mediation_brief" in res.reason


def test_an_unlisted_newer_file_never_displaces_the_accepted_one() -> None:
    lib, read = _setup(with_dana=False)
    lib.add("f-newer", "Template - Mediation Brief.docx", "mediation_brief", b"a newer file of the same name")
    res = resolve(lib, read, "mediation_brief", matter_attorney=None)
    assert res.file is not None and res.file.file_id == "f-firm" and res.data == FIRM_BYTES


def test_bytes_that_no_longer_match_the_ledger_are_an_integrity_failure() -> None:
    lib, read = _setup()
    dana = read.ledger.entry_for("mediation_brief", DANA.key)  # type: ignore[union-attr]
    lib.tamper("f-dana", dana.version, b"changed under the same version")  # type: ignore[union-attr]
    res = resolve(lib, read, "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    assert res.status == INTEGRITY_FAILURE
    assert res.data is None
    assert res.file is not None and res.file.file_id == "f-dana", "no silent fallback to the firm's file"
    assert dana.sha256 in res.reason  # type: ignore[union-attr]


def test_an_accepted_file_gone_from_the_library_is_an_integrity_failure_not_none() -> None:
    lib, read = _setup(with_dana=False)
    lib.remove("f-firm")
    res = resolve(lib, read, "mediation_brief", matter_attorney=None)
    assert res.status == INTEGRITY_FAILURE and "no longer in the library" in res.reason


@pytest.mark.parametrize(
    "read",
    [
        LedgerRead(UNUSABLE, None, "the ledger does not match the digest"),
        LedgerRead(UNUSABLE, Ledger(), "a read marked unusable is unusable whatever it carries"),
        LedgerRead(OK, None),
    ],
)
def test_an_unreadable_ledger_is_cannot_look_never_none_accepted(read) -> None:
    lib, _ = _setup()
    res = resolve(lib, read, "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    assert res.status == CANNOT_LOOK
    assert res.status != NONE_ACCEPTED and res.file is None and res.data is None


def test_an_empty_ledger_is_none_accepted() -> None:
    lib, _ = _setup()
    res = resolve(lib, LedgerRead(EMPTY, Ledger()), "mediation_brief", matter_attorney=None)
    assert res.status == NONE_ACCEPTED


def test_a_store_that_cannot_be_reached_is_cannot_look() -> None:
    lib, read = _setup()
    lib.unavailable = "the case system timed out"
    res = resolve(lib, read, "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    assert res.status == CANNOT_LOOK and "timed out" in res.reason


class _CurrentOnly(MemoryLibrary):
    """A store like today's case system: it names and fetches the current version only."""

    def list_versions(self, file_id: str) -> VersionListing:
        full = super().list_versions(file_id)
        return VersionListing(file_id, full.versions[:1], complete=False)

    def fetch_version(self, file_id: str, version: str) -> bytes:
        newest = super().list_versions(file_id).newest
        if newest is None or newest.version != version:
            raise NotSupportedYet("only the current version can be fetched")
        return super().fetch_version(file_id, version)


def test_a_store_that_cannot_fetch_the_accepted_version_says_so_with_both_ids() -> None:
    lib = _CurrentOnly()
    v1 = lib.add("f-firm", "Template - Memo.docx", "memo", FIRM_BYTES)
    read = LedgerRead(
        OK, accept(Ledger(), Entry("memo", None, "f-firm", v1, _sha(FIRM_BYTES), STYLES, "a", "2026-11-06T17:00:00Z"))
    )
    v2 = lib.save("f-firm", b"look-only edit")
    res = resolve(lib, read, "memo", matter_attorney=None)
    assert res.status == VERSION_UNAVAILABLE
    assert res.newer is not None and (res.newer.accepted_version, res.newer.newest_version) == (v1, v2)
    assert res.data is None


def test_the_answer_is_a_plain_json_record_without_the_bytes() -> None:
    lib, read = _setup()
    res = resolve(lib, read, "mediation_brief", matter_attorney=DANA, known_attorneys=ROSTER)
    record = json.loads(json.dumps(res.to_json()))
    assert record["status"] == RESOLVED and record["kind"] == "mediation_brief"
    assert record["file"]["sha256"] == _sha(DANA_BYTES) and record["file"]["contract"] == MARKED
    assert record["signer"] == {"key": DANA.key, "name": DANA.name, "source": "matter"}
    assert "data" not in record and DANA_BYTES.decode() not in json.dumps(record)


def test_memory_store_versions_are_newest_first_and_complete() -> None:
    lib = MemoryLibrary()
    v1 = lib.add("f", "t", "memo", b"1")
    v2 = lib.save("f", b"2")
    listing = lib.list_versions("f")
    assert [v.version for v in listing.versions] == [v2, v1] and listing.complete
    assert isinstance(listing.newest, FileVersion)
    filed = lib.file_new_version("f", b"3", expected_version=v2)
    assert filed.version_before == v2 and filed.sha256 == _sha(b"3")
    assert lib.fetch_version("f", filed.version_after) == b"3"
    assert lib.find_candidates("memo")[0].version == filed.version_after
