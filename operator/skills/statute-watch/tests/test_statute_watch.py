"""Tests for statute-watch: selection, rendering through the send gate's own
filters, the envelope, and the pre-run end to end.

Run from operator/:  python -m pytest skills/statute-watch -q

Every fixture name, number and date below is invented.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import stat
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_DIR = Path(__file__).resolve().parents[1]
_OPERATOR = _DIR.parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pre_run = _load("statute_watch_pre_run_test", _DIR / "pre_run.py")
report = pre_run._REPORT
render = pre_run._RENDER
pull = pre_run._PULL
envelope_mod = pre_run._ENVELOPE
citation_filter = _load("sw_citation_filter", _OPERATOR / "safety_substrate" / "citation_filter.py")
identifier_filter = _load("sw_identifier_filter", _OPERATOR / "safety_substrate" / "identifier_filter.py")

TODAY = date(2026, 10, 1)
RECIPIENT = "office@firm.test"
CFG = {
    "business_hours": {"timezone": "America/Los_Angeles"},
    "personas": [{"skills": [{"name": "statute-watch", "settings": {"recipient": RECIPIENT}}]}],
}


def _matter(mid: str, number: str | None, statute: str | None, **extra) -> dict:
    row = {"id": mid, "number": number, "clientIds": ["c-" + mid], "staffId": "s-1", "statute": statute}
    row.setdefault("caseNumber", False)
    row.setdefault("filed", False)
    row.update(extra)
    return row


def _iso(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat() + "T00:00:00"


THREE = {
    "matters": [
        _matter("m-3", "20471", _iso(40)),
        _matter("m-1", "20455", _iso(3)),
        _matter("m-2", "2026-PI-107", _iso(12)),
        _matter("m-filed", "20001", _iso(5), filed=True),
        _matter("m-numbered", "20002", _iso(6), caseNumber=True),
        _matter("m-far", "20003", _iso(92)),
        _matter("m-past", "20004", _iso(-1)),
        _matter("m-none", "20005", None),
    ]
}
DETAILS = {
    "staff": {"s-1": "Okonkwo"},
    "matters": {
        "m-1": {"clientLastName": "Lindqvist", "courtDocuments": 0},
        "m-2": {"clientLastName": "Garcia v. Allstate Insurance", "courtDocuments": 2},
        "m-3": {"clientLastName": "Van Der Berg", "courtDocuments": 3},
    },
}


def _full(raw=THREE, details=DETAILS):
    sel = report.select(raw, TODAY)
    listed = sel.cases[: report.RENDER_CAP]
    body = render.render_full(report.rows(listed, details), total=len(sel.cases), unreadable=sel.unreadable)
    return sel, listed, body


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_selects_the_window_unfiled_soonest_first() -> None:
    sel = report.select(THREE, TODAY)
    assert [c.matter_id for c in sel.cases] == ["m-1", "m-2", "m-3"]
    assert [c.days_left for c in sel.cases] == [3, 12, 40]
    assert sel.open_matters == 8
    assert sel.unreadable == 0


def test_window_is_today_through_ninety_one_days() -> None:
    raw = {"matters": [_matter("a", "301", _iso(0)), _matter("b", "302", _iso(91)), _matter("c", "303", _iso(92))]}
    assert [c.matter_id for c in report.select(raw, TODAY).cases] == ["a", "b"]


def test_failed_layout_reads_and_junk_dates_are_counted_not_dropped() -> None:
    raw = {"matters": [_matter("a", "301", _iso(4)), {"id": "b", "layoutError": True}, _matter("c", "303", "soon")]}
    sel = report.select(raw, TODAY)
    assert sel.unreadable == 2
    assert [c.matter_id for c in sel.cases] == ["a"]


@pytest.mark.parametrize("raw", [{"listError": "SmokeballApiError"}, {}, None, {"matters": "x"}])
def test_an_unread_matter_list_is_a_failure_not_an_empty_report(raw) -> None:
    with pytest.raises(report.PullFailed):
        report.select(raw, TODAY)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Garcia", "Garcia"),
        ("Maria V. Garcia", "Maria"),
        ("Maria J. Garcia", "Maria Garcia"),
        ("Garcia v. Allstate Insurance", "Garcia"),
        ("Garcia vs. Allstate", "Garcia"),
        ("In re Estate of Poe", None),
        ("O'Neil-Smith", "O'Neil-Smith"),
        ("St. John", "St John"),
        ("Unit 42", "Unit"),
        ("", None),
        (None, None),
    ],
)
def test_surname_never_reads_as_a_caption(raw, expected) -> None:
    got = report.surname(raw)
    assert got == expected
    if got:
        assert not citation_filter.contains_citation("client " + got + ": statute date")


# ---------------------------------------------------------------------------
# Rendering through the send gate's own filters
# ---------------------------------------------------------------------------


def _register_from_handoff(tmp_path: Path, listed) -> identifier_filter.ProvenanceRegister:
    """Seed a register ONLY from what the handoff file carries, the way the
    overlay does: date atoms, then (matterNumber, dates) records whose number
    reads as a case number or a bare digit run."""
    record = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.json").read_text())
    reg = identifier_filter.ProvenanceRegister()
    for day in record["dates"]:
        reg.add_read_text(day)
    for entry in record["records"]:
        number = entry["matterNumber"]
        hits = identifier_filter.unverified_identifiers(number, identifier_filter.ProvenanceRegister())
        shaped = bool(hits) and all(h.kind is identifier_filter.IdKind.CASE_NUMBER for h in hits)
        assert shaped or identifier_filter._BARE_MATTER_NUMBER_RE.fullmatch(number), number
        reg.add_record(number, entry["dates"])
    assert len(record["records"]) == len(listed)
    return reg


def test_full_body_passes_both_filters_with_a_handoff_seeded_register(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _sel, listed, body = _full()
    pre_run._HANDOFF.write_pre_run_handoff(pre_run.handoff_payload(listed), skill="statute-watch", started_at="x")
    reg = _register_from_handoff(tmp_path, listed)
    text = render.subject(TODAY) + "\n" + body
    assert identifier_filter.check(text, reg).unverified == ()
    assert citation_filter.scan(text) == []
    assert not citation_filter.contains_citation(text)


def test_a_mispaired_date_would_be_refused(tmp_path, monkeypatch) -> None:
    """The check above can fail: swap two cases' dates and the pair gate sees it."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _sel, listed, body = _full()
    pre_run._HANDOFF.write_pre_run_handoff(pre_run.handoff_payload(listed), skill="statute-watch", started_at="x")
    reg = _register_from_handoff(tmp_path, listed)
    swapped = body.replace(render.long_date(listed[0].statute), render.long_date(listed[1].statute), 1)
    assert identifier_filter.check(swapped, reg).unverified


def test_a_full_name_with_a_middle_initial_is_refused_and_the_last_name_is_not() -> None:
    assert citation_filter.contains_citation("1. Matter 20455, client Maria V. Garcia: statute date")
    assert not citation_filter.contains_citation("1. Matter 20455, client Garcia: statute date")


def test_the_subject_month_is_not_a_date_atom() -> None:
    subject = render.subject(TODAY)
    assert subject == "Statute report, October 2026"
    assert identifier_filter.unverified_identifiers(subject, identifier_filter.ProvenanceRegister()) == []


def test_skeleton_is_identifier_free_and_counts_only() -> None:
    for total in (0, 1, 7):
        skeleton = render.render_skeleton(total)
        assert identifier_filter.unverified_identifiers(skeleton, identifier_filter.ProvenanceRegister()) == []
        assert not citation_filter.contains_citation(skeleton)
    assert render.render_skeleton(1).startswith("1 open case has a statute date")


def test_no_dates_but_statute_dates_and_no_em_dashes() -> None:
    sel, listed, body = _full()
    found = {
        h.canonical
        for h in identifier_filter.unverified_identifiers(body, identifier_filter.ProvenanceRegister())
        if h.kind is identifier_filter.IdKind.DATE
    }
    assert found == {c.statute.isoformat() for c in listed}
    for text in (body, render.render_skeleton(len(sel.cases)), render.subject(TODAY)):
        assert "—" not in text and "–" not in text


def test_body_lines_and_absences() -> None:
    raw = {"matters": [_matter("m-1", None, _iso(0), staffId=None), _matter("m-2", "20456", _iso(1))]}
    details = {"staff": {}, "matters": {"m-2": {"clientError": True, "documentsError": True}}}
    _sel, _listed, body = _full(raw, details)
    assert (
        "1. Matter with no number on file, client name not available: statute date October 1, 2026, due today. "
        "No responsible attorney on file. Court-named documents: not checked." in body
    )
    assert "2. Matter 20456, client name not available: statute date October 2, 2026, 1 day left." in body
    assert "Attorney not available." in body


def test_empty_set_and_unreadable_line() -> None:
    raw = {"matters": [_matter("a", "301", None), {"id": "b", "layoutError": True}]}
    _sel, _listed, body = _full(raw, {})
    assert body == render.EMPTY_LINE + "\n\n1 open case could not be checked this month.\n"


def test_cap_at_one_hundred_with_a_remainder_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    raw = {"matters": [_matter("m-%03d" % i, str(30000 + i), _iso(i % 90)) for i in range(105)]}
    sel, listed, body = _full(raw, {})
    assert len(listed) == 100
    assert body.count("\n100. ") == 1 and "\n101. " not in body
    assert "And 5 more cases." in body
    pre_run._HANDOFF.write_pre_run_handoff(pre_run.handoff_payload(listed), skill="statute-watch", started_at="x")
    reg = _register_from_handoff(tmp_path, listed)
    assert identifier_filter.check(body, reg).unverified == ()


# ---------------------------------------------------------------------------
# The pre-run, end to end
# ---------------------------------------------------------------------------


class _Clock:
    """Reads back t0 for today's date, then t0 + 12 minutes: the pull took time."""

    def __init__(self) -> None:
        self.t0 = datetime(2026, 10, 1, 14, 7, tzinfo=timezone.utc)
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.t0 if self.calls == 1 else self.t0 + timedelta(minutes=12)


def _run(tmp_path, monkeypatch, *, cfg=CFG, raw=THREE, details=DETAILS, dry=False):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    beats: list = []
    monkeypatch.setattr(pre_run, "_heartbeat", lambda *a: beats.append(a) or True)
    out = io.StringIO()
    with redirect_stdout(out):
        code = pre_run.run(cfg, pull_matters=lambda: raw, pull_details=lambda cases: details, clock=_Clock(), dry=dry)
    assert code == 0
    return json.loads(out.getvalue().strip().splitlines()[-1]), out.getvalue(), beats


def _smd_files(tmp_path: Path) -> list[str]:
    root = tmp_path / ".smd"
    return sorted(str(p.relative_to(tmp_path)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_report_ready_writes_envelope_and_handoff_stamped_after_the_pull(tmp_path, monkeypatch) -> None:
    verdict, stdout, beats = _run(tmp_path, monkeypatch)
    assert verdict["wakeAgent"] is True and verdict["dispatch_expected"] is True
    assert verdict["cases"] == 3 and verdict["unreadable"] == 0
    env = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.dispatch.json").read_text())
    handoff = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.json").read_text())
    assert env["started_at"] == "2026-10-01T14:19:00Z" == handoff["started_at"]
    (dispatch,) = env["dispatches"]
    assert dispatch["recipients"] == [RECIPIENT] and dispatch["appends"] == []
    assert dispatch["subject"] == "Statute report, October 2026"
    assert env["in_turn_enforce"] is True
    assert env["in_turn"] == [{"name": "statute_report_skeleton", "template": dispatch["skeleton_body"], "slots": {}}]
    assert dispatch["body_sha256_full"] == pre_run._H.canonical_body_sha256(dispatch["full_body"])
    assert [b[1] for b in beats] == ["EMITTED_WAKE"]
    assert beats[0][3]["body_sha256"] == envelope_mod.wake_stamps(env)
    assert (tmp_path / ".smd" / "pre_run" / "statute-watch.dispatch.json").stat().st_mode & 0o077 == 0


def test_stdout_carries_no_case_data(tmp_path, monkeypatch) -> None:
    _verdict, stdout, beats = _run(tmp_path, monkeypatch)
    env = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.dispatch.json").read_text())
    for needle in ("20455", "20471", "2026-PI-107", "Lindqvist", "Garcia", "Okonkwo", "October", "2026-10", "m-1"):
        assert needle in env["dispatches"][0]["full_body"] or needle.startswith(("2026-10", "m-"))
        assert needle not in stdout
        assert needle not in json.dumps(beats)


def test_a_matter_list_failure_sends_nothing(tmp_path, monkeypatch) -> None:
    verdict, _stdout, beats = _run(tmp_path, monkeypatch, raw={"listError": "SmokeballApiError"})
    assert verdict == {"wakeAgent": False, "status": "matter_list_failed", "dry_run": False}
    assert _smd_files(tmp_path) == []
    assert [b[1] for b in beats] == ["SUPPRESSED_WAKE"]


def test_no_recipient_no_envelope(tmp_path, monkeypatch) -> None:
    cfg = {"personas": [{"skills": [{"name": "statute-watch"}]}]}
    verdict, _stdout, _beats = _run(tmp_path, monkeypatch, cfg=cfg)
    assert verdict["wakeAgent"] is False and verdict["status"] == "recipient_unauthored"
    assert _smd_files(tmp_path) == []


def test_dry_run_writes_nothing_and_prints_counts(tmp_path, monkeypatch) -> None:
    verdict, stdout, beats = _run(tmp_path, monkeypatch, dry=True)
    assert verdict == {
        "wakeAgent": False,
        "status": "dry_run",
        "dry_run": True,
        "open_matters": 8,
        "cases": 3,
        "listed": 3,
        "unreadable": 0,
    }
    assert _smd_files(tmp_path) == [] and beats == []
    verdict, _stdout, beats = _run(tmp_path, monkeypatch, dry=True, raw={"listError": "x"})
    assert verdict["status"] == "matter_list_failed" and beats == [] and _smd_files(tmp_path) == []


def test_today_is_the_seat_local_day() -> None:
    late_evening = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)  # 22:00 Sep 30 in Los Angeles
    assert pre_run.local_today(late_evening, pre_run.seat_timezone(CFG)) == date(2026, 9, 30)
    assert pre_run.local_today(late_evening, "Not/AZone") == date(2026, 10, 1)


def test_recipient_must_be_one_address() -> None:
    def cfg(value):
        return {"personas": [{"skills": [{"name": "statute-watch", "settings": {"recipient": value}}]}]}

    assert pre_run.report_recipient(cfg(" a@b.test ")) == "a@b.test"
    for bad in (None, "", "nobody", "a@b.test, c@d.test", ["a@b.test"]):
        assert pre_run.report_recipient(cfg(bad)) is None


# ---------------------------------------------------------------------------
# The connector-venv reads, driven through a stand-in interpreter
# ---------------------------------------------------------------------------


def test_snippets_compile_and_carry_the_court_pattern() -> None:
    ast.parse(pull.MATTERS_SNIPPET)
    ast.parse(pull.DETAILS_SNIPPET)
    assert repr(pull.COURT_DOC_PATTERN) in pull.DETAILS_SNIPPET

    def court_named(file_name: str) -> bool:
        return re.search(pull.COURT_DOC_PATTERN, file_name, re.IGNORECASE) is not None

    assert court_named("Conformed Complaint.pdf")
    assert court_named("POS - summons.pdf")
    assert not court_named("Medical records Kaiser.pdf")


def _fake_connector(tmp_path: Path, matters: dict, details: dict) -> str:
    script = tmp_path / "fake-python"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        "snippet = sys.argv[2]\n"
        "sys.stdin.read()\n"
        "print(json.dumps(" + repr(matters) + " if 'StatuteOfLimitationDate' in snippet else " + repr(details) + "))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_main_end_to_end_through_the_subprocess_seam(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "customer.yaml"
    cfg_path.write_text(json.dumps(CFG))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_TIMEZONE", "America/Los_Angeles")
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_STATUTE_WATCH_DRY", raising=False)
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date()
    raw = {"matters": [_matter("m-1", "20455", (today + timedelta(days=9)).isoformat())]}
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", _fake_connector(tmp_path, raw, DETAILS))
    out = io.StringIO()
    with redirect_stdout(out):
        assert pre_run.main() == 0
    verdict = json.loads(out.getvalue().strip().splitlines()[-1])
    assert verdict["status"] == "report_ready" and verdict["cases"] == 1
    assert "20455" not in out.getvalue()
    env = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.dispatch.json").read_text())
    assert "1. Matter 20455, client Lindqvist:" in env["dispatches"][0]["full_body"]


def test_main_with_no_connector_sends_nothing(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "customer.yaml"
    cfg_path.write_text(json.dumps(CFG))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", str(tmp_path / "missing-python"))
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        assert pre_run.main() == 0
    assert json.loads(out.getvalue().strip())["status"] == "matter_list_failed"
    assert _smd_files(tmp_path) == []
    assert not os.path.exists(tmp_path / ".smd")


# ---------------------------------------------------------------------------
# Review fixes: the silent-zero guard, a traceless suppress, template parity
# ---------------------------------------------------------------------------


def _no_statute_fleet(count: int = 20) -> dict:
    return {"matters": [_matter("z-%02d" % i, str(40000 + i), None) for i in range(count)]}


def test_many_matters_and_no_statute_field_is_a_failed_read(tmp_path, monkeypatch) -> None:
    verdict, _stdout, beats = _run(tmp_path, monkeypatch, raw=_no_statute_fleet())
    assert verdict == {"wakeAgent": False, "status": "statute_field_absent", "dry_run": False}
    assert _smd_files(tmp_path) == []
    assert [b[1] for b in beats] == ["SUPPRESSED_WAKE"] and beats[0][2] == "statute_field_absent"


def test_the_same_fleet_with_one_statute_date_sends_normally(tmp_path, monkeypatch) -> None:
    raw = _no_statute_fleet()
    raw["matters"][7]["statute"] = _iso(200)  # readable, outside the window: an empty report, not a failure
    verdict, _stdout, beats = _run(tmp_path, monkeypatch, raw=raw)
    assert verdict["status"] == "report_ready" and verdict["cases"] == 0
    env = json.loads((tmp_path / ".smd" / "pre_run" / "statute-watch.dispatch.json").read_text())
    assert env["dispatches"][0]["full_body"] == render.EMPTY_LINE + "\n"
    assert [b[1] for b in beats] == ["EMITTED_WAKE"]


def test_a_short_list_with_no_statutes_is_an_empty_report_not_a_failure() -> None:
    sel = report.select(_no_statute_fleet(report.SILENT_ZERO_FLOOR - 1), TODAY)
    assert sel.cases == () and sel.unreadable == 0


def test_a_suppress_whose_heartbeat_did_not_land_says_so_on_stderr(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SMD_WORKSPACE_BROKER_SOCKET", raising=False)
    monkeypatch.delenv("SMD_AUDIT_BROKER_SOCKET", raising=False)
    pre_run.run(CFG, pull_matters=lambda: {"listError": "x"}, pull_details=lambda cases: {}, clock=_Clock())
    captured = capsys.readouterr()
    assert "matter_list_failed" in captured.err and "heartbeat not recorded" in captured.err
    assert json.loads(captured.out)["status"] == "matter_list_failed"


def test_output_format_carries_every_authored_phrase_render_uses() -> None:
    """The template page and the renderer cannot drift: each authored phrase
    render.py prints appears verbatim (placeholders for values) in the spec."""
    spec = (_DIR / "references" / "output-format.md").read_text()
    phrases = [
        render.INTRO,
        render.EMPTY_LINE,
        render.SKELETON_TEMPLATE.format(count="<N>", cases="cases have"),
        render.unreadable_line(2).replace("2", "<N>", 1),
        render.more_line(2).replace("2", "<N>", 1),
        render.SUBJECT_TEMPLATE.format(month="<Month>", year="<Year>"),
    ]
    assert [p for p in phrases if p not in spec] == []
