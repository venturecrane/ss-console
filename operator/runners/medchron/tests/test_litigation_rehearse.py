"""The rehearsal's guarantees: no file stage, no write to the seat, no touch of
the lane's state, the read limit, the report, and the one-line verdict."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from litigation_testkit import M1, M2, LitSeat, ScriptedLit, make_inputs, matter_docs
from medchron import __main__ as cli
from medchron.litigation import manifest, rehearse

TODAY = dt.date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    lane_state = tmp_path / "volume" / "litigation" / "state"
    monkeypatch.setenv(manifest.DATA_ENV, str(tmp_path / "volume"))
    monkeypatch.delenv(manifest.STATE_ENV, raising=False)
    assert manifest.state_dir() == lane_state


def _seat() -> LitSeat:
    # The same paper names as matter 1 (the scripted model cites by name), new
    # ids, saved on the two weekdays before "today" for the projection.
    when = {"f-c1": "2026-10-06T09:00:00", "f-p1": "2026-10-05T09:00:00"}
    m2 = [("m2" + fid[1:], name, blob, when.get(fid, "2026-01-02T09:00:00")) for fid, name, blob, _w in matter_docs()]
    return LitSeat({M1: matter_docs(), M2: m2})


def _inputs(tmp_path: Path, seed: bool = True) -> Path:
    root = make_inputs(tmp_path / "inputs")
    if seed:
        b = root / "baseline"
        (b / "matters").mkdir(parents=True, exist_ok=True)
        (b / "manifest").mkdir(exist_ok=True)
        (b / "baseline.json").write_text(json.dumps({"source": "test"}))
        (b / "matters" / f"{M2}.json").write_text(json.dumps({"fields_read": ["case", "defendants", "discovery"]}))
        (b / "manifest" / f"{M2}.json").write_text(
            json.dumps({"m2-p1": "", "m2-a1": "", "m2-b1": ""})
        )  # the complaint is new since the seed
    return root


def _rehearsal(tmp_path: Path, client: ScriptedLit, seat: LitSeat, **kw) -> rehearse.RehearsalRun:
    jd = tmp_path / "job"
    rehearse.ensure_job(jd, "example", [])
    return rehearse.RehearsalRun(
        jd,
        report_path=tmp_path / "report.json",
        inputs_dir=str(_inputs(tmp_path)),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=kw.pop("state_dir", tmp_path / "rstate"),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        **kw,
    )


def test_a_rehearsal_files_nothing_writes_nothing_and_reports(tmp_path):
    seat = _seat()
    r = _rehearsal(tmp_path, ScriptedLit(), seat)
    v = r.run()
    assert v.verdict == "held" and v.reason.startswith("rehearsal: ") and v.files == [] and v.folder_id == ""
    assert seat.created == [] and seat.sent == []
    st = r._state()
    assert "file" not in st and "report" not in st and st["book"]["status"] == "done"
    assert r._out().is_file()  # the workbook is built (and leak-scanned), never filed
    rep = json.loads((tmp_path / "report.json").read_text())
    assert tuple(rep) == rehearse.REPORT_KEYS
    assert rep["counts"]["matters_inventoried"] == 2 and rep["counts"]["matters_read"] == 2
    assert rep["counts"]["state_seeded_from_inputs"] is True
    assert rep["cents"]["total"] > 0 and rep["cents"]["by_stage"]["read1"] > 0
    assert set(rep["cents"]["by_model"]) == {"claude-sonnet-5"}
    assert rep["peak_rss"]["self"] > 0 and set(rep["stages"]) == set(rehearse.REHEARSAL_STAGES)
    p = rep["projection"]
    assert "dateModified" in p["method"] and len(p["weekdays"]) == 5 and p["projected_monthly_cents"] is not None
    assert p["weekdays"]["2026-10-06"] == 1 and p["weekdays"]["2026-10-05"] == 1


def test_the_lanes_state_is_never_touched(tmp_path):
    r = _rehearsal(tmp_path, ScriptedLit(), _seat())
    assert r.run().verdict == "held"
    assert not (tmp_path / "volume").exists()
    assert (tmp_path / "rstate" / "baseline.json").is_file()
    assert not (tmp_path / "rstate" / "matters" / f"{M1}.json").exists()  # nothing committed
    with pytest.raises(rehearse.RehearsalError, match="lane's own"):
        _rehearsal(tmp_path, ScriptedLit(), _seat(), state_dir=manifest.state_dir())


def test_the_read_only_seat_refuses_a_write_before_it_is_made(tmp_path):
    seat = _seat()
    ro = rehearse.ReadOnlySeat(seat)
    with pytest.raises(Exception, match="read-only"):
        ro.create_folder("m", "Litigation Status")
    with pytest.raises(Exception, match="read-only"):
        ro.add_file("m", "f", "x.xlsx", b"x")
    assert seat.created == [] and seat.sent == [] and ro.writes_refused == 2
    assert ro.list_files(M1)  # reads pass through


def test_the_read_limit_reads_named_matters_first_but_lists_them_all(tmp_path):
    client = ScriptedLit()
    r = _rehearsal(tmp_path, client, _seat(), read_limit=1, read_matters=[M2])
    assert r.run().verdict == "held"
    rep = json.loads((tmp_path / "report.json").read_text())
    c = rep["counts"]
    assert c["matters_inventoried"] == 2 and c["matters_read_ids"] == [M2] and c["would_read_without_limit"] == 2
    assert r._plan()[M1]["limited_out"]["read_groups"]
    assert rehearse.pick(
        {
            "a": {"read_groups": ["case"], "audit": "all", "candidates": [1]},
            "b": {"read_groups": ["case"], "audit": "all", "candidates": [1, 2, 3]},
            "c": {"read_groups": [], "audit": "none", "candidates": [1, 2, 3, 4]},
        },
        2,
        [],
    ) == ["b", "a"]


def test_the_cli_rehearsal_prints_one_held_line(tmp_path, monkeypatch, capsys):
    seat = _seat()
    monkeypatch.setattr("medchron.seat.open_seat", lambda slug: seat)
    from medchron.litigation import run as run_mod

    real = run_mod.LitigationRun.__init__

    def scripted(self, *a, **k):
        real(self, *a, **{**k, "client": ScriptedLit(), "today": lambda: TODAY})

    monkeypatch.setattr(run_mod.LitigationRun, "__init__", scripted)
    inputs = _inputs(tmp_path)
    code = cli.main(
        [
            "litigate",
            str(tmp_path / "job"),
            "--inputs",
            str(inputs),
            "--pricing",
            str(tmp_path / "pricing.json"),
            "--rehearse",
            str(tmp_path / "rep.json"),
            "--state-dir",
            str(tmp_path / "s"),
            "--read-limit",
            "1",
        ]
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 1 and len(out) == 1 and json.loads(out[0])["reason"].startswith("rehearsal: ")
    assert (tmp_path / "rep.json").is_file() and seat.sent == [] and not (tmp_path / "volume").exists()


def test_a_read_limit_without_rehearse_is_refused(tmp_path, capsys):
    code = cli.main(["litigate", str(tmp_path / "job"), "--inputs", str(_inputs(tmp_path)), "--read-limit", "2"])
    v = json.loads(capsys.readouterr().out)
    assert code == 2 and v["reason"].startswith("rehearsal: ")


def test_rtf_and_doc_degrade_without_striprtf_or_antiword(tmp_path, monkeypatch):
    import builtins

    from medchron.litigation import extract

    real_import = builtins.__import__

    def no_striprtf(name, *a, **k):
        if name.startswith("striprtf") or name.startswith("RTFDE"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_striprtf)
    monkeypatch.setattr(extract.shutil, "which", lambda n: None)
    (tmp_path / "a.rtf").write_bytes(rb"{\rtf1\ansi Answer filed 04/30/2026\par}")
    rec = extract.extract_file(tmp_path / "a.rtf", ".rtf", tmp_path / "a.txt", None)
    assert rec["ok"] and "Answer filed" in (tmp_path / "a.txt").read_text()
    (tmp_path / "b.doc").write_bytes(b"\xd0\xcf\x11\xe0 word")
    assert extract.extract_file(tmp_path / "b.doc", ".doc", tmp_path / "b.txt", None)["problem"].startswith(
        "doc_unreadable"
    )
    (tmp_path / "c.rtf").write_bytes(rb"{\rtf1\ansi{\fonttbl{\f0 Times;}}}")
    assert extract.extract_file(tmp_path / "c.rtf", ".rtf", tmp_path / "c.txt", None)["problem"].startswith(
        "empty_text"
    )
