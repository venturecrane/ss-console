"""A repair never adds a fact, and a set whose repairs run out ends in one clear state.

The 2026-11-05 District discovery job, as falsifiers: the audit flagged a
request, the repair rewrote it on a premise no source and no earlier text
holds, and later repairs looped and spent until the set failed and SMD
stopped it by hand. Each rule below has its own test, written so that undoing
that one rule fails it (a mutation per rule).

Every name, number and sentence is invented (Example / Exampletown).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from drafting_testkit import firm_data
from jobs.drafting import firm as firm_mod, newfacts, notes as notes_mod, repair_budget, repair_guard
from jobs.drafting.outcome import DraftingFailed
from jobs.proof import gate
from test_drafting_proof_findings import AuditClient, _drafts, pricing  # noqa: F401 - pricing is a pytest fixture, used by name
from test_drafting_review import KINDS, _run, one_set

RFA = "Requests for Admission"
SOURCE = (
    "The incident occurred on March 3, 2024 at the Exampletown store. Officer Smith wrote "
    '"the floor was wet near aisle 4". Itemized statement total charges 1500.00.'
)
BEFORE = "REQUEST FOR ADMISSION NO. 2\n\nAdmit that YOU owned the PREMISES on the date of the INCIDENT."
CLEAN = "- claim | SUPPORTED | cite\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    from test_drafting_review import CHECKER

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def _base() -> newfacts.Known:
    return newfacts.known(SOURCE).plus(BEFORE)


def _new(text: str) -> list[tuple[str, str]]:
    return newfacts.new_facts(text, _base())


# ---- rule 1: no new fact, by kind ---------------------------------------------------------------


def test_a_name_no_source_holds_is_new_and_one_the_file_holds_is_not():
    assert ("name", "Diaz") in _new("Admit that Officer Diaz inspected the PREMISES.")
    assert _new("Admit that Officer Smith inspected the PREMISES.") == []


def test_a_capital_that_only_starts_a_sentence_or_a_line_is_not_a_name():
    assert _new("Admit that YOU owned the PREMISES. Further, admit it.\n\nDescribe the PREMISES.") == []


def test_a_date_is_known_by_its_value_whatever_its_format():
    assert _new("Admit that YOU owned the PREMISES on 03/03/2024.") == []
    assert ("date", "April 9, 2024") in _new("Admit that YOU owned the PREMISES on April 9, 2024.")
    assert ("date", "June 1") in _new("Admit that YOU received a letter dated June 1.")


def test_a_dollar_figure_is_known_by_its_value():
    assert _new("Admit that the charges were $1,500.") == []
    assert ("dollar figure", "$2,750") in _new("Admit that the repair cost $2,750.")


def test_a_number_is_new_unless_known_and_a_request_label_is_never_one():
    assert ("number", "17") in _new("Admit that YOU had 17 employees.")
    assert _new("Admit, as in REQUEST FOR ADMISSION No. 31, that YOU owned the PREMISES near aisle 4.") == []


def test_a_quotation_must_be_in_the_known_text_and_a_term_label_is_not_one():
    assert _new('Admit that Officer Smith wrote "the floor was wet".') == []
    assert ("quoted string", "the floor was slick") in _new('Admit that Officer Smith wrote "the floor was slick".')
    assert _new('"STORE INCIDENT" means the INCIDENT at the store.') == []


def test_a_marker_states_no_fact():
    assert _new("Admit that YOU owned the PREMISES. {{NOT IN RECORD: Officer Diaz, 5/6/2023, $9,999}}") == []


def test_the_text_being_repaired_counts_as_known():
    base = newfacts.known(SOURCE)
    assert ("name", "Quill") in newfacts.new_facts("Admit that Quill Example signed it.", base)
    assert newfacts.new_facts("Admit that Quill Example signed it.", base.plus("signed by Quill Example")) == []


# ---- rule 1: the vet, per request ---------------------------------------------------------------


def _doc(items: dict[int, str]) -> str:
    body = "\n\n".join(f"REQUEST FOR ADMISSION NO. {n}\n\n{t}" for n, t in items.items())
    return f'# DEFINITIONS\n\n1. "PREMISES" means the store.\n\n# REQUESTS FOR ADMISSION\n\n{body}\n'


def test_only_the_request_that_adds_a_fact_is_put_back_and_the_other_repairs_are_kept():
    before = _doc({1: "Admit that YOU owned the store.", 2: "Admit that YOU swept it."})
    after = _doc({1: "Admit that YOU owned the PREMISES.", 2: "Admit that Officer Diaz swept it."})
    vetted, refused = repair_guard.vet(before, after, newfacts.known(SOURCE), set())
    assert [r.key for r in refused] == [("REQUESTS FOR ADMISSION", 2)]
    assert "Admit that YOU owned the PREMISES." in vetted  # kept
    assert "Admit that YOU swept it." in vetted and "Diaz" not in vetted  # put back as it was


def test_a_frozen_request_is_never_changed_again():
    before = _doc({1: "Admit that YOU owned the store.", 2: "Admit that YOU swept it."})
    after = _doc({1: "Admit that YOU owned the store.", 2: "Admit that YOU swept the store."})
    vetted, refused = repair_guard.vet(before, after, newfacts.known(SOURCE), {("REQUESTS FOR ADMISSION", 2)})
    assert refused == [] and "Admit that YOU swept it." in vetted and "swept the store" not in vetted


def test_a_repair_with_no_new_fact_is_returned_unchanged():
    before = _doc({1: "Admit that YOU owned the store."})
    after = _doc({1: "Admit that YOU owned the store at aisle 4."})
    assert repair_guard.vet(before, after, newfacts.known(SOURCE), set()) == (after, [])


# ---- rule 2: the budget, from firm config ---------------------------------------------------------


class _Run:
    def __init__(self, tmp_path: Path, budget: dict, spent: list[float]):
        self.firm = type("F", (), {"data": {"budget": budget}})()
        self.budget = type("B", (), {"refresh": staticmethod(lambda: spent[0])})()
        self.data = tmp_path
        self.job = type("J", (), {"job_id": "01TESTJOB0000000000000AAAA"})()


def _plan(tmp_path: Path, budget: dict, spent: list[float]) -> repair_budget.Plan:
    u = type("U", (), {"key": "set-1", "label": RFA, "data": tmp_path})()
    return repair_budget.plan(_Run(tmp_path, budget, spent), u)


def test_the_rounds_and_the_dollar_cap_come_from_the_firm_with_defaults(tmp_path):
    p = _plan(tmp_path, {"per_job_cap_usd": 40.0}, [0.0])
    assert (p.rounds, p.cap_usd) == (repair_budget.DEFAULT_ROUNDS, 10.0)
    p = _plan(tmp_path, {"per_job_cap_usd": 40.0, "repair_rounds": 3, "repair_cap_usd": 2.5}, [0.0])
    assert (p.rounds, p.cap_usd) == (3, 2.5)
    assert p.over(3) is None and p.over(4) == "after 3 repairs"


def test_the_dollar_cap_stops_the_next_round_and_counts_only_repair_spend(tmp_path):
    spent = [7.0]  # compose, digest and the first audit: not repairs
    p = _plan(tmp_path, {"per_job_cap_usd": 40.0, "repair_cap_usd": 2.0}, spent)
    p.begin()
    spent[0] = 8.5
    assert p.over(2) is None
    spent[0] = 9.0
    assert "repair cap spent" in (p.over(3) or "")


def test_code_decides_hold_or_file_by_kind():
    assert repair_budget.decide({"COMPOUND", "UNDEFINED"}) == "file"
    for k in ("DRIFTS", "INVENTED", "ARITHMETIC", "NOT_VERBATIM", "MISSING_ITEM", "MECHANICAL", "SOMETHING_NEW"):
        assert repair_budget.decide({"COMPOUND", k}) == "hold"


def _audit(rows: str) -> str:
    return f"# Audit v2\n\nTallies: {{}}\n\n## AUDIT: REQUESTS FOR ADMISSION\n\n{rows}\n"


def test_running_out_on_form_findings_files_them_as_open_items_and_records_one_alert(tmp_path):
    p = _plan(tmp_path, {"per_job_cap_usd": 40.0}, [0.0])
    p.begin()
    g = repair_guard.Guard(tmp_path, SOURCE)
    draft = _doc({1: "Admit that YOU owned and swept the store."})
    audit = _audit("- `RFA 1: owned and swept` | COMPOUND | two facts in one request")
    assert p.close(g, audit, draft, 2, "after 8 repairs") == 2
    items = repair_budget.open_items(tmp_path)
    assert items == [
        {
            "where": "REQUESTS FOR ADMISSION, No. 1",
            "text": "REQUEST FOR ADMISSION NO. 1 Admit that YOU owned and swept the store.",
            "kind": repair_budget.KIND_WORDS["COMPOUND"],
            "finding": "two facts in one request",
        }
    ]
    rec = json.loads((tmp_path / repair_budget.EXHAUSTED).read_text())
    assert rec == [
        {
            "job_id": "01TESTJOB0000000000000AAAA",
            "set": RFA,
            "kinds": ["COMPOUND"],
            "count": 1,
            "why": "after 8 repairs",
        }
    ]


def test_running_out_on_a_finding_that_changes_what_a_request_asks_holds_with_the_kinds(tmp_path):
    p = _plan(tmp_path, {"per_job_cap_usd": 40.0}, [0.0])
    g = repair_guard.Guard(tmp_path, SOURCE)
    audit = _audit("- `RFA 1: owned` | DRIFTS | the file says otherwise\n- `RFA 1: owned` | COMPOUND | two")
    with pytest.raises(DraftingFailed) as e:
        p.close(g, audit, _doc({1: "Admit that YOU owned the store."}), 3, "after 8 repairs")
    assert str(e.value).startswith("audit_unrepaired: 2 audit finding(s) (COMPOUND, DRIFTS) in the Requests")
    assert "store" not in str(e.value)  # kinds and counts only: a reason reaches SMD's alerts
    assert not (tmp_path / repair_budget.OPEN).exists()


def test_the_lane_pages_once_naming_the_job_the_set_and_the_kinds(tmp_path, monkeypatch):
    from jobs.proof import watch

    data = tmp_path / "jobs" / "J1" / "data"
    data.mkdir(parents=True)
    (data / repair_budget.EXHAUSTED).write_text(
        json.dumps([{"job_id": "J1", "set": RFA, "kinds": ["COMPOUND"], "count": 1, "why": "x"}])
    )
    sent = []
    monkeypatch.setattr(watch, "alert", lambda d, job, code, detail="": sent.append((job, code, detail)) or True)
    lane = type("L", (), {"job_dir": staticmethod(lambda j: tmp_path / "jobs" / j)})()
    repair_budget.alert_exhausted(lane, "J1")
    repair_budget.alert_exhausted(lane, "J1")
    assert sent == [("J1", "repair_unconverged", "job J1: Requests for Admission: 1 unresolved (COMPOUND)")]
    assert repair_budget.REPAIR_ALERT in gate.CODES


def test_the_open_items_have_a_code_heading_that_a_rewrite_never_takes_for_smds(tmp_path):
    (tmp_path / repair_budget.OPEN).write_text(
        json.dumps(
            [{"where": "REQUESTS FOR ADMISSION, No. 1", "text": "Admit it.", "kind": "asks two things", "finding": "f"}]
        )
    )
    lines = notes_mod.open_lines(tmp_path, None)
    assert lines == ['REQUESTS FOR ADMISSION, No. 1 reads "Admit it.". It asks two things (f).']
    md = f"# Attorney notes: x\n\n## {notes_mod.OPEN_ITEMS_HEADING}\n\n- {lines[0]}\n"
    assert notes_mod.smd_sections(md) == []


def test_the_firm_may_author_the_repair_budget_and_never_a_non_positive_one(tmp_path):
    import yaml

    from drafting_testkit import make_inputs

    made = []

    def problems(**budget):
        made.append(1)
        root = make_inputs(tmp_path / f"firm{len(made)}")
        data = yaml.safe_load((root / "drafting-firm.yaml").read_text())
        data["budget"].update(budget)
        return firm_mod.validate(data)

    assert problems(repair_rounds=4, repair_cap_usd=6.0) == []
    assert {"budget.repair_rounds: must be > 0", "budget.repair_cap_usd: must be > 0"} <= set(
        problems(repair_rounds=0, repair_cap_usd=-1)
    )
    assert "budget.repair_rounds: a whole number" in problems(repair_rounds=2.5)


def test_new_facts_compares_money_by_value():
    f = newfacts.read("paid $1,500.00 and 1500.00")
    assert {v for v, _ in f.money} == {Decimal("1500.00")}


# ---- the 2026-11-05 job, end to end ---------------------------------------------------------------


def _flag(word: str, verdict: str, n: int = 2):
    def answer(params):
        body = params["messages"][-1]["content"]
        if word in body:
            t = {k: int(k == verdict) for k in ("DRIFTS", "INVENTED", "ARITHMETIC")}
            tally = f"SUPPORTED=0 DRIFTS={t['DRIFTS']} INVENTED={t['INVENTED']} ARITHMETIC={t['ARITHMETIC']}"
            extra = "" if verdict in t else f" {verdict}=1"
            return f"- `RFA {n}: {word}` | {verdict} | the file does not bear this out\n{tally}{extra}"
        return CLEAN

    return answer


def _with_request_2(text: str) -> str:
    return one_set(RFA).replace(
        f"REQUEST FOR ADMISSION NO. 2\n\n{KINDS[RFA][2]}", f"REQUEST FOR ADMISSION NO. 2\n\n{text}"
    )


def test_a_repair_that_invents_a_premise_is_refused_not_retried_and_the_set_held(tmp_path, pricing):
    """The audit flags No. 2; the repair rewrites it around a meeting with an
    officer on a date no source holds. Refused: No. 2 goes back to what it
    was, the repair is never asked again, and a premise the file does not
    bear out is not a form defect, so the set holds for SMD. On origin/main
    the invented request was accepted and delivered."""
    client = AuditClient(
        drafts=_drafts(**{RFA: _with_request_2("Admit that YOU owned the WRONGSTORE.")}),
        audit_fn=_flag("WRONGSTORE", "DRIFTS"),
        repair_fix=("YOU owned the WRONGSTORE.", "YOU met Officer Diaz on April 9, 2026 about the PREMISES."),
    )
    r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("audit_unrepaired: 1 audit finding(s) (DRIFTS)"), v.reason
    assert "refused for adding a fact" in v.reason and "Diaz" not in v.reason
    assert client.count("REPAIR-PROMPT") == 1
    assert seat.created == [] and seat.sent == []
    rec = json.loads((r.data / "set-1" / repair_guard.STATE).read_text())["rejected"]
    assert rec[0]["key"] == ["REQUESTS FOR ADMISSION", 2]
    assert {k for k, _ in rec[0]["facts"]} == {"name", "date"}
    kept = (r.data / "set-1" / "draft-v2.md").read_text()
    assert "Diaz" not in kept and "Admit that YOU owned the WRONGSTORE." in kept


def test_a_refused_repair_of_a_form_defect_files_the_set_with_the_request_as_an_open_item(tmp_path, pricing):
    compound = "Admit that YOU owned the PREMISES and that YOU swept the floor daily."
    client = AuditClient(
        drafts=_drafts(**{RFA: _with_request_2(compound)}),
        audit_fn=_flag("swept the floor daily", "COMPOUND"),
        repair_fix=("and that YOU swept the floor daily", "on the day Officer Diaz inspected it"),
    )
    r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert client.count("REPAIR-PROMPT") == 1
    assert "Diaz" not in (r.data / "set-1" / "draft-final.md").read_text()
    notes = (r.data / "notes-final.md").read_text()
    assert f"## {notes_mod.OPEN_ITEMS_HEADING}" in notes
    assert "swept the floor daily" in notes and repair_budget.KIND_WORDS["COMPOUND"] in notes
    rec = json.loads((r.data / repair_budget.EXHAUSTED).read_text())
    assert [(x["set"], x["kinds"]) for x in rec] == [(RFA, ["COMPOUND"])]


def test_the_firms_round_budget_ends_a_set_that_keeps_finding_new_form_defects(tmp_path, pricing):
    """Every repair reword clears nothing new-fact-wise but the reader flags
    the reworded request again: one authored round, then the set files."""
    over = {"budget": {**firm_data()["budget"], "repair_rounds": 1}}
    compound = "Admit that YOU owned the PREMISES and that YOU swept the floor daily."
    client = AuditClient(
        drafts=_drafts(**{RFA: _with_request_2(compound)}),
        audit_fn=_flag("floor", "COMPOUND"),
        fixes=[("swept the floor daily", "swept the floor each day"), ("each day", "every day")],
    )
    r, v, _ = _run(tmp_path, pricing, client, inputs_over=over)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert client.count("REPAIR-PROMPT") == 1
    rec = json.loads((r.data / repair_budget.EXHAUSTED).read_text())
    assert rec[0]["why"] == "after 1 repairs" and rec[0]["kinds"] == ["COMPOUND"]
    assert "swept the floor each day" in (r.data / "notes-final.md").read_text()


def test_the_format_checks_one_repair_is_kept_only_where_it_adds_no_fact(tmp_path, monkeypatch):
    """The format check's one whole-document repair is read the same way: a
    paragraph it adds around an officer and a date no source holds is put back."""
    from drafting_testkit import ScriptedClient, seat_with, standard_docs
    from jobs.drafting import compose, format_check
    from jobs_testkit import PRICING
    from test_drafting_run import _run as run_brief

    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING))
    real, calls = format_check.check, {"check": 0}

    def check(*a, **k):
        calls["check"] += 1
        if calls["check"] == 1:
            r = format_check.Result()
            r.model("sections: 'VII. CAUSATION' missing or out of the authored order")
            return r
        return real(*a, **k)

    def repair_format(self, doc, findings):
        return doc.replace(
            "The record supports this section",
            "Officer Diaz confirmed this on April 9, 2026. The record supports this section",
            1,
        )

    monkeypatch.setattr(format_check, "check", check)
    monkeypatch.setattr(compose.Drafter, "repair_format", repair_format)
    r, v, _ = run_brief(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    assert v.outcome == "delivered", v.reason
    assert "Diaz" not in (r.data / "draft-final.md").read_text()
    rec = json.loads((r.data / repair_guard.STATE).read_text())["rejected"]
    assert [x["at"] for x in rec] == ["format"]


def test_a_refused_repair_of_a_premise_holds_at_once_and_pays_for_no_further_round(tmp_path, pricing):
    """No. 2's repair invents a premise and is refused; No. 3's repair is fine.
    The set will hold whatever No. 3 does, so it holds now: no delta audit of
    No. 3's repair is bought (no ``audit-v2.md``)."""

    def answer(params):
        body = params["messages"][-1]["content"]
        rows = []
        if "WRONGSTORE" in body:
            rows.append("- `RFA 2: WRONGSTORE` | DRIFTS | the file does not bear this out")
        if "swept the floor daily" in body:
            rows.append("- `RFA 3: swept` | COMPOUND | two facts in one request")
        if not rows:
            return CLEAN
        drifts = sum("DRIFTS" in r for r in rows)
        return "\n".join(rows) + f"\nSUPPORTED=0 DRIFTS={drifts} INVENTED=0 ARITHMETIC=0 COMPOUND={len(rows) - drifts}"

    doc = _with_request_2("Admit that YOU owned the WRONGSTORE.").replace(
        f"REQUEST FOR ADMISSION NO. 3\n\n{KINDS[RFA][2]}",
        "REQUEST FOR ADMISSION NO. 3\n\nAdmit that YOU owned the PREMISES and that YOU swept the floor daily.",
    )
    client = AuditClient(
        drafts=_drafts(**{RFA: doc}),
        audit_fn=answer,
        fixes=[("YOU owned the WRONGSTORE.", "YOU met Officer Diaz about the PREMISES.")],
        repair_fix=(" and that YOU swept the floor daily", ""),
    )
    r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and "refused for adding a fact" in v.reason, v.reason
    assert client.count("REPAIR-PROMPT") == 1
    assert not (r.data / "set-1" / "audit-v2.md").exists()


def test_the_replay_judges_a_finished_jobs_own_rounds_at_no_cost(tmp_path):
    """The rehearsal tool: reads a job's drafts, digest and record text, calls
    nothing, and names the refused request and the kinds, not the words."""
    data, u = tmp_path / "data", tmp_path / "data" / "set-1"
    u.mkdir(parents=True)
    (data / "digest.md").write_text("## MEDICAL | ER record | 01/15/2026\nOfficer Smith noted the spill.\n")
    text = tmp_path / "elsewhere" / "doc1.txt"
    text.parent.mkdir()
    text.write_text("Signed by Quill Example.")
    (data / "extracted.jsonl").write_text(json.dumps({"name": "d", "text_path": "/opt/data/x/doc1.txt"}) + "\n")
    (u / "draft-v1.md").write_text(_doc({1: "Admit that YOU owned the store.", 2: "Admit it."}))
    (u / "draft-v2.md").write_text(
        _doc({1: "Admit that Quill Example owned the store.", 2: "Admit that Officer Diaz saw it."})
    )
    (u / "draft-v3.md").write_text(_doc({1: "Admit that Quill Example owned the store.", 2: "Admit it."}))
    out = repair_guard.replay(data)
    assert out[0].startswith("sources: digest") and "record text 1 of 1 files" in out[0]
    assert out[1] == "set-1 repair v1->v2: REFUSED 1 (REQUESTS FOR ADMISSION No. 2); new facts by kind {'name': 1}"
    assert out[2] == "set-1 repair v2->v3: kept"
    assert "Diaz" not in "\n".join(out) and "Diaz" in "\n".join(repair_guard.replay(data, show=True))


# ---- the job index: read once, cached, a lookup per repair (the 11-05 rehearsal) -------------


def test_the_job_index_is_built_once_cached_by_sha_and_holds_no_text(tmp_path, monkeypatch):
    built = []
    real = newfacts.Builder.feed

    def counting(self, text):
        built.append(len(text))
        return real(self, text)

    monkeypatch.setattr(newfacts.Builder, "feed", counting)
    newfacts._MEMO.clear()
    k = newfacts.index(lambda: (SOURCE,), tmp_path)
    assert built and k.flat is None  # the record's text is never kept
    assert k.has_quote(newfacts._flat("the floor was wet near aisle 4")) and not k.has_quote("the floor was slick")
    cached = list(tmp_path.glob("known-index-*"))
    assert sorted(p.suffix for p in cached) == [".bin", ".json"]
    assert SOURCE.split()[3] not in cached[0].with_suffix(".bin").read_bytes().decode("latin-1")
    newfacts._MEMO.clear()
    built.clear()
    again = newfacts.index(lambda: (SOURCE,), tmp_path)
    assert built == [] and again.words == k.words  # read from the cache, not rebuilt
    built.clear()
    newfacts.index(lambda: (SOURCE + " more",), tmp_path)
    assert built  # another corpus, another key


def test_a_guard_given_text_uses_the_jobs_cached_index(tmp_path):
    newfacts._MEMO.clear()
    (tmp_path / "set-1").mkdir()
    g = repair_guard.Guard(tmp_path / "set-1", SOURCE)
    assert isinstance(g.sources, newfacts.Known)
    assert list(tmp_path.glob("known-index-*.json"))  # in the job's data dir, not the set's
    assert repair_guard.Guard(tmp_path / "set-1", SOURCE).sources is g.sources


def test_the_replay_lists_the_copy_once_and_never_searches_per_file(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "set-1").mkdir(parents=True)
    rows = []
    for i in range(30):
        f = tmp_path / "texts" / f"doc{i}.txt"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"Record {i} signed by Quill Example.")
        rows.append(json.dumps({"name": f"d{i}", "text_path": f"/opt/data/jobs/x/doc{i}.txt"}))
    (data / "extracted.jsonl").write_text("\n".join(rows) + "\n")
    walks = []
    real_walk = repair_guard.os.walk
    monkeypatch.setattr(repair_guard.os, "walk", lambda p: walks.append(p) or real_walk(p))
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rglob per row")))
    files, listed = repair_guard._record_files(data)
    assert (len(files), listed, len(walks)) == (30, 30, 1)


def test_a_long_source_is_read_in_slices_and_a_quotation_across_a_cut_is_still_known(tmp_path, monkeypatch):
    monkeypatch.setattr(newfacts.Builder, "SLICE", 16)
    newfacts._MEMO.clear()
    text = "alpha bravo charlie\ndelta echo foxtrot\ngolf hotel india\n"
    k = newfacts.index(lambda: (text,), tmp_path)
    assert k.has_quote("bravo charlie delta echo") and k.has_quote("foxtrot golf hotel india")
    assert not k.has_quote("bravo charlie echo delta")
