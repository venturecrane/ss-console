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


def test_a_named_pdf_with_an_active_key_holds_rather_than_going_altered() -> None:
    assert as_stored(pdf(1))
    assert not as_stored(b"%PDF-1.4 << /OpenAction << /S /JavaScript /JS (x) >> >>")
    assert not as_stored(b"%PDF-1.4 /Encrypt << >>")
