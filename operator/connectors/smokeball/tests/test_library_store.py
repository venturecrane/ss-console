"""The Smokeball ``LibraryStore`` against a fake client that records every call.

What this case system can say about a file's versions is pinned here: the
current version only (``complete=False``), its bytes only, and never who saved
it. Earlier versions are ``NotSupportedYet``, a named outcome.
"""

from __future__ import annotations

import hashlib

import pytest

from operator_connector_sdk.library import (
    EMPTY,
    OK,
    RESOLVED,
    STYLES,
    VERSION_UNAVAILABLE,
    Entry,
    Ledger,
    LedgerRead,
    LibraryStore,
    LibraryUnavailable,
    NotSupportedYet,
    accept,
    resolve,
)
from smokeball_connector.client import SmokeballApiError, SmokeballWriteError
from smokeball_connector.library import LibraryConfig
from smokeball_connector.library_store import SmokeballLibrary, attorney_file_name

MATTER = "m-lib"
FOLDER = "fold-lib"
CFG = LibraryConfig(authored=True, matter_number="OPS-1", folder_name="Document Library", source="test")


class RecordingClient:
    """The client calls the store makes, recorded; files held by id."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.fail_meta: Exception | None = None
        self.bump_on_download = False

    def put(self, fid: str, name: str, version: str, data: bytes, folder: str | None = FOLDER, **extra) -> None:
        self.files[fid] = {
            "id": fid,
            "name": name,
            "fileExtension": ".docx",
            "versionId": version,
            "folder": {"id": folder} if folder else None,
            "isDeleted": False,
            "lastUpdated": "2026-11-01T10:00:00Z",
            **extra,
        }
        self.blobs[fid] = data

    def get(self, path: str, **params):
        self.calls.append(("get", path))
        if path == "/matters":
            return {"value": [{"id": MATTER, "number": "OPS-1"}]}
        if path == f"/matters/{MATTER}/documents/folders":
            return {"value": [{"id": FOLDER, "name": "Document Library"}]}
        if path == f"/matters/{MATTER}/documents/files":
            return {"value": list(self.files.values())}
        prefix = f"/matters/{MATTER}/documents/files/"
        if path.startswith(prefix):
            if self.fail_meta:
                raise self.fail_meta
            fid = path[len(prefix) :]
            if fid not in self.files:
                raise SmokeballApiError("GET", path, 404, "not found")
            return dict(self.files[fid])
        raise AssertionError(path)

    def download_file(self, matter_id: str, file_id: str):
        self.calls.append(("download", matter_id, file_id))
        if self.bump_on_download:
            self.files[file_id]["versionId"] += "-later"
        return {"fileId": file_id}, self.blobs[file_id]

    def add_file_version(self, matter_id, file_id, data, *, expected_version, expected_folder=None, expected_name=None):
        self.calls.append(("add_version", matter_id, file_id, expected_version))
        if self.files[file_id]["versionId"] != expected_version:
            raise SmokeballWriteError("add_file_version: its version changed; nothing sent")
        return {"fileId": file_id, "matterId": matter_id, "versionBefore": expected_version, "uploaded": True}


def _store() -> tuple[SmokeballLibrary, RecordingClient]:
    client = RecordingClient()
    client.put("f-firm", "Template - Mediation Brief", "v-firm-1", b"firm")
    client.put("f-dana", "Template - Mediation Brief - Dana Example", "v-dana-1", b"dana")
    client.put("f-memo", "Template - Memo", "v-memo-1", b"memo")
    client.put("f-root", "Template - Mediation Brief", "v-root-1", b"elsewhere", folder=None)
    return SmokeballLibrary(client, CFG, {"atty-dana": "Dana Example"}), client


def test_it_is_a_library_store() -> None:
    assert isinstance(_store()[0], LibraryStore)


def test_find_candidates_for_the_firm_reads_the_kinds_file_in_the_library_folder() -> None:
    store, client = _store()
    found = store.find_candidates("mediation_brief")
    assert [(f.file_id, f.title, f.version, f.attorney) for f in found] == [
        ("f-firm", "Template - Mediation Brief.docx", "v-firm-1", None)
    ]
    assert ("get", f"/matters/{MATTER}/documents/files") in client.calls


def test_find_candidates_for_an_attorney_reads_that_attorneys_file_by_its_label() -> None:
    store, _ = _store()
    found = store.find_candidates("mediation_brief", "atty-dana")
    assert [(f.file_id, f.attorney) for f in found] == [("f-dana", "atty-dana")]
    assert attorney_file_name("mediation_brief", "Dana Example") == "Template - Mediation Brief - Dana Example.docx"


def test_list_versions_names_the_current_version_only_and_says_so() -> None:
    store, _ = _store()
    listing = store.list_versions("f-firm")
    assert [v.version for v in listing.versions] == ["v-firm-1"]
    assert listing.complete is False
    assert listing.versions[0].saved_by is None, "this case system's history cannot say who saved a file"


def test_a_file_not_in_the_library_lists_no_versions() -> None:
    store, client = _store()
    client.files["f-memo"]["isDeleted"] = True
    assert store.list_versions("f-gone").versions == ()
    assert store.list_versions("f-memo").versions == ()


def test_fetch_of_the_current_version_downloads_and_rechecks_the_version() -> None:
    store, client = _store()
    assert store.fetch_version("f-firm", "v-firm-1") == b"firm"
    meta_reads = [c for c in client.calls if c == ("get", f"/matters/{MATTER}/documents/files/f-firm")]
    assert len(meta_reads) == 2 and ("download", MATTER, "f-firm") in client.calls


def test_fetch_of_an_earlier_version_is_not_supported_yet_and_downloads_nothing() -> None:
    store, client = _store()
    with pytest.raises(NotSupportedYet, match="current version"):
        store.fetch_version("f-firm", "v-firm-0")
    assert not [c for c in client.calls if c[0] == "download"]


def test_a_save_during_the_fetch_is_could_not_look_not_bytes() -> None:
    store, client = _store()
    client.bump_on_download = True
    with pytest.raises(LibraryUnavailable, match="saved again"):
        store.fetch_version("f-firm", "v-firm-1")


def test_a_transport_failure_is_library_unavailable_never_an_empty_answer() -> None:
    store, client = _store()
    client.fail_meta = SmokeballApiError("GET", "/x", 503, "busy")
    with pytest.raises(LibraryUnavailable):
        store.list_versions("f-firm")


def test_an_unauthored_library_is_library_unavailable() -> None:
    store = SmokeballLibrary(RecordingClient(), LibraryConfig(authored=False, source="not authored"))
    with pytest.raises(LibraryUnavailable, match="not authored"):
        store.find_candidates("memo")


def test_file_new_version_sends_with_the_expected_version_and_refuses_a_moved_file() -> None:
    store, client = _store()
    filed = store.file_new_version("f-memo", b"new", expected_version="v-memo-1")
    assert client.calls[-1] == ("add_version", MATTER, "f-memo", "v-memo-1")
    assert filed.version_before == "v-memo-1" and filed.version_after == ""
    assert filed.sha256 == hashlib.sha256(b"new").hexdigest()
    with pytest.raises(LibraryUnavailable, match="nothing sent"):
        store.file_new_version("f-memo", b"new", expected_version="v-memo-0")


def _ledger(file_id: str, version: str, data: bytes) -> LedgerRead:
    entry = Entry(
        "mediation_brief", None, file_id, version, hashlib.sha256(data).hexdigest(), STYLES, "a", "2026-11-06T17:00:00Z"
    )
    return LedgerRead(OK, accept(Ledger(), entry))


def test_the_resolver_over_smokeball_uses_the_accepted_current_version() -> None:
    store, _ = _store()
    res = resolve(store, _ledger("f-firm", "v-firm-1", b"firm"), "mediation_brief", matter_attorney=None)
    assert res.status == RESOLVED and res.data == b"firm" and res.newer is None


def test_the_resolver_over_smokeball_reports_a_later_save_it_cannot_fetch_past() -> None:
    store, client = _store()
    client.files["f-firm"]["versionId"] = "v-firm-2"
    res = resolve(store, _ledger("f-firm", "v-firm-1", b"firm"), "mediation_brief", matter_attorney=None)
    assert res.status == VERSION_UNAVAILABLE
    assert res.newer is not None and (res.newer.accepted_version, res.newer.newest_version) == ("v-firm-1", "v-firm-2")


def test_the_resolver_over_smokeball_never_uses_an_unaccepted_library_file() -> None:
    store, client = _store()
    res = resolve(store, LedgerRead(EMPTY, Ledger()), "mediation_brief", matter_attorney=None)
    assert res.status == "none_accepted"
    assert not [c for c in client.calls if c[0] == "download"]
