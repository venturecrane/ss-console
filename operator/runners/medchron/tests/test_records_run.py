"""The records job (ADR 0102 amendment 4): parse, resolve, pre-flight, locate
once per (record, file), cut, facts in code, and a verdict that carries no
client text. The model is faked at the page finder's HTTP seam and the
doorway's client; Smokeball is a fake client that refuses any write."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron import proof_mode
from medchron.records import assemble
from medchron.records.run import RecordsRun
from records_testkit import (
    MATTER,
    PARSED,
    REQUEST,
    FakeFinderModel,
    FakeParseClient,
    FakeSmokeball,
    job_dir,
    pdf,
    setup_env,
)


@pytest.fixture
def model() -> FakeFinderModel:
    return FakeFinderModel()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel) -> dict[str, Path]:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    return setup_env(tmp_path, monkeypatch, model)


def _seat() -> tuple[FakeSmokeball, str]:
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    sb.add(2, "Retainer agreement.pdf", pdf(2))
    return sb, f


def _run(
    tmp_path: Path, env: dict[str, Path], sb: Any, parse: Any = None, **job: Any
) -> tuple[RecordsRun, dict[str, Any]]:
    jd = job_dir(tmp_path, **job)
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=parse or FakeParseClient(),
        log=lambda _m: None,
    )
    return r, json.loads(r.run().to_json())


def test_a_whole_request_delivers_both_records_with_code_written_facts(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb, f = _seat()
    r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "delivered", v
    assert v["items"] == [{"n": 1, "outcome": "attached", "files": 1}, {"n": 2, "outcome": "attached", "files": 1}]
    assert [(c["n"], c["pages"], c["page_count"]) for c in v["cuts"]] == [(1, "1-4", 4), (2, "5-10", 6)]
    assert v["proof"]["readback"] == "confirmed" and v["proof"]["review"] == "code_only"
    facts = json.loads((r.data / "facts.json").read_text())
    assert facts["reply_text"].startswith(assemble.OPENING)
    assert "Northside Clinic records pp. 1-4.pdf, pages 1-4 (4 pages)" in facts["reply_text"]
    assert facts["hold_reason"] is None
    # One locate per (record, file): two match calls, the pages read once.
    assert model.match_calls() == 2 and model.index_calls() == 2
    assert sorted(p.name for p in (r.data / "locate").rglob("*.json")) == [f"{f}.json", f"{f}.json"]
    assert sb.writes == []


def test_the_verdict_carries_no_file_names_or_page_text(tmp_path: Path, env: dict[str, Path]) -> None:
    sb, _ = _seat()
    _r, v = _run(tmp_path, env, sb)
    raw = json.dumps(v)
    for words in ("Northside", "Clinic", "discharge", "progress", "3/3/2021", "visit", REQUEST[:20]):
        assert words not in raw, words


def test_a_rerun_makes_no_request_and_never_overwrites_a_locate_result(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb, f = _seat()
    r, first = _run(tmp_path, env, sb)
    saved = {p: p.read_bytes() for p in (r.data / "locate").rglob("*.json")}
    calls = len(model.bodies)
    r.reopen(["locate"])
    second = json.loads(r.run().to_json())
    assert len(model.bodies) == calls  # nothing asked again
    assert {p: p.read_bytes() for p in saved} == saved
    assert second["items"] == first["items"] and second["cuts"] == first["cuts"]


def test_a_cap_reached_mid_read_holds_with_a_words_only_reply(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb, _ = _seat()
    # A pre-flight price low enough to start; the parse ($0.005) and the first
    # page batch ($0.01) then take the job past a $0.01 cap.
    (env["inputs"] / "records-firm.yaml").write_text(
        "per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.0001\n", encoding="utf-8"
    )
    r, v = _run(tmp_path, env, sb, per_job_cap_usd=0.01)
    assert v["verdict"] == "held" and v["reason"].startswith("per_job_cap_usd:"), v
    assert "proof" not in v and v["cuts"] == []
    facts = json.loads((r.data / "facts.json").read_text())
    assert facts["reply_text"].endswith(assemble.HELD_LINE) and not facts["reply_text"].startswith(assemble.OPENING)
    assert model.index_calls() == 1  # no batch after the stop
    # The page read before the stop is kept in the index.
    from smokeball_connector import page_finder as pf

    sha = json.loads((r.data / "files.json").read_text())[sb.rows[0]["id"]]["sha256"]
    assert len(pf.load_index(sha)["pages"]) == 8


def test_preflight_holds_before_any_page_is_bought(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb, _ = _seat()
    (env["inputs"] / "records-firm.yaml").write_text(
        "per_job_cap_usd: 0.05\nmonthly_budget_usd: 100.0\nusd_per_page: 1.0\n", encoding="utf-8"
    )
    _r, v = _run(tmp_path, env, sb, per_job_cap_usd=None)
    assert v["verdict"] == "held" and v["reason"].startswith("per_job_cap_usd:") and v["stage"] == "preflight"
    assert model.bodies == []


def test_a_record_for_another_person_holds_before_any_search(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb, _ = _seat()
    other = {"items": [{**PARSED["items"][0], "for_another_person": True}]}
    r, v = _run(tmp_path, env, sb, parse=FakeParseClient(other))
    assert v["verdict"] == "held" and v["reason"].startswith("for_another_person:")
    assert model.bodies == [] and sb.downloads == []
    assert json.loads((r.data / "facts.json").read_text())["reply_text"] == assemble.HELD_LINE


def test_a_parse_that_rewords_the_request_is_refused(tmp_path: Path, env: dict[str, Path]) -> None:
    sb, _ = _seat()
    bad = {"items": [{**PARSED["items"][0], "asked": "the clinic note from March"}]}
    _r, v = _run(tmp_path, env, sb, parse=FakeParseClient(bad))
    assert v["verdict"] == "held" and v["reason"].startswith("scope_unresolved:")


def test_the_parse_is_cached_across_jobs(tmp_path: Path, env: dict[str, Path]) -> None:
    sb, _ = _seat()
    parse = FakeParseClient()
    _run(tmp_path / "a", env, sb, parse=parse)
    _run(tmp_path / "b", env, sb, parse=parse)
    assert parse.calls == 1


def test_a_record_not_in_the_file_is_reported_in_the_renderers_words(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    model.match["the discharge summary"] = [
        {"asked": "the discharge summary", "found": False, "sections": [], "reason": "no discharge"}
    ]
    sb, _ = _seat()
    r, v = _run(tmp_path, env, sb)
    assert v["items"][1] == {"n": 2, "outcome": "not_found", "files": 0}
    text = json.loads((r.data / "facts.json").read_text())["reply_text"]
    assert (
        "Not found:\n- the discharge summary: not found in Northside Clinic records.pdf (all 10 pages were read)"
        in text
    )


def test_a_proof_run_ends_dry_run_with_the_would_be_reply(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    sb, _ = _seat()
    r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "dry_run" and len(v["cuts"]) == 2
    proof = r.job.job_dir / "proof"
    assert (proof / "reply.txt").read_text().startswith(assemble.OPENING)
    assert [c["sha256"] for c in json.loads((proof / "cuts.json").read_text())] == [c["sha256"] for c in v["cuts"]]


def _open(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]) -> list[tuple]:
    from medchron import arrivals

    asked: list[tuple] = []

    def fake(seat: Any, statuses: Any) -> list[dict[str, str]]:
        asked.append(tuple(statuses))
        return rows

    monkeypatch.setattr(arrivals, "open_matters", fake)
    return asked


def test_a_matter_number_resolves_to_its_one_open_matter(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _open(monkeypatch, [{"id": MATTER, "number": "2024-0117"}, {"id": "x", "number": "2024-0118"}])
    sb, _ = _seat()
    r, v = _run(tmp_path, env, sb, matter_id=" 2024-0117 ")
    assert v["verdict"] == "delivered" and asked == [("Open", "Pending")]
    assert json.loads((r.data / "matter.json").read_text()) == {"matter_id": MATTER}


@pytest.mark.parametrize("numbers", [[], ["2024-0117", "2024-0117"]], ids=["none", "two"])
def test_a_matter_number_naming_none_or_many_holds_before_any_search(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel, numbers: list[str]
) -> None:
    _open(monkeypatch, [{"id": f"m{i}", "number": n} for i, n in enumerate(numbers)])
    sb, _ = _seat()
    _r, v = _run(tmp_path, env, sb, matter_id="2024-0117")
    assert v["verdict"] == "held" and v["reason"].startswith("scope_unresolved:")
    assert model.bodies == [] and sb.downloads == []


def test_a_found_record_with_no_cut_never_delivers(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The renderer says nothing of a found item whose pages are not attached
    (``unplaced``): delivering that reply would silently drop the record."""
    monkeypatch.setattr(assemble, "ranges_to_cut", lambda *_a, **_k: [])
    sb, _ = _seat()
    _r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "held" and v["reason"].startswith("cut_refused:")
    assert "proof" not in v


def test_no_delivered_verdict_carries_an_unplaced_record(tmp_path: Path, env: dict[str, Path]) -> None:
    sb, _ = _seat()
    _r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "delivered" and not [i for i in v["items"] if i["outcome"] in ("unplaced", "unresolved")]


def test_more_cuts_than_three_replies_carry_hold(
    tmp_path: Path, env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from records_contract import batches as batches_mod

    def refuse(_rows: Any) -> Any:
        raise batches_mod.BatchesRefused("more than 9 files were staged for one request")

    monkeypatch.setattr(batches_mod, "batches", refuse)
    sb, _ = _seat()
    _r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "held" and v["reason"].startswith("cut_refused:")
