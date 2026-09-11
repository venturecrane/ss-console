"""Tests for the AgentMail lists check (check-agentmail-lists.py).

The failure class it exists for: a rostered recipient on a send-block list (or
excluded by a non-empty send-allow) is SILENTLY unreachable -- the Operator
"sends", the vendor drops it, nothing records a failure. Both of the control's
own failure modes are pinned: reading a block list as empty (an undocumented
entry shape guessed at), and paging on an empty vendor state.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BIN / "lib"))

_spec = importlib.util.spec_from_file_location("check_agentmail_lists", _BIN / "check-agentmail-lists.py")
lists = importlib.util.module_from_spec(_spec)
sys.modules["check_agentmail_lists"] = lists
_spec.loader.exec_module(lists)


# ---------------------------------------------------------------------------
# roster extraction
# ---------------------------------------------------------------------------


def test_rostered_recipients_covers_all_four_authored_sources():
    config = {
        "users": [{"email": "scott@smd.services", "role": "principal"}],
        "escalation": {
            "red_flag_recipients": ["scott@smd.services"],
            "failure_recipients": ["team@smd.services"],
        },
        "scope": {
            "outbound_roster": [{"address": "ap-client-standin@agentmail.to", "class": "client"}],
            "inbound_allow_from": ["smdurgan@smdurgan.com", "not-an-address"],
        },
    }
    rostered = lists.rostered_recipients(config)
    assert rostered == [
        "scott@smd.services",
        "team@smd.services",
        "ap-client-standin@agentmail.to",
        "smdurgan@smdurgan.com",
    ]


def test_rostered_recipients_dedupes_case_insensitively():
    config = {"users": [{"email": "Scott@SMD.services"}, {"email": "scott@smd.services"}]}
    assert lists.rostered_recipients(config) == ["scott@smd.services"]


# ---------------------------------------------------------------------------
# entry-shape parsing (vendor shapes are probed, never assumed)
# ---------------------------------------------------------------------------


def test_entries_parse_from_strings_and_entry_mappings():
    assert lists._entries_of({"entries": ["a@b.c", {"entry": "d.e"}]}, "/x") == ["a@b.c", "d.e"]
    assert lists._entries_of({}, "/x") == []


def test_an_unrecognized_entry_shape_holds_rather_than_reading_empty():
    with pytest.raises(lists.ListsError):
        lists._entries_of({"entries": [{"address": "a@b.c"}]}, "/x")
    with pytest.raises(lists.ListsError):
        lists._entries_of({"something_else": True}, "/x")


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------


def test_a_send_block_match_on_the_address_is_the_finding():
    report = lists.grade_seat(
        "pilot",
        "pilot@agentmail.to",
        ["scott@smd.services"],
        org_block=["scott@smd.services"],
        org_allow=[],
        inbox_block=[],
        inbox_allow=[],
    )
    assert [f.kind for f in report.findings] == ["send_block_match"]
    assert report.findings[0].scope == "org"


def test_a_domain_block_entry_matches_every_rostered_address_on_it():
    report = lists.grade_seat(
        "pilot",
        "pilot@agentmail.to",
        ["scott@smd.services", "team@smd.services"],
        org_block=[],
        org_allow=[],
        inbox_block=["smd.services"],
        inbox_allow=[],
    )
    assert len(report.findings) == 2
    assert all(f.kind == "send_block_match" for f in report.findings)


def test_a_nonempty_send_allow_that_omits_a_rostered_address_is_the_finding():
    report = lists.grade_seat(
        "pilot",
        "pilot@agentmail.to",
        ["scott@smd.services", "vendor@records.invalid"],
        org_block=[],
        org_allow=[],
        inbox_block=[],
        inbox_allow=["scott@smd.services"],
    )
    assert [(f.kind, f.recipient) for f in report.findings] == [("send_allow_omission", "vendor@records.invalid")]


def test_an_empty_allow_list_constrains_nothing():
    report = lists.grade_seat(
        "pilot",
        "pilot@agentmail.to",
        ["scott@smd.services"],
        org_block=[],
        org_allow=[],
        inbox_block=[],
        inbox_allow=[],
    )
    assert report.findings == [] and not report.is_finding


def test_matching_is_case_insensitive_and_ignores_blank_entries():
    assert lists._matches("SCOTT@SMD.SERVICES", "scott@smd.services")
    assert lists._matches("@smd.services", "scott@smd.services")
    assert not lists._matches("", "scott@smd.services")
    assert not lists._matches("other.invalid", "scott@smd.services")


# ---------------------------------------------------------------------------
# report + exit shape
# ---------------------------------------------------------------------------


def test_findings_render_the_series_marker_and_digest():
    """The rolling-issue contract (ss#2582 discipline): the CONSTANT series
    marker is what locates the one open issue; the digest only says whether
    the set moved. A findings-derived issue key would re-file the whole report
    as a duplicate the day one new address lands."""
    finding = lists.SeatListsReport(
        slug="pilot",
        inbox="pilot@agentmail.to",
        rostered=1,
        findings=[lists.ListsFinding(scope="org", kind="send_block_match", entry="x@y.z", recipient="x@y.z")],
    )
    rendered = lists.render([finding])
    assert "reconcile-series: agentmail-lists" in rendered
    assert "reconcile-findings:" in rendered
    assert "silently dropped" in rendered
    held = lists.SeatListsReport(slug="pilot", inbox="pilot@agentmail.to")
    held.held = "agentmail GET /lists/send/block failed: HTTP 500"
    rendered_hold = lists.render([held])
    assert rendered_hold.startswith("HOLD")
    # The marker still prints (constant), but no digest: nothing was found.
    assert "reconcile-series: agentmail-lists" in rendered_hold
    assert "reconcile-findings:" not in rendered_hold


def test_digest_is_stable_content_only_and_moves_with_the_set():
    def _report(entries):
        return lists.SeatListsReport(
            slug="pilot",
            inbox="pilot@agentmail.to",
            findings=[lists.ListsFinding(scope="org", kind="send_block_match", entry=e, recipient=e) for e in entries],
        )

    # Stable across runs (no volatile fields in the key)...
    assert lists.finding_digest([_report(["a@b.c"])]) == lists.finding_digest([_report(["a@b.c"])])
    # ...and it MOVES when the set grows, which is what triggers the comment
    # on the SAME rolling issue rather than a second issue.
    assert lists.finding_digest([_report(["a@b.c"])]) != lists.finding_digest([_report(["a@b.c", "d@e.f"])])
    assert lists.finding_digest([_report([])]) == ""


def test_rostered_addresses_are_stripped_like_vendor_entries():
    """#2284 family: _matches strips+lowers vendor entries; the rostered side
    must normalize identically or a padded authoring reads as unlisted."""
    config = {"users": [{"email": "  Scott@SMD.services  "}]}
    assert lists.rostered_recipients(config) == ["scott@smd.services"]


def test_missing_key_holds(monkeypatch, capsys):
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    assert lists.main([]) == lists.EXIT_HOLD
    assert "AGENTMAIL_API_KEY unset" in capsys.readouterr().out


def test_the_documented_rest_paths_are_the_ones_called():
    """Pins the probed vendor paths (docs.agentmail.to, probe date 2026-08-31)
    so a refactor cannot silently drift onto a guessed endpoint."""
    calls: list[str] = []

    def _fake_get(path, api_key, *, opener=None):
        calls.append(path)
        return {"entries": []}

    original = lists._get
    lists._get = _fake_get
    try:
        lists.fetch_list("key", "send", "block")
        lists.fetch_list("key", "send", "allow", inbox="pilot@agentmail.to")
    finally:
        lists._get = original
    assert calls[0].startswith("/lists/send/block?")
    assert calls[1].startswith("/inboxes/pilot%40agentmail.to/lists/send/allow?")


# ---------------------------------------------------------------------------
# org-scope 403 calibration (first live run, 2026-08-31, run 33430061160)
#
# The CI key is the ss#2258 SCOPED per-inbox key; the org-wide key exists
# nowhere by design, so org-scope list GETs 403 forever. That designed state
# must be a NOTED SKIP (claim shrinks to the inbox scope, which overrides org
# anyway), while every other failure keeps holding -- including a 403 at INBOX
# scope, which the scoped key should never produce and which is therefore a
# vendor question, not a designed state.
# ---------------------------------------------------------------------------


def _http_error(code: int) -> "urllib.error.HTTPError":
    import io
    import urllib.error

    return urllib.error.HTTPError("https://api.agentmail.to/v0/x", code, "err", {}, io.BytesIO(b""))


def test_get_types_a_403_as_forbidden_and_other_statuses_as_plain_holds():
    def _opener_403(request, timeout):
        raise _http_error(403)

    def _opener_500(request, timeout):
        raise _http_error(500)

    with pytest.raises(lists.ListsForbidden):
        lists._get("/lists/send/block", "key", opener=_opener_403)
    with pytest.raises(lists.ListsError) as excinfo:
        lists._get("/lists/send/block", "key", opener=_opener_500)
    assert not isinstance(excinfo.value, lists.ListsForbidden)


_MINIMAL_CONFIG = {"users": [{"email": "scott@smd.services", "role": "principal"}]}


def _wire_main(monkeypatch, *, org_exc=None, inbox_result=None, inbox_exc=None):
    """Drive main() end to end with the vendor stubbed at fetch_list."""

    def _fake_fetch(api_key, direction, kind, *, inbox=None, opener=None):
        if inbox is None:
            if org_exc is not None:
                raise org_exc
            return []
        if inbox_exc is not None:
            raise inbox_exc
        return list(inbox_result or [])

    monkeypatch.setenv("AGENTMAIL_API_KEY", "scoped-key")
    monkeypatch.setattr(lists, "fetch_list", _fake_fetch)
    monkeypatch.setattr(lists, "agentmail_seats", lambda: ["pilot"])
    monkeypatch.setattr(lists, "load_config", lambda slug: dict(_MINIMAL_CONFIG))


def test_org_scope_403_is_a_noted_skip_and_the_inbox_check_still_runs(monkeypatch, capsys):
    _wire_main(monkeypatch, org_exc=lists.ListsForbidden("agentmail GET /lists/send/block failed: HTTP 403"))
    code = lists.main([])
    out = capsys.readouterr().out
    assert code == lists.EXIT_CLEAN
    # The residual is SAID, first line, and the summary's claim shrinks.
    assert out.startswith(lists.ORG_SCOPE_SKIP_NOTE)
    assert "outside this control's view" in out
    assert "per-inbox send scope only" in out
    # The inbox half still evaluated and the rolling-issue plumbing survives.
    assert "ok    pilot@agentmail.to [pilot]" in out
    assert "reconcile-series: agentmail-lists" in out
    assert "HOLD" not in out


def test_org_scope_403_with_an_inbox_finding_still_exits_finding(monkeypatch, capsys):
    _wire_main(
        monkeypatch,
        org_exc=lists.ListsForbidden("HTTP 403"),
        inbox_result=["scott@smd.services"],  # inbox send-block matches a rostered address
    )
    code = lists.main([])
    out = capsys.readouterr().out
    assert code == lists.EXIT_FINDING
    assert "silently dropped" in out
    assert out.startswith(lists.ORG_SCOPE_SKIP_NOTE)


def test_org_scope_non_403_failure_still_holds(monkeypatch, capsys):
    _wire_main(monkeypatch, org_exc=lists.ListsError("agentmail GET /lists/send/block failed: HTTP 500"))
    code = lists.main([])
    out = capsys.readouterr().out
    assert code == lists.EXIT_HOLD
    assert "HOLD  agentmail: org-scope lists unreadable" in out
    assert lists.ORG_SCOPE_SKIP_NOTE not in out


# ---------------------------------------------------------------------------
# per-seat key selection (second live-fire calibration, 2026-08-31): keys are
# inbox-scoped, so the shared CI key read scott@ clean and 403'd on
# pilot-smokeball's own inbox. Three 403 forks, each pinned:
#   per-seat key set + works        -> the per-seat key is the one USED
#   per-seat key set + 403          -> honest HOLD naming the per-seat var
#   shared key only + 403           -> noted skip NAMING the missing env var
# ---------------------------------------------------------------------------


def test_seat_key_env_follows_the_provisioner_convention():
    assert lists.seat_key_env("pilot-smokeball") == "AGENTMAIL_API_KEY__PILOT_SMOKEBALL"
    assert lists.seat_key_env("scott") == "AGENTMAIL_API_KEY__SCOTT"


def _record_fetch(monkeypatch, *, forbid_inbox=False):
    """Stub fetch_list, recording which key each inbox-scope read used."""
    used: dict[str, str] = {}

    def _fake_fetch(api_key, direction, kind, *, inbox=None, opener=None):
        if inbox is not None:
            used[inbox] = api_key
            if forbid_inbox:
                raise lists.ListsForbidden(f"agentmail GET /inboxes/{inbox}/lists/{direction}/{kind} failed: HTTP 403")
        return []

    monkeypatch.setattr(lists, "fetch_list", _fake_fetch)
    monkeypatch.setattr(lists, "load_config", lambda slug: dict(_MINIMAL_CONFIG))
    return used


def test_a_vaulted_per_seat_key_is_preferred_over_the_shared_key(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY__PILOT_SMOKEBALL", "pilot-scoped-key")
    used = _record_fetch(monkeypatch)
    report = lists.check_seat("pilot-smokeball", "shared-key", ([], []))
    assert report.held is None and report.skipped is None
    assert used["pilot-smokeball@agentmail.to"] == "pilot-scoped-key"


def test_an_empty_per_seat_env_var_falls_back_to_the_shared_key(monkeypatch):
    # GitHub passes an unset secret as the EMPTY string; empty must read as
    # absent, not as a per-seat key that mysteriously 401s.
    monkeypatch.setenv("AGENTMAIL_API_KEY__PILOT_SMOKEBALL", "")
    used = _record_fetch(monkeypatch)
    lists.check_seat("pilot-smokeball", "shared-key", ([], []))
    assert used["pilot-smokeball@agentmail.to"] == "shared-key"


def test_inbox_403_under_the_per_seat_key_holds_honestly(monkeypatch, capsys):
    # A seat's OWN scoped key that cannot read its own inbox lists is the
    # vendor question (vendor-ask runbook, question 4) -- the control holds
    # rather than shrinking its claim again.
    monkeypatch.setenv("AGENTMAIL_API_KEY__PILOT_SMOKEBALL", "pilot-scoped-key")
    _record_fetch(monkeypatch, forbid_inbox=True)
    report = lists.check_seat("pilot-smokeball", "shared-key", ([], []))
    assert report.held is not None
    assert "HTTP 403" in report.held
    assert "AGENTMAIL_API_KEY__PILOT_SMOKEBALL" in report.held
    assert "HOLD  pilot-smokeball@agentmail.to" in lists.render([report])


def test_inbox_403_under_only_the_shared_key_is_a_noted_skip_naming_the_var(monkeypatch, capsys):
    monkeypatch.delenv("AGENTMAIL_API_KEY__PILOT_SMOKEBALL", raising=False)
    _record_fetch(monkeypatch, forbid_inbox=True)
    report = lists.check_seat("pilot-smokeball", "shared-key", ([], []))
    assert report.held is None
    assert report.skipped is not None
    assert "set AGENTMAIL_API_KEY__PILOT_SMOKEBALL to close" in report.skipped
    rendered = lists.render([report])
    assert "n/a   pilot-smokeball@agentmail.to" in rendered
    assert "HOLD" not in rendered


def test_a_skipped_seat_does_not_redden_a_run_that_measured_others(monkeypatch, capsys):
    # scott@ reads clean under the shared key while pilot skips: the run is
    # CLEAN, with the remediation named in the report -- not red forever.
    monkeypatch.setenv("AGENTMAIL_API_KEY", "shared-key")
    monkeypatch.delenv("AGENTMAIL_API_KEY__PILOT_SMOKEBALL", raising=False)
    monkeypatch.delenv("AGENTMAIL_API_KEY__SCOTT", raising=False)

    def _fake_fetch(api_key, direction, kind, *, inbox=None, opener=None):
        if inbox is not None and inbox.startswith("pilot-smokeball@"):
            raise lists.ListsForbidden(f"agentmail GET /inboxes/{inbox}/... failed: HTTP 403")
        return []

    monkeypatch.setattr(lists, "fetch_list", _fake_fetch)
    monkeypatch.setattr(lists, "agentmail_seats", lambda: ["pilot-smokeball", "scott"])
    monkeypatch.setattr(lists, "load_config", lambda slug: dict(_MINIMAL_CONFIG))
    code = lists.main([])
    out = capsys.readouterr().out
    assert code == lists.EXIT_CLEAN
    assert "n/a   pilot-smokeball@agentmail.to" in out
    assert "ok    scott@agentmail.to" in out
    assert "1 skipped" in out


def test_every_seat_skipped_is_still_the_loud_hold(monkeypatch, capsys):
    # All-skipped means nothing was measured; clean and unmeasured must not
    # print the same exit code (the sibling reconcilers' rule).
    monkeypatch.delenv("AGENTMAIL_API_KEY__PILOT", raising=False)  # _wire_main's seat is "pilot"
    _wire_main(
        monkeypatch,
        inbox_exc=lists.ListsForbidden("agentmail GET /inboxes/... failed: HTTP 403"),
    )
    code = lists.main([])
    out = capsys.readouterr().out
    assert code == lists.EXIT_HOLD
    assert "n/a   pilot@agentmail.to" in out
