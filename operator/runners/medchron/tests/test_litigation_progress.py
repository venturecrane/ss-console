"""The long stages say where they are (counts only), and the lane's heartbeat
does not depend on the log."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron.litigation.progress import Progress
from medchron.litigation.run import LitigationRun
from medchron_testkit import make_pdf

LINE = re.compile(r"^\[(inventory|extract|read1|read2|read3)\] \d+/\d+ [a-z ]+(, \d+ [a-z ]+)*$")


def test_progress_speaks_every_n_and_when_quiet_too_long():
    out: list[str] = []
    now = [0.0]
    p = Progress(out.append, "extract", 5, "docs", every=2, seconds=60, clock=lambda: now[0])
    p.step()
    assert out == []
    p.step()
    assert out == ["[extract] 2/5 docs"]
    now[0] = 61.0
    p.add("vision pages")  # inside one long document: still speaks
    assert out[-1] == "[extract] 2/5 docs, 1 vision pages"
    p.add("vision pages")
    assert len(out) == 2  # not again until the next interval
    p.step(3)
    assert out[-1] == "[extract] 5/5 docs, 2 vision pages"


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def test_a_run_logs_progress_for_every_long_stage_and_never_a_name(tmp_path):
    docs = matter_docs() + [("f-s1", "Scanned POS.pdf", make_pdf(["", ""]), "2026-06-01T00:00:00")]
    seat = LitSeat({M1: docs})
    lines: list[str] = []
    r = LitigationRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=ScriptedLit(),
        log=lines.append,
        today=lambda: dt.date(2026, 10, 7),
        readback_pause=0.0,
    )
    r.run()
    prog = [ln for ln in lines if LINE.match(ln)]
    stages = {ln.split("]")[0][1:] for ln in prog}
    assert {"inventory", "extract", "read1", "read2", "read3"} <= stages, lines
    assert any(ln.startswith("[extract] ") and "2 vision pages" in ln for ln in prog), prog
    assert any(ln == "[read1] 1/1 matters started" for ln in prog)
    names = [n for _f, n, _b, _d in docs] + ["Gamma", "Delta", "Exampletown"]
    for ln in prog:
        assert not any(n.split(".")[0] in ln for n in names), ln


def test_the_lane_heartbeat_is_a_timer_not_the_log():
    import inspect

    from medchron.litigation_lane import LitigationLane

    src = inspect.getsource(LitigationLane._beat)
    assert "stop.wait" in src and "self.heartbeat" in src and "log" not in src
