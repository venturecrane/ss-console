"""``SmokeballClient.add_file_version``: a new version of an existing file by
id, the method the ops workspace proved on 2026-11-04 (compare-and-stop read,
the presign GET for that file, the recorded PUT). No network: ``request`` and
the presigned PUT are stubbed. Ids are invented."""

from __future__ import annotations

from typing import Any

import pytest

from smokeball_connector.client import SmokeballClient, SmokeballWriteError

FID = "file-0001"
META = {
    "id": FID,
    "versionId": FID + "0" * 32,
    "isDeleted": False,
    "folder": {"id": "folder-0001"},
    "name": "Brief",
    "fileExtension": ".docx",
}


def _client(meta: dict[str, Any], upload: dict[str, Any] | None = None):
    c = SmokeballClient.__new__(SmokeballClient)
    calls: list[tuple[str, str]] = []
    puts: list[bytes] = []

    def request(method: str, path: str, **_: Any) -> Any:
        calls.append((method, path))
        return upload if path.endswith("/upload") else meta

    c.request = request  # type: ignore[method-assign]
    c._put_presigned = lambda url, data: puts.append(data)  # type: ignore[method-assign]
    return c, calls, puts


def _send(c: SmokeballClient, **kw: Any) -> dict[str, Any]:
    args = {"expected_version": META["versionId"], "expected_folder": "folder-0001", "expected_name": "Brief.docx"}
    return c.add_file_version("matter-0001", FID, b"new bytes", **{**args, **kw})


def test_the_new_version_goes_to_the_same_file_by_its_own_presign() -> None:
    c, calls, puts = _client(META, {"fileId": FID, "uploadUrl": "https://example.invalid/put"})
    assert _send(c)["fileId"] == FID
    assert calls == [
        ("GET", f"/matters/matter-0001/documents/files/{FID}"),
        ("GET", f"/matters/matter-0001/documents/files/{FID}/upload"),
    ]
    assert puts == [b"new bytes"]


@pytest.mark.parametrize(
    "meta, kw",
    [
        ({**META, "versionId": FID + "1" * 32}, {}),  # someone saved over it
        ({**META, "isDeleted": True}, {}),
        ({**META, "folder": {"id": "folder-0002"}}, {}),
        ({**META, "name": "Other"}, {}),
        (META, {"expected_version": ""}),
    ],
)
def test_compare_and_stop_sends_nothing(meta, kw) -> None:
    """FALSIFIER: drop a compare and the firm's own version is buried."""
    c, calls, puts = _client(meta, {"fileId": FID, "uploadUrl": "https://example.invalid/put"})
    with pytest.raises(SmokeballWriteError, match="nothing sent"):
        _send(c, **kw)
    assert puts == [] and len(calls) == 1


def test_an_upload_answer_for_another_file_sends_nothing() -> None:
    c, _calls, puts = _client(META, {"fileId": "file-0002", "uploadUrl": "https://example.invalid/put"})
    with pytest.raises(SmokeballWriteError, match="nothing sent"):
        _send(c)
    assert puts == []
