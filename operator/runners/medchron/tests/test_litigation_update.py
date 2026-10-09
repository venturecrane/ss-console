"""Update mode: a matter on the list is kept current from the arrived
documents; code decides which of the read's changes stand."""

from __future__ import annotations

import copy
import datetime as dt
import re
from types import SimpleNamespace

from medchron.litigation import manifest, update, vocab


def _src(fid):
    return {"file_id": fid, "name": fid, "doc_date": None}


PRIOR = {
    "case_name": "A v. B",
    "court": "Sacramento",
    "case_number": "24CV1",
    "firm_role": "plaintiff",
    "case_status": {"value": vocab.ACTIVE, "detail": None, "source": _src("old-complaint")},
    "complaint_filed": {"date": "2026-03-02", "source": _src("old-complaint")},
    "next_court_date": {"date": "2026-11-01", "event": "CMC", "source": _src("old-notice")},
    "defendants": [
        {
            "name": "Zeta Corp",
            "status": vocab.SERVED_NO_ANSWER,
            "out_for_service": {"value": None, "source": None},
            "served": {"date": "2026-04-01", "method": "personal", "source": _src("old-pos")},
            "answered": {"date": None, "source": None},
            "flags": [],
        }
    ],
    "discovery_propounded": [
        {
            "set": "Form Interrogatories, Set One",
            "served_on": "Zeta Corp",
            "date": "2026-05-01",
            "source": _src("old-frog"),
        }
    ],
    "discovery_served_on_client": [],
}
TEXTS = {"new-answer": "ANSWER of Zeta Corp ... filed 09/30/2026", "new-email": "see attached"}
ALL = list(vocab.GROUPS)


def test_a_cited_answer_date_stands():
    fresh = copy.deepcopy(PRIOR)
    d = fresh["defendants"][0]
    d["answered"] = {"date": "2026-09-30", "source": _src("new-answer")}
    d["status"] = vocab.ANSWERED
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["defendants"][0]["answered"]["date"] == "2026-09-30"
    assert "defendants[Zeta Corp].answered" in log["changed"]
    assert "defendants[Zeta Corp].status" in log["changed"]  # a status change always goes to the second read
    assert log["reverted"] == []


def test_a_date_the_cited_document_does_not_carry_is_put_back():
    fresh = copy.deepcopy(PRIOR)
    fresh["defendants"][0]["answered"] = {"date": "2026-09-29", "source": _src("new-answer")}
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["defendants"][0]["answered"]["date"] is None
    assert "defendants[Zeta Corp].answered" in log["reverted"]


def test_a_change_citing_an_old_document_is_put_back():
    fresh = copy.deepcopy(PRIOR)
    fresh["complaint_filed"] = {"date": "2026-03-01", "source": _src("old-complaint")}
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["complaint_filed"]["date"] == "2026-03-02"
    assert log["reverted"] == ["complaint_filed"]


def test_a_new_document_cannot_unserve_a_defendant_or_drop_one():
    fresh = copy.deepcopy(PRIOR)
    fresh["defendants"][0]["served"] = {"date": None, "method": None, "source": None}
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["defendants"][0]["served"]["date"] == "2026-04-01"
    fresh2 = copy.deepcopy(PRIOR)
    fresh2["defendants"] = []
    out2, log2 = update.guard(PRIOR, fresh2, ALL, ["new-answer"], TEXTS)
    assert [d["name"] for d in out2["defendants"]] == ["Zeta Corp"]
    assert any("dropped by the read" in x for x in log2["reverted"])


def test_a_dropped_discovery_set_is_restored_and_an_uncited_new_one_refused():
    fresh = copy.deepcopy(PRIOR)
    fresh["discovery_propounded"] = [
        {
            "set": "Special Interrogatories, Set One",
            "served_on": "Zeta Corp",
            "date": "2026-09-30",
            "source": _src("old-frog"),
        }
    ]
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert [r["set"] for r in out["discovery_propounded"]] == ["Form Interrogatories, Set One"]
    assert len(log["reverted"]) == 2


def test_identity_fields_are_not_an_updates_to_change():
    fresh = copy.deepcopy(PRIOR)
    fresh["case_number"] = "24CV9"
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["case_number"] == "24CV1" and "case_number" in log["reverted"]


def test_an_unchanged_copy_changes_nothing():
    out, log = update.guard(PRIOR, copy.deepcopy(PRIOR), ALL, ["new-answer"], TEXTS)
    assert out == PRIOR and log == {"changed": [], "reverted": []}


FIRM = SimpleNamespace(
    court_rx=[re.compile(r"(?i)\banswer\b|\bcomplaint\b")],
    process_servers=["one legal"],
    discovery_rx=[re.compile(r"(?i)interrogator")],
    settlement_rx=[re.compile(r"(?i)\bsettle")],
)


def test_screen_keeps_only_emails_whose_text_carries_litigation_words():
    p = update.plan_changed([], ["e1", "e2", "e3"], {})
    assert p["read_groups"] == [] and p["candidates"] == ["e1", "e2", "e3"]
    texts = {"e1": "lunch on friday?", "e2": "we can settle at 50k", "e3": "One Legal served the summons"}
    s = update.screen_plan(p, texts, FIRM)
    assert s["trigger_files"] == ["e2", "e3"] and s["screened_out"] == ["e1"]
    assert s["read_groups"] == [vocab.GROUP_CASE, vocab.GROUP_DEFENDANTS]


def test_screen_reads_how_people_write_about_a_hearing_in_mail():
    # 2026-10-06: "Mersberg: CMC Statement - hearing set for 10/21" was screened
    # out on the firm's file-name patterns alone.
    s = update.screen_plan(update.plan_changed([], ["e1"], {}), {"e1": "the hearing is set for 10/21"}, FIRM)
    assert s["trigger_files"] == ["e1"] and vocab.GROUP_CASE in s["read_groups"]


def test_a_court_form_date_printed_in_spaced_characters_is_carried():
    from medchron.litigation import gates

    text = "set for a Case Management Conference on  1 / 2 5 / 2 0 2 7 at 9:00 AM"
    assert gates.text_carries(text, "2027-01-25")
    assert not gates.text_carries(text, "2027-01-26")  # every digit still has to match


def test_an_email_whose_text_could_not_be_read_is_read_not_screened_out():
    s = update.screen_plan(update.plan_changed([], ["e1"], {}), {}, FIRM)
    assert s["trigger_files"] == ["e1"] and s["read_groups"] == list(vocab.GROUPS)


def test_a_reworded_discovery_set_is_the_same_row_not_a_second_one():
    fresh = copy.deepcopy(PRIOR)
    fresh["discovery_propounded"] = [
        {
            "set": "Form Interrogatories - General, Set 1",
            "served_on": "Defendant Zeta Corp",
            "date": "2026-05-01",
            "source": _src("old-frog"),
        }
    ]
    out, log = update.guard(PRIOR, fresh, ALL, ["new-answer"], TEXTS)
    assert out["discovery_propounded"] == PRIOR["discovery_propounded"]  # one row, the list's own wording
    assert log == {"changed": [], "reverted": []}


def test_a_settlement_paper_starts_a_case_status_update(tmp_path):
    from medchron.litigation.firm import load
    from litigation_testkit import make_inputs

    firm = load(make_inputs(tmp_path / "in"))
    base = [{"id": "c", "name": "Complaint", "ext": ".pdf", "modified": "2026-03-01", "created": "2026-03-01"}]
    prior = {**copy.deepcopy(PRIOR), "fields_read": ALL}
    stip = base + [
        {"id": "s", "name": "Notice of settlement", "ext": ".pdf", "modified": "2026-10-01", "created": "2026-10-01"}
    ]
    p = manifest.plan_matter(stip, prior, manifest.current_manifest(base), firm, dt.date(2026, 10, 2))
    assert p["trigger_files"] == ["s"] and vocab.GROUP_CASE in p["read_groups"]


def test_screen_with_nothing_kept_reads_nothing():
    s = update.screen_plan(update.plan_changed([], ["e1"], {}), {"e1": "lunch"}, FIRM)
    assert s["read_groups"] == [] and s["audit"] == "none" and s["candidates"] == []


def test_a_named_paper_plans_an_update_of_its_groups_only():
    p = update.plan_changed(["pos1"], [], {"pos1": "server"})
    assert p["mode"] == update.MODE and p["read_groups"] == [vocab.GROUP_DEFENDANTS]
    assert p["candidates"] == ["pos1"]


def test_a_matter_on_the_list_with_no_arrivals_reads_nothing_and_a_two_pass_seed_is_not_reaudited():
    files = [{"id": "a", "name": "Complaint.pdf", "modified": "x", "created": "2026-01-01"}]
    prior = {**copy.deepcopy(PRIOR), "fields_read": ALL, "provenance": {"two_pass": True}}
    p = manifest._plan(files, prior, {"a": "x"}, ["a"], dt.date(2026, 10, 9))
    assert p["read_groups"] == [] and p["audit"] == "none"


def test_the_discovery_seed_merges_cited_sets_and_refuses_an_uncited_one(tmp_path):
    import json

    from medchron.litigation import baseline

    state, root = tmp_path / "state", tmp_path / "disc"
    (state / "matters").mkdir(parents=True)
    (root / "runs").mkdir(parents=True)
    (root / "snip").mkdir()
    for mid in ("m1", "m2"):
        (state / "matters" / f"{mid}.json").write_text(json.dumps({"fields_read": ["case", "defendants"]}))
    (root / "files.json").write_text(json.dumps({"101": {"id": "m1"}, "102": {"id": "m2"}, "103": {"id": "m9"}}))
    docs = [
        {"fid": "f-frog", "name": "FROG", "date": "2026-05-01"},
        {"fid": "f-resp", "name": "Resp", "date": "2026-06-01"},
    ]
    for n in ("101", "102"):
        (root / "snip" / f"{n}.json").write_text(json.dumps(docs))
    ok = {
        "direction": "served_on_our_client",
        "propounding_party": "Zeta",
        "set_type": "form_interrogatories",
        "set_number": "One",
        "served_date": "2026-05-01",
        "served_source_doc": 1,
        "response_status": "responses_served",
        "response_date": "2026-06-01",
        "response_source_doc": 2,
    }
    bad = {**ok, "served_source_doc": 9}
    (root / "runs" / "merged.json").write_text(
        json.dumps({"101": {"sets": [ok]}, "102": {"sets": [bad]}, "103": {"sets": [ok]}})
    )
    assert baseline.merge_discovery(state, root) == {"merged": 1, "uncited": 1, "not_seeded": 1, "already_read": 0}
    m1 = json.loads((state / "matters" / "m1.json").read_text())
    row = m1["discovery_served_on_client"][0]
    assert row["set"] == "Form Interrogatories, Set One" and row["source"]["file_id"] == "f-frog"
    assert row["responses_served"]["value"] == "yes" and row["responses_served"]["source"]["file_id"] == "f-resp"
    assert "discovery" in m1["fields_read"]
    assert "discovery" not in json.loads((state / "matters" / "m2.json").read_text())["fields_read"]
