"""The negotiation watch: which documents it reads, the rows it builds, the
notices it composes, the seed-first-run rule, and the cursor that moves only
after the write reads back. Every name, number and date is invented."""

from __future__ import annotations

import json
import re
import shutil
import sys

from types import SimpleNamespace

import pytest

from medchron import arrivals
from medchron.negotiation import extract as X, notice as N, offer_check, rows as R, run as run_mod
from medchron.negotiation.select import select
from medchron_testkit import PRICING

JOB = "01KTJ0BX0000000000000000NG"
M1 = "11111111-1111-4111-8111-111111111111"
DOC = {"name": "Offer letter 10-07.pdf", "ext": ".pdf"}
#: The fixture document's text: it prints every amount and date the run tests'
#: events carry, so the pre-write code check (offer_check) passes them; the
#: falsifiers for that check live in test_negotiation_proof.py.
LETTER = (
    "We offer $15,000 to settle, dated October 7, 2026. Our tender of $956,000 on 9/23/2026 stands. "
    "Offer of $9,000 dated 2025-01-15."
)


@pytest.fixture(autouse=True)
def _review_passes(monkeypatch):
    """The independent model review, stubbed to find nothing (no paid call in
    unit tests); test_negotiation_proof.py drives its holds."""
    monkeypatch.setattr(offer_check, "model_holds", lambda *a, **k: {})


def _offer(**over):
    e = {
        "kind": "offer",
        "by": "Example Mutual Insurance Company",
        "to": "Ashton client",
        "amount": 15000,
        "amount_confirmed": True,
        "date": "2026-10-07",
        "plaintiff_index": None,
        "note": "",
        # The document's sides, stamped by extract.parse: the carrier wrote to the firm.
        "author_side": "paying_side",
        "addressed_to": "our_firm",
    }
    e.update(over)
    return e


def _tab(rows=(), details=None, pidx=0, name="Dana Example"):
    return {"plaintiff_index": pidx, "name": name, "rows": list(rows), "details": details}


# ---- selection ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,ext,want",
    [
        ("Offer letter 10-07", ".pdf", True),
        ("RE: settlement demand", ".msg", True),
        ("CCP 998 to defendant", ".docx", True),
        ("Policy Limits Tender", ".pdf", True),
        ("Case evaluation", ".eml", True),
        ("Counteroffer", ".doc", True),
        ("Medical bills", ".pdf", False),
        ("Offer photo", ".jpg", False),
        ("Scan_0042", ".pdf", False),
    ],
)
def test_select_reads_offer_named_documents_in_a_readable_format(name, ext, want):
    assert select({"id": "f", "name": name, "ext": ext}) is want


def test_select_never_reads_a_deleted_file():
    assert select({"id": "f", "name": "Offer", "ext": ".pdf", "deleted": True}) is False


# ---- rows -----------------------------------------------------------------------------------
def test_an_offer_never_pairs_into_a_filled_row_it_gets_its_own_naming_what_it_answers():
    """FALSIFIER: pair the offer into row 0 and that row's note ("no response in
    the file") becomes a false statement in the firm's Smokeball."""
    tab = _tab(
        [
            {
                "row": 0,
                "demand_amount": 50000,
                "demand_date": "2026-09-01",
                "note": "Our demand; no response in the file",
            }
        ],
        "Entered 10/9/26.",
    )
    plan = R.plan_document([_offer()], DOC, [tab], set())
    tp = plan["tabs"][0]
    assert [a["row"] for a in tp.args] == [1]
    assert tp.args[0]["offer_amount"] == "15000" and tp.args[0]["offer_date"] == "2026-10-07"
    assert not any(k.startswith("demand") for k in tp.args[0])
    assert tp.args[0]["note"] == (
        "Example Mutual offer; answers our demand of 9/1/26; from 'Offer letter 10-07.pdf' letter"
    )
    assert plan["offers"][0]["status"] == "to_write" and plan["offers"][0]["row"] == 1


def test_an_offer_with_no_open_demand_starts_the_next_row_with_a_sourced_note():
    tab = _tab(
        [
            {
                "row": 0,
                "demand_amount": 50000,
                "demand_date": "2026-09-01",
                "offer_amount": 9000,
                "offer_date": "2026-09-10",
            }
        ],
        "Entered 10/9/26.",
    )
    tp = R.plan_document([_offer()], DOC, [tab], set())["tabs"][0]
    assert tp.args[0]["row"] == 1
    assert tp.args[0]["note"] == "Example Mutual offer, per 'Offer letter 10-07.pdf' letter"


def test_our_demand_then_their_offer_in_one_letter_share_a_row():
    demand = _offer(kind="demand", by="Ashton & Example", amount=90000, date="2026-10-01")
    tp = R.plan_document([_offer(), demand], DOC, [_tab()], {"ashton"})["tabs"][0]
    assert [a["row"] for a in tp.args] == [0, 0]
    assert tp.args[0]["demand_amount"] == "90000" and tp.args[1]["offer_amount"] == "15000"


def test_an_unconfirmed_amount_enters_the_date_only_and_says_check_the_letter():
    plan = R.plan_document([_offer(amount_confirmed=False)], DOC, [_tab()], set())
    args = plan["tabs"][0].args[0]
    assert "offer_amount" not in args and args["offer_date"] == "2026-10-07"
    assert "amount to be checked against the letter" in args["note"]
    assert plan["offers"][0]["date_only"] is True


def test_a_joint_offer_goes_once_on_the_first_plaintiff_marked_joint():
    tabs = [_tab(pidx=1, name="Bo Example"), _tab(pidx=0, name="Ann Example")]
    plan = R.plan_document([_offer(plaintiff_index=None)], DOC, tabs, set())
    assert list(plan["tabs"]) == [0]
    assert plan["tabs"][0].args[0]["note"].startswith("Joint, all plaintiffs. ")


def test_a_firm_kept_tab_is_never_written_and_the_offer_is_reported():
    tab = _tab([{"row": 0, "offer_amount": 1000, "offer_date": "2026-01-01"}], "Firm notes, kept by hand")
    plan = R.plan_document([_offer()], DOC, [tab], set())
    assert not plan["tabs"][0].args
    assert plan["offers"][0]["status"] == "not_entered" and "kept by the firm" in plan["offers"][0]["reason"]


def test_an_offer_already_on_the_tab_is_already_present():
    tab = _tab([{"row": 0, "offer_amount": 15000, "offer_date": "2026-10-07"}], "Entered 10/9/26.")
    plan = R.plan_document([_offer()], DOC, [tab], set())
    assert not plan["tabs"][0].args and plan["offers"] == []


def test_the_same_amount_on_another_date_is_a_possible_duplicate_not_written():
    tab = _tab([{"row": 0, "offer_amount": 15000, "offer_date": "2026-09-07"}], "Entered 10/9/26.")
    plan = R.plan_document([_offer()], DOC, [tab], set())
    assert not plan["tabs"][0].args
    assert plan["offers"][0]["status"] == "possible_duplicate" and "row 1" in plan["offers"][0]["reason"]


def test_reiterations_acceptances_and_rejections_add_no_row():
    events = [_offer(kind=k) for k in ("other", "acceptance", "rejection", "mediation_proposal")]
    plan = R.plan_document(events, DOC, [_tab()], set())
    assert plan["offers"] == [] and not plan["tabs"]


def test_details_only_on_an_empty_tab():
    assert R.details_for(_tab(), "2026-10-12").startswith("Entered 10/12/26 ")
    assert R.details_for(_tab([{"row": 0}], "x"), "2026-10-12") == ""


# ---- the notice --------------------------------------------------------------------------
def test_the_notice_names_matter_party_amount_date_and_the_entry():
    rec = {"event": _offer(), "status": "written", "row": 2}
    text = N.compose({"number": "200123", "title": "Doe v. Example"}, DOC, rec)
    assert text.splitlines() == [
        'New offer on matter 200123, "Doe v. Example".',
        'Example Mutual offer of $15,000 dated 10/7/26, per "Offer letter 10-07.pdf" letter.',
        "Entered in Negotiation Details, row 3.",
    ]
    assert "—" not in text


# The seat's outgoing-email check (overlay shared/output_checklist.py) holds a
# reply carrying an unquoted run of 4+ capitals it does not know, and lets
# double-quoted text through as someone else's words. The firm keeps many matter
# titles and file names in capitals; unquoted, 201167's alert was held unsent
# twice on 2026-10-20. This mirrors that rule's two patterns (_CAPS, _QUOTED).
_CAPS = re.compile(r"\b[A-Z]{4,}\b")
_QUOTED = re.compile(r'"[^"\n]{1,200}"')


def _unquoted_capitals(text: str) -> list[str]:
    spans = [(m.start(), m.end()) for m in _QUOTED.finditer(text)]
    return [m.group(0) for m in _CAPS.finditer(text) if not any(a <= m.start() < b for a, b in spans)]


def test_capitals_in_the_firms_record_names_ride_inside_double_quotes():
    doc = {**DOC, "name": "SETTLEMENT OFFER LETTER 10-07.pdf"}
    rec = {"event": _offer(), "status": "written", "row": 0}
    text = N.compose({"number": "201167", "title": "JONES, MARIA v. ACME TRUCKING"}, doc, rec)
    assert _unquoted_capitals(text) == [], text
    unread = N.compose({"number": "201167", "title": "JONES v. ACME"}, doc, {**rec, "unread": True})
    assert _unquoted_capitals(unread) == [], unread


def test_a_double_quote_in_a_record_name_cannot_end_the_quoting_early():
    doc = {**DOC, "name": 'OFFER "FINAL" LETTER.pdf'}
    rec = {"event": _offer(), "status": "written", "row": 0}
    text = N.compose({"number": "201167", "title": 'SMITH "JUNIOR" v. ACME'}, doc, rec)
    assert _unquoted_capitals(text) == [], text


def test_a_pipe_in_a_record_name_never_reaches_the_alert():
    """Two pipes on a line read as a table row to the outgoing-email check
    (overlay _PIPE_ROW), which held the alert."""
    doc = {**DOC, "name": "Offer | Example Mutual | 10-07.pdf"}
    rec = {"event": {**_offer(), "by": "Example | Mutual"}, "status": "written", "row": 0}
    text = N.compose({"number": "202508", "title": "Doe | Roe | MVA"}, doc, rec)
    assert "|" not in text, text


def test_a_not_entered_notice_asks_for_the_letter_to_be_checked():
    rec = {"event": _offer(), "status": "not_entered", "reason": "the tab's ten rows are full"}
    text = N.compose({"number": "200123", "title": ""}, DOC, rec)
    assert text.endswith("Not entered in Negotiation Details, please check the letter: the tab's ten rows are full.")


# ---- the read's structured answer ----------------------------------------------------------------
def test_parse_takes_only_the_schemas_json():
    assert X.parse(json.dumps({"events": [_offer()]}))[0]["amount"] == 15000
    for bad in ("{}", "not json", json.dumps({"events": "none"})):
        with pytest.raises(ValueError):
            X.parse(bad)


# ---- the run: seed, deliver, cursor ---------------------------------------------------------------
class Seat:
    def __init__(self, files):
        self.files = files
        self.client = SimpleNamespace(get=self._get)

    def _get(self, path, **params):
        assert path == "/matters"
        return (
            {"value": [{"id": M1, "number": "200123", "title": "Doe v. Example"}]}
            if params["Offset"] == 0
            else {"value": []}
        )

    def list_files(self, mid):
        return list(self.files)


class Layout:
    def __init__(self, status="written", rows=None, details=None, events=None):
        self.status = status
        self.rows = rows or []
        self.details = details
        self.calls: list = []
        self.order: list = []

    def get_matter_layouts(self, mid, section=""):
        return {
            "status": "ok",
            "items": [
                {
                    "parent_index": 0,
                    "description": "Dana Example",
                    "negotiation": {"rows": self.rows, "details": self.details},
                }
            ],
        }

    def add_negotiation_rows(self, mid, rows, plaintiff_index=None, details=""):
        self.calls.append((mid, rows, plaintiff_index, details))
        self.order.append("write")
        if self.status == "raise":
            raise RuntimeError("vendor down")
        return {"status": self.status, "entries": [{"status": "written"} for _ in rows]}


def _job(tmp_path, **over):
    jd = tmp_path / "job"
    (jd / "data").mkdir(parents=True)
    (jd / "job.json").write_text(
        json.dumps(
            {
                "kind": "negotiation",
                "job_id": JOB,
                "slug": "example",
                "matter_statuses": ["Open"],
                "firm_words": ["ashton"],
                "per_job_cap_usd": 5.0,
                "monthly_budget_usd": 25.0,
                "month_cents_used": 0,
                **over,
            }
        )
    )
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING))
    return jd, pricing


def _run(tmp_path, seat, layout, monkeypatch, events=None, fail_read=False, **job):
    jd, pricing = _job(tmp_path, **job)

    def read(doorway, doc, text, rows, tabs):
        if fail_read:
            raise RuntimeError("model down")
        return events if events is not None else [_offer()]

    monkeypatch.setattr(run_mod.read_mod, "read_document", read)
    r = run_mod.NegotiationRun(
        jd,
        state_dir=tmp_path / "state",
        pricing=str(pricing),
        seat_factory=lambda: seat,
        layout=layout,
        log=lambda m: None,
    )
    monkeypatch.setattr(r, "_text", lambda mid, f: LETTER)
    return r


FILES = [{"id": "f1", "name": "Intake", "ext": ".pdf", "modified": "1"}]
NEW = FILES + [{"id": "f2", "name": "Offer letter 10-07", "ext": ".pdf", "modified": "1"}]


#: A tab the 2026-10-09 fill already covered: an offer row under the Operator's summary.
COVERED = {
    "rows": [
        {"row": 0, "offer_amount": "9000", "offer_date": "2026-08-01", "note": "Example Mutual offer, per 'x' letter"}
    ],
    "details": "Entered 10/9/26 from the offer letters and emails saved in this file.",
}


def test_the_first_run_seeds_the_cursor_and_delivers_nothing(tmp_path, monkeypatch):
    layout = Layout(**COVERED)
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch).run()
    assert v["verdict"] == "delivered" and v["matters_seeded"] == 1 and v["notices"] == [] and v["docs_read"] == 0
    assert layout.calls == []
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1", "f2": "1"}


SEEDED = [
    {"id": "f1", "name": "Offer letter 9-30", "ext": ".pdf", "modified": "1", "created": "2026-10-08T22:10:00Z"},
    {"id": "f2", "name": "Offer letter 10-09", "ext": ".pdf", "modified": "1", "created": "2026-10-09T15:30:00.123Z"},
]


def test_a_first_run_seed_cutoff_reads_what_was_saved_after_the_fill(tmp_path, monkeypatch):
    """FALSIFIER: seed the whole file set on the first run and the offer saved
    after the 2026-10-09 fill (f2) is never entered or emailed."""
    layout = Layout(**COVERED)
    v = _run(tmp_path, Seat(SEEDED), layout, monkeypatch, seed_saved_before="2026-10-09T14:00:00Z").run()
    assert v["matters_seeded"] == 1 and v["docs_read"] == 1
    assert len(layout.calls) == 1 and len(v["notices"]) == 1 and v["notices"][0]["status"] == "entered"
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1", "f2": "1"}


def test_a_file_saved_exactly_at_the_cutoff_is_new_and_one_before_is_not(tmp_path, monkeypatch):
    files = [
        {**SEEDED[0], "created": "2026-10-09T13:59:59Z"},
        {**SEEDED[1], "created": "2026-10-09T14:00:00Z"},
    ]
    seen: list[str] = []
    r = _run(tmp_path, Seat(files), Layout(**COVERED), monkeypatch, seed_saved_before="2026-10-09T14:00:00Z")
    monkeypatch.setattr(r, "_text", lambda mid, f: seen.append(f["id"]) or LETTER)
    r.run()
    assert seen == ["f2"]


def test_an_unauthored_cutoff_keeps_seeding_everything(tmp_path, monkeypatch):
    layout = Layout(**COVERED)
    v = _run(tmp_path, Seat(SEEDED), layout, monkeypatch).run()
    assert v["docs_read"] == 0 and v["notices"] == [] and layout.calls == []


def test_a_seeded_matter_with_no_offer_entered_reads_its_earlier_offers_quietly(tmp_path, monkeypatch):
    """FALSIFIER (2026-10-12): seed every pre-cutoff file as handled and a
    carrier's tender saved a month before the first run, on a matter whose tab
    holds no offer, is never entered. It is entered now, and NOT emailed as new
    (the firm has had it); the offer saved after the cutoff still is."""
    layout = Layout()
    v = _run(tmp_path, Seat(SEEDED), layout, monkeypatch, seed_saved_before="2026-10-09T14:00:00Z").run()
    assert v["docs_read"] == 2 and len(layout.calls) == 2
    assert v["seed_offers_entered"] == 1 and len(v["notices"]) == 1
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1", "f2": "1"}
    assert (
        not (tmp_path / "state" / "seed-quiet.json").exists()
        or json.loads((tmp_path / "state" / "seed-quiet.json").read_text()) == {}
    )


def test_a_quiet_seed_read_that_fails_stays_quiet_for_the_next_run(tmp_path, monkeypatch):
    layout = Layout()
    _run(
        tmp_path, Seat(SEEDED[:1]), layout, monkeypatch, fail_read=True, seed_saved_before="2026-10-09T14:00:00Z"
    ).run()
    assert json.loads((tmp_path / "state" / "seed-quiet.json").read_text()) == {M1: ["f1"]}
    shutil.rmtree(tmp_path / "job")  # the next run's job dir; the lane's state carries over
    v = _run(tmp_path, Seat(SEEDED[:1]), layout, monkeypatch, seed_saved_before="2026-10-09T14:00:00Z").run()
    assert v["seed_offers_entered"] == 1 and v["notices"] == []


def test_a_seed_whose_tabs_cannot_be_read_is_not_taken_as_empty(tmp_path, monkeypatch):
    """A failed read must not look like "no offer entered" (it would read the whole file)."""
    layout = Layout()
    layout.get_matter_layouts = lambda mid, section="": {"status": "refused"}
    v = _run(tmp_path, Seat(SEEDED), layout, monkeypatch, seed_saved_before="2026-10-09T14:00:00Z").run()
    assert v["seed_offers_entered"] == 0
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1"}  # f1 seeded as handled
    assert not (tmp_path / "state" / "seed-quiet.json").exists()


def test_a_new_offer_is_written_announced_and_the_cursor_moves_after(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = Layout()
    r = _run(tmp_path, Seat(NEW), layout, monkeypatch)
    real_commit = arrivals.commit
    monkeypatch.setattr(
        run_mod.arrivals, "commit", lambda *a, **k: (layout.order.append("commit"), real_commit(*a, **k))
    )
    v = r.run()
    assert layout.order == ["write", "commit"]
    assert len(v["notices"]) == 1 and v["notices"][0]["status"] == "entered"
    assert v["notices"][0]["text"].startswith('New offer on matter 200123, "Doe v. Example".')
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1", "f2": "1"}


def test_an_offer_already_present_gets_no_email(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = Layout(rows=[{"row": 0, "offer_amount": 15000, "offer_date": "2026-10-07"}], details="Entered 10/9/26.")
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch).run()
    assert v["notices"] == [] and layout.calls == []


def test_a_readback_mismatch_is_reported_as_not_entered(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    v = _run(tmp_path, Seat(NEW), Layout(status="readback_mismatch"), monkeypatch).run()
    assert v["notices"][0]["status"] == "not_entered"
    assert "please check the letter" in v["notices"][0]["text"]


def test_a_failed_write_leaves_the_cursor_where_it_was(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    v = _run(tmp_path, Seat(NEW), Layout(status="raise"), monkeypatch).run()
    assert v["verdict"] == "delivered" and v["notices"] == []
    assert arrivals.cursor("negotiation", M1, data=tmp_path / "state") == {"f1": "1"}


def test_a_failed_read_stays_new_then_is_handed_to_the_firm_after_three(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    for attempt in range(1, 4):
        jd = tmp_path / f"t{attempt}"
        jd.mkdir()
        v = _run(jd, Seat(NEW), Layout(), monkeypatch, fail_read=True)
        v.state = tmp_path / "state"
        out = v.run()
        cur = arrivals.cursor("negotiation", M1, data=tmp_path / "state")
        if attempt < 3:
            assert out["notices"] == [] and cur == {"f1": "1"}
        else:
            assert out["notices"][0]["status"] == "not_entered" and cur == {"f1": "1", "f2": "1"}


def test_the_cli_prints_one_verdict_line(tmp_path):
    import subprocess

    jd, _ = _job(tmp_path)
    (jd / "job.json").write_text("{}")
    p = subprocess.run(  # noqa: S603 - argv is this interpreter, the module and a temp job dir
        [sys.executable, "-m", "medchron", "negotiate", str(jd)], capture_output=True, text=True, check=False
    )
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["verdict"] == "failed" and p.returncode == 2
    assert json.loads((jd / "verdict.json").read_text())["verdict"] == "failed"


def test_the_real_read_renders_the_runs_own_tabs_and_parses_the_answer(tmp_path, monkeypatch):
    """The read itself, unstubbed, with the tab shape NegotiationRun._tabs
    produces. FALSIFIER: render a key the tabs do not carry (it read
    ``p["index"]``) and every document fails with KeyError before the model is
    called; the 2026-10-10 dry run on the firm's data caught exactly that."""
    seen = {}

    class Doorway:
        def call(self, stage, **kw):
            seen.update(kw)
            return SimpleNamespace(text=json.dumps({"events": [_offer()]}), stop_reason="end_turn")

    jd, pricing = _job(tmp_path)
    layout = Layout(rows=[{"row": 0, "demand_amount": 50000, "demand_date": "2026-09-01"}], details="Entered 10/9/26.")
    r = run_mod.NegotiationRun(
        jd,
        state_dir=tmp_path / "state",
        pricing=str(pricing),
        seat_factory=lambda: Seat(NEW),
        layout=layout,
        log=lambda m: None,
    )
    tabs = r._tabs(M1)
    events = X.read_document(Doorway(), DOC, "We offer $15,000.", [x for t in tabs for x in t["rows"]], tabs)
    assert events[0]["amount"] == 15000
    prompt = seen["messages"][0]["content"]
    assert "- plaintiff_index 0: Dana Example" in prompt
    assert "- row 0: demand 50000 on 2026-09-01" in prompt and "We offer $15,000." in prompt
    assert seen["model"] == X.MODEL


def test_the_read_asks_for_structured_output_never_a_forced_tool():
    """claude-opus-5-5 refuses tool_choice of type tool or any with a 400; the
    ashton-price dry run (2026-10-10) failed every read on it. FALSIFIER: put
    the forced tool call back and the request carries tool_choice again."""
    from medchron.llm import build_params

    seen = {}

    class Doorway:
        def call(self, stage, **kw):
            seen.update(kw)
            return SimpleNamespace(text=json.dumps({"events": []}), stop_reason="end_turn")

    X.read_document(Doorway(), DOC, "text", [], [{"plaintiff_index": 0, "name": "A"}])
    assert "tool_choice" not in seen and "tools" not in seen
    params = build_params(
        X.STAGE, model=X.MODEL, messages=[{"role": "user", "content": "x"}], max_tokens=10, effort=seen["effort"]
    )
    assert params["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": X.SCHEMA}}
    assert "tool_choice" not in params


def test_a_cut_off_answer_is_a_failed_read_not_an_empty_one():
    class Doorway:
        def call(self, stage, **kw):
            return SimpleNamespace(text='{"events": [', stop_reason="max_tokens")

    with pytest.raises(ValueError, match="max_tokens"):
        X.read_document(Doorway(), DOC, "text", [], [{"plaintiff_index": 0, "name": "A"}])


# ---- the firm's-data dry run, 2026-10-10: each defect it found -----------------------------------
@pytest.mark.parametrize(
    "name,want",
    [
        ("Hospital Encounters 1.25.24+3.7.24", False),
        ("FW: 3d Counter Demand to Offer", True),
        ("Counteroffer from carrier", True),
        ("CCP 998 to defendant", True),
        ("Claim 1998123 records", False),
    ],
)
def test_the_selector_reads_counter_and_998_only_as_words(name, want):
    assert select({"id": "f", "name": name, "ext": ".pdf"}) is want


def test_a_carrier_counter_offer_is_announced():
    """FALSIFIER: notice only offer/998/tender kinds and a carrier's counter-offer
    is written with no email (Kemper's 28k counter, 2026-10-10)."""
    ev = _offer(kind="counter_offer", by="Kemper (adjuster)", amount=28000)
    plan = R.plan_document([ev], DOC, [_tab()], {"ashton"})
    assert plan["offers"] and plan["offers"][0]["status"] == "to_write"


def test_a_bare_mention_of_an_offer_is_no_row_and_no_email():
    """A second-hand "discuss the 998" with no amount and no date (Peschke)."""
    plan = R.plan_document([_offer(kind="998_offer_to_us", amount=None, date=None)], DOC, [_tab()], set())
    assert plan["offers"] == [] and not any(tp.args for tp in plan["tabs"].values())


def test_an_offer_with_no_date_is_held_neither_entered_nor_announced():
    """FALSIFIER (2026-10-12): an undated $100,000 tender was written with no
    date and emailed as "(date not stated)". It is held instead, and listed."""
    for ev in (_offer(date=None), _offer(amount_confirmed=False, date=None)):
        plan = R.plan_document([ev], DOC, [_tab()], set())
        assert plan["offers"] == [] and not any(tp.args for tp in plan["tabs"].values())
        assert plan["held"][0]["status"] == "held"


def test_a_held_offer_is_listed_in_the_verdict_never_emailed(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = Layout()
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=[_offer(date=None)]).run()
    assert v["notices"] == [] and layout.calls == [] and v["offers_held"] == 1
    assert v["held"][0]["file_id"] == "f2" and v["held"][0]["amount"] == 15000


# ---- who wrote the document, and to whom (2026-10-12) -----------------------------------------------
@pytest.mark.parametrize(
    "author,to",
    [
        ("our_firm", "third_party"),  # the firm's letter to a lienholder reciting the offer
        ("our_firm", "paying_side"),  # the firm's own letter quoting the carrier's offer back
        ("paying_side", "third_party"),  # the carrier writing to a medical provider
        ("third_party", "our_firm"),  # a lienholder's letter mentioning the settlement
        ("unknown", "our_firm"),
        ("paying_side", "unknown"),
    ],
)
def test_an_offer_is_entered_only_from_the_paying_side_writing_to_us(author, to):
    """FALSIFIER: drop rows.admissible and each of these documents' recital of
    an offer is entered and announced as a new offer."""
    plan = R.plan_document([_offer(author_side=author, addressed_to=to)], DOC, [_tab()], set())
    assert plan["offers"] == [] and not any(tp.args for tp in plan["tabs"].values())
    assert plan["excluded"] and plan["excluded"][0]["reason"]


@pytest.mark.parametrize("to", ["our_firm", "our_client"])
def test_the_paying_sides_offer_to_the_firm_or_client_is_entered(to):
    plan = R.plan_document([_offer(addressed_to=to)], DOC, [_tab()], set())
    assert plan["offers"][0]["status"] == "to_write"


def test_our_demand_in_our_letter_to_the_carrier_is_entered_but_not_one_to_a_lienholder():
    demand = _offer(kind="demand", by="Ashton for the client", author_side="our_firm", addressed_to="paying_side")
    plan = R.plan_document([demand], DOC, [_tab()], {"ashton"})
    assert any(tp.args for tp in plan["tabs"].values())
    plan = R.plan_document([{**demand, "addressed_to": "third_party"}], DOC, [_tab()], {"ashton"})
    assert not any(tp.args for tp in plan["tabs"].values())


def test_a_carriers_counter_recited_in_our_own_letter_is_not_their_offer():
    ev = _offer(kind="counter_offer", by="Example Mutual", author_side="our_firm", addressed_to="paying_side")
    plan = R.plan_document([ev], DOC, [_tab()], {"ashton"})
    assert plan["offers"] == [] and not any(tp.args for tp in plan["tabs"].values())


def _answer(**doc):
    return json.dumps(
        {
            "unsent_draft": False,
            "author_side": "paying_side",
            "addressed_to": "our_firm",
            "document_date": "2026-09-08",
            **doc,
            "events": [_offer(kind="policy_limits_tender", amount=100000, date=None)],
        }
    )


def test_parse_dates_the_paying_sides_undated_offer_by_its_own_document():
    """The offer IS the letter: an undated tender in the carrier's own email is
    dated by that email, the header's sent date first."""
    assert X.parse(_answer())[0]["date"] == "2026-09-08"
    assert X.parse(_answer(), sent="2026-09-09")[0]["date"] == "2026-09-09"
    e = X.parse(_answer(document_date=None))[0]
    assert e["date"] is None and e["author_side"] == "paying_side" and e["addressed_to"] == "our_firm"


def test_parse_never_dates_a_recital_by_the_reciting_letter():
    """FALSIFIER: date every undated event by its document and the firm's 9/15
    letter to a lienholder would date the carrier's tender 9/15."""
    e = X.parse(_answer(author_side="our_firm", addressed_to="third_party", document_date="2026-09-15"))[0]
    assert e["date"] is None and e["author_side"] == "our_firm"


def test_parse_reads_an_unknown_side_as_unknown():
    e = X.parse(_answer(author_side="the moon", addressed_to=None))[0]
    assert e["author_side"] == "unknown" and e["addressed_to"] == "unknown"


@pytest.mark.parametrize(
    "header,ext,want",
    [
        ("Date: 2026-09-08 12:49:00-07:00", ".msg", "2026-09-08"),
        ("Date: Tue, 08 Sep 2026 12:49:00 -0700", ".eml", "2026-09-08"),
        ("Date: 2026-09-08 12:49:00-07:00", ".pdf", None),
        ("Date: not a date", ".msg", None),
    ],
)
def test_an_emails_sent_date_comes_from_its_header(header, ext, want):
    text = f"From: a@example.com\nTo: b@example.com\n{header}\nSubject: x\n\nbody"
    assert X.sent_date(text, ext) == want


def test_the_schema_asks_who_wrote_the_document_and_to_whom():
    for key in ("author_side", "addressed_to", "document_date"):
        assert key in X.SCHEMA["required"]
    assert "addressed_to" in X.SYSTEM and "third_party" in X.SYSTEM


def test_an_unsent_draft_records_no_event():
    """The firm's own demand draft (placeholders, attorney notes) saved to the
    file must not be entered as a demand made (Livingston-Joy, 2026-10-10)."""
    assert X.parse(json.dumps({"unsent_draft": True, "events": [_offer(kind="demand")]})) == []
    assert "unsent_draft" in X.SCHEMA["required"]


def test_plaintiff_names_come_from_the_matters_contacts(tmp_path, monkeypatch):
    class Client:
        def get(self, path, **params):
            if path == f"/matters/{M1}":
                return {"items": {"Plaintiff": [{"contact": {"id": "c0"}}, {"contact": {"id": "c1"}}]}}
            return {
                "c0": {"person": {"firstName": "Ann", "lastName": "Example"}},
                "c1": {"company": {"name": "Bo LLC"}},
            }[path.rsplit("/", 1)[1]]

    class NamelessLayout(Layout):
        def get_matter_layouts(self, mid, section=""):
            return {"status": "ok", "items": [{"parent_index": 0, "negotiation": {"rows": []}}, {"parent_index": 1}]}

    r = _run(tmp_path, Seat(NEW), NamelessLayout(), monkeypatch)
    r._seat = SimpleNamespace(client=Client())
    assert [t["name"] for t in r._tabs(M1)] == ["Ann Example", "Bo LLC"]


def test_the_email_does_not_repeat_the_matter_number():
    rec = {"event": _offer(), "status": "written", "row": 1}
    text = N.compose({"number": "200226", "title": "200226 - Doe, Dana - Motor Vehicle Accident"}, DOC, rec)
    assert text.splitlines()[0] == 'New offer on matter 200226, "Doe, Dana - Motor Vehicle Accident".'
    assert "accepted" not in text


def test_an_accepted_offer_is_entered_but_never_emailed(tmp_path, monkeypatch):
    """The firm asked to hear "when new offer is received" so an attorney can
    act; an offer the same document shows was accepted (Peschke's 9/23 tender,
    accepted and paid) is not actionable. FALSIFIER: notice every written offer
    and the firm is emailed about a settled case."""
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    events = [
        _offer(kind="policy_limits_tender", amount=956000, date="2026-09-23"),
        _offer(kind="acceptance", by="Ashton & Price for the client", amount=956000, date="2026-09-23"),
    ]
    layout = Layout()
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=events).run()
    assert len(layout.calls) == 1 and layout.calls[0][1][0]["offer_amount"] == "956000"
    assert v["notices"] == []


def test_an_outstanding_offer_is_emailed_however_old_its_date(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    events = [_offer(amount=9000, date="2025-01-15")]
    v = _run(tmp_path, Seat(NEW), Layout(), monkeypatch, events=events).run()
    assert len(v["notices"]) == 1 and "dated 1/15/25" in v["notices"][0]["text"]


def test_an_offer_the_same_document_accepted_is_marked():
    events = [_offer(), _offer(kind="acceptance", by="Ashton client", date="2026-10-08")]
    plan = R.plan_document(events, DOC, [_tab()], {"ashton"})
    assert plan["offers"][0]["accepted"] == "2026-10-08"


# ---- "answers our ..." names what the earlier row IS (2026-10-10) ---------------------------------
PESCHKE_ROW = {
    "row": 0,
    "demand_amount": "1000000",
    "demand_date": "2026-07-28",
    "note": "Our 998 offer, per 'Peschke - 998 to Def Rahimi' letter; no response in the file",
}
NERSESYAN_ROW = {
    "row": 2,
    "demand_amount": "500000",
    "demand_date": "2026-10-02",
    "note": "Our counter, per '3d Counter Demand to Offer Claim Number: 25123597817' email; no response in the file",
}


def test_an_offer_answering_our_998_offer_says_998_offer_not_demand():
    """FALSIFIER: name every earlier row "our demand" and the firm's $1,000,000
    998 offer is recorded in its Smokeball as a demand."""
    tab = _tab([PESCHKE_ROW], "Entered 10/9/26.")
    ev = _offer(kind="policy_limits_tender", amount=956000, date="2026-09-23")
    note = R.plan_document([ev], DOC, [tab], set())["tabs"][0].args[0]["note"]
    assert "; answers our 998 offer of 7/28/26; " in note and "demand" not in note


def test_an_offer_answering_our_counter_says_counter():
    tab = _tab([{"row": 0, "demand_amount": "1", "offer_amount": "1"}, {**NERSESYAN_ROW, "row": 1}], "Entered 10/9/26.")
    ev = _offer(kind="counter_offer", by="Kemper", amount=28000, date="2026-10-09")
    note = R.plan_document([ev], DOC, [tab], set())["tabs"][0].args[0]["note"]
    assert "; answers our counter of 10/2/26; " in note


def test_an_unknown_earlier_row_is_named_by_its_date_and_figure_never_guessed():
    tab = _tab(
        [{"row": 0, "demand_amount": "1000000", "demand_date": "2026-07-28", "note": "per phone call"}], "Entered x"
    )
    note = R.plan_document([_offer()], DOC, [tab], set())["tabs"][0].args[0]["note"]
    assert "; answers our 7/28/26 figure of $1,000,000; " in note


def test_a_joint_prefix_does_not_hide_the_rows_kind():
    row = {**PESCHKE_ROW, "note": "Joint, all plaintiffs. Our demand, per 'x' letter"}
    note = R.plan_document([_offer()], DOC, [_tab([row], "Entered x")], set())["tabs"][0].args[0]["note"]
    assert "; answers our demand of 7/28/26; " in note
