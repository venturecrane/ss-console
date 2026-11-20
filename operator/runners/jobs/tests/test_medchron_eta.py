"""The expected finish (``medchron/eta``): rises with pages, falls with
workers, drops a finished stage, and is written once on the run state."""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron import eta
from jobs.medchron.state import RunState


def test_minutes_rise_with_pages_and_fall_with_workers() -> None:
    sizes = [0, 100, 494, 700, 986, 2000, 3821, 6500, 12000]
    for w in (1, 4, 8):
        totals = [eta.expected(p, p // 3, w)["total"] for p in sizes]
        assert totals == sorted(totals), f"not monotonic in pages at {w} workers"
    for p in sizes[1:]:
        assert eta.expected(p, p // 3, 8)["total"] < eta.expected(p, p // 3, 1)["total"]


def test_the_serial_figures_are_the_measured_ones() -> None:
    m = eta.expected(3821, 2088, 1)
    assert m["writer"] == 362.0 and m["reader"] == 34.0 and m["review"] == 40.5 and m["other"] == 34.0
    assert m["vision"] == round(2088 / eta.VISION_PAGES_PER_MIN, 1)


def test_a_done_stage_drops_out() -> None:
    before = eta.expected(986, 100, 8)
    after = eta.expected(986, 100, 8, done=["vision", "map"])
    assert after["vision"] == after["writer"] == 0.0 and after["review"] == before["review"]


def test_stamp_writes_once_and_survives_a_reload(tmp_path: Path) -> None:
    ext = tmp_path / "extracted.jsonl"
    st = RunState.load_or_new(tmp_path / "state.json", slug="example", unit="alpha")
    lines: list[str] = []
    eta.stamp(st, ext, lines.append)
    assert st.expected_finish is None, "nothing to read before extract"
    ext.write_text(json.dumps({"id": "a", "pages": 494}) + "\n" + json.dumps({"id": "b", "pages": 10, "scan": True}))
    eta.stamp(st, ext, lines.append)
    first = st.expected_finish
    assert first is not None and set(first) == {"at", "computed_at", "basis", "minutes"}
    ext.write_text(json.dumps({"id": "a", "pages": 9000}) + "\n")
    eta.stamp(st, ext, lines.append)
    assert st.expected_finish == first, "written once"
    assert RunState.load_or_new(tmp_path / "state.json", slug="example", unit="alpha").expected_finish == first
    assert len([x for x in lines if x.startswith("[eta]")]) == 1
