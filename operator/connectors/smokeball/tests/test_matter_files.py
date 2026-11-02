"""find_files_on_matter and stage_matter_files (ADR 0101): every refusal path."""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter

from smokeball_connector import matter_files as mf

MATTER = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


def _pdf(pages: int, *, width: int = 200) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=200)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _fid(n: int) -> str:
    return f"{n:08d}-aaaa-bbbb-cccc-dddddddddddd"


class _Resp:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self.status_code = status
        self._data = data

    def iter_bytes(self):
        for i in range(0, len(self._data), 1000):
            yield self._data[i : i + 1000]


class _Http:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    @contextmanager
    def stream(self, _method: str, url: str):
        yield _Resp(self.blobs[url])


class FakeClient:
    """Listing pages, a folder tree, file metadata and presigned downloads."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.meta: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.fail_listing_at: int | None = None
        self.fail_folders = False
        self._http = _Http(self.blobs)

    def add(self, n: int, name: str, data: bytes, *, ext: str = ".pdf", folder: str | None = None, matter=MATTER):
        fid = _fid(n)
        row = {
            "id": fid,
            "name": name,
            "fileExtension": ext,
            "sizeBytes": len(data),
            "dateModified": f"2026-01-{n % 28 + 1:02d}",
        }
        if folder:
            row["folder"] = {"id": folder, "href": f"/matters/{MATTER}/documents/folders/{folder}"}
        self.rows.append(row)
        self.meta[fid] = {**row, "matter": {"href": f"/matters/{matter}"}}
        self.blobs[f"https://s3/{fid}"] = data
        return fid

    def get(self, path: str, **params: Any) -> Any:
        if path.endswith("/documents/files"):
            if self.fail_listing_at is not None and params["Offset"] >= self.fail_listing_at:
                raise RuntimeError("429")
            off = params["Offset"]
            return {"value": self.rows[off : off + params["Limit"]]}
        if path.endswith("/documents/folders"):
            if self.fail_folders:
                raise RuntimeError("500")
            return {
                "value": [
                    {
                        "folders": [
                            {"id": "f-med", "name": "Medical", "folders": [{"id": "f-rec", "name": "Records"}]},
                            {"id": "f-corr", "name": "Correspondence"},
                        ]
                    }
                ]
            }
        fid = path.rsplit("/", 1)[-1]
        return self.meta[fid]

    def request(self, method: str, path: str, **_: Any) -> Any:
        fid = path.split("/files/")[1].split("/")[0]
        return {"downloadUrl": f"https://s3/{fid}"}


@pytest.fixture(autouse=True)
def _spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(mf.SPOOL_DIR_ENV, str(tmp_path / "spool"))
    return tmp_path / "spool"


STAFF = ("@firm.example",)


# ---- find -------------------------------------------------------------------


def test_find_matches_every_word_in_any_order_across_pages() -> None:
    c = FakeClient()
    for n in range(1, 1201):
        c.add(n, f"misc {n}", b"x")
    c.add(1300, "2024-03 MRI Report - Banner", b"x", folder="f-rec")
    out = mf.find_files(c, MATTER, name="mri banner report")
    assert out["searchComplete"] is True and out["searchedFiles"] == 1201
    assert [f["name"] for f in out["files"]] == ["2024-03 MRI Report - Banner"]
    assert out["files"][0]["folder"] == "Medical/Records"


def test_find_by_folder_name_and_path_suffix_and_extension() -> None:
    c = FakeClient()
    c.add(1, "a", b"x", folder="f-rec")
    c.add(2, "b", b"x", folder="f-corr")
    c.add(3, "c", b"x", ext=".docx", folder="f-rec")
    assert {f["name"] for f in mf.find_files(c, MATTER, folder="records")["files"]} == {"a", "c"}
    assert {f["name"] for f in mf.find_files(c, MATTER, folder="Medical/Records", extension="pdf")["files"]} == {"a"}
    assert mf.find_files(c, MATTER, folder="Medical/Correspondence")["files"] == []


def test_find_says_partial_past_the_page_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mf, "_MAX_PAGES", 2)
    c = FakeClient()
    for n in range(1, 1001):
        c.add(n, "same", b"x")
    out = mf.find_files(c, MATTER, name="same")
    assert out["searchComplete"] is False and "may still be on the matter" in out["note"]


def test_find_a_failed_page_is_an_error_never_none_found() -> None:
    c = FakeClient()
    for n in range(1, 700):
        c.add(n, "x", b"x")
    c.fail_listing_at = 500
    out = mf.find_files(c, MATTER, name="x")
    assert "error" in out and "files" not in out


def test_find_folder_filter_with_unreadable_folders_is_an_error() -> None:
    c = FakeClient()
    c.fail_folders = True
    assert "error" in mf.find_files(c, MATTER, folder="Medical")


@pytest.mark.parametrize("matter", ["2026-PI-104", "", None])
def test_find_refuses_a_non_uuid_matter(matter: Any) -> None:
    assert "error" in mf.find_files(FakeClient(), matter, name="x")


def test_find_needs_something_to_look_for() -> None:
    assert "error" in mf.find_files(FakeClient(), MATTER)


def test_find_caps_and_says_so() -> None:
    c = FakeClient()
    for n in range(1, 40):
        c.add(n, "record", b"x")
    out = mf.find_files(c, MATTER, name="record", limit=5)
    assert out["returned"] == 5 and out["matchCount"] == 39 and "Narrow" in out["note"]


# ---- stage: refusals --------------------------------------------------------


def test_stage_unauthored_stages_nothing(_spool: Path) -> None:
    c = FakeClient()
    fid = c.add(1, "Records", _pdf(2))
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=())
    assert out["status"] == "refused" and "not authored" in out["reason"]
    assert not _spool.exists() or not list(_spool.iterdir())


def test_stage_reads_recipients_from_the_seat_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text("matter_file_delivery:\n  recipients: ['@Firm.example', 'not-an-address']\n")
    monkeypatch.setenv(mf.CUSTOMER_YAML_ENV, str(cfg))
    assert mf.load_recipients() == ("@firm.example",)
    cfg.write_text("matter_file_delivery: {}\n")
    assert mf.load_recipients() == ()


def test_stage_refuses_a_file_whose_record_names_another_matter() -> None:
    c = FakeClient()
    fid = c.add(1, "Records", _pdf(1), matter=OTHER)
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=STAFF)
    assert out["status"] == "refused" and "does not name this matter" in out["refused"][0]["reason"]


def test_stage_refuses_a_record_that_names_no_matter() -> None:
    c = FakeClient()
    fid = c.add(1, "Records", _pdf(1))
    del c.meta[fid]["matter"]
    assert "does not name this matter" in mf.stage(c, MATTER, [fid], recipients=STAFF)["refused"][0]["reason"]


def test_stage_refuses_non_pdf_by_extension_and_by_bytes() -> None:
    c = FakeClient()
    a = c.add(1, "Letter", b"PK\x03\x04", ext=".docx")
    b = c.add(2, "Fake", b"not a pdf at all")
    out = mf.stage(c, MATTER, [a, b], recipients=STAFF)
    reasons = [r["reason"] for r in out["refused"]]
    assert "only PDF" in reasons[0] and "not a PDF" in reasons[1]


def test_stage_refuses_an_oversize_whole_file_and_points_at_a_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mf, "MAX_FILE_BYTES", 2000)
    c = FakeClient()
    fid = c.add(1, "Big", _pdf(30))
    reason = mf.stage(c, MATTER, [fid], recipients=STAFF)["refused"][0]["reason"]
    assert "too large to fetch" in reason or "page range" in reason


def test_stage_refuses_past_the_total_but_keeps_what_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _pdf(1)
    monkeypatch.setattr(mf, "MAX_TOTAL_BYTES", len(data) + 10)
    c = FakeClient()
    a, b = c.add(1, "A", data), c.add(2, "B", _pdf(1, width=210))
    out = mf.stage(c, MATTER, [a, b], recipients=STAFF)
    assert [s["fileId"] for s in out["staged"]] == [a]
    assert "over the" in out["refused"][0]["reason"]


@pytest.mark.parametrize("pages", ["0-2", "5-3", "x", "1-500"])
def test_stage_refuses_a_bad_range(pages: str) -> None:
    c = FakeClient()
    fid = c.add(1, "A", _pdf(3))
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": pages}], recipients=STAFF)
    assert out["status"] == "refused"


def test_stage_refuses_a_range_past_the_last_page() -> None:
    c = FakeClient()
    fid = c.add(1, "A", _pdf(3))
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": "2-4"}], recipients=STAFF)
    assert "has 3 pages" in out["refused"][0]["reason"]


@pytest.mark.parametrize(
    "files",
    [
        [],
        "abc",
        [{"file_id": "../../etc/passwd"}],
        [{"file_id": _fid(1)}] * 2,
        [{"file_id": _fid(n)} for n in range(10)],
    ],
)
def test_stage_refuses_malformed_requests(files: Any) -> None:
    assert mf.stage(FakeClient(), MATTER, files, recipients=STAFF)["status"] == "refused"


def test_stage_one_files_fault_never_stops_the_rest() -> None:
    c = FakeClient()
    good = c.add(1, "Good", _pdf(1))
    bad = _fid(2)
    c.meta[bad] = {"matter": {"href": f"/matters/{MATTER}"}, "fileExtension": ".pdf"}
    out = mf.stage(c, MATTER, [bad, good], recipients=STAFF)
    assert [s["fileId"] for s in out["staged"]] == [good]
    assert out["refused"][0]["fileId"] == bad


# ---- stage: what goes -------------------------------------------------------


def test_stage_whole_file_spools_exact_bytes_owner_only(_spool: Path) -> None:
    c = FakeClient()
    data = _pdf(2)
    fid = c.add(1, "MRI: Report/2024", data)
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=STAFF)
    (row,) = out["staged"]
    assert row["attachmentName"] == "MRI Report 2024.pdf"
    path = _spool / f"{row['spoolToken']}.bin"
    assert path.read_bytes() == data and row["sha256"] == hashlib.sha256(data).hexdigest()
    assert path.stat().st_mode & 0o077 == 0
    meta = json.loads((_spool / f"{row['spoolToken']}.json").read_text())
    assert meta["matter_id"] == MATTER and meta["file_id"] == fid


def test_stage_cuts_exactly_the_named_pages() -> None:
    c = FakeClient()
    w = PdfWriter()
    for width in range(100, 110):
        w.add_blank_page(width=width, height=200)
    buf = io.BytesIO()
    w.write(buf)
    fid = c.add(1, "Discovery Set", buf.getvalue())
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": "3-5"}], recipients=STAFF)
    (row,) = out["staged"]
    assert row["attachmentName"] == "Discovery Set pp. 3-5.pdf"
    cut = PdfReader(io.BytesIO((Path(mf.spool_dir()) / f"{row['spoolToken']}.bin").read_bytes()))
    assert [int(p.mediabox.width) for p in cut.pages] == [102, 103, 104]


def test_attachment_names_are_plain_and_unique() -> None:
    taken: set[str] = set()
    assert mf.attachment_name("a.pdf", None, taken) == "a.pdf"
    assert mf.attachment_name("A", None, taken) == "A (2).pdf"
    assert mf.attachment_name("", (4, 4), taken) == "document pp. 4.pdf"
    assert len(mf.attachment_name("x" * 400, None, set())) <= 120
