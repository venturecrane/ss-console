"""The deadline digest names the case and the date, in the firm's words (2026-09-28).

The pilot's digest that morning, verbatim, failed "would a great case manager
have sent this?":

    Subject: [Deadlines] 1 need you, 2026-09-28
    1. matter 2026-PI-105, court-date 2026-10-02 (due in 4 days)
       a court date the firm authored

No case, no event, our vocabulary, and nothing asked of the reader. The pull
already read everything a better line needs: the calendar title ("Final Status
Conference - Okafor v. Grand Valley Market (Dept 47)") and the matter record
whose ``title`` is "2026-PI-105 - Okafor, Denise - Personal Injury - Plaintiff -
Grand Valley Market, Inc.". These tests run that record through the real pull
parse, the casework filter, the projection and the envelope, and pin the words.

Two things the words must still survive, both checked here against the
ss-console source-of-truth copies of the send gate's filters:

* the citation scan: no caption ("Okafor v. Grand Valley") may reach a
  pre-rendered body, because nothing read inside the session registers it and
  the full body would fall back to the counts-only skeleton;
* the identifier gate's per-line matter/date pairing: the date keeps its year
  so the gate still sees "2026-PI-105" beside "Oct 2, 2026" and judges the pair
  against the records this run seeded.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

_SKILL = Path(__file__).resolve().parent
_SUBSTRATE = _SKILL.parents[1] / "safety_substrate"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pre_run = _load(_SKILL / "pre_run.py", "escalator_pre_run_names")
_items = _load(_SKILL / "digest_items.py", "escalator_digest_items_names")
_cw = _load(_SKILL / "casework_ledger.py", "escalator_cw_ledger_names")
_citations = _load(_SUBSTRATE / "citation_filter.py", "substrate_citation_filter_names")
_identifiers = _load(_SUBSTRATE / "identifier_filter.py", "substrate_identifier_filter_names")

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

OKAFOR_TITLE = "2026-PI-105 - Okafor, Denise - Personal Injury - Plaintiff - Grand Valley Market, Inc."
OKAFOR_EVENT = {
    "id": "ev-okafor-fsc",
    "matterId": "m-105",
    "subject": "Final Status Conference - Okafor v. Grand Valley Market (Dept 47)",
    "startTime": "2026-10-02T15:30:00Z",
    "matterNumber": "2026-PI-105",
    "matterTitle": OKAFOR_TITLE,
}
RIVERA_TASK = {
    "id": "t-rivera-serve",
    "matterId": "m-101",
    "subject": "Serve discovery responses",
    "dueDate": "2026-07-08",
    "matterNumber": "2026-PI-101",
    "matterTitle": "2026-PI-101 - Rivera, Marco - Personal Injury - Plaintiff - Sunland Transit",
}
# No title on the record: the line names the matter by number alone.
LIEN_TASK = {
    "id": "t-lien",
    "matterId": "m-104",
    "subject": "Chase Medi-Cal (DHCS) final lien payoff demand",
    "dueDate": "2026-09-30",
    "matterNumber": "2026-PI-104",
}

DATE_PREP = {"date_prep": {"level": "prepares", "window_days": 14}}

#: Our words, never the firm's. None may reach a reader.
INTERNAL_PHRASES = ("court-date", "task-deadline", "a court date the firm authored")

BEFORE_SUBJECT = "[Deadlines] 1 need you, 2026-09-28"
BEFORE_BODY = (
    "## Needs you today (1)\n\n"
    "1. matter 2026-PI-105, court-date 2026-10-02 (due in 4 days)\n"
    "   a court date the firm authored\n\n"
    "Reply to this email with the numbers you have, or say all; each one goes quiet for 7 days "
    "and stays open. Say which ones are done and I'll close them in Smokeball. This is an "
    "internal note; no client was contacted.\n"
)
FOOTER = (
    "Reply to this email with the numbers you have, or say all; each one goes quiet for 7 days "
    "and stays open. Say which ones are done and I'll close them in Smokeball. This is an "
    "internal note; no client was contacted.\n"
)


class _PullSource:
    """The real parse over a pull shaped like the connector subprocess output."""

    def __init__(self, tasks: list[dict], events: list[dict]):
        raw = {"tasks": {"value": tasks}, "events": {"value": events}}
        deadlines, problem, stats = _pre_run.parse_pull(raw, now=NOW)
        assert problem is None, problem
        self._deadlines = deadlines
        self.probe_stats = stats

    def pull_deadlines(self):
        return self._deadlines


def _run(tmp_path: Path, monkeypatch, *, tasks=(), events=(), case_manager=None, casework_rows=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    doc: dict = {"escalation": {"red_flag_recipients": ["triage@firm.test"]}}
    if case_manager is not None:
        doc["case_manager"] = case_manager
    (tmp_path / "customer.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "customer.yaml"))
    ledger_file = tmp_path / "casework.jsonl"
    ledger_file.write_text("".join(json.dumps(r) + "\n" for r in casework_rows), encoding="utf-8")
    monkeypatch.setenv("SMD_CASEWORK_LEDGER_PATH", str(ledger_file))
    with redirect_stdout(io.StringIO()):
        code = asyncio.new_event_loop().run_until_complete(
            _pre_run.run_once(
                [_PullSource(list(tasks), list(events))],
                _pre_run.EscalationWindows(),
                lambda: None,
                today=TODAY,
                now=NOW,
                fire_policy=_pre_run.FirePolicy(),
                ledger_events=[],
            )
        )
    assert code == 0
    pre_run_dir = tmp_path / ".smd" / "pre_run"
    envelope = json.loads((pre_run_dir / "deadline-miss-escalator.dispatch.json").read_text())
    handoff = json.loads((pre_run_dir / "deadline-miss-escalator.json").read_text())
    [dispatch] = envelope["dispatches"]
    return dispatch, handoff


def _gate_register(handoff: dict):
    """The register the overlay seeds from this run's handoff: its date atoms,
    and each matter's number WITH its own dates (the pairing half)."""
    register = _identifiers.ProvenanceRegister()
    for record in handoff.get("records") or []:
        register.add_record(record["matterNumber"], record["dates"])
    return register


# ---------------------------------------------------------------------------
# The Okafor email, before and after
# ---------------------------------------------------------------------------


def test_the_okafor_email_names_the_case_and_the_date(tmp_path, monkeypatch):
    dispatch, _handoff = _run(tmp_path, monkeypatch, events=[OKAFOR_EVENT], case_manager=DATE_PREP)
    assert dispatch["subject"] == "[Deadlines] Okafor: Final Status Conference Fri Oct 2"
    assert dispatch["full_body"] == (
        "## Needs you today (1)\n\n"
        "1. 2026-PI-105 Okafor: Final Status Conference, Fri Oct 2, 2026 (in 4 days)\n"
        "   No prep note has gone out for this yet.\n\n" + FOOTER
    )
    # The reply loop is untouched: one number, one row behind it.
    assert [a["n"] for a in dispatch["appends"]] == [1]


def test_several_items_count_in_the_subject_and_each_line_names_its_own(tmp_path, monkeypatch):
    dispatch, _handoff = _run(
        tmp_path,
        monkeypatch,
        tasks=[RIVERA_TASK, LIEN_TASK],
        events=[OKAFOR_EVENT],
        case_manager=DATE_PREP,
    )
    assert dispatch["subject"] == "[Deadlines] 2 tasks and 1 date need you, Sep 28"
    assert dispatch["full_body"] == (
        "## Needs you today (3)\n\n"
        "Most overdue first.\n\n"
        "1. 2026-PI-101 Rivera: Serve discovery responses, due Jul 8, 2026 (overdue 82 days)\n"
        "2. 2026-PI-104: Chase Medi-Cal (DHCS) final lien payoff demand, due Sep 30, 2026 (in 2 days)\n"
        "3. 2026-PI-105 Okafor: Final Status Conference, Fri Oct 2, 2026 (in 4 days)\n"
        "   No prep note has gone out for this yet.\n\n" + FOOTER
    )
    assert [a["n"] for a in dispatch["appends"]] == [1, 2, 3]


def test_no_prep_line_without_date_prep_or_once_a_brief_is_out(tmp_path, monkeypatch):
    unconfigured, _ = _run(tmp_path / "a", monkeypatch, events=[OKAFOR_EVENT])
    assert "prep note" not in unconfigured["full_body"]
    assert (
        "1. 2026-PI-105 Okafor: Final Status Conference, Fri Oct 2, 2026 (in 4 days)\n\n" in (unconfigured["full_body"])
    )
    key = _cw.item_key(matter_id="m-105", kind="date", source_id="ev-okafor-fsc")
    briefed = {
        "item_key": key,
        "matter_id": "m-105",
        "kind": "date",
        "source_id": "ev-okafor-fsc",
        "event": "briefed",
        "n": 1,
        "thread_ref": "t-1",
        "dispatch_ref": "b" * 32,
        "payload": {"action": "keep", "class": "at_stake", "evidence": []},
        "ts": "2026-09-25T14:00:00Z",
    }
    # Unanswered brief inside notify_days: the backstop fires the date, and a
    # brief HAS gone out, so the line does not claim otherwise.
    near = {**OKAFOR_EVENT, "startTime": "2026-09-30T15:30:00Z"}
    backstop, _ = _run(tmp_path / "b", monkeypatch, events=[near], case_manager=DATE_PREP, casework_rows=[briefed])
    assert "1. 2026-PI-105 Okafor: Final Status Conference, Wed Sep 30, 2026 (in 2 days)" in backstop["full_body"]
    assert "prep note" not in backstop["full_body"]


def test_no_internal_phrase_reaches_a_subject_or_body(tmp_path, monkeypatch):
    runs = [
        _run(tmp_path / "one", monkeypatch, events=[OKAFOR_EVENT], case_manager=DATE_PREP)[0],
        _run(tmp_path / "many", monkeypatch, tasks=[RIVERA_TASK, LIEN_TASK], events=[OKAFOR_EVENT])[0],
        _run(tmp_path / "bare", monkeypatch, events=[{**OKAFOR_EVENT, "subject": "", "matterTitle": ""}])[0],
    ]
    golden = json.loads((_SKILL / "tests" / "fixtures" / "golden-no-case-manager.json").read_text())
    runs += golden["envelope"]["dispatches"]
    for dispatch in runs:
        for text in (dispatch["subject"], dispatch["full_body"], dispatch["skeleton_body"]):
            for phrase in INTERNAL_PHRASES:
                assert phrase not in text, (phrase, text)
            assert "—" not in text
    # The falsifier: the pre-change email carries every one of them.
    assert all(p in BEFORE_SUBJECT + BEFORE_BODY for p in INTERNAL_PHRASES[::2])


def test_the_send_gate_passes_the_words_it_can_see(tmp_path, monkeypatch):
    dispatch, handoff = _run(
        tmp_path, monkeypatch, tasks=[RIVERA_TASK, LIEN_TASK], events=[OKAFOR_EVENT], case_manager=DATE_PREP
    )
    sent = dispatch["subject"] + "\n" + dispatch["full_body"]
    # Citation scan, with NO caption allowlist: a pre-rendered dispatch runs
    # before the turn reads anything, and the handoff seeds no captions.
    assert not _citations.contains_citation(sent), _citations.scan(sent)
    # The caption the record carries would have been refused (the falsifier).
    assert _citations.contains_citation("Okafor v. Grand Valley Market")
    # Identifier gate: every matter/date pair on a line is one this run read.
    register = _gate_register(handoff)
    assert not _identifiers.check(sent, register).unverified
    pairs = {h.canonical for h in _identifiers._extract_pairs(sent)}
    assert _identifiers.pair_key("2026PI105", "2026-10-02") in pairs, "the gate must still see the pairing"
    # And a line pairing Okafor's matter with Rivera's date is caught.
    mispaired = sent.replace("Fri Oct 2, 2026", "Wed Jul 8, 2026")
    assert _identifiers.check(mispaired, register).unverified


# ---------------------------------------------------------------------------
# The words, unit by unit
# ---------------------------------------------------------------------------


def test_matter_name_reads_the_client_surname_off_the_title_and_nothing_else():
    assert _items.matter_name(OKAFOR_TITLE, "2026-PI-105") == "Okafor"
    assert _items.matter_name("201520 - Cruz-Hernandez, Fernando - Motor Vehicle Accident", "201520") == (
        "Cruz-Hernandez"
    )
    assert _items.matter_name("201520 - O'Neil, Pat - MVA", "201520") == "O'Neil"
    # A title in another layout, or for another number, is not guessed at.
    assert _items.matter_name(OKAFOR_TITLE, "2026-PI-101") is None
    assert _items.matter_name("Okafor, Denise - Personal Injury", "2026-PI-105") is None
    assert _items.matter_name(OKAFOR_TITLE, None) is None
    assert _items.matter_name(None, "2026-PI-105") is None
    # Several clients, a caption, or anything that is not a plain surname.
    assert _items.matter_name("2026-PI-105 - AGUILAR, RICHARD | De La Cruz, Ana - PI", "2026-PI-105") is None
    assert _items.matter_name("2026-PI-105 - Okafor v. Grand Valley - PI", "2026-PI-105") is None
    assert _items.matter_name("2026-PI-105 - Okafor 2, Denise - PI", "2026-PI-105") is None


def test_a_calendar_title_is_cut_at_its_caption():
    assert _items.display_label(OKAFOR_EVENT["subject"]) == "Final Status Conference"
    assert _items.display_label("Okafor v. Grand Valley Market - Final Status Conference") is None
    assert _items.display_label("Mediation - In re Estate of Doe - Room 4") == "Mediation"


def test_the_head_keeps_the_word_matter_where_the_reply_parser_needs_it():
    assert _items.head_words({"matter_number": "2026-PI-105", "matter_name": "Okafor"}) == "2026-PI-105 Okafor"
    assert _items.head_words({"matter_number": "201520", "matter_name": "Crawford"}) == "matter 201520 Crawford"
    assert _items.head_words({"matter_number": "PI-2026-0001"}) == "matter PI-2026-0001"
    assert _items.head_words({"matter_number_absent": "no_number_on_record"}) == "matter with no number on record"
    assert _items.head_words({"matter_number_absent": "lookup_failed"}) == "matter number unavailable"


def test_the_subject_reads_as_a_sentence_at_every_count():
    one = {"needs_you": [{"matter_number": "2026-PI-101", "matter_name": "Rivera", "label": "task-deadline",
                          "subject_display": "Serve discovery responses", "authored_date": "2026-07-08",
                          "days_out": -82}]}  # fmt: skip
    assert _items.subject_line(one, TODAY) == "[Deadlines] Rivera: Serve discovery responses overdue since Jul 8"
    due = {"needs_you": [dict(one["needs_you"][0], authored_date="2026-10-07", days_out=9)]}
    assert _items.subject_line(due, "2026-09-28") == "[Deadlines] Rivera: Serve discovery responses due Oct 7"
    nameless = {"needs_you": [{"matter_number_absent": "lookup_failed", "label": "court-date"}]}
    assert _items.subject_line(nameless, TODAY) == "[Deadlines] 1 date needs you, Sep 28"
    two = {"needs_you": one["needs_you"], "blanket_ack_only": due["needs_you"]}
    assert _items.subject_line(two, TODAY) == "[Deadlines] 2 tasks need you, Sep 28"
    dates = {"needs_you": [dict(nameless["needs_you"][0]), dict(nameless["needs_you"][0])]}
    assert _items.subject_line(dates, TODAY) == "[Deadlines] 2 dates need you, Sep 28"
    mixed = {"needs_you": [one["needs_you"][0], nameless["needs_you"][0]]}
    assert _items.subject_line(mixed, TODAY) == "[Deadlines] 1 task and 1 date need you, Sep 28"
    lone_task = {"needs_you": [{"matter_number_absent": "lookup_failed", "label": "task-deadline"}]}
    assert _items.subject_line(lone_task, TODAY) == "[Deadlines] 1 task needs you, Sep 28"
    assert _items.subject_line({}, TODAY) == "[Deadlines] No dates need you today, Sep 28"


def test_a_task_label_does_not_repeat_the_client_the_line_already_names():
    """2026-09-28: "2026-PI-102 <Surname>: Send preservation letter ... - <Surname>".
    The trailing " - <surname>" the matter head already shows is dropped; any
    other suffix, or a label that is only the name, is kept."""
    item = {
        "matter_number": "2026-PI-900",
        "matter_name": "Doe",
        "label": "task-deadline",
        "subject_display": "Send preservation letter to Acme Plaza - Doe",
        "authored_date": "2026-08-21",
        "days_out": -38,
    }
    assert _items.what_words(item) == "Send preservation letter to Acme Plaza"
    assert _items.what_words(dict(item, subject_display="Send preservation letter - DOE")) == "Send preservation letter"
    assert _items.what_words(dict(item, subject_display="Call Roe - Acme")) == "Call Roe - Acme"
    assert _items.what_words(dict(item, subject_display="Doe")) == "Doe"
    assert _items.what_words(dict(item, matter_name=None)) == "Send preservation letter to Acme Plaza - Doe"
    assert _items.subject_line({"needs_you": [item]}, TODAY) == (
        "[Deadlines] Doe: Send preservation letter to Acme Plaza overdue since Aug 21"
    )
