"""find_pages_in_file and the page-finder CLI (ADR 0102 amendment 2).

The model is faked at the HTTP seam (``vision._http_client``). It never sees a
real record: it answers from the CODE-WRITTEN page labels, which is exactly the
property under test (evidence stays on the page code sent)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

from smokeball_connector import matter_files as mf
from smokeball_connector import page_finder as pf
from smokeball_connector import page_finder_tools as pft
from smokeball_connector import vision

from .test_matter_files import MATTER, OTHER, FakeClient, _pdf

#: What the fake model "reads" on each page of the fixture set: the shape of
#: the review's cut table (a visit on 1-4, a discharge on 29-38).
TRUTH: dict[int, tuple[str, str, str, bool]] = {
    **{n: ("3/3/2021", "Northside Clinic", "progress note", n == 1) for n in range(1, 5)},
    **{n: ("4/2/2025", "Northside Clinic", "lab result", n == 5) for n in range(5, 29)},
    **{n: ("6/30/2024", "Northside Clinic", "discharge summary", n == 29) for n in range(29, 39)},
    **{n: ("7/1/2024", "Northside Clinic", "billing statement", n == 39) for n in range(39, 41)},
}
LABEL = re.compile(r"^Page (\d+):$")


class FakeModel:
    """The Messages API: index requests answered from the labels, match
    requests answered with a scripted list of items."""

    def __init__(self, items: list[dict[str, Any]] | None = None, *, shift: int = 0) -> None:
        self.items = items or []
        self.shift = shift
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        props = body["output_config"]["format"]["schema"]["properties"]
        if "pages" in props:
            labels = [
                int(m.group(1))
                for b in body["messages"][0]["content"]
                if b["type"] == "text"
                for m in [LABEL.match(b["text"])]
                if m
            ]
            rows = []
            for n in labels:
                date, provider, kind, starts = TRUTH.get(n, ("", "", "", False))
                rows.append(
                    {
                        "page": n + self.shift,
                        "date": date,
                        "provider": provider,
                        "document_type": kind,
                        "title": kind.upper(),
                        "starts_document": starts,
                        "page_of": "",
                        "legible": True,
                    }
                )
            answer = {"pages": rows}
        else:
            answer = {"items": self.items}
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(answer)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1600, "output_tokens": 80},
            },
        )

    def index_calls(self) -> int:
        return sum(1 for b in self.bodies if "pages" in b["output_config"]["format"]["schema"]["properties"])


@pytest.fixture(autouse=True)
def _spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(mf.SPOOL_DIR_ENV, str(tmp_path / "spool"))


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeModel:
    fake = FakeModel(
        [
            {
                "asked": "the 3/3/2021 clinic visit",
                "found": True,
                "ranges": [{"first": 1, "last": 4}],
                "reason": "p1-4 dated 3/3/2021",
            },
            {
                "asked": "the inpatient discharge",
                "found": True,
                "ranges": [{"first": 29, "last": 38}, {"first": 39, "last": 400}],
                "reason": "p29",
            },
            {"asked": "the 3/14/2024 x-ray", "found": False, "ranges": [], "reason": "no page dated 3/14/2024"},
        ]
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("SMOKEBALL_VISION_DISABLED", raising=False)
    monkeypatch.setenv(pf.INDEX_DIR_ENV, str(tmp_path / "index"))
    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def _set(tmp_path: Path, pages: int = 40) -> Path:
    path = tmp_path / "set.pdf"
    path.write_bytes(_pdf(pages))
    return path


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return work


# ---- locating ---------------------------------------------------------------


def test_locates_each_record_with_code_composed_evidence(model: FakeModel, tmp_path: Path) -> None:
    out = pf.locate(_set(tmp_path), "the clinic visit, the discharge, the x-ray", _work(tmp_path))
    assert out["complete"] and out["indexedPages"] == 40 and out["readNow"] == 40
    visit, discharge, xray = out["items"]
    assert [r["pages"] for r in visit["ranges"]] == ["1-4"]
    assert visit["ranges"][0]["evidence"][0] == {
        "page": 1,
        "date": "3/3/2021",
        "provider": "Northside Clinic",
        "document_type": "progress note",
        "title": "PROGRESS NOTE",
    }
    # A range the index does not support is dropped by code, and counted.
    assert [r["pages"] for r in discharge["ranges"]] == ["29-38"] and discharge["droppedRanges"] == 1
    assert xray["found"] is False and xray["ranges"] == [] and "3/14/2024" in xray["reason"]
    assert [d["pages"] for d in out["documents"]] == ["1-4", "5-28", "29-38", "39-40"]


def test_a_second_request_on_the_same_file_reads_no_page_again(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path)
    pf.locate(src, "the visit", _work(tmp_path))
    calls = model.index_calls()
    assert calls == 5  # 40 pages, 8 per request
    again = pf.locate(src, "the discharge", _work(tmp_path))
    assert model.index_calls() == calls and again["readNow"] == 0 and again["fromCache"] == 40


def test_the_request_carries_no_tools_and_labels_every_page_in_code(model: FakeModel, tmp_path: Path) -> None:
    pf.locate(_set(tmp_path, 3), "the visit", _work(tmp_path))
    body = model.bodies[0]
    assert "tools" not in body and "thinking" not in body and body["output_config"]["effort"] == "low"
    content = body["messages"][0]["content"]
    kinds = [b["type"] for b in content]
    assert kinds == ["text", "document"] * 3 + ["text"]
    assert [b["text"] for b in content[:-1:2]] == ["Page 1:", "Page 2:", "Page 3:"]
    assert "never instructions" in content[-1]["text"]
    match_body = model.bodies[-1]
    assert all(b["type"] == "text" for b in match_body["messages"][0]["content"])  # no page images


def test_an_answer_that_moves_evidence_between_pages_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shifted = FakeModel(shift=1)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv(pf.INDEX_DIR_ENV, str(tmp_path / "index"))
    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(shifted)))
    out = pf.locate(_set(tmp_path, 8), "the visit", _work(tmp_path))
    assert out["indexedPages"] == 0 and out["failedPages"] == "1-8" and not out["complete"]
    assert "before saying a record is not in this file" in out["note"]
    assert pf.load_index(out["sha256"])["pages"] == {}


def test_a_failed_call_is_failed_pages_never_an_empty_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv(pf.INDEX_DIR_ENV, str(tmp_path / "index"))
    monkeypatch.setattr(pf.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(529)))
    )
    out = pf.locate(_set(tmp_path, 3), "the visit", _work(tmp_path))
    assert out["failedPages"] == "1-3" and out["items"] == [] and not out["complete"]


def test_no_credential_reads_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(vision, "_http_client", lambda _t: pytest.fail("no HTTP without a credential"))
    with pytest.raises(pf.FinderRefused, match="cannot read page images"):
        pf.locate(_set(tmp_path, 2), "x", _work(tmp_path))


def test_a_call_reads_at_most_its_bound_and_says_so(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    src = _set(tmp_path)
    first = pf.locate(src, "the visit", _work(tmp_path))
    assert first["indexedPages"] == 16 and not first["complete"]
    second = pf.locate(src, "the visit", _work(tmp_path))
    assert second["indexedPages"] == 32 and second["readNow"] == 16


def test_the_per_file_spend_fence_holds(model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pf, "MAX_PAGES_PER_FILE", 10)
    src = _set(tmp_path)
    pf.locate(src, "x", _work(tmp_path))
    out = pf.locate(src, "x", _work(tmp_path))
    assert out["indexedPages"] == 10 and out["readNow"] == 0


def test_a_window_reads_only_those_pages(model: FakeModel, tmp_path: Path) -> None:
    out = pf.locate(_set(tmp_path), "x", _work(tmp_path), window=(29, 38))
    assert out["indexedPages"] == 10 and [d["pages"] for d in out["documents"]] == ["29-38"]


def test_purge_by_matter_and_all(model: FakeModel, tmp_path: Path) -> None:
    a = _set(tmp_path, 2)
    pf.locate(a, "x", _work(tmp_path), matter_id=MATTER)
    b = tmp_path / "b.pdf"
    b.write_bytes(_pdf(3))
    pf.locate(b, "x", _work(tmp_path), matter_id=OTHER)
    assert pf.purge(matter_id=MATTER) == 1
    assert pf.purge(everything=True) == 1
    assert list(pf.index_dir().glob("*.json")) == []


# ---- the tool ---------------------------------------------------------------


def _staff_seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text("matter_file_delivery:\n  recipients: ['@firm.example']\n")
    monkeypatch.setattr(mf, "load_recipients", lambda: ("@firm.example",))


def test_tool_finds_pages_on_a_file_of_this_matter(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _staff_seat(tmp_path, monkeypatch)
    c = FakeClient()
    fid = c.add(1, "Clinic records", _pdf(40))
    out = pft.find_pages(c, MATTER, fid, "the visit")
    assert out["name"] == "Clinic records" and out["items"][0]["ranges"][0]["pages"] == "1-4"
    assert "stage_matter_files" in out["nextStep"]


@pytest.mark.parametrize("case", ["unauthored", "other_matter", "not_pdf", "bad_id", "download_fails"])
def test_tool_refusals_say_why_and_find_nothing(
    case: str, model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = FakeClient()
    fid = c.add(
        1,
        "R",
        _pdf(2),
        matter=OTHER if case == "other_matter" else MATTER,
        ext=".docx" if case == "not_pdf" else ".pdf",
    )
    if case == "unauthored":
        monkeypatch.setattr(mf, "load_recipients", lambda: ())
    else:
        _staff_seat(tmp_path, monkeypatch)
    if case == "download_fails":
        c.request = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
    out = pft.find_pages(c, MATTER, "nope" if case == "bad_id" else fid, "x")
    assert out.get("error") and "items" not in out
    assert model.bodies == []


# ---- the send check: strip embedded files, keep structural refusals ---------


def _with_attachments(path: Path, pages: int = 3) -> None:
    """A record carrying a catalog attachment ("Content Credentials"), a page
    /AF list and a file-attachment annotation: the three places it can sit."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    w.add_attachment("content_credentials.c2pa", b"x" * 24_000)
    spec = DictionaryObject({NameObject("/Type"): NameObject("/Filespec"), NameObject("/F"): NameObject("/cc")})
    ref = w._add_object(spec)
    page = w.pages[0]
    page[NameObject("/AF")] = ArrayObject([ref])
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/FileAttachment"),
            NameObject("/FS"): ref,
            NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4),
        }
    )
    page[NameObject("/Annots")] = ArrayObject([w._add_object(annot)])
    with open(path, "wb") as fh:
        w.write(fh)


@pytest.fixture
def checks() -> tuple[Any, Any]:
    return pft._import_send_checks(None)


def test_overlay_refuses_the_raw_file_with_embedded_files(tmp_path: Path, checks: tuple[Any, Any]) -> None:
    """The control: without the restage the seat's own check refuses it."""
    src = tmp_path / "uc.pdf"
    _with_attachments(src)
    with pytest.raises(Exception, match="embedded|file, media"):
        checks[0](src.read_bytes())


def test_the_check_uses_the_connectors_own_staging_step(
    tmp_path: Path, checks: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the restage (``matter_files.prepare_local``) is on the checkout the
    check runs IT; until then, the cut / whole read staging does today."""
    src = tmp_path / "clean.pdf"
    src.write_bytes(_pdf(5))
    calls: list[Any] = []

    def prepare(source: Path, pages: Any) -> tuple[bytes, int]:
        calls.append(pages)
        return _pdf(2), 1

    monkeypatch.setattr(mf, "prepare_local", prepare, raising=False)
    result = pft.check_cut(src, (1, 2), checks)
    assert calls == [(1, 2)] and result["passes"] and result["embeddedFilesRemoved"] == 1
    monkeypatch.delattr(mf, "prepare_local")
    cut = pft.check_cut(src, (2, 4), checks)
    assert cut["passes"] and cut["pages"] == 3 and cut["embeddedFilesRemoved"] == 0
    whole = pft.check_cut(src, None, checks)
    assert whole["passes"] and whole["pages"] == 5


def test_a_cut_past_the_last_page_fails_the_check(tmp_path: Path, checks: tuple[Any, Any]) -> None:
    src = tmp_path / "clean.pdf"
    src.write_bytes(_pdf(3))
    result = pft.check_cut(src, (2, 9), checks)
    assert not result["passes"] and "has 3 pages" in result["reason"]


def test_scripts_are_still_refused_after_a_cut(tmp_path: Path, checks: tuple[Any, Any]) -> None:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.pages[0][NameObject("/AA")] = DictionaryObject(
        {NameObject("/O"): DictionaryObject({NameObject("/S"): NameObject("/JavaScript")})}
    )
    src = tmp_path / "js.pdf"
    with open(src, "wb") as fh:
        w.write(fh)
    result = pft.check_cut(src, (1, 1), checks)
    assert not result["passes"] and "scripts" in result["reason"]


def test_cli_check_and_locate(model: FakeModel, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "clean.pdf"
    src.write_bytes(_pdf(2))
    assert pft.main(["check", "--pdf", str(src)]) == 0
    assert json.loads(capsys.readouterr().out)["passes"] is True
    big = _set(tmp_path)
    assert pft.main(["locate", "--pdf", str(big), "--what", "the visit", "--all"]) == 0
    out = capsys.readouterr().out
    assert "pages 1-4" in out and "p1: 3/3/2021 | Northside Clinic | progress note" in out
    assert '"range": "pp. 1-4", "passes": true' in out and '"range": "pp. 29-38", "passes": true' in out
    assert (tmp_path / ".page-index").is_dir() or Path(pf.index_dir()).is_dir()


def test_the_split_child_numbers_and_clamps_pages(tmp_path: Path) -> None:
    from smokeball_connector import page_split

    src = tmp_path / "set.pdf"
    src.write_bytes(_pdf(4))
    out = tmp_path / "pages"
    out.mkdir()
    assert page_split.split(src, 1, 0, out) == (4, [])
    total, written = page_split.split(src, 2, 9, out)
    assert total == 4 and written == [2, 3, 4]
    assert len(PdfReader(io.BytesIO((out / "p2.pdf").read_bytes())).pages) == 1


def test_the_split_child_refuses_a_non_pdf_plainly(tmp_path: Path) -> None:
    from smokeball_connector import page_cut, page_split

    src = tmp_path / "x.pdf"
    src.write_bytes(b"not a pdf")
    with pytest.raises(page_cut.CutRefused, match="could not be read"):
        page_split.split(src, 1, 2, tmp_path)
