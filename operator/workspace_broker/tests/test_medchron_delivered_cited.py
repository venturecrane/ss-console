"""An update carries the delivered rows' cited sources (2026-11-06).

A delivered job's dir is wiped 72 hours after delivery, and an update read the
cited sources it must not file again from that dir. The delivered row's
``delivery.cited_sources`` outlives it: the broker puts every delivered row's
record on the update's queued envelope. Synthetic names, ids and hashes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker.medchron_ledger import work_digest

from test_medchron_identity import ROOT, call, run_envelope, update_envelope, verbs  # noqa: F401 - verbs is a pytest fixture, used by name

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _deliver(v, job_id, cited):
    call(v, "medchron_job_record", peer_uid=ROOT, job_id=job_id, state="running", fields={})
    call(
        v,
        "medchron_job_record",
        peer_uid=ROOT,
        job_id=job_id,
        state="delivered",
        fields={
            "covered": {"covered": ["d-1"], "uncovered": [], "pulled": 1},
            "delivery": {"folder_id": "f-1", "cited_sources": cited},
        },
    )


def test_an_update_is_queued_with_every_delivered_rows_cited_sources(verbs):
    v, _db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    _deliver(v, first["job_id"], {"sample": {"d-1": SHA_A, "d-2": SHA_B}})
    second = call(v, "medchron_job_submit", envelope=update_envelope(ids=("d-3",)))
    assert second["accepted"], second
    _deliver(v, second["job_id"], {"sample": {"d-3": SHA_C, "d-2": "not a hash"}})
    third = call(v, "medchron_job_submit", envelope=update_envelope(ids=("d-4",)))
    assert third["accepted"], third
    queued = json.loads((queue / f"{third['job_id']}.json").read_text())
    assert queued["delivered_cited"] == {"sample": {"d-1": SHA_A, "d-2": SHA_B, "d-3": SHA_C}}
    # Not part of the work: a resubmission is still the same job.
    assert work_digest(queued) == work_digest({k: v for k, v in queued.items() if k != "delivered_cited"})


def test_a_run_and_a_matter_with_no_record_carry_none(verbs):
    v, _db, queue = verbs
    first = call(v, "medchron_job_submit", envelope=run_envelope())
    assert "delivered_cited" not in json.loads((queue / f"{first['job_id']}.json").read_text())
    _deliver(v, first["job_id"], {})
    second = call(v, "medchron_job_submit", envelope=update_envelope())
    assert second["accepted"], second
    assert "delivered_cited" not in json.loads((queue / f"{second['job_id']}.json").read_text())
