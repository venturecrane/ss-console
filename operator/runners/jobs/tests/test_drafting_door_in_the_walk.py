"""The walk closes the request door before the first stage that reads the
request, and from then on reads the words added before it closed."""

from __future__ import annotations

import inspect
import json

from drafting_testkit import envelope, make_inputs, make_job, seat_with, standard_docs
from jobs import request_additions as ra
from jobs.drafting.revise.run import ReviseRun
from jobs.drafting.run import DraftingRun

WORDS = "All appropriate topics, including those listed."


def _run(tmp_path, pricing_path):
    inputs = make_inputs(tmp_path / "inputs")
    jd = make_job(tmp_path / "job", cls="discovery_set")
    (jd / "envelope.json").write_text(json.dumps(envelope(cls="discovery_set")))
    seat = seat_with(standard_docs())
    return DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing_path),
        seat_factory=lambda: seat,
        client=object(),
        log=lambda _m: None,
        readback_pause=0.0,
    )


def test_the_run_reads_the_words_added_after_it_loaded(tmp_path, pricing_path) -> None:
    r = _run(tmp_path, pricing_path)
    doc = json.loads((r.job.job_dir / "job.json").read_text())
    doc["request_text"] += f"\n\n{WORDS}"
    (r.job.job_dir / "job.json").write_text(json.dumps(doc))
    assert WORDS not in r.job.request_text
    r._close_request()
    assert r.job.request_text.endswith(WORDS)
    assert (r.job.job_dir / ra.DOOR).is_file()


def test_every_walk_closes_the_door_before_the_request_is_read() -> None:
    """FALSIFIER: drop a call and a stage reads a request the daemon may still
    be writing to, and the attorney is told his words are in a draft they are
    not in."""
    fresh = inspect.getsource(DraftingRun._walk)
    assert fresh.index("self._close_request()") < fresh.index("self._place_finish()")
    assert fresh.rindex("self._close_request()") < fresh.index("self._finish(digest)")
    assert fresh.count("self._close_request()") == 2
    revise = inspect.getsource(ReviseRun._walk)
    assert revise.index("self._close_request()") < revise.index("self._estimate_revise")
