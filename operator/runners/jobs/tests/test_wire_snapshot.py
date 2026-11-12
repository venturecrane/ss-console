"""The runner-to-broker wire, frozen: every verb the jobs runner sends, with
the shape of each request it sends and each reply it gets back.

``goldens/wire.json`` holds, per verb, the set of request shapes and reply
shapes (keys and value types, recursively). They come from two places, both
running code: the job host matrix (``goldens/host_matrix/``, every request a
real lane sent the real broker over its socket) and, for the verbs no lane
host sends (the CLIs: backfills, credits, library setup and edit hand-offs,
notes, attachments), a call of each verb on the real broker as root, with no
payload and with a known job id.

Any change to a verb, a request shape or a reply shape fails here until
``goldens/wire.json`` is regenerated in the same PR (``JOBS_WRITE_WIRE=1``) and
the diff is read. ``operator/bin/goldens/wire-census.py`` (a CI step) checks
that every ``"action": "<verb>"`` the runner's source names is in this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from host_matrix_kit import GOLDENS as HOST_GOLDENS, OPERATOR

WIRE = Path(__file__).resolve().parent / "goldens" / "wire.json"
#: Runner verbs no lane host sends; each is called directly on the broker.
DIRECT = (
    "attachment_put",
    "drafting_job_resume",
    "job_note_send",
    "library_edit_status",
    "library_edit_submit",
    "library_setup_accept_status",
    "library_setup_accept_submit",
    "medchron_allowance_credit",
    "medchron_covered_recompute",
    "medchron_job_list",
    "medchron_job_resume",
    "medchron_units_backfill",
    "note_preview",
    "note_record",
)


def shape(v: Any) -> Any:
    """Keys and value types, recursively; a list is the union of its items' shapes."""
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        shapes = sorted({json.dumps(shape(x), sort_keys=True) for x in v})
        return [json.loads(s) for s in shapes]
    if isinstance(v, bool):
        return "bool"
    if v is None:
        return "null"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def _add(out: dict[str, Any], action: str, request: dict[str, Any], reply: dict[str, Any]) -> None:
    row = out.setdefault(action, {"requests": set(), "replies": set()})
    row["requests"].add(json.dumps(shape(request), sort_keys=True))
    row["replies"].add(json.dumps(shape(reply), sort_keys=True))


def _from_host_matrix(out: dict[str, Any]) -> None:
    for p in sorted(HOST_GOLDENS.glob("*__*.json")):
        for step in json.loads(p.read_text(encoding="utf-8")):
            for w in step["wire"]:
                _add(out, w["request"]["action"], w["request"], w["reply"])


def _direct(out: dict[str, Any], tmp_path: Path, monkeypatch: Any) -> None:
    from bin.lib.golden_broker import build_broker
    from workspace_broker.canon import canonical
    from workspace_broker.request_errors import error_response_for

    from jobs import job_lanes

    monkeypatch.setenv("SMD_MATTER_FILE_REVIEW_DIR", str(tmp_path / "review"))
    queues = {lane: tmp_path / "run" / job_lanes.LANE_QUEUE_DIR[lane] for lane in job_lanes.QUEUED_LANES}
    b = build_broker(tmp_path, tmp_path / "audit" / "audit.db", queues)
    known = b.medchron._db.submit(
        {"matter": {"id": "m-1", "number": "2026-PI-102", "title": ""}, "units": [], "incident": {}, "cap_usd": 1.0},
        remaining=1,
    )
    for verb in DIRECT:
        for payload in ({}, {"job_id": known}):
            request = {"action": verb, **payload}
            try:
                reply = b.handle(request, 1, 0)
            except Exception as exc:  # noqa: BLE001 - the server's own mapping of every exception
                reply = error_response_for(exc)
            _add(out, verb, request, json.loads(canonical(reply)))


def snapshot(tmp_path: Path, monkeypatch: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _from_host_matrix(out)
    _direct(out, tmp_path, monkeypatch)
    return {
        verb: {
            "requests": [json.loads(s) for s in sorted(r["requests"])],
            "replies": [json.loads(s) for s in sorted(r["replies"])],
        }
        for verb, r in sorted(out.items())
    }


def test_the_runner_broker_wire_is_frozen(tmp_path, monkeypatch):
    """MUTATION: rename a verb, add or drop a request key, or change a reply's shape, and this fails."""
    got = snapshot(tmp_path, monkeypatch)
    if os.environ.get("JOBS_WRITE_WIRE"):
        WIRE.write_text(json.dumps({"verbs": got}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(WIRE.read_text(encoding="utf-8"))["verbs"]
    assert sorted(got) == sorted(want), "a verb was added to or removed from the runner's wire"
    changed = [v for v in got if got[v] != want[v]]
    assert changed == [], f"request or reply shapes changed for {changed}"
    assert OPERATOR.is_dir()
