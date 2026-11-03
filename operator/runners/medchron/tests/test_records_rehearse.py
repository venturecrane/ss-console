"""`medchron records --rehearse <dir>`: the job's own assembly on copies of a
seat's page indexes, reading no page, byte-identical across runs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from medchron import proof_mode
from medchron.records.rehearse import rehearse
from medchron.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env


@pytest.fixture
def seat_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Path], FakeFinderModel]:
    """A live job's indexes, copied into a rehearsal dir with its records and its reply as the key."""
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    model = FakeFinderModel()
    env = setup_env(tmp_path, monkeypatch, model)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r = RecordsRun(
        job_dir(tmp_path),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient(),
        log=lambda _m: None,
    )
    assert r.run().verdict == "delivered"
    files = json.loads((r.data / "files.json").read_text())
    (meta,) = files.values()
    d = tmp_path / "rehearsal"
    (d / "index").mkdir(parents=True)
    (d / "files").mkdir()
    sha = meta["sha256"]
    # The seat's index, with its remembered matches dropped: the rehearsal asks them again.
    idx = json.loads((tmp_path / "index" / f"{sha}.json").read_text())
    idx["matches"] = {}
    (d / "index" / f"{sha}.json").write_text(json.dumps(idx))
    shutil.copy(r.data / "files" / f"{next(iter(files))}.pdf", d / "files" / f"{sha}.pdf")
    records = [{**rec, "files": [sha]} for rec in json.loads((r.data / "records.json").read_text())]
    (d / "records.json").write_text(json.dumps(records))
    (d / "names.json").write_text(json.dumps({sha: meta["name"]}))
    facts = json.loads((r.data / "facts.json").read_text())
    (d / "expected.json").write_text(
        json.dumps({"text": facts["reply_text"], "html": facts["reply_html"], "items": facts["items"]})
    )
    model.bodies.clear()
    return d, env, model


def test_the_rehearsal_reproduces_the_live_reply_and_reads_no_page(seat_run) -> None:
    d, env, model = seat_run
    report, code = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    out = json.loads(report)
    assert code == 0, out["differs_from_expected"]
    assert out["page_reads_attempted"] == 0 and model.index_calls() == 0 and model.match_calls() == 2


def test_two_rehearsals_print_the_same_bytes_and_the_second_asks_nothing(seat_run) -> None:
    d, env, model = seat_run
    first, _ = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    calls = len(model.bodies)
    second, code = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    strip = lambda s: {k: v for k, v in json.loads(s).items() if k != "cents"}  # noqa: E731 - the spend is the run's
    assert code == 0 and strip(first) == strip(second) and len(model.bodies) == calls
    assert (d / "out" / "reply.txt").read_bytes() == json.loads(second)["text"].encode()


def test_a_page_read_fails_the_rehearsal(seat_run, monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector import page_finder as pf

    d, env, _model = seat_run
    real = pf.locate_cached

    def reads_a_page(*a, **k):
        pf.index_batch([(1, b"%PDF-")])
        return real(*a, **k)

    monkeypatch.setattr(pf, "locate_cached", reads_a_page)
    report, code = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    assert code == 1 and json.loads(report)["page_reads_attempted"] == 1


def test_a_reply_that_differs_from_the_key_fails(seat_run) -> None:
    d, env, _model = seat_run
    key = json.loads((d / "expected.json").read_text())
    (d / "expected.json").write_text(json.dumps({**key, "text": key["text"] + " extra"}))
    report, code = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    assert code == 1 and json.loads(report)["differs_from_expected"] == ["text"]


def test_an_index_that_does_not_cover_the_file_settles_nothing(seat_run) -> None:
    d, env, _model = seat_run
    (path,) = list((d / "index").glob("*.json"))
    idx = json.loads(path.read_text())
    idx["pages"].pop("10")
    path.write_text(json.dumps(idx))
    report, code = rehearse(d, pricing=str(env["pricing"]), log=lambda _m: None)
    out = json.loads(report)
    assert code == 1 and out["unsettled"] == [1, 2] and out["page_reads_attempted"] == 0
