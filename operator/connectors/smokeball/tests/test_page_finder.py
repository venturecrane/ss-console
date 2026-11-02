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
#: The checkout's operator/ directory: CI installs the connector as a wheel, so
#: the CLI cannot find the overlay and broker checks from its own path there.
REPO_OPERATOR = Path(__file__).resolve().parents[3]


class FakeModel:
    """The Messages API: index requests answered from the labels, match
    requests answered with a scripted list of items."""

    def __init__(
        self, items: list[dict[str, Any]] | None = None, *, shift: int = 0, refuse: frozenset[int] = frozenset()
    ) -> None:
        self.refuse = refuse
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
                section = {"heading": kind.upper(), "document_type": kind, "date": date, "provider": provider}
                rows.append(
                    {
                        "page": n + self.shift,
                        "continues_previous": not starts,
                        "sections": [section] if starts else [],
                        "dates": [f"DOS {date}"] if date else [],
                        "running_header": "",
                        "page_of": "",
                        "legible": True,
                    }
                )
            answer = {"pages": rows}
            if self.refuse & set(labels):
                return httpx.Response(
                    200,
                    json={"content": [], "stop_reason": "refusal", "usage": {"input_tokens": 900, "output_tokens": 0}},
                )
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
                "sections": ["S1"],
                "reason": "S1 dated 3/3/2021",
            },
            {
                "asked": "the inpatient discharge",
                "found": True,
                "sections": ["S3", "S99"],
                "reason": "S3",
            },
            {"asked": "the 3/14/2024 x-ray", "found": False, "sections": [], "reason": "no page dated 3/14/2024"},
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
        "section": "S1",
        "pages": "1-4",
        "heading": "PROGRESS NOTE",
        "document_type": "progress note",
        "date": "3/3/2021",
        "provider": "Northside Clinic",
        "dates": ["DOS 3/3/2021"],
    }
    # A section id the index does not hold is dropped by code, and counted.
    assert [r["pages"] for r in discharge["ranges"]] == ["29-38"] and discharge["droppedSections"] == 1
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
    assert (
        out["indexedPages"] == 0
        and [r["page"] for r in out["failedPages"]] == list(range(1, 9))
        and not out["complete"]
    )
    assert "Do not say a record is absent" in out["note"]
    assert pf.load_index(out["sha256"])["pages"] == {}


def test_a_failed_call_is_failed_pages_never_an_empty_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv(pf.INDEX_DIR_ENV, str(tmp_path / "index"))
    monkeypatch.setattr(pf.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(529)))
    )
    out = pf.locate(_set(tmp_path, 3), "the visit", _work(tmp_path))
    assert [r["page"] for r in out["failedPages"]] == [1, 2, 3] and "529" in out["failedPages"][0]["reason"]
    assert out["items"] == [] and not out["complete"]


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
    return pft._import_send_checks(REPO_OPERATOR)


def test_overlay_refuses_the_raw_file_with_embedded_files(tmp_path: Path, checks: tuple[Any, Any]) -> None:
    """The control: without the restage the seat's own check refuses it."""
    src = tmp_path / "uc.pdf"
    _with_attachments(src)
    with pytest.raises(Exception, match="embedded|file, media"):
        checks[0](src.read_bytes())


def test_the_check_uses_the_connectors_own_staging_step(
    tmp_path: Path, checks: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check runs the connector's own staging step (``prepare_local``),
    then the real one: a cut, a whole file, and a record with embedded files."""
    src = tmp_path / "clean.pdf"
    src.write_bytes(_pdf(5))
    calls: list[Any] = []

    def prepare(source: Path, pages: Any) -> tuple[bytes, int]:
        calls.append(pages)
        return _pdf(2), 1

    original = mf.prepare_local
    monkeypatch.setattr(mf, "prepare_local", prepare)
    result = pft.check_cut(src, (1, 2), checks)
    assert calls == [(1, 2)] and result["passes"] and result["embeddedFilesRemoved"] == 1
    monkeypatch.setattr(mf, "prepare_local", original)
    cut = pft.check_cut(src, (2, 4), checks)
    assert cut["passes"] and cut["pages"] == 3 and cut["embeddedFilesRemoved"] == 0
    whole = pft.check_cut(src, None, checks)
    assert whole["passes"] and whole["pages"] == 5
    stamped = tmp_path / "stamped.pdf"
    _with_attachments(stamped)
    restaged = pft.check_cut(stamped, None, checks)
    assert restaged["passes"] and restaged["embeddedFilesRemoved"] >= 1, restaged


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
    assert pft.main(["check", "--pdf", str(src), "--repo", str(REPO_OPERATOR)]) == 0
    assert json.loads(capsys.readouterr().out)["passes"] is True
    big = _set(tmp_path)
    assert pft.main(["locate", "--pdf", str(big), "--what", "the visit", "--all", "--repo", str(REPO_OPERATOR)]) == 0
    out = capsys.readouterr().out
    assert "pages 1-4" in out and "S1 pp. 1-4: 3/3/2021 | Northside Clinic | progress note | PROGRESS NOTE" in out
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
    assert len(PdfReader(io.BytesIO((out / "p2.pdf").read_bytes())).pages) == 1  # whole page, the default


def test_the_split_child_refuses_a_non_pdf_plainly(tmp_path: Path) -> None:
    from smokeball_connector import page_cut, page_split

    src = tmp_path / "x.pdf"
    src.write_bytes(b"not a pdf")
    with pytest.raises(page_cut.CutRefused, match="could not be read"):
        page_split.split(src, 1, 2, tmp_path)


# ---- the section boundaries (the workspace's answer-key traps) --------------


def _page(*starts: tuple[str, str, str, str], cont: bool = False, dates: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "continues_previous": cont,
        "sections": [dict(zip(("heading", "document_type", "date", "provider"), st)) for st in starts],
        "dates": list(dates),
        "running_header": "",
        "page_of": "",
        "legible": True,
    }


def _spans(pages: dict[str, dict[str, Any]]) -> list[tuple[str, int, int]]:
    return [(s["heading"], s["first"], s["last"]) for s in pf.sections(pages)]


VISIT = ("OFFICE VISIT", "office visit note", "3/3/2021", "Clinic A")


def test_a_section_starting_mid_page_shares_that_page() -> None:
    pages = {
        "1": _page(VISIT),
        "2": _page(cont=True),
        "3": _page(("SPEECH NOTE", "speech therapy note", "4/4/2021", "Clinic A"), cont=True),
        "4": _page(cont=True),
    }
    assert _spans(pages) == [("OFFICE VISIT", 1, 3), ("SPEECH NOTE", 3, 4)]


def test_a_section_without_a_continued_top_ends_the_page_before() -> None:
    pages = {"1": _page(VISIT), "2": _page(cont=True), "3": _page(("NEXT", "letter", "", ""))}
    assert _spans(pages) == [("OFFICE VISIT", 1, 2), ("NEXT", 3, 3)]


def test_continued_pages_and_embedded_imaging_stay_in_their_note() -> None:
    """A long note with a "(continued)" running header through imaging pages:
    the model reports no new section on them, so the note runs on."""
    pages = {
        "1": _page(VISIT),
        **{str(n): _page(cont=True) for n in range(2, 9)},
        "9": _page(("LAB", "lab result", "", "")),
    }
    assert _spans(pages) == [("OFFICE VISIT", 1, 8), ("LAB", 9, 9)]


def test_a_canceled_appointment_and_the_visit_on_one_date_are_two_sections() -> None:
    canceled = ("APPOINTMENT CANCELED", "canceled appointment", "3/3/2021", "Clinic A")
    pages = {"1": _page(canceled, VISIT), "2": _page(cont=True), "3": _page(cont=True)}
    secs = pf.sections(pages)
    assert [(s["document_type"], s["first"], s["last"]) for s in secs] == [
        ("canceled appointment", 1, 1),
        ("office visit note", 1, 3),
    ]


def test_a_date_printed_only_on_a_late_page_belongs_to_the_section() -> None:
    report = ("EXPERT REPORT", "expert report", "", "Dr. Example")
    pages = {"1": _page(report), **{str(n): _page(cont=True) for n in range(2, 32)}}
    pages["32"] = _page(cont=True, dates=("Signed 04/01/2023",))
    (sec,) = pf.sections(pages)
    assert (sec["first"], sec["last"]) == (1, 32) and sec["dates"] == ["Signed 04/01/2023"]


def test_a_continuation_after_an_unindexed_gap_is_never_attached_to_an_earlier_section() -> None:
    pages = {"1": _page(VISIT), "5": _page(cont=True)}
    assert _spans(pages) == [("OFFICE VISIT", 1, 1), ("(untitled: starts before the indexed pages)", 5, 5)]


def test_the_match_gets_pdf_positions_and_the_trap_rules(model: FakeModel, tmp_path: Path) -> None:
    pf.locate(_set(tmp_path, 8), "the visit", _work(tmp_path))
    text = " ".join(b["text"] for b in model.bodies[-1]["messages"][0]["content"])
    assert "S1 | pp. 1-4 |" in text
    for rule in (
        "never substitute another",
        "never a canceled appointment",
        "printed only on a later page",
        "Page numbers printed on the paper are not PDF positions",
    ):
        assert rule in text
    index_text = model.bodies[0]["messages"][0]["content"][-1]["text"]
    assert "never a page number printed on the page" in index_text and 'is NOT a "' not in index_text
    assert "continued" in index_text and "canceled appointment" in index_text


def test_found_false_or_unknown_sections_give_no_pages(model: FakeModel, tmp_path: Path) -> None:
    model.items = [
        {"asked": "Clinic B discharge", "found": False, "sections": ["S3"], "reason": "Clinic B has none"},
        {"asked": "x", "found": True, "sections": ["S77"], "reason": "?"},
    ]
    out = pf.locate(_set(tmp_path), "Clinic B discharge", _work(tmp_path))
    assert [i["ranges"] for i in out["items"]] == [[], []]
    assert [i["found"] for i in out["items"]] == [False, False]


# ---- refusals, strips, the whole file, the spend tally ---------------------


def _refusing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refuse: set[int]) -> FakeModel:
    fake = FakeModel([{"asked": "x", "found": True, "sections": ["S1"], "reason": "S1"}], refuse=frozenset(refuse))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv(pf.INDEX_DIR_ENV, str(tmp_path / "index"))
    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def test_a_refused_page_is_split_out_and_never_sinks_its_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One page the model declines: the batch of 8 is retried page by page, the
    other 7 index, and the declined page is listed with its stop_reason."""
    _refusing(monkeypatch, tmp_path, {3})
    out = pf.locate(_set(tmp_path, 8), "x", _work(tmp_path))
    assert out["indexedPages"] == 7 and not out["complete"] and out["remainingPages"] == 0
    assert out["unreadablePages"] == [
        {
            "page": 3,
            "reason": "the model declined to read it (stop_reason refusal); it is tried once more on the next call",
        }
    ]
    assert "could not be read" in out["note"] and "failedPages" not in out
    # The unreadable page stays INSIDE its section, named, never skipped.
    (sec,) = out["documents"][:1]
    assert sec["pages"] == "1-4" and sec["unreadablePages"] == {"3": out["unreadablePages"][0]["reason"]}


def test_a_refused_page_is_retried_once_on_a_later_call_then_settled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    src = _set(tmp_path, 8)
    first = pf.locate(src, "x", _work(tmp_path))
    assert "tried once more on the next call" in first["unreadablePages"][0]["reason"]
    before = fake.index_calls()
    second = pf.locate(src, "x", _work(tmp_path))
    assert fake.index_calls() == before + 1  # page 3 alone, once more
    assert "declined 2 times" in second["unreadablePages"][0]["reason"]
    third = pf.locate(src, "x", _work(tmp_path))
    assert fake.index_calls() == before + 1 and third["readNow"] == 0  # settled: not paid for again
    assert third["unreadablePages"][0]["page"] == 3


def test_a_refused_page_read_on_its_retry_stops_being_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    src = _set(tmp_path, 8)
    pf.locate(src, "x", _work(tmp_path))
    fake.refuse = frozenset()
    out = pf.locate(src, "x", _work(tmp_path))
    assert out["complete"] and "unreadablePages" not in out and out["readNow"] == 1


def test_strips_are_cut_to_the_viewed_top_and_bottom(model: FakeModel, tmp_path: Path) -> None:
    from smokeball_connector import page_split

    src = tmp_path / "set.pdf"
    w = PdfWriter()
    w.add_blank_page(width=600, height=1000)
    rotated = w.add_blank_page(width=600, height=1000)
    rotated.rotate(90)
    with open(src, "wb") as fh:
        w.write(fh)
    out = tmp_path / "pages"
    out.mkdir()
    page_split.split(src, 1, 2, out, view="bands")
    top, bottom = PdfReader(str(out / "p1.pdf")).pages
    assert [float(v) for v in top.mediabox] == [0, 780, 600, 1000]
    assert [float(v) for v in bottom.mediabox] == [0, 0, 600, 120]
    assert [float(v) for v in top.cropbox] == [0, 780, 600, 1000]
    # Rotated 90: the visual top is the page's left edge.
    rtop, _ = PdfReader(str(out / "p2.pdf")).pages
    assert [float(v) for v in rtop.mediabox] == [0, 0, 132, 1000]
    full = tmp_path / "full"
    full.mkdir()
    page_split.split(src, 1, 1, full, view="full")
    assert len(PdfReader(str(full / "p1.pdf")).pages) == 1
    pf.locate(_set(tmp_path, 2), "x", _work(tmp_path))
    assert "TWO strips" not in model.bodies[0]["messages"][0]["content"][-1]["text"]  # full is the default


def test_full_pages_by_default_and_strips_only_behind_the_flag(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _set(tmp_path, 2)
    assert pf.locate(src, "x", _work(tmp_path))["view"] == "full"
    monkeypatch.setenv(pf.VIEW_ENV, "bands")
    out = pf.locate(src, "x", _work(tmp_path))
    assert out["readNow"] == 2 and out["view"] == "bands"  # its own index
    assert "TWO strips" in model.bodies[-2]["messages"][0]["content"][-1]["text"]


def test_a_long_file_indexes_whole_across_calls(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    src = _set(tmp_path)
    reads = []
    for _ in range(4):
        reads.append(pf.locate(src, "x", _work(tmp_path))["readNow"])
    assert reads == [16, 16, 8, 0]
    assert pf.MAX_PAGES_PER_FILE >= 10_000


def test_the_spend_is_tallied_for_the_file_across_calls(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    src = _set(tmp_path)
    first = pf.locate(src, "x", _work(tmp_path))
    second = pf.locate(src, "x", _work(tmp_path))
    total = second["tokensForThisFile"]["input_tokens"]
    assert total == first["tokens"]["input_tokens"] + second["tokens"]["input_tokens"]


def test_cli_all_reads_everything_then_matches_once(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    big = _set(tmp_path)
    assert pft.main(["locate", "--pdf", str(big), "--what", "the visit", "--all", "--no-check", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["complete"] and out["indexedPages"] == 40
    matches = [b for b in model.bodies if "items" in b["output_config"]["format"]["schema"]["properties"]]
    assert len(matches) == 1
    assert out["tokensThisRun"]["input_tokens"] == out["tokensForThisFile"]["input_tokens"]


def test_cli_all_stops_and_says_so_when_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(pf.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(529)))
    )
    big = _set(tmp_path, 3)
    assert pft.main(["locate", "--pdf", str(big), "--what", "x", "--all", "--no-check"]) == 0
    captured = capsys.readouterr()
    assert "stopped: a call read no new page (3 failed)" in captured.err
    assert "failed: p1: HTTP 529" in captured.out


# ---- the second acceptance run's defects ------------------------------------


def _cont(header: str, *starts: tuple[str, str, str, str]) -> dict[str, Any]:
    row = _page(*starts, cont=True)
    row["running_header"] = header
    return row


def test_a_continued_note_keeps_running_through_an_embedded_report() -> None:
    """The acceptance shape: a 35-page discharge summary (538-572) whose page 541
    carries an embedded flowsheet the model indexes as its own section. The
    running header still says the summary continues, so the summary runs on."""
    summary = ("DISCHARGE SUMMARY", "discharge summary", "6/30/2024", "Hospital B")
    flowsheet = ("VITALS FLOWSHEET", "flowsheet", "6/28/2024", "Hospital B")
    pages = {"538": _page(summary), "539": _page(cont=True), "540": _page(cont=True)}
    pages["541"] = _cont("DISCHARGE SUMMARY (continued)", flowsheet)
    for n in range(542, 573):
        pages[str(n)] = _cont("DISCHARGE SUMMARY (continued)")
    pages["573"] = _page(("OFFICE VISIT", "office visit note", "8/1/2024", "Clinic A"))
    secs = pf.sections(pages)
    assert [(s["heading"], s["first"], s["last"]) for s in secs] == [
        ("DISCHARGE SUMMARY", 538, 572),
        ("VITALS FLOWSHEET", 541, 572),
        ("OFFICE VISIT", 573, 573),
    ]
    assert secs[1]["insideOf"] == "S1"


def test_continued_rows_run_to_their_last_continued_page() -> None:
    """The 12/11 rows: 36-44 and 45-56, each with an embedded result mid-run."""
    note = ("SPEECH THERAPY NOTE", "speech therapy note", "12/11/2024", "Clinic C")
    second = ("SPEECH THERAPY NOTE", "speech therapy note", "12/11/2024", "Clinic C")
    result = ("AUDIOGRAM", "test result", "12/11/2024", "Clinic C")
    pages = {"36": _page(note)}
    for n in range(37, 45):
        pages[str(n)] = (
            _cont("SPEECH THERAPY NOTE (cont'd)", result) if n == 41 else _cont("SPEECH THERAPY NOTE (cont'd)")
        )
    pages["45"] = _page(second)
    for n in range(46, 57):
        pages[str(n)] = _cont("SPEECH THERAPY NOTE cont.", result) if n == 52 else _cont("SPEECH THERAPY NOTE cont.")
    spans = [(s["first"], s["last"]) for s in pf.sections(pages) if s["document_type"] == "speech therapy note"]
    assert spans == [(36, 44), (45, 56)]


def test_without_a_continued_header_a_start_still_ends_the_running_note() -> None:
    pages = {
        "1": _page(VISIT),
        "2": _page(cont=True),
        "3": _page(("LAB", "lab result", "", ""), cont=True),
        "4": _page(cont=True),
    }
    assert _spans(pages) == [("OFFICE VISIT", 1, 3), ("LAB", 3, 4)]


def test_refused_responses_are_in_the_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A refusal bills its input tokens; the tally counts them (8-page batch
    refused once, then 8 single pages of which 1 refused: 2 refusals x 900)."""
    _refusing(monkeypatch, tmp_path, {3})
    out = pf.locate(_set(tmp_path, 8), "x", _work(tmp_path))
    read = 7 * 1600 + 1600  # seven single pages, plus the match call
    assert out["tokens"]["input_tokens"] == read + 2 * 900
    assert out["tokensForThisFile"]["input_tokens"] == out["tokens"]["input_tokens"]


def test_a_negative_over_unread_pages_is_not_established(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    fake.items = [
        {"asked": "the x-ray", "found": False, "sections": [], "reason": "none in the sections"},
        {"asked": "the visit", "found": True, "sections": ["S1"], "reason": "S1"},
    ]
    out = pf.locate(_set(tmp_path, 8), "x", _work(tmp_path))
    xray, visit = out["items"]
    assert xray["established"] is False and xray["reason"].startswith("NOT ESTABLISHED")
    assert "pages 3 could not be read" in xray["notEstablishedBecause"]
    assert visit["established"] is True and visit["unreadableInside"] == ["3"]


def test_a_negative_with_pages_left_is_not_established(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    out = pf.locate(_set(tmp_path), "x", _work(tmp_path))
    xray = out["items"][2]
    assert xray["established"] is False and "24 pages are not read yet" in xray["notEstablishedBecause"]
    done = _set(tmp_path, 8)
    full = pf.locate(done, "x", _work(tmp_path))
    assert full["items"][2]["established"] is True and "NOT ESTABLISHED" not in full["items"][2]["reason"]


# ---- follow-ups: the note's own label, standalone files, flat negatives -----


def _labelled(banner: str, label: str, *starts: tuple[str, str, str, str]) -> dict[str, Any]:
    row = _page(*starts, cont=True)
    row["running_header"] = banner
    row["note_label"] = label
    return row


def test_the_notes_own_label_keeps_it_running_under_an_encounter_banner() -> None:
    """The banner names the encounter on every page and never says continued;
    the note's own label line does. The summary must run 538-572."""
    summary = ("DISCHARGE SUMMARY", "discharge summary", "6/30/2024", "Hospital B")
    flowsheet = ("VITALS FLOWSHEET", "flowsheet", "6/28/2024", "Hospital B")
    banner = "Hospital B | Encounter 6/27/2024"
    pages = {"538": _page(summary)}
    for n in range(539, 573):
        starts = (flowsheet,) if n == 541 else ()
        pages[str(n)] = _labelled(banner, "Discharge Summary (continued)", *starts)
    pages["573"] = _page(("OFFICE VISIT", "office visit note", "8/1/2024", "Clinic A"))
    spans = [(s["heading"], s["first"], s["last"]) for s in pf.sections(pages)]
    assert spans[0] == ("DISCHARGE SUMMARY", 538, 572)


def test_the_label_wins_over_a_banner_that_says_continued() -> None:
    """A banner may carry "continued" (a multi-page encounter header); when the
    page's own label is a different, non-continued note, what starts there
    ends the running one."""
    note = ("PROGRESS NOTE", "progress note", "1/1/2024", "Clinic A")
    pages = {
        "1": _page(note),
        "2": _labelled("Encounter (continued)", "Operative Report", ("OPERATIVE REPORT", "operative report", "", "")),
        "3": _labelled("Encounter (continued)", "Operative Report (continued)"),
    }
    assert [(s["heading"], s["first"], s["last"]) for s in pf.sections(pages)] == [
        ("PROGRESS NOTE", 1, 2),
        ("OPERATIVE REPORT", 2, 3),
    ]


def test_the_index_asks_for_the_note_label_separately(model: FakeModel, tmp_path: Path) -> None:
    from smokeball_connector import page_finder_prompts as prompts

    required = prompts.INDEX_SCHEMA["properties"]["pages"]["items"]["required"]
    assert "note_label" in required and "running_header" in required
    assert "not the encounter banner" in prompts.INDEX_INSTRUCTION
    assert pf.PROMPT_VERSION >= 4


def test_the_tool_prefers_a_records_own_file() -> None:
    assert "OWN file" in pft.TOOL_NOTE and "rather than these pages" in pft.TOOL_NOTE


def test_a_not_established_item_carries_no_flat_negative(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    fake.items = [
        {"asked": "the x-ray", "found": False, "sections": [], "reason": "The x-ray does not exist in this file."}
    ]
    out = pf.locate(_set(tmp_path, 8), "x", _work(tmp_path))
    (xray,) = out["items"]
    assert "does not exist" not in xray["reason"] and xray["reason"].startswith("NOT ESTABLISHED")
    assert "not a finding that it is absent" in xray["reason"]
