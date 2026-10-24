"""The heartbeat's read of the seat-spec verdict (shared.spec_check).

Silence must never read as health: a stale or missing state file on a Machine
past its first half hour ships verdict ``stale``, an unreadable one
``unreadable``, and only the first half hour of uptime is "nothing to say yet".
"""

from __future__ import annotations

import json
from pathlib import Path

from shared import heartbeat as hb
from shared.spec_check import STALE_SECONDS, read_spec

NOW = 1_800_000_000.0


def _state(path: Path, **over) -> Path:
    doc = {
        "v": 1,
        "verdict": "verified",
        "checked_at": "2027-01-15T08:00:00Z",
        "spec_sha256": "a" * 64,
        "key_id": "b" * 16,
        "compiled_from": "c" * 40,
        "unmet_count": 0,
        "unmet": [],
        "customer_yaml_match": True,
    }
    doc.update(over)
    path.write_text(json.dumps(doc))
    return path


def _now_for(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_a_fresh_verdict_ships_as_written(tmp_path):
    p = _state(tmp_path / "state.json")
    got = read_spec(p, now_fn=lambda: _now_for("2027-01-15T08:01:00Z"))
    assert got is not None
    assert got["verdict"] == "verified" and got["spec_sha256"] == "a" * 64
    assert got["customer_yaml_match"] is True


def test_a_stale_verdict_is_a_stale_verdict(tmp_path):
    p = _state(tmp_path / "state.json")
    got = read_spec(p, now_fn=lambda: _now_for("2027-01-15T08:00:00Z") + STALE_SECONDS + 1)
    assert got is not None and got["verdict"] == "stale" and got["spec_sha256"] is None


def test_no_file_is_nothing_to_say_early_and_stale_later(tmp_path):
    missing = tmp_path / "nope.json"
    assert read_spec(missing, now_fn=lambda: NOW, uptime_fn=lambda: 60.0) is None
    got = read_spec(missing, now_fn=lambda: NOW, uptime_fn=lambda: STALE_SECONDS + 1.0)
    assert got is not None and got["verdict"] == "stale"


def test_an_unreadable_or_unknown_verdict_is_unreadable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_spec(bad, now_fn=lambda: NOW)["verdict"] == "unreadable"  # type: ignore[index]
    p = _state(tmp_path / "state.json", verdict="trust-me")
    got = read_spec(p, now_fn=lambda: _now_for("2027-01-15T08:00:00Z"))
    assert got is not None and got["verdict"] == "unreadable"


def test_malformed_fields_are_dropped_not_shipped(tmp_path):
    p = _state(
        tmp_path / "state.json",
        spec_sha256="short",
        unmet=["skill:ok", "bad id with spaces"],
        unmet_count=2,
    )
    got = read_spec(p, now_fn=lambda: _now_for("2027-01-15T08:00:00Z"))
    assert got is not None
    assert got["spec_sha256"] is None
    assert got["unmet"] == ["skill:ok", "malformed-requirement"]


_BASE = {
    "heartbeat_ts": "2027-01-15T08:00:00Z",
    "last_audit_ts": None,
    "last_skill_ts": None,
    "uptime_seconds": None,
    "version": None,
}


def test_the_heartbeat_payload_carries_spec_only_when_there_is_one():
    assert "spec" not in hb.build_payload(**_BASE)
    body = hb.build_payload(**_BASE, spec={"v": 1, "verdict": "absent"})
    assert body["spec"] == {"v": 1, "verdict": "absent"}
