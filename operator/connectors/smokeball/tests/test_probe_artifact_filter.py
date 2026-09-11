"""Probe-artifact exclusion on list_tasks (ss #2403).

A rehearsal probe task once outlived its test and became a live chase's
tracking anchor. Marked rows (``[SMD-PROBE ...]`` at the start of the subject,
provenance stamp aside) are dropped by default and the drop is COUNTED —
a filter that can hide rows silently is a suppression channel. The match is
position-anchored: a mid-subject occurrence is NOT a probe, so real work
cannot be hidden by quoting the marker.
"""

from __future__ import annotations


from smokeball_connector import server


class _TasksClient:
    def __init__(self, payload) -> None:
        self._payload = payload

    def get(self, path: str, **params):
        if path == "/tasks":
            return self._payload
        return {}


def _wire(monkeypatch, payload):
    monkeypatch.setattr(server, "_get_client", lambda: _TasksClient(payload))


def _task(subject: str, task_id: str = "t-1"):
    return {"id": task_id, "subject": subject}


def test_probe_rows_dropped_and_counted(monkeypatch):
    _wire(
        monkeypatch,
        {
            "value": [
                _task("[Operator] [SMD-PROBE 2026-08-18T14:00Z] drafting prove-out", "t-p"),
                _task("[Operator] Client verification outstanding - FROG Set One", "t-r"),
            ]
        },
    )
    resp = server.list_tasks()
    assert [t["id"] for t in resp["value"]] == ["t-r"]
    assert resp["probeArtifactsExcluded"] == 1


def test_unstamped_probe_subject_also_dropped(monkeypatch):
    _wire(monkeypatch, {"value": [_task("[SMD-PROBE 2026-08-18T14:00Z] x", "t-p")]})
    resp = server.list_tasks()
    assert resp["value"] == []
    assert resp["probeArtifactsExcluded"] == 1


def test_mid_subject_marker_is_not_a_probe(monkeypatch):
    # Falsifier: only a position-anchored marker matches — a real task that
    # QUOTES the marker must not be hidden.
    _wire(monkeypatch, {"value": [_task("Review the [SMD-PROBE] cleanup contract", "t-r")]})
    resp = server.list_tasks()
    assert [t["id"] for t in resp["value"]] == ["t-r"]
    assert "probeArtifactsExcluded" not in resp


def test_opt_in_returns_probe_rows(monkeypatch):
    _wire(monkeypatch, {"value": [_task("[SMD-PROBE 2026-08-18T14:00Z] x", "t-p")]})
    resp = server.list_tasks(include_probe_artifacts=True)
    assert [t["id"] for t in resp["value"]] == ["t-p"]
    assert "probeArtifactsExcluded" not in resp


def test_bare_list_envelope_filtered(monkeypatch):
    _wire(
        monkeypatch,
        [
            _task("[SMD-PROBE 2026-08-18T14:00Z] x", "t-p"),
            _task("Real task", "t-r"),
        ],
    )
    resp = server.list_tasks()
    assert [t["id"] for t in resp] == ["t-r"]


def test_no_probes_leaves_envelope_untouched(monkeypatch):
    _wire(monkeypatch, {"value": [_task("Real task", "t-r")]})
    resp = server.list_tasks()
    assert [t["id"] for t in resp["value"]] == ["t-r"]
    assert "probeArtifactsExcluded" not in resp
