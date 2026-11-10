"""Each lane's record of an ORDINARY completed job, compared with goldens.

``goldens/lane_endings/<case>.json`` holds what each lane sent its broker and
wrote to its state file (the wake text included) for one ordinary job, captured
before the 2026-11-10 money fixes. A change to how a lane runs or records a job
must leave these equal, except the paths ``allowed-changes.json`` names for
that case. Each case runs the lane's real class through the same test kit its
own suite uses. Set ``JOBS_WRITE_LANE_GOLDENS=1`` to rewrite them (and then
read the diff).
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import pytest

GOLDENS = Path(__file__).resolve().parent / "goldens" / "lane_endings"
ALLOWED = json.loads((GOLDENS / "allowed-changes.json").read_text(encoding="utf-8"))
#: A records review id is random per run (``records_review``).
_RANDOM_ID = re.compile(r"\b[0-9a-f]{24}\b")


def _normal(tmp_path: Path, doc: Any) -> Any:
    text = json.dumps(doc, sort_keys=True, default=str)
    return json.loads(_RANDOM_ID.sub("<ID>", text.replace(str(tmp_path), "<TMP>")))


def _diffs(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else str(k)
            out += [sub] if (k in a) != (k in b) else _diffs(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diffs(x, y, f"{path}.{i}" if path else str(i))]
    return [] if a == b else [path or "<root>"]


def _demand(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_demand_lane as t

    lane, client = t._lane(tmp_path)
    jid = t._submit(lane, client)
    assert lane.tick() == "delivered"
    return client.records, lane._daemon_state(jid)


def _drafting(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_drafting_lane as t

    lane, client = t._lane(tmp_path)
    jid = t._submit(lane, client)
    assert lane.tick() == "delivered"
    return client.records, lane._daemon_state(jid)


def _litigation(tmp_path: Path, mp: Any, held: bool = False) -> tuple[Any, Any]:
    import test_litigation_lane as t

    lane, client = t._lane(tmp_path, script=t.HELD) if held else t._lane(tmp_path)
    jid = t._submit(lane, client)
    assert lane.tick() == ("held" if held else "delivered")
    return client.records, lane._daemon_state(jid)


def _casefile(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_casefile_lane as t

    lane, client = t._lane(tmp_path)
    jid = t._submit(lane, client)
    tick = lane.tick()
    return {"tick": tick, "records": client.records}, lane._daemon_state(jid)


def _negotiation(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_negotiation_lane as t

    notices = [{"matter_id": "m", "matter_number": "1", "status": "entered", "text": "x"}] * 2
    lane, client = t._lane(tmp_path, notices, [t.N1, t.N2])
    assert lane.tick() == "delivered"
    return client.records, lane._daemon_state(t.JOB)


def _records(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_records_lane as t

    seat = inspect.unwrap(t.seat)(tmp_path, mp)
    lane, client, _ = t._lane(tmp_path, seat, [])
    t._submit(lane, client)
    assert lane.tick() == "review"
    return client.records, lane._daemon_state(t.JOB)


def _medchron(tmp_path: Path, mp: Any) -> tuple[Any, Any]:
    import test_upload_daemon as t

    d, broker = t._daemon(tmp_path)
    t._submit(d, broker, "01A")
    assert d.tick() == "delivered"
    return broker.records, d._daemon_state("01A")


CASES: dict[str, Callable[[Path, Any], tuple[Any, Any]]] = {
    "casefile": _casefile,
    "demand": _demand,
    "drafting": _drafting,
    "litigation": _litigation,
    "litigation_held": lambda p, mp: _litigation(p, mp, held=True),
    "medchron": _medchron,
    "negotiation": _negotiation,
    "records": _records,
}


def test_every_golden_has_a_case_and_an_allowed_list():
    files = {p.stem for p in GOLDENS.glob("*.json")} - {"allowed-changes"}
    assert files == set(CASES) == set(ALLOWED) - {"_why"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_ordinary_job_records_what_the_golden_holds(tmp_path, monkeypatch, case):
    """MUTATION: change what any lane records for an ordinary job and its case fails."""
    records, state = CASES[case](tmp_path, monkeypatch)
    got = _normal(tmp_path, {"records": records, "state": state})
    path = GOLDENS / f"{case}.json"
    if os.environ.get("JOBS_WRITE_LANE_GOLDENS"):
        path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    want = _normal(tmp_path, json.loads(path.read_text(encoding="utf-8")))
    assert [d for d in _diffs(want, got) if d not in ALLOWED[case]] == []
