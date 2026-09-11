"""Tests for bin/audit-chain-watch.py, the daily off-box chain check (ss#2500).

Everything here is driven through injected runners and uploaders. Nothing in
this file touches a seat, D1, or R2: a control's test suite that needed the
live system to run would be skipped in CI, which is how a control ends up
shipping with detection nobody has ever seen fire.

The load-bearing assertions:

  * a tail-truncated export against a real pin is a FINDING, and the same export
    with no pin is a HOLD rather than a pass;
  * a hold and a finding are distinguishable in the exit code, and a hold is
    never silently upgraded to clean by a successful archive;
  * the SQL builder cannot be escaped out of by a hostile break string, which is
    the one place seat-controlled text reaches a statement.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[2]
sys.path.insert(0, str(_OPERATOR))
sys.path.insert(0, str(_OPERATOR / "workspace_broker"))

from chain import CHAIN_COLUMNS, GENESIS, compute_row_hash  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

# The script has a dash in its name, so it is loaded by path rather than
# imported. Same shape the other bin/ script tests use.
_spec = importlib.util.spec_from_file_location(
    "audit_chain_watch", _OPERATOR / "bin" / "audit-chain-watch.py"
)
watch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# Registered BEFORE exec: @dataclass resolves its own module out of sys.modules,
# and an unregistered module makes the decorator raise at import time.
sys.modules["audit_chain_watch"] = watch
_spec.loader.exec_module(watch)


def _chain(n: int) -> list[dict]:
    rows: list[dict] = []
    prev = GENESIS
    for i in range(n):
        row = {
            "id": f"01J0000000000000000000{i:04d}",
            "ts": f"2026-08-20T00:00:{i % 60:02d}Z",
            "action_type": "TOOL_CALL_COMPLETED",
            "actor": "agent",
            "actor_role": "agent",
            "skill_name": "unit-test",
            "matter_ref": None,
            "input_digest": None,
            "output_digest": None,
            "diff_digest": None,
            "trust_ceiling": "draft",
            "metadata": None,
        }
        row["prev_hash"] = prev
        row["row_hash"] = compute_row_hash(prev, [row[c] for c in CHAIN_COLUMNS])
        prev = row["row_hash"]
        rows.append(row)
    return rows


def _pin(rows: list[dict], index: int, *, audit_rows: int | None = None) -> dict:
    return {
        "audit_head": rows[index]["row_hash"],
        "audit_rows": len(rows) if audit_rows is None else audit_rows,
        "first_seen_heartbeat_ts": "2026-08-20T00:00:00Z",
        "last_seen_heartbeat_ts": "2026-08-20T00:05:00Z",
    }


# ---------------------------------------------------------------------------
# evaluate_export
# ---------------------------------------------------------------------------


def test_truncated_tail_against_a_real_pin_is_a_finding():
    rows = _chain(30)
    pin = _pin(rows, -1)
    outcome = watch.evaluate_export("seat", rows[:-1], pin)
    assert outcome.state == watch.FINDING
    assert "truncated" in outcome.headline
    assert outcome.details["pin_verdict"] == "pin_absent"


def test_the_same_truncated_export_with_no_pin_is_a_hold_not_a_pass():
    """The negative control for the test above.

    Without a pin the export is self-consistent and there is nothing to say.
    Saying "clean" would repeat exactly the claim this issue exists to retire.
    """
    rows = _chain(30)
    outcome = watch.evaluate_export("seat", rows[:-1], None)
    assert outcome.state == watch.HOLD
    assert outcome.details["pin_verdict"] == "pin_not_supplied"


def test_a_descending_pin_is_clean():
    rows = _chain(30)
    outcome = watch.evaluate_export("seat", rows, _pin(rows, 10))
    assert outcome.state == watch.CLEAN
    assert outcome.details["pin_verdict"] == "pin_descends"


def test_a_regressed_head_is_a_finding():
    rows = _chain(30)
    outcome = watch.evaluate_export("seat", rows[:12], _pin(rows, -1))
    assert outcome.state == watch.FINDING


def test_a_shrunken_row_count_is_a_finding_even_when_the_head_still_descends():
    """The second signal, isolated.

    The pin's head is row 5 and it IS in the export, so the head check passes.
    The pin also recorded 30 rows and the export holds 10.
    """
    rows = _chain(30)
    pin = _pin(rows, 5, audit_rows=30)
    outcome = watch.evaluate_export("seat", rows[:10], pin)
    assert outcome.state == watch.FINDING
    assert "shrank" in outcome.headline


def test_a_null_row_count_does_not_manufacture_a_shrink():
    rows = _chain(30)
    pin = _pin(rows, 5, audit_rows=None)
    pin["audit_rows"] = None
    assert watch.evaluate_export("seat", rows, pin).state == watch.CLEAN


def test_a_malformed_pin_is_a_hold_not_an_accusation():
    rows = _chain(10)
    pin = _pin(rows, -1)
    pin["audit_head"] = "garbage"
    outcome = watch.evaluate_export("seat", rows, pin)
    assert outcome.state == watch.HOLD
    assert "instrument" in outcome.headline


def test_a_broken_chain_outranks_the_pin_check():
    rows = _chain(10)
    pin = _pin(rows, -1)
    rows[4]["skill_name"] = "tampered"  # breaks its own recomputation
    outcome = watch.evaluate_export("seat", rows, pin)
    assert outcome.state == watch.FINDING
    assert "does not verify" in outcome.headline


# ---------------------------------------------------------------------------
# SQL construction
# ---------------------------------------------------------------------------


def test_sql_text_cannot_be_escaped_out_of():
    hostile = "'; DROP TABLE cost_anomaly_alerts; --"
    literal = watch.sql_text(hostile)
    # No quote, no semicolon, no comment marker survives into the statement:
    # the whole value is hex between a fixed opener and a fixed closer.
    assert literal.startswith("CAST(x'") and literal.endswith("' AS TEXT)")
    body = literal[len("CAST(x'") : -len("' AS TEXT)")]
    assert all(c in "0123456789abcdef" for c in body)
    assert bytes.fromhex(body).decode("utf-8") == hostile


def test_sql_int_refuses_anything_that_is_not_an_integer():
    assert watch.sql_int(0) == "0"
    for bad in ("1", 1.0, True, None):
        with pytest.raises(TypeError):
            watch.sql_int(bad)


def test_first_result_set_raises_rather_than_returning_empty_on_a_strange_envelope():
    """ "Could not read" must never be indistinguishable from "no pin recorded"."""
    assert watch.first_result_set(json.dumps([{"results": [{"a": 1}]}])) == [{"a": 1}]
    assert watch.first_result_set(json.dumps({"results": []})) == []
    with pytest.raises(RuntimeError):
        watch.first_result_set(json.dumps("nope"))
    with pytest.raises(RuntimeError):
        watch.first_result_set(json.dumps({"results": "nope"}))


def test_the_alert_row_is_written_with_the_audit_integrity_source():
    seen: list[list[str]] = []

    def runner(cmd):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{"results": []}]), "")

    console = watch.ConsoleD1(db="test-db", runner=runner)
    console.write_alert(
        entity_id="ent-1",
        slug="seat",
        summary="seat: the ledger was truncated.",
        details={"note": "'; DROP TABLE x; --"},
    )
    sql = seen[-1][-1]
    assert "'audit_integrity'" in sql
    assert "audit_chain:" in bytes.fromhex(_first_hex(sql, 3)).decode("utf-8")
    assert "DROP TABLE x" not in sql


def _first_hex(sql: str, index: int) -> str:
    """The nth hex blob literal in a statement, for asserting on inlined values."""
    parts = sql.split("CAST(x'")
    return parts[index + 1].split("'")[0]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def test_the_archive_key_and_digest_are_reproducible(tmp_path):
    rows = _chain(5)
    uploaded: list[tuple[Path, str]] = []

    def uploader(local: Path, destination: str) -> None:
        uploaded.append((local, destination))

    first = watch.archive_export(
        "seat", rows, bucket="b", uploader=uploader, work_dir=tmp_path / "one"
    )
    second = watch.archive_export(
        "seat", rows, bucket="b", uploader=uploader, work_dir=tmp_path / "two"
    )
    assert first.key.startswith("audit/seat/") and first.key.endswith(".json.gz")
    assert uploaded[0][1] == f"s3://b/{first.key}"
    # Same rows, same bytes, same digest -- a gzip mtime would break this and
    # make every day's digest incomparable for no reason.
    assert first.sha256 == second.sha256
    # And the digest is over the bytes on the wire, so an auditor reproduces it
    # by hashing the object they downloaded.
    assert first.sha256 == __import__("hashlib").sha256(uploaded[0][0].read_bytes()).hexdigest()


def test_the_archive_key_names_the_day_the_time_and_the_chain_tip():
    """``audit/<slug>/<date>/<HHMMSS>Z-<head12>.json.gz``.

    The date is a path segment so a day's copies list together; the head rides
    in the name so a reader sees which tip a copy carries without fetching it.
    """
    when = datetime(2026, 8, 21, 13, 39, 7, tzinfo=timezone.utc)
    head = "a1b2c3d4e5f6" + "0" * 52
    assert watch.archive_key("seat", head, now=when) == (
        "audit/seat/2026-08-21/133907Z-a1b2c3d4e5f6.json.gz"
    )


def test_a_headless_export_says_nohead_rather_than_inventing_a_tip():
    """An export with no chained rows has no tip, and the key must say so."""
    when = datetime(2026, 8, 21, 8, 0, 0, tzinfo=timezone.utc)
    for head in (None, ""):
        assert watch.archive_key("seat", head, now=when) == (
            "audit/seat/2026-08-21/080000Z-nohead.json.gz"
        )


def test_two_runs_in_one_utc_day_write_two_different_keys(tmp_path):
    """The 2026-08-21 defect, as an assertion.

    The 08:00Z run wrote the day's object; the audit-7y bucket lock over the
    audit/ prefix then correctly refused the 13:39Z overwrite with
    ObjectLockedByBucketPolicy, which HELD every seat and masked a clean
    verdict. An object-locked prefix and a once-per-day key cannot both stand.
    """
    rows = _chain(4)
    uploaded: list[str] = []

    def uploader(local: Path, destination: str) -> None:
        uploaded.append(destination)

    morning = watch.archive_export(
        "seat",
        rows,
        bucket="b",
        uploader=uploader,
        work_dir=tmp_path / "am",
        now=datetime(2026, 8, 21, 8, 0, 0, tzinfo=timezone.utc),
    )
    afternoon = watch.archive_export(
        "seat",
        rows,
        bucket="b",
        uploader=uploader,
        work_dir=tmp_path / "pm",
        now=datetime(2026, 8, 21, 13, 39, 7, tzinfo=timezone.utc),
    )
    assert morning.key != afternoon.key
    assert uploaded[0] != uploaded[1]
    # Same UTC day, so they list together under one prefix.
    assert morning.key.startswith("audit/seat/2026-08-21/")
    assert afternoon.key.startswith("audit/seat/2026-08-21/")
    # Same rows, so the digest is still reproducible across the two copies.
    assert morning.sha256 == afternoon.sha256


def test_the_chain_verdict_survives_an_upload_that_raises(tmp_path, monkeypatch):
    """Verify first, archive second, and never let the copy hide the verdict.

    On 2026-08-21 the archive hold REPLACED the outcome, so a run that had just
    proven a 1,585-row chain intact reported only that an upload failed.
    """
    rows = _chain(6)
    pin = {"audit_head": rows[-1]["row_hash"], "audit_rows": len(rows)}

    class _Console:
        def newest_pin(self, slug):
            return pin

    def explode(*a, **kw):
        raise RuntimeError("An error occurred (ObjectLockedByBucketPolicy)")

    monkeypatch.setattr(watch, "archive_export", explode)
    monkeypatch.setattr(watch, "seam_client_from_env", lambda slug: _Reader(rows))

    outcome = watch.process_seat("seat", _Console(), bucket="b", archive=True)

    # The run is still a hold -- the copy is half the issue.
    assert outcome.state == watch.HOLD
    assert "ObjectLockedByBucketPolicy" in outcome.headline

    # ...and the verdict is reported, ahead of the hold line.
    text = "\n".join(watch.summary_lines([outcome], "lock note"))
    verdict_line = "chain intact, 6 chained rows"
    assert verdict_line in text
    assert text.index(verdict_line) < text.index("could not be written")
    assert outcome.details["chain_verdict"]["state"] == watch.CLEAN


class _Reader:
    def __init__(self, rows):
        self._rows = rows

    def read_all(self, table):
        return list(self._rows)


# The rule body the LIVE bucket answered with on 2026-08-21. Pinned to the
# observed wire shape, not to a shape this file invented: the previous probe
# asked S3 get-object-lock-configuration and got ObjectLockConfigurationNotFound
# even with credentials, because R2's bucket lock is a Cloudflare API resource
# and not S3 object lock at all.
_OBSERVED_RULE = {
    "id": "audit-7y",
    "enabled": True,
    "prefix": "audit/",
    "condition": {"type": "Age", "maxAgeSeconds": 220752000},
}


def _lock_body(*rules) -> dict:
    return {"success": True, "result": {"rules": list(rules)}}


def test_the_lock_probe_asks_the_r2_bucket_lock_endpoint(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)
    asked: list[str] = []

    def fetch(url):
        asked.append(url)
        return _lock_body(_OBSERVED_RULE)

    ok, note = watch.probe_bucket_lock("smd-audit-archive", fetch=fetch)
    assert ok is True
    assert asked == [
        f"https://api.cloudflare.com/client/v4/accounts/{'a' * 32}"
        "/r2/buckets/smd-audit-archive/lock"
    ]
    assert "audit-7y" in note


def test_the_observed_seven_year_age_rule_confirms_the_lock():
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(_OBSERVED_RULE))
    assert ok is True
    assert "220752000" in note


def test_an_indefinite_rule_confirms_the_lock():
    rule = dict(_OBSERVED_RULE, condition={"type": "Indefinite"})
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is True
    assert "indefinitely" in note


def test_a_date_rule_in_the_future_confirms_the_lock():
    rule = dict(_OBSERVED_RULE, condition={"type": "Date", "date": "2099-01-01T00:00:00Z"})
    ok, _ = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is True


def test_a_date_rule_already_past_does_not_confirm_the_lock():
    rule = dict(_OBSERVED_RULE, condition={"type": "Date", "date": "2020-01-01T00:00:00Z"})
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is False
    assert "has passed" in note


def test_a_disabled_rule_does_not_confirm_the_lock():
    rule = dict(_OBSERVED_RULE, enabled=False)
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is False
    assert "not enabled" in note
    assert "immutability is unproven" in note


def test_a_rule_scoped_below_the_archive_prefix_does_not_confirm_the_lock():
    """A per-seat rule locks one seat's objects and leaves every other seat's loose."""
    rule = dict(_OBSERVED_RULE, prefix="audit/ashton-price/")
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is False
    assert "does not cover" in note


def test_a_rule_covering_the_whole_bucket_does_confirm_the_lock():
    """The other side of the prefix check: broader than audit/ still covers audit/."""
    ok, _ = watch.evaluate_lock_payload(
        "smd-audit-archive", _lock_body(dict(_OBSERVED_RULE, prefix=""))
    )
    assert ok is True


def test_an_age_shorter_than_the_retention_commitment_does_not_confirm_the_lock():
    rule = dict(_OBSERVED_RULE, condition={"type": "Age", "maxAgeSeconds": 604800})
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body(rule))
    assert ok is False
    assert "short of the" in note


def test_a_success_false_body_does_not_confirm_the_lock():
    payload = {
        "success": False,
        "errors": [{"code": 10006, "message": "Bucket not found"}],
    }
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", payload)
    assert ok is False
    assert "success=false" in note
    assert "Bucket not found" in note


def test_no_rules_at_all_does_not_confirm_the_lock():
    ok, note = watch.evaluate_lock_payload("smd-audit-archive", _lock_body())
    assert ok is False
    assert "no lock rules at all" in note


def test_an_api_error_is_reported_verbatim_and_does_not_confirm_the_lock(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a" * 32)

    def fetch(url):
        raise RuntimeError("HTTP Error 403: Forbidden")

    ok, note = watch.probe_bucket_lock("smd-audit-archive", fetch=fetch)
    assert ok is False
    assert "HTTP Error 403: Forbidden" in note
    assert "immutability is unproven" in note


def test_an_unsafe_account_id_never_reaches_a_url(monkeypatch):
    """The only interpolation into an api.cloudflare.com URL is charset-gated."""
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc/../../evil")

    def fetch(url):  # pragma: no cover - reaching this is the failure
        raise AssertionError(f"a URL was built from an unsafe account id: {url}")

    ok, note = watch.probe_bucket_lock("smd-audit-archive", fetch=fetch)
    assert ok is False
    assert "unsafe CLOUDFLARE_ACCOUNT_ID" in note


# ---------------------------------------------------------------------------
# Exit contract
# ---------------------------------------------------------------------------


def _outcome(state: str) -> "watch.SeatOutcome":
    return watch.SeatOutcome("seat", state, "headline", {})


def test_exit_codes_are_the_control_probes_tri_state():
    assert watch.resolve_exit([_outcome(watch.CLEAN)], [], True) == watch.EXIT_CLEAN
    assert watch.resolve_exit([_outcome(watch.HOLD)], [], True) == watch.EXIT_HOLD
    assert watch.resolve_exit([_outcome(watch.FINDING)], [], True) == watch.EXIT_FINDING
    # A finding outranks a hold: it is the louder fact and it is already on the
    # alert sink. The hold still shows in the report.
    assert (
        watch.resolve_exit([_outcome(watch.FINDING), _outcome(watch.HOLD)], [], True)
        == watch.EXIT_FINDING
    )
    # An unlocked archive prefix reddens an otherwise clean run. An off-box copy
    # anyone can delete is a backup, not a record.
    assert watch.resolve_exit([_outcome(watch.CLEAN)], [], False) == watch.EXIT_HOLD
    # So does a finding that could not be written to the sink.
    assert watch.resolve_exit([_outcome(watch.CLEAN)], ["could not write"], True) == watch.EXIT_HOLD


def test_zero_seats_is_a_hold(monkeypatch, tmp_path):
    """A run that measured nothing must never exit 0.

    This is the send-reconciler shape: the job was green for weeks while
    scanning an empty set.
    """
    monkeypatch.setattr(watch, "_REPO", tmp_path)
    assert watch.main([]) == watch.EXIT_HOLD


def test_authored_seats_skips_template_dirs(tmp_path):
    base = tmp_path / "operator" / "customers"
    for name in ("_template", "_hosted-template", "real-seat", "no-yaml"):
        (base / name).mkdir(parents=True)
    for name in ("_template", "real-seat"):
        (base / name / "customer.yaml").write_text("slug: x\n")
    assert watch.authored_seats(tmp_path) == ["real-seat"]


# ---------------------------------------------------------------------------
# Seat roster: authored config is not the same question as "the seat exists"
# ---------------------------------------------------------------------------


def test_a_seat_that_is_both_authored_and_provisioned_is_probed():
    roster = watch.partition_seats(["live-seat"], ["live-seat"])
    assert roster.probed == ["live-seat"]
    assert roster.skipped == []
    assert roster.orphaned == []


def test_an_authored_seat_with_no_fleet_status_row_is_skipped_by_name():
    """The pilot-law shape: a config authored 2026-06-05 and never provisioned.

    Enumerating from the filesystem made the first live run red forever on a
    connection error to a machine that was never stood up.
    """
    roster = watch.partition_seats(["live-seat", "never-provisioned"], ["live-seat"])
    assert roster.probed == ["live-seat"]
    assert roster.skipped == ["never-provisioned"]

    notices = watch.roster_notices(roster)
    assert [(n.slug, n.state) for n in notices] == [("never-provisioned", watch.SKIP)]
    # Named in the report, never silent (#2366): the denominator has to be visible.
    line = "\n".join(watch.summary_lines(notices, "lock note"))
    assert "SKIP" in line
    assert "never-provisioned: authored but never provisioned (no fleet_status row)." in line


def test_a_skipped_seat_does_not_redden_the_run():
    """A seat that does not exist has no audit record to be wrong about."""
    skip = watch.SeatOutcome("never-provisioned", watch.SKIP, "headline", {})
    assert watch.resolve_exit([_outcome(watch.CLEAN), skip], [], True) == watch.EXIT_CLEAN


def test_a_provisioned_seat_with_no_authored_config_is_a_hold():
    """Drift the other way, and that direction IS a finding worth waking up to.

    A fleet_status row with no customer.yaml means a live seat's ledger is
    outside this control's reach entirely. Silently narrowing to the
    intersection would hide exactly that.
    """
    roster = watch.partition_seats(["live-seat"], ["live-seat", "unauthored-seat"])
    assert roster.probed == ["live-seat"]
    assert roster.orphaned == ["unauthored-seat"]

    notices = watch.roster_notices(roster)
    assert [(n.slug, n.state) for n in notices] == [("unauthored-seat", watch.HOLD)]
    assert watch.resolve_exit(notices, [], True) == watch.EXIT_HOLD


def test_main_holds_when_the_seat_roster_cannot_be_read(monkeypatch, tmp_path, capsys):
    """An unreadable D1 must not read as "no seats", which would be a quiet green."""
    base = tmp_path / "operator" / "customers" / "live-seat"
    base.mkdir(parents=True)
    (base / "customer.yaml").write_text("slug: live-seat\n")
    monkeypatch.setattr(watch, "_REPO", tmp_path)

    def explode(self):
        raise RuntimeError("d1 execute failed: unauthorized")

    monkeypatch.setattr(watch.ConsoleD1, "provisioned_slugs", explode)
    assert watch.main([]) == watch.EXIT_HOLD
    assert "seat roster could not be read" in capsys.readouterr().out


def test_main_holds_when_no_authored_seat_is_provisioned(monkeypatch, tmp_path, capsys):
    base = tmp_path / "operator" / "customers" / "never-provisioned"
    base.mkdir(parents=True)
    (base / "customer.yaml").write_text("slug: never-provisioned\n")
    monkeypatch.setattr(watch, "_REPO", tmp_path)
    monkeypatch.setattr(watch.ConsoleD1, "provisioned_slugs", lambda self: [])
    assert watch.main([]) == watch.EXIT_HOLD
    assert "measured nothing" in capsys.readouterr().out
