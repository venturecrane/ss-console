"""Every distinct DELIVER wake a job writes is admitted under its own id.

The 2026-11-03 drafting job on the A&P seat: attempt 1 ended failed and its
wake was admitted as ``drafting-<job>``. SMD resumed it, attempt 2 delivered,
and its wake (new text) was posted under the SAME id. The gateway's durable
admission refuses a known id carrying different text (``admission_conflict``,
503), so all five posts failed, ``wake_lost`` paged, and the attorney's reply
went out only after a hand ``jobs rewake``.

The gate below models that admission: the same id with the same text is a
duplicate (202, not run again); the same id with other text is refused.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from jobs import rewake
from jobs.daemon import Daemon
from test_upload_daemon import _submit, _wake_daemon

FAIL_THEN_DELIVER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n == 1:
    sys.exit(3)  # no verdict: the job fails; SMD is paged and no wake is owed
print(json.dumps([{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 1.0,
                   "pages": 3, "documents": 1, "folder_id": "folder-9",
                   "files": [{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}],
                   "proof": {"readback": "confirmed", "files": [{"name": "x.docx", "file_id": "f1", "size": 10, "sha256": "a" * 64}], "review": "passed", "review_cents": 0}}]))
"""


class _AdmissionGate:
    """A handoff gate with the gateway's durable admission: keyed on the id."""

    def __init__(self):
        self.admitted: dict[str, str] = {}
        self.posts: list[tuple[str, int]] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                hid, task = body["handoff_id"], body["task"]
                known = outer.admitted.get(hid)
                if known is None:
                    outer.admitted[hid] = task
                    status, raw = 202, b'{"accepted": true}'
                elif known == task:
                    status, raw = 202, b'{"accepted": true, "duplicate": true}'
                else:
                    status, raw = 503, b'{"error": "Durable admission failed: admission_conflict"}'
                outer.posts.append((hid, status))
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self):
        self._srv.shutdown()


def _failed_then_resumed_and_delivered(tmp_path):
    """Since 2026-11-06 a failed chronology pages SMD once and posts no wake
    (the firm is told nothing). A job that failed on an OLDER image did post
    one, under the bare id, so the gate starts with that admission on record:
    the resumed job's delivered wake must still be admitted beside it."""
    gate = _AdmissionGate()
    d, broker = _wake_daemon(tmp_path, gate.url, script=FAIL_THEN_DELIVER)
    _submit(d, broker, "01A")
    assert d.tick() == "failed"
    d.tick()  # nothing is owed: a failure wakes no turn
    assert gate.posts == []
    assert [k.split(":")[0] for k in d._daemon_state("01A")["proof_alerts"]] == ["job_stopped"]  # SMD paged once
    gate.admitted["medchron-01A"] = "Outcome: failed. (posted by an image before 2026-11-06)"
    (d.queue / ".resume-01A.json").write_text(json.dumps({"job_id": "01A", "reason": "fixed", "redo": []}))
    assert d.tick() == "delivered"
    d.tick()  # the delivered wake
    return gate, d


def test_a_resumed_jobs_delivered_wake_is_admitted_after_its_failure(tmp_path):
    gate, d = _failed_then_resumed_and_delivered(tmp_path)
    gate.close()
    assert gate.posts == [("medchron-01A-w2", 202)]  # the bare id would be refused: other text
    assert "Outcome: delivered." in gate.admitted["medchron-01A-w2"]
    st = d._daemon_state("01A")
    assert st["wake"]["pending"] is False and st["wake"]["outcome"] == "delivered"
    assert st["wake_seq"] == 2 and d.wakes_failed == 0


def test_a_re_post_of_the_same_wake_keeps_its_id_and_dedupes(tmp_path):
    """A wake the gateway admitted but the daemon never marked consumed (a crash
    between the answer and the write) is posted again under the SAME id, so the
    gateway drops it instead of running a second deliver turn."""
    gate, d = _failed_then_resumed_and_delivered(tmp_path)
    wake = d._daemon_state("01A")["wake"]
    d._write_state("01A", wake={**wake, "pending": True, "sent_at": None, "outcome": None})
    d.tick()
    gate.close()
    assert gate.posts[-1] == ("medchron-01A-w2", 202)
    assert list(gate.admitted) == ["medchron-01A", "medchron-01A-w2"]


def test_a_rewake_of_the_second_wake_does_not_collide_with_the_first(tmp_path):
    gate, d = _failed_then_resumed_and_delivered(tmp_path)
    d._write_state("01A", reply_watch=None)
    assert rewake.rewake(d.run_dir, "medchron", "01A", "scott", "held", False, clock=d.clock) == 0
    d.tick()
    gate.close()
    assert gate.posts[-1] == ("medchron-01A-w2-r1", 202)
    assert gate.admitted["medchron-01A-w2-r1"] == gate.admitted["medchron-01A-w2"]


@pytest.mark.parametrize(
    "state, seq",
    [
        ({}, 1),  # a new job: its first wake keeps the bare id
        ({"resumes": 1}, 2),  # resumed before the count existed: its first wake went bare
        ({"delivered_by": "smd", "attempts": 1}, 2),  # SMD's record over a run that failed
        ({"delivered_by": "smd"}, 1),  # a note job: never ran, its one wake
        ({"wake_seq": 3, "resumes": 9}, 4),  # the count wins once it exists
    ],
)
def test_a_wake_is_numbered_past_every_wake_the_job_already_posted(state, seq):
    wake = {"task": "t"}
    assert rewake.stamp(state, wake) is True and wake["seq"] == seq
    assert rewake.stamp(state, wake) is False and wake["seq"] == seq  # numbered once


def test_every_waking_lane_posts_through_the_numbered_dispatch():
    """drafting, demand, negotiation, litigation, casefile, note and the
    chronology lane all wake through ``Daemon._dispatch_wakes``; the records
    lane wakes nothing (its review watch replies instead)."""
    from jobs.casefile_lane import CasefileLane
    from jobs.demand_lane import DemandLane
    from jobs.drafting_lane import DraftingLane
    from jobs.litigation_lane import LitigationLane
    from jobs.negotiation_lane import NegotiationLane
    from jobs.note_lane import NoteLane
    from jobs.medchron.lane import MedchronLane
    from jobs.records_lane import RecordsLane

    for cls in (DraftingLane, DemandLane, NegotiationLane, LitigationLane, CasefileLane, NoteLane, MedchronLane):
        assert cls._dispatch_wakes is Daemon._dispatch_wakes, cls.__name__
        assert cls.dispatch_wakes is Daemon.dispatch_wakes, cls.__name__
    assert RecordsLane.dispatch_wakes is not Daemon.dispatch_wakes
