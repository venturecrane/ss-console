"""find_files_on_matter and stage_matter_files (ADR 0102): every refusal path."""

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


def test_stage_whole_file_spools_the_rewrite_owner_only(_spool: Path) -> None:
    c = FakeClient()
    w = PdfWriter()
    for width in (101, 102):
        w.add_blank_page(width=width, height=200)
    buf = io.BytesIO()
    w.write(buf)
    fid = c.add(1, "MRI: Report/2024", buf.getvalue())
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=STAFF)
    (row,) = out["staged"]
    assert row["attachmentName"] == "MRI Report 2024.pdf"
    assert "embeddedItemsRemoved" not in row
    path = _spool / f"{row['spoolToken']}.bin"
    data = path.read_bytes()
    # The staged bytes are the rewrite: the same pages, hashed as they go.
    assert row["sha256"] == hashlib.sha256(data).hexdigest() and row["sizeBytes"] == len(data)
    assert [int(p.mediabox.width) for p in PdfReader(io.BytesIO(data)).pages] == [101, 102]
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


# ---- the page cut never reads a large source whole ------------------------


def _big_pdf(path: Path, pages: int, stream_bytes: int) -> None:
    """A PDF whose pages each carry an uncompressed content stream of ``stream_bytes``."""
    from pypdf.generic import DecodedStreamObject, NameObject

    w = PdfWriter()
    filler = b"% " + b"x" * (stream_bytes - 3) + b"\n"
    for _ in range(pages):
        page = w.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(filler)
        page[NameObject("/Contents")] = w._add_object(stream)
    with open(path, "wb") as fh:
        w.write(fh)


def test_the_reader_is_given_a_file_handle_never_a_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector import page_cut

    src = tmp_path / "s.pdf"
    src.write_bytes(_pdf(4))
    seen: list[object] = []
    import pypdf

    real = pypdf.PdfReader

    def spy(stream, *a, **k):
        seen.append(stream)
        return real(stream, *a, **k)

    monkeypatch.setattr(pypdf, "PdfReader", spy)
    assert page_cut._child(str(src), 2, 3, str(tmp_path / "out.pdf")) == 0
    assert seen and hasattr(seen[0], "read") and not isinstance(seen[0], (str, Path))
    assert len(PdfReader(str(tmp_path / "out.pdf")).pages) == 2


def test_a_large_source_is_cut_without_loading_it_whole(tmp_path: Path) -> None:
    """80 MB source, 3 pages cut: the cutting process never holds the file whole.

    Measured as the peak of Python allocations inside that one process
    (tracemalloc), which is exactly what reading the source whole would show
    (pypdf's ``BytesIO(fh.read())`` is one 80 MB allocation). Resident-set size
    is printed for the record but not asserted: it varies by platform allocator."""
    import subprocess
    import sys

    src = tmp_path / "big.pdf"
    _big_pdf(src, pages=160, stream_bytes=512 * 1024)
    size = src.stat().st_size
    assert size > 80 * 1024 * 1024
    probe = (
        "import resource, sys, tracemalloc\n"
        "from smokeball_connector import page_cut\n"
        "tracemalloc.start()\n"
        "code = page_cut._child(*sys.argv[1:2], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])\n"
        "rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(tracemalloc.get_traced_memory()[1], rss)\n"
        "sys.exit(code)\n"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter and a constant probe script; paths built by the test
        [sys.executable, "-I", "-c", probe, str(src), "3", "5", str(tmp_path / "cut.pdf")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    peak_bytes, rss = (int(v) for v in done.stdout.strip().splitlines()[-1].split())
    assert len(PdfReader(str(tmp_path / "cut.pdf")).pages) == 3
    assert peak_bytes < size * 0.25, (peak_bytes, rss, size)


def test_a_cut_that_needs_more_memory_than_the_cap_is_a_plain_refusal(tmp_path: Path) -> None:
    import sys

    from smokeball_connector import page_cut

    if sys.platform != "linux":
        pytest.skip("RLIMIT_AS is enforced on Linux (the seat), not on macOS")
    src = tmp_path / "s.pdf"
    src.write_bytes(_pdf(3))
    with pytest.raises(page_cut.CutRefused, match="too large to cut pages"):
        page_cut.cut(src, 1, 2, tmp_path / "o.pdf", limit=16 * 1024 * 1024)


@pytest.mark.parametrize(
    ("mem_total_kb", "expected"),
    [
        (1024 * 1024, int(1024 * 1024 * 1024 * 0.40)),  # a 1 GB seat: 40%, just above the floor
        (512 * 1024, 384 * 1024 * 1024),  # 512 MB: the floor
        (8 * 1024 * 1024, 3 * 1024 * 1024 * 1024),  # A&P (8 GB): the ceiling
        (2 * 1024 * 1024, int(2 * 1024 * 1024 * 1024 * 0.40)),  # 2 GB: 40%
    ],
)
def test_the_memory_cap_is_derived_from_the_seat(tmp_path: Path, mem_total_kb: int, expected: int) -> None:
    from smokeball_connector import page_cut

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {mem_total_kb} kB\nMemFree:        1 kB\n")
    got = page_cut.memory_limit(str(meminfo))
    assert got == expected
    assert page_cut.MEMORY_FLOOR_BYTES <= got <= page_cut.MEMORY_CEILING_BYTES


def test_an_unreadable_meminfo_falls_back_to_the_floor(tmp_path: Path) -> None:
    from smokeball_connector import page_cut

    assert page_cut.memory_limit(str(tmp_path / "missing")) == page_cut.MEMORY_FLOOR_BYTES
    (tmp_path / "junk").write_text("MemTotal: lots\n")
    assert page_cut.memory_limit(str(tmp_path / "junk")) == page_cut.MEMORY_FLOOR_BYTES


def test_the_derived_cap_is_what_the_child_is_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector import page_cut

    seen: dict[str, str] = {}

    def fake_run(argv, **kw):
        seen.update(kw["env"])
        raise page_cut.subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(page_cut, "memory_limit", lambda: 123 * 1024 * 1024)
    monkeypatch.setattr(page_cut.subprocess, "run", fake_run)
    with pytest.raises(page_cut.CutRefused, match="longer than"):
        page_cut.cut(tmp_path / "s.pdf", 1, 2, tmp_path / "o.pdf")
    assert seen["SMD_PAGE_CUT_MEMORY_LIMIT"] == str(123 * 1024 * 1024)


def test_a_cut_refusal_reaches_the_staging_result(tmp_path: Path) -> None:
    c = FakeClient()
    fid = c.add(1, "A", b"%PDF-1.4 not really a pdf")
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": "1-2"}], recipients=STAFF)
    assert out["status"] == "refused" and "could not be read as a PDF" in out["refused"][0]["reason"]


# ---- the restage strips embedded files and keeps every active-content refusal --

_OPERATOR = Path(__file__).resolve().parents[3]


def _load(rel: str, name: str) -> Any:
    """The overlay's and broker's own checks, loaded from this tree in place
    (both import only the stdlib and pypdf). Absent outside a repo checkout."""
    import importlib.util

    path = _OPERATOR / rel
    if not path.is_file():
        pytest.skip(f"{rel} is not in this checkout")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    import sys

    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # a dataclass resolves its module by name while it is built
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate() -> Any:
    return _load("hermes-smd-overlay/shared/outbound_attachment.py", "_overlay_outbound_attachment")


@pytest.fixture(scope="module")
def broker() -> Any:
    return _load("workspace_broker/agentmail_attachments.py", "_broker_agentmail_attachments")


def _passes_the_reply_checks(gate: Any, broker: Any, data: bytes) -> None:
    """What the reply plugin (check_record_pdf) and the broker's reply verb run."""
    reader = gate._pdf_reader(data)
    assert len(reader.pages)
    gate._pdf_refuse_active_content(reader)
    assert b"/Encrypt" not in data and not broker.active_outside_streams(data)


def _credentialed_scan(pages: int = 3) -> bytes:
    """A scan as A&P's scanner leaves it: a C2PA "Content Credentials" item in
    the catalog's EmbeddedFiles and /AF, the same item on a page's /AF, and a
    file-attachment annotation."""
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    w.add_attachment("Content Credentials", b"c2pa manifest bytes " * 1200)
    names = w.root_object["/Names"]["/EmbeddedFiles"]["/Names"]
    filespec = names[1]
    w.root_object[NameObject("/AF")] = ArrayObject([filespec])
    page = w.pages[0]
    page[NameObject("/AF")] = ArrayObject([filespec])
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/FileAttachment"),
            NameObject("/Rect"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(10), FloatObject(10)]),
            NameObject("/FS"): filespec,
        }
    )
    page[NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_the_synthetic_scan_is_refused_as_it_comes(gate: Any) -> None:
    """The falsifier: without the restage the reply check refuses this file."""
    data = _credentialed_scan()
    with pytest.raises(gate.AttachmentError):
        gate._pdf_refuse_active_content(gate._pdf_reader(data))


@pytest.mark.parametrize("pages", [None, "1-2"])
def test_an_embedded_content_credential_is_stripped_and_the_file_passes(
    _spool: Path, gate: Any, broker: Any, pages: str | None
) -> None:
    c = FakeClient()
    fid = c.add(1, "UC Davis Discharge Summary", _credentialed_scan())
    req = {"file_id": fid, **({"pages": pages} if pages else {})}
    out = mf.stage(c, MATTER, [req], recipients=STAFF)
    assert out["status"] == "staged", out
    (row,) = out["staged"]
    assert row["embeddedItemsRemoved"] >= 1
    data = (_spool / f"{row['spoolToken']}.bin").read_bytes()
    for marker in (b"/EmbeddedFile", b"/Filespec", b"/AF", b"/FileAttachment", b"c2pa manifest"):
        assert marker not in data, marker
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == (3 if pages is None else 2)
    assert not reader.attachments
    _passes_the_reply_checks(gate, broker, data)


def _js_pdf(where: str) -> bytes:
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    w = PdfWriter()
    for _ in range(2):
        w.add_blank_page(width=200, height=200)
    w.add_attachment("Content Credentials", b"c2pa")
    action = w._add_object(
        DictionaryObject(
            {
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('x');"),
            }
        )
    )
    if where == "catalog":
        w.root_object[NameObject("/OpenAction")] = action
    else:
        w.pages[0][NameObject("/AA")] = DictionaryObject({NameObject("/O"): action})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.mark.parametrize(("where", "pages"), [("catalog", None), ("page", None), ("page", "1-1")])
def test_a_pdf_with_javascript_still_refuses(
    _spool: Path, gate: Any, broker: Any, where: str, pages: str | None
) -> None:
    """The strip removes embedded files only: the script survives the rewrite,
    and the reply's check refuses it exactly as before."""
    c = FakeClient()
    fid = c.add(1, "Scripted", _js_pdf(where))
    out = mf.stage(c, MATTER, [{"file_id": fid, **({"pages": pages} if pages else {})}], recipients=STAFF)
    (row,) = out["staged"]
    data = (_spool / f"{row['spoolToken']}.bin").read_bytes()
    assert b"/EmbeddedFile" not in data
    with pytest.raises(gate.AttachmentError, match="scripts, triggers|an action other than"):
        gate._pdf_refuse_active_content(gate._pdf_reader(data))
    assert broker.active_outside_streams(data)


def test_an_encrypted_whole_file_refuses_at_stage() -> None:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    c = FakeClient()
    fid = c.add(1, "Locked", buf.getvalue())
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=STAFF)
    assert out["status"] == "refused" and "encrypted" in out["refused"][0]["reason"]


def _object_heavy(pages: int = 4, heavy_page: int = 4) -> bytes:
    """Light pages, and one page carrying more objects than the reply check allows."""
    from pypdf.generic import ArrayObject, NameObject, NumberObject

    from smokeball_connector import page_cut

    w = PdfWriter()
    for i in range(1, pages + 1):
        page = w.add_blank_page(width=200, height=200)
        if i == heavy_page:
            page[NameObject("/SMDBulk")] = ArrayObject(NumberObject(n) for n in range(page_cut.MAX_PDF_OBJECTS + 10))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_a_whole_file_over_the_object_bound_refuses_with_the_cut_hint(gate: Any) -> None:
    data = _object_heavy()
    with pytest.raises(gate.AttachmentError, match="more objects"):  # the reply would be held for it
        gate._pdf_refuse_active_content(gate._pdf_reader(data))
    c = FakeClient()
    fid = c.add(1, "Shriners Records", data)
    out = mf.stage(c, MATTER, [{"file_id": fid}], recipients=STAFF)
    assert out["status"] == "refused" and not out["staged"]
    reason = out["refused"][0]["reason"]
    assert "50,000 PDF objects" in reason and "page range" in reason


def test_a_cut_of_the_object_heavy_file_passes(_spool: Path, gate: Any, broker: Any) -> None:
    c = FakeClient()
    fid = c.add(1, "Shriners Records", _object_heavy())
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": "1-3"}], recipients=STAFF)
    assert out["status"] == "staged", out
    (row,) = out["staged"]
    data = (_spool / f"{row['spoolToken']}.bin").read_bytes()
    assert len(PdfReader(io.BytesIO(data)).pages) == 3
    _passes_the_reply_checks(gate, broker, data)


def test_a_cut_over_the_object_bound_asks_for_fewer_pages() -> None:
    c = FakeClient()
    fid = c.add(1, "Shriners Records", _object_heavy())
    out = mf.stage(c, MATTER, [{"file_id": fid, "pages": "3-4"}], recipients=STAFF)
    assert out["status"] == "refused" and "fewer pages" in out["refused"][0]["reason"]


def test_prepare_local_is_the_shared_hook(tmp_path: Path, gate: Any, broker: Any) -> None:
    """The page finder's check calls this exact name and signature."""
    import inspect

    sig = inspect.signature(mf.prepare_local)
    assert list(sig.parameters) == ["source", "pages"]
    src = tmp_path / "s.pdf"
    src.write_bytes(_credentialed_scan())
    whole, removed = mf.prepare_local(src, None)
    assert removed >= 1 and len(PdfReader(io.BytesIO(whole)).pages) == 3
    _passes_the_reply_checks(gate, broker, whole)
    cut, _ = mf.prepare_local(src, (2, 3))
    assert len(PdfReader(io.BytesIO(cut)).pages) == 2
    src.write_bytes(_object_heavy())
    with pytest.raises(mf.StageRefused, match="page range"):
        mf.prepare_local(src, None)


def test_the_object_bound_mirrors_the_reply_check(gate: Any) -> None:
    from smokeball_connector import page_cut

    assert page_cut.MAX_PDF_OBJECTS == gate._MAX_PDF_OBJECTS
