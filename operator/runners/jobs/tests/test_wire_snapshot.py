"""The runner-to-broker wire, frozen: every verb the jobs runner sends.

``goldens/wire.json`` (``wire_kit.py`` says its three parts) is built from
running code only:

* each job host matrix cell checks its own LIVE traffic against its
  ``by_cell`` row (``test_host_matrix_goldens.py``); this file checks that the
  rows are exactly the matrix's cells, so no row outlives its cell;
* the verbs no lane host sends (the CLIs: backfills, credits, library setup and
  edit hand-offs, notes, attachments) are called here on the real broker, with
  no payload and with a known job id, and compared BY VALUE (``direct``);
* ``verbs``, the per-verb union the wire census reads, must equal the union of
  the two.

``JOBS_WRITE_WIRE=1`` rewrites the direct part and the union (the cells' rows
are written by the matrix under the same switch; run it first).
``JOBS_GOLDENS_OUT=<dir>`` writes them to ``<dir>/wire.json`` instead (the
integrity job, ``operator/bin/goldens/golden-integrity.py``).
``operator/bin/goldens/wire-census.py`` (a CI step) checks that every
``"action": "<verb>"`` the runner's source names is in this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from host_matrix_cells import CELLS
from host_matrix_kit import OPERATOR, secret_names
from host_matrix_normal import Normal
from wire_kit import locked, read, target, union

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


def direct(tmp_path: Path, monkeypatch: Any) -> dict[str, list[dict[str, Any]]]:
    """Each direct verb on the real broker, request and reply by value (normalised)."""
    from bin.lib.golden_broker import build_broker
    from workspace_broker.canon import canonical
    from workspace_broker.request_errors import error_response_for

    from jobs import job_lanes

    monkeypatch.setenv("SMD_MATTER_FILE_REVIEW_DIR", str(tmp_path / "review"))
    queues = {lane: tmp_path / "run" / job_lanes.LANE_QUEUE_DIR[lane] for lane in job_lanes.QUEUED_LANES}
    b = build_broker(tmp_path, tmp_path / "audit" / "audit.db", queues)
    norm = Normal([tmp_path], secret_names())
    known = b.medchron._db.submit(
        {"matter": {"id": "m-1", "number": "2026-PI-102", "title": ""}, "units": [], "incident": {}, "cap_usd": 1.0},
        remaining=1,
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for verb in DIRECT:
        for payload in ({}, {"job_id": known}):
            request = {"action": verb, **payload}
            try:
                reply = b.handle(request, 1, 0)
            except Exception as exc:  # noqa: BLE001 - the server's own mapping of every exception
                reply = error_response_for(exc)
            out.setdefault(verb, []).append(
                norm({"request": request, "reply": json.loads(canonical(reply))}),
            )
    return out


def test_the_runner_broker_wire_is_frozen(tmp_path, monkeypatch):
    """MUTATION: rename a direct verb, change any value a direct verb answers,
    leave a row for a cell the matrix no longer has, or let the union drift
    from its parts, and this fails."""
    got = direct(tmp_path, monkeypatch)
    dest = target()
    if dest is not None:
        with locked(dest) as doc:
            doc["direct"] = got
            doc["verbs"] = union(doc.get("by_cell", {}), got)
        return
    want = read()
    assert sorted(want["by_cell"]) == sorted(CELLS), "wire.json's cell rows are not exactly the matrix's cells"
    changed = sorted(v for v in set(got) | set(want["direct"]) if got.get(v) != want["direct"].get(v))
    assert changed == [], f"a direct verb's request or reply changed: {changed}"
    assert want["verbs"] == union(want["by_cell"], want["direct"]), "the verb union does not match its parts"
    assert OPERATOR.is_dir()
