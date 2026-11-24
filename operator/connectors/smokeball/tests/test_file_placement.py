"""Copy and move documents already on a matter into one of its folders
(file_placement.py, 2026-11-24).

An attorney asked the Operator to create a folder and place six existing
documents in it. It made the folder, left it empty, and told him it could not
move or copy a document. These tests run the REAL ``SmokeballClient`` over an
``httpx.MockTransport`` fake tenant that answers the vendor's own wire shapes:
the matter-scoped file read (400 for a file of another matter, as the "Patch a
file" contract documents), the folder tree, the two-stage upload, the presigned
download, and ``PATCH .../files/{id}`` with ``folderId`` (the move). No live
calls. Every name and id here is invented.

The falsifiers: without the verbs nothing here can pass; drop the folder check
and the cross-matter and missing-folder tests fail; drop the sha read-back and
the corrupted-copy and changed-bytes tests come back confirmed.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from smokeball_connector import file_placement, server
from smokeball_connector.client import SmokeballClient

M = "matter-aaaa"
OTHER = "matter-bbbb"
FOLDER = "folder-new1"
OLD = "folder-old1"
OTHER_FOLDER = "folder-zzz9"
S3 = "https://s3.example.invalid"


class Tenant:
    """A fake Smokeball document store speaking HTTP."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.folders = {M: [{"id": OLD, "name": "Correspondence"}, {"id": FOLDER, "name": "Mediation"}]}
        self.folders[OTHER] = [{"id": OTHER_FOLDER, "name": "Pleadings"}]
        self.requests: list[httpx.Request] = []
        self.ignore_patch = False
        self.corrupt_uploads = False
        self.change_bytes_on_move = False
        self._n = 0

    def add(self, fid: str, matter: str, name: str, ext: str, data: bytes, folder: str | None = None) -> None:
        rec: dict[str, Any] = {"id": fid, "name": name, "fileExtension": ext, "isDeleted": False}
        rec["sizeBytes"], rec["_matter"] = len(data), matter
        if folder:
            rec["folder"] = {"id": folder, "href": f"/folders/{folder}"}
        self.files[fid] = rec
        self.blobs[fid] = data

    def writes(self) -> list[tuple[str, str]]:
        writes = ("POST", "PUT", "PATCH", "DELETE")
        return [(r.method, r.url.path) for r in self.requests if r.method in writes and "/oauth2/" not in r.url.path]

    def _public(self, rec: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in rec.items() if not k.startswith("_")}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if str(request.url).startswith(S3 + "/dl/"):
            return httpx.Response(200, content=self.blobs[path.rsplit("/", 1)[1]])
        if str(request.url).startswith(S3 + "/up/"):
            fid = path.rsplit("/", 1)[1]
            data = request.content + (b"!" if self.corrupt_uploads else b"")
            self.blobs[fid] = data
            self.files[fid]["sizeBytes"] = len(request.content)  # the index lists the size sent
            self.files[fid]["isDeleted"] = False
            return httpx.Response(200)
        if m := re.fullmatch(r"/matters/([^/]+)/documents/folders", path):
            return httpx.Response(200, json={"value": [{"folders": self.folders.get(m.group(1), [])}]})
        if (m := re.fullmatch(r"/matters/([^/]+)/documents/files", path)) and method == "POST":
            return self._upload(m.group(1), json.loads(request.content))
        if m := re.fullmatch(r"/matters/([^/]+)/documents/files", path):
            rows = [self._public(r) for r in self.files.values() if r["_matter"] == m.group(1)]
            return httpx.Response(200, json={"value": rows})
        if m := re.fullmatch(r"/matters/([^/]+)/documents/files/([^/]+)(/download)?", path):
            matter, fid, download = m.group(1), m.group(2), m.group(3)
            rec = self.files.get(fid)
            if rec is None:
                return httpx.Response(404, json={"title": "Not Found"})
            if rec["_matter"] != matter:
                return httpx.Response(400, text="The file is not part of this matter.")
            if method == "PATCH":
                return self._patch(fid, json.loads(request.content))
            if download:
                body = {"downloadUrl": f"{S3}/dl/{fid}", "name": rec["name"], "fileExtension": rec["fileExtension"]}
                return httpx.Response(200, json={**body, "fileId": fid, "sizeBytes": rec["sizeBytes"]})
            return httpx.Response(200, json=self._public(rec))
        raise AssertionError(f"unscripted {method} {path}")

    def _upload(self, matter: str, body: dict[str, Any]) -> httpx.Response:
        self._n += 1
        fid = f"file-copy{self._n}"
        name, dot, ext = body["fileName"].rpartition(".")
        self.add(fid, matter, name if dot else ext, f".{ext}" if dot else "", b"", body.get("folderId"))
        self.files[fid]["isDeleted"] = True  # not readable until the bytes land
        return httpx.Response(202, json={"fileId": fid, "uploadUrl": f"{S3}/up/{fid}"})

    def _patch(self, fid: str, body: dict[str, Any]) -> httpx.Response:
        rec = self.files[fid]
        assert body["fileName"] == rec["name"] + rec["fileExtension"]  # the name goes back unchanged
        if not self.ignore_patch and "folderId" in body:
            rec["folder"] = {"id": body["folderId"], "href": f"/folders/{body['folderId']}"}
        if self.change_bytes_on_move:
            self.blobs[fid] += b"?"
        return httpx.Response(
            202, json={"fileId": fid, "uploadUrl": f"{S3}/up/{fid}", "expiry": "2026-11-24T20:00:00Z"}
        )


@pytest.fixture()
def tenant(monkeypatch: pytest.MonkeyPatch) -> Tenant:
    t = Tenant()
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(t.handle))
    monkeypatch.setattr(server, "_get_client", lambda: client)
    t.add("file-0001", M, "Demand to carrier", ".pdf", b"%PDF demand bytes", OLD)
    t.add("file-0002", M, "Treatment summary", ".docx", b"PK docx bytes")
    t.add("file-0009", OTHER, "Someone else's answer", ".pdf", b"%PDF other matter", OTHER_FOLDER)
    return t


def _tool(name: str):
    return {"copy": file_placement.copy_files_to_folder_tool, "move": file_placement.move_files_to_folder_tool}[name]


def test_both_verbs_are_on_the_mcp_surface() -> None:
    names = {t.name for t in server.server.tool_surface()}
    assert {"copy_files_to_folder", "move_files_to_folder"} <= names


# ---- copy ------------------------------------------------------------------------
def test_copy_files_each_document_into_the_folder_and_keeps_the_originals(tenant: Tenant) -> None:
    out = _tool("copy")(M, ["file-0001", "file-0002"], FOLDER)
    assert out["readback"] == "confirmed" and "status" not in out, out
    assert out["placed"] == 2 and out["folderName"] == "Mediation"
    for row in out["files"]:
        assert row["result"] == "copied" and row["readback"] == "confirmed"
        copy = tenant.files[row["newFileId"]]
        assert copy["folder"]["id"] == FOLDER and tenant.blobs[row["newFileId"]] == tenant.blobs[row["fileId"]]
        assert row["readbackSha256"] == row["sha256"]
    assert tenant.files["file-0001"]["folder"]["id"] == OLD  # the original stays put
    assert "folder" not in tenant.files["file-0002"]
    posts = [json.loads(r.content) for r in tenant.requests if r.method == "POST" and r.url.path.endswith("/files")]
    assert posts == [
        {"fileName": "Demand to carrier.pdf", "folderId": FOLDER},
        {"fileName": "Treatment summary.docx", "folderId": FOLDER},
    ]
    assert not any(r.method == "PATCH" for r in tenant.requests)


def test_a_second_copy_call_writes_nothing_new(tenant: Tenant) -> None:
    _tool("copy")(M, ["file-0001"], FOLDER)
    before = tenant.writes()
    again = _tool("copy")(M, ["file-0001"], FOLDER)
    assert again["readback"] == "confirmed" and again["files"][0]["result"] == "already_copied"
    assert tenant.writes() == before


def test_a_copy_whose_bytes_read_back_different_fails_the_call(tenant: Tenant) -> None:
    tenant.corrupt_uploads = True
    out = _tool("copy")(M, ["file-0001"], FOLDER)
    assert out["status"] == "readback_mismatch", out
    assert out["files"][0]["status"] == "readback_mismatch"
    assert "Do NOT retry" in out["readback_note"] and "—" not in out["readback_note"]


# ---- move ------------------------------------------------------------------------
def test_move_sends_one_patch_per_file_and_proves_folder_name_and_bytes(tenant: Tenant) -> None:
    out = _tool("move")(M, ["file-0001", "file-0002"], FOLDER)
    assert out["readback"] == "confirmed" and out["placed"] == 2, out
    patches = [r for r in tenant.requests if r.method == "PATCH"]
    assert [p.url.path for p in patches] == [
        f"/matters/{M}/documents/files/file-0001",
        f"/matters/{M}/documents/files/file-0002",
    ]
    assert json.loads(patches[0].content) == {"fileName": "Demand to carrier.pdf", "folderId": FOLDER}
    assert tenant.files["file-0001"]["folder"]["id"] == FOLDER and tenant.files["file-0002"]["folder"]["id"] == FOLDER
    assert out["files"][0]["fromFolderId"] == OLD and out["files"][1]["fromFolderId"] is None
    # A move sends no bytes: no upload POST and no presigned PUT.
    assert not any(r.method in ("POST", "PUT") for r in tenant.requests if not r.url.path.endswith("/token"))


def test_a_move_that_never_lands_is_link_not_visible(tenant: Tenant) -> None:
    tenant.ignore_patch = True
    out = _tool("move")(M, ["file-0001"], FOLDER)
    assert out["status"] == "link_not_visible", out
    assert out["files"][0]["result"] == "moved" and out["files"][0]["status"] == "link_not_visible"


def test_a_move_whose_bytes_change_fails_the_call(tenant: Tenant) -> None:
    tenant.change_bytes_on_move = True
    out = _tool("move")(M, ["file-0001"], FOLDER)
    assert out["status"] == "readback_mismatch"
    assert "bytes changed" in out["readback_detail"]


def test_a_file_already_in_the_folder_is_not_written(tenant: Tenant) -> None:
    out = _tool("move")(M, ["file-0001"], OLD)
    assert out["readback"] == "confirmed" and out["files"][0]["result"] == "already_in_folder"
    assert tenant.writes() == []


# ---- the matter binding: refusals send nothing -----------------------------------
@pytest.mark.parametrize("verb", ["copy", "move"])
def test_a_file_of_another_matter_refuses_the_whole_call(tenant: Tenant, verb: str) -> None:
    with pytest.raises(file_placement.PlacementRefused, match="not a document on matter"):
        _tool(verb)(M, ["file-0001", "file-0009"], FOLDER)
    assert tenant.writes() == []
    assert tenant.files["file-0001"]["folder"]["id"] == OLD


@pytest.mark.parametrize("verb", ["copy", "move"])
def test_a_folder_of_another_matter_is_refused(tenant: Tenant, verb: str) -> None:
    with pytest.raises(file_placement.PlacementRefused, match="is not a folder on matter"):
        _tool(verb)(M, ["file-0001"], OTHER_FOLDER)
    assert tenant.writes() == []


@pytest.mark.parametrize("verb", ["copy", "move"])
def test_a_folder_that_is_not_there_is_refused_with_the_way_forward(tenant: Tenant, verb: str) -> None:
    with pytest.raises(file_placement.PlacementRefused) as err:
        _tool(verb)(M, ["file-0001"], "folder-gone")
    assert "list_folders" in str(err.value) and "create_folder" in str(err.value)
    assert "Nothing was copied or moved" in str(err.value)
    assert tenant.writes() == []


def test_a_record_that_names_another_matter_is_refused(tenant: Tenant) -> None:
    tenant.files["file-0001"]["matter"] = {"id": OTHER}
    with pytest.raises(file_placement.PlacementRefused, match="belongs to another matter"):
        _tool("move")(M, ["file-0001"], FOLDER)
    assert tenant.writes() == []


def test_a_deleted_file_is_refused(tenant: Tenant) -> None:
    tenant.files["file-0002"]["isDeleted"] = True
    with pytest.raises(file_placement.PlacementRefused, match="reads deleted"):
        _tool("copy")(M, ["file-0001", "file-0002"], FOLDER)
    assert tenant.writes() == []


@pytest.mark.parametrize("bad", [[], ["../../webhooks"], "file 1"])
def test_ids_that_are_not_ids_are_refused_before_any_request(tenant: Tenant, bad: Any) -> None:
    with pytest.raises(file_placement.PlacementRefused):
        _tool("copy")(M, bad, FOLDER)
    assert tenant.requests == []
