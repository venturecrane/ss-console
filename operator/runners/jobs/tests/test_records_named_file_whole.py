"""A records request that NAMES a file gets the whole file, exactly as the
matter holds it (2026-11-24, A&P job 01M87C6...): a request naming one 17-page
PDF went out as the 4 pages the page finder matched, its sha256 not the matter
file's, and SMD's review rejected the reply. Code decides that a record names a
file (``rank.names_whole``), never a model; the cut is skipped and the bytes
are proved by sha256. Every name and text here is invented."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from jobs import proof_mode
from jobs.records import rank
from jobs.records.named import as_stored
from jobs.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env


def _item(asked: str, *, dates: list[str] | None = None, provider: str = "") -> dict[str, Any]:
    return {
        "asked": asked,
        "hints": {"dates": dates or [], "provider": provider, "pages": ""},
        "for_another_person": False,
    }


@pytest.fixture
def model() -> FakeFinderModel:
    return FakeFinderModel()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel) -> dict[str, Path]:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    return setup_env(tmp_path, monkeypatch, model)


def _run(tmp_path: Path, env: dict[str, Path], sb: Any, items: list[dict[str, Any]], text: str) -> tuple[Any, dict]:
    r = RecordsRun(
        job_dir(tmp_path, request_text=text),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient({"items": items}),
        log=lambda _m: None,
    )
    return r, json.loads(r.run().to_json())


def test_a_named_17_page_pdf_goes_whole_with_the_matter_file_s_sha(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    """FALSIFIER: without the rule the record is sent to the page finder (the
    fake model reads a visit on pages 1-4 of any file) and nothing whole, with
    the matter file's sha256, is attached."""
    stored = pdf(17)
    sb = FakeSmokeball()
    f = sb.add(1, "Declaration of Jane Roe.pdf", stored)
    sb.add(2, "Northside Clinic records.pdf", pdf(10))
    text = "Please send me the Declaration of Jane Roe on this matter."
    r, v = _run(tmp_path, env, sb, [_item("the Declaration of Jane Roe")], text)
    assert v["verdict"] == "delivered", v
    (cut,) = json.loads((r.data / "cuts.json").read_text())
    assert (cut["file_id"], cut["pages"], cut["page_count"]) == (f, "", 17)
    sent = (r.data / cut["path"]).read_bytes()
    assert hashlib.sha256(sent).hexdigest() == hashlib.sha256(stored).hexdigest() == cut["sha256"]
    # The ask named it: no page was read to find it.
    assert model.index_calls() == 0 and model.match_calls() == 0
    facts = json.loads((r.data / "facts.json").read_text())
    assert "Declaration of Jane Roe.pdf, the whole file (17 pages)" in facts["reply_text"]


def test_a_part_of_a_larger_file_is_still_cut(tmp_path: Path, env: dict[str, Path]) -> None:
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    item = _item("the 3/3/2021 Northside Clinic visit", dates=["3/3/2021"], provider="Northside Clinic")
    r, _v = _run(tmp_path, env, sb, [item], "Please send the 3/3/2021 Northside Clinic visit.")
    assert [(c["pages"], c["page_count"]) for c in json.loads((r.data / "cuts.json").read_text())] == [("1-4", 4)]


def test_names_whole_is_code_and_never_a_part() -> None:
    assert rank.names_whole(_item("the Declaration of Jane Roe"), "Declaration of Jane Roe.pdf")
    assert rank.names_whole(_item("the 17-page Declaration of Jane Roe"), "Declaration of Jane Roe.pdf")
    assert rank.names_whole(_item("the whole Parent Statements folder"), "Parent Statements")
    assert not rank.names_whole(_item("the Declaration of John Roe"), "Declaration of Jane Roe.pdf")
    assert not rank.names_whole(_item("the ER visit from the hospital records"), "Hospital records.pdf")
    assert not rank.names_whole(_item("the hospital records", dates=["3/2/2024"]), "Hospital records.pdf")
    assert not rank.names_whole(_item("the records"), "Records.pdf")  # stop words name nothing
    # A page range in the words is a part, even when the parser left it out of the hints (review 2026-11-24).
    assert not rank.names_whole(_item("page 3 of the Declaration of Jane Roe"), "Declaration of Jane Roe.pdf")
    assert not rank.names_whole(_item("pages 12-14 of the Declaration of Jane Roe"), "Declaration of Jane Roe.pdf")
    assert not rank.names_whole(_item("the 17-page Declaration of Jane Roe 4"), "Declaration of Jane Roe.pdf")


@pytest.mark.parametrize(
    ("asked", "folder"),
    [
        # The live phrasing that was cut to 4 pages (2026-11-24), names invented.
        ("the file named Dana Roe Statement.pdf on matter 900417", ""),
        ("the document called Dana Roe Statement", ""),
        ("file Dana Roe Statement.pdf from the Discovery folder", "Discovery"),
        ("the file titled Dana Roe Statement in matter no. 2026-PI-101", ""),
        ("the document entitled Dana Roe Statement in the Discovery folder", "Discovery"),
    ],
)
def test_framing_around_a_file_name_still_names_the_whole_file(asked: str, folder: str) -> None:
    """FALSIFIER: "named", "called", the matter number and the folder's own
    words read as extra words, so each of these went to the page finder."""
    assert rank.names_whole(_item(asked), "Dana Roe Statement.pdf", folder=folder)


def test_framing_never_hides_a_part() -> None:
    name = "Dana Roe Statement.pdf"
    assert not rank.names_whole(_item("pages 2-5 of the file named Dana Roe Statement.pdf on matter 900417"), name)
    assert not rank.names_whole(_item("the file named Dana Roe Statement.pdf", dates=["3/2/2024"]), name)
    # A folder word that is not the file's own folder still narrows.
    assert not rank.names_whole(_item("file Dana Roe Statement.pdf from the Medical folder"), name, folder="Discovery")


def test_a_folder_frames_a_name_only_when_the_words_call_it_a_folder() -> None:
    """Re-review of #4210: the folder's words were subtracted whether or not the
    request named the folder, so "the Banner ER records" sent Banner.pdf (in a
    folder ER) whole instead of the ER pages."""
    assert not rank.names_whole(_item("the Banner ER records"), "Banner.pdf", folder="ER")
    assert rank.names_whole(_item("Banner.pdf from the ER folder"), "Banner.pdf", folder="ER")
    assert rank.names_folder(_item("the records in the Medical folder"), "Medical")
    assert not rank.names_folder(_item("the Medical records"), "Medical")


class _FolderSeat(FakeSmokeball):
    """A matter whose PDFs sit in named folders."""

    def __init__(self, folders: dict[str, str]) -> None:
        super().__init__()
        self.folders = folders  # folder id -> name

    def add_in(self, n: int, name: str, data: bytes, folder_id: str) -> str:
        f = self.add(n, name, data)
        self.rows[-1]["folder"] = {"id": folder_id}
        self.meta[f]["folder"] = {"id": folder_id}
        return f

    def get(self, path: str, **params: Any) -> Any:
        if path.endswith("/documents/folders"):
            return {"value": [{"folders": [{"id": k, "name": v} for k, v in self.folders.items()]}]}
        return super().get(path, **params)


@pytest.mark.parametrize("name", ["Medical Records.pdf", "Medical.pdf"])
def test_words_naming_a_folder_of_several_pdfs_send_no_file_whole(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel, name: str
) -> None:
    """FALSIFIER: "the records in the Medical folder" sent the one file whose
    name matched (Medical Records.pdf, or Medical.pdf) whole, from a folder of
    several."""
    sb = _FolderSeat({"F1": "Medical", "F2": "Billing"})
    sb.add_in(1, name, pdf(6), "F1")
    sb.add_in(2, "Northside Clinic records.pdf", pdf(10), "F1")
    sb.add_in(3, "Invoice.pdf", pdf(2), "F2")
    asked = "the records in the Medical folder"
    r, _v = _run(tmp_path, env, sb, [_item(asked)], f"Please send me {asked}.")
    cuts = json.loads((r.data / "cuts.json").read_text()) if (r.data / "cuts.json").is_file() else []
    assert not any(c["pages"] == "" and c["page_count"] == 6 for c in cuts), cuts
    located = list((r.data / "locate").rglob("*.json")) if (r.data / "locate").is_dir() else []
    assert not any(json.loads(p.read_text()).get("namedFile") for p in located)


def test_the_live_phrasing_goes_whole_end_to_end(tmp_path: Path, env: dict[str, Path], model: FakeFinderModel) -> None:
    stored = pdf(17)
    sb = FakeSmokeball()
    f = sb.add(1, "Dana Roe Statement.pdf", stored)
    sb.add(2, "Northside Clinic records.pdf", pdf(10))
    asked = "the file named Dana Roe Statement.pdf on matter 900417"
    r, v = _run(tmp_path, env, sb, [_item(asked)], f"Please send me {asked}.")
    assert v["verdict"] == "delivered", v
    (cut,) = json.loads((r.data / "cuts.json").read_text())
    assert (cut["file_id"], cut["pages"], cut["page_count"]) == (f, "", 17)
    assert cut["sha256"] == hashlib.sha256(stored).hexdigest()
    assert model.index_calls() == 0


def test_a_named_pdf_with_an_active_key_holds_rather_than_going_altered() -> None:
    assert as_stored(pdf(1))
    assert not as_stored(b"%PDF-1.4 << /OpenAction << /S /JavaScript /JS (x) >> >>")
    assert not as_stored(b"%PDF-1.4 /Encrypt << >>")
