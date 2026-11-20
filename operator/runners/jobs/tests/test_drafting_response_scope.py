"""A response job reads only what its sets need, states its cost and finish time, and stops at the cap.

The falsifiers (every name and value invented; sizes from three real matters
measured 2026-11-20: 575 / 1,367 / 1,425 pages, 201-468 scan pages, 1.4M-4.4M
characters, 285-331 files):

1. Files sort into the closed categories by deepest folder, then name, then
   extension, then a provider's folder; a firm's patterns replace the default.
2. A unit reads only its categories; a request for production reads an inventory.
3. A file no set reads leaves the corpus with a reason, so its scans are never
   queued; a scan page no unit's selection reads is never queued either.
4. A pack over its cap keeps billing pages and first pages, and counts what it left out.
5. The projection subtracts the pages the page store already holds, read, never paid.
6. A job over the firm's cap stops before any paid stage.
7. Other classes still digest as before; a response job composes from its packs.
8. The three measured sizes project without error, and more pages never cost less.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import firm_data, make_inputs, make_job, seat_with, standard_docs
from jobs.demand import transcribe
from jobs.drafting import response_cost, response_scope as rs
from jobs.drafting.evidence_cost import load_pricing
from jobs.drafting.run import DraftingRun
from jobs.matter import paid as matter_paid, policy as matter_policy, store as page_store
from test_drafting_review import Client
from test_drafting_served import ResponsesClient, _response_doc, _set, _write_served


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    from test_drafting_review import CHECKER

    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path):
    from jobs_testkit import PRICING

    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


PATS = rs.patterns(None)


def _row(i, name, folder="/", ext=".pdf", **kw):
    return {"id": f"d{i}", "name": name, "folder": folder, "ext": ext, **kw}


# ---- 1. categories ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row, want",
    [
        (_row(1, "ER bill 1-15-26.pdf", "/Medical"), "billing"),  # a bill in a medical folder is still a bill
        (_row(2, "visit.pdf", "/Medical/Bills"), "billing"),  # the deepest folder first
        (_row(3, "Medical records.pdf", "/Discovery"), "prior_discovery"),  # the deepest folder wins over the name
        (_row(15, "Records.pdf", "/Discovery/Medical"), "medical_records"),
        (_row(4, "Summons.pdf", "/Court"), "pleadings"),
        (_row(5, "CHP report.pdf"), "incident_report"),
        (_row(6, "Paystubs 2025.pdf", "/Wage loss"), "employment_wage"),
        (_row(7, "dec page.pdf", "/1st party"), "insurance"),
        (_row(8, "Medicare conditional payment.pdf"), "insurance"),
        (_row(9, "note.msg", "/Mail", ext=".msg"), "correspondence"),
        (_row(10, "IMG_0042.jpg", "/Scene", ext=".jpg"), "photographs"),
        (_row(11, "styles.css", "/Saved page_files", ext=".css"), "other"),
        (_row(12, "costs.pdf", "/Accounting"), "other"),  # a firm cost folder is never billing
        (_row(13, "visit 2-2-26.pdf", "/Exampletown Spine Center"), "medical_records"),  # a provider's folder
        (_row(14, "misc.pdf", "/Loose"), "other"),
    ],
)
def test_files_sort_into_one_category_each_by_the_default_patterns(row, want):
    assert rs.categorize(row, PATS, ["Exampletown Spine Center"]) == want


def test_a_firm_pattern_list_replaces_the_default_and_is_a_closed_shape():
    pats = rs.patterns({rs.KEY: {"patterns": {"billing": [r"\bCHARGES\b"]}}})
    assert rs.categorize(_row(1, "ER bill.pdf"), pats) == "other"  # BILL is no longer the firm's word
    assert rs.categorize(_row(2, "ER charges.pdf"), pats) == "billing"
    assert rs.validate({"patterns": {"billing": ["("]}})[0].startswith("response_scope.patterns.billing: invalid")
    assert rs.validate({"patterns": {"receipts": ["x"]}})
    assert rs.validate({"categories": {}})
    data = firm_data(inputs={}, **{rs.KEY: {"patterns": {"nonsense": ["x"]}}})
    from jobs.drafting import firm

    assert any(p.startswith("response_scope.patterns.nonsense") for p in firm.validate(data))
    assert not any(
        p.startswith("response_scope")
        for p in firm.validate(firm_data(inputs={}, **{rs.KEY: {"patterns": {"billing": ["BILL"]}}}))
    )


# ---- a data dir as preflight leaves it -----------------------------------------------------------


def _page(n: int, body: str) -> str:
    return f"[p.{n}]\n{body}\n"


def _data(tmp_path: Path, files: list[tuple[str, str, str, list[str], list[int]]]) -> Path:
    """files: (id, name, folder, page texts ('' for a scan), scan pages)."""
    data = tmp_path / "data"
    (data / "text").mkdir(parents=True)
    rows = []
    for fid, name, folder, pages, scans in files:
        text = "".join(_page(i, t) for i, t in enumerate(pages, 1))
        p = data / "text" / f"{fid}.txt"
        p.write_text(text, encoding="utf-8")
        rows.append({"id": fid, "name": name, "folder": folder, "ext": ".pdf", "pages": len(pages),
                     "transcribe": scans, "text_path": str(p), "chars": len(text), "source_path": str(p)})  # fmt: skip
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return data


def _plan(*types: str) -> list[dict]:
    return [{"key": f"set-{i}", "variant": t, "label": t, "served": i - 1} for i, t in enumerate(types, 1)]


# ---- 2 and 3. needs and scope ------------------------------------------------------------------------


def test_a_unit_reads_only_its_categories(tmp_path):
    data = _data(
        tmp_path,
        [
            ("m", "ER record.pdf", "/Medical", ["Seen for neck pain."], []),
            ("w", "Paystubs.pdf", "/Wage", ["Hourly rate 20.00."], []),
            ("c", "Complaint.pdf", "/Pleadings", ["Plaintiff alleges."], []),
        ],
    )
    rs.scope(data, None, _plan(rs.FORM))
    income = rs.pack(data, rs.FORM, "income").text
    assert "=== FILE: Paystubs.pdf (folder /Wage) ===" in income and "[p.1]" in income
    assert "neck pain" not in income and "alleges" not in income
    assert "neck pain" in rs.pack(data, rs.FORM, "injuries_treatment").text
    prod = rs.pack(data, "requests_for_production").text
    assert "| ER record.pdf | /Medical | medical_records | 1 |" in prod and "neck pain" not in prod
    assert rs.text_needs("requests_for_production") == frozenset()


def test_an_income_group_with_no_wage_file_is_a_small_pack_not_an_error(tmp_path):
    data = _data(tmp_path, [("m", "ER record.pdf", "/Medical", ["Seen."], [])])
    rs.scope(data, None, _plan(rs.FORM))
    got = rs.pack(data, rs.FORM, "income")
    assert got.text == "" and got.pages_left_out == 0


def test_a_file_no_set_reads_leaves_the_corpus_and_its_scans_are_never_queued(tmp_path):
    data = _data(
        tmp_path,
        [
            ("p", "IMG_0001.pdf", "/Photos", ["", "", ""], [1, 2, 3]),
            ("b", "ER bill.pdf", "/Medical", ["Total charges $1,200.00"], []),
        ],
    )
    rec = rs.scope(data, None, _plan("special_interrogatories"))
    rows = rs.corpus_rows(data)
    assert [r["id"] for r in rows] == ["b"]
    unread = json.loads((data / rs.UNREAD).read_text())
    assert unread == [{"id": "p", "name": "IMG_0001.pdf", "category": "photographs", "scanned_pages": 3,
                       "reason": "photographs: no set in this job reads it"}]  # fmt: skip
    assert rec["scan_pages_unread_files"] == 3
    assert all(r["id"] != "p" for r, _ in transcribe._todo(rows))  # what the transcription queues


def test_a_scan_page_no_units_selection_reads_is_never_queued(tmp_path):
    # 400 scanned medical pages: at 2,500 characters each, 1M, twice the special cap.
    data = _data(tmp_path, [("m", "Radiology.pdf", "/Medical", [""] * 400, list(range(1, 401)))])
    rec = rs.scope(data, None, _plan("special_interrogatories"))
    queued = rs.corpus_rows(data)[0]["transcribe"]
    assert queued[:2] == [1, 2] and 150 < len(queued) < 200  # first pages first, under 500,000 characters
    assert rec["scan_pages_untranscribed"] == 400 - len(queued)
    assert rec["scan_pages_to_read"] == len(queued)


# ---- 4. over a cap ----------------------------------------------------------------------------------


def test_a_pack_over_its_cap_keeps_billing_and_first_pages_and_counts_what_it_left_out(tmp_path):
    big = "x" * 20_000
    files = [("b", "ER bill.pdf", "/Medical", [f"charges {i} " + big for i in range(5)], [])]
    files += [(f"m{k}", f"record {k}.pdf", "/Medical", [f"m{k} p{i} " + big for i in range(30)], []) for k in range(4)]
    files += [("h", "Spine records.pdf", "/Medical", [big] * 3 + ["Exampletown Spine Center diagnosis " + big], [])]
    data = _data(tmp_path, files)
    rs.scope(data, None, _plan("special_interrogatories"))
    got = rs.pack(data, "special_interrogatories", providers=["Exampletown Spine Center"])
    assert got.chars <= rs.SPECIAL_CAP
    assert all(f"charges {i} " in got.text for i in range(5))  # every billing page
    assert all(f"m{k} p0 " in got.text and f"m{k} p1 " in got.text for k in range(4))  # every file's first two
    assert "Exampletown Spine Center diagnosis" in got.text  # the ranked page
    assert got.pages_left_out > 0 and got.chars_left_out > 0
    assert got.pages_read + got.pages_left_out == 5 + 120 + 4
    note = rs.left_out_note("Responses to Special Interrogatories, Set One", got.record())
    assert note and f"{got.pages_left_out:,} pages" in note and "medical records" in note


def test_a_pack_under_its_cap_is_the_digest_framing_whole(tmp_path):
    data = _data(tmp_path, [("m", "ER record.pdf", "/Medical", ["Seen.", "Released."], [])])
    rs.scope(data, None, _plan("special_interrogatories"))
    got = rs.pack(data, "special_interrogatories")
    assert got.text == "=== FILE: ER record.pdf (folder /Medical) ===\n[p.1]\nSeen.\n[p.2]\nReleased.\n\n"
    assert got.pages_left_out == 0 and rs.left_out_note("x", got.record()) is None


# ---- 5. the page store ---------------------------------------------------------------------------------


def test_the_projection_subtracts_pages_already_in_the_store(tmp_path, monkeypatch, pricing):
    data = _data(tmp_path, [("m", "ER record.pdf", "/Medical", ["", "", "", ""], [1, 2, 3, 4])])
    monkeypatch.setenv(page_store.ENV, str(tmp_path / "store"))
    rows = rs.corpus_rows(data)
    model = "claude-sonnet-5"
    method = matter_paid.method(matter_policy.DRAFTING, model).version
    sha = matter_paid.sha_file(Path(rows[0]["source_path"]))
    st = page_store.from_env("drafting", log=lambda _m: None)
    for n in (1, 3):
        assert st.put(sha, n, method, f"page {n} text", "MATTER-A", stop_reason="end_turn")
    before = sorted((tmp_path / "store").rglob("*.json"))
    assert response_cost.stored_pages(rows, model, log=lambda _m: None) == 2
    assert sorted((tmp_path / "store").rglob("*.json")) == before  # read only
    fd = firm_data()
    card = load_pricing(pricing)
    cold = response_cost.project(rows, {"m": "medical_records"}, _plan("special_interrogatories"), fd, card)
    warm = response_cost.project(
        rows, {"m": "medical_records"}, _plan("special_interrogatories"), fd, card, stored_pages=2
    )
    assert (cold["transcription_pages"], warm["transcription_pages"]) == (4, 2)
    assert warm["parts"]["transcription"] == pytest.approx(2 * 0.012, abs=0.01) and warm["usd"] < cold["usd"]


def test_the_store_off_counts_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(page_store.ENV, raising=False)
    data = _data(tmp_path, [("m", "ER record.pdf", "/Medical", [""], [1])])
    assert response_cost.stored_pages(rs.corpus_rows(data), "claude-sonnet-5") == 0


# ---- 6 and 7. the run -----------------------------------------------------------------------------------


def _run(tmp_path, pricing, client, cap=100.0):
    fd = firm_data()
    make_inputs(tmp_path / "inputs", budget={**fd["budget"], "per_job_cap_usd": cap})
    jd = make_job(tmp_path / "job", cls="discovery_response")
    doc = json.loads((jd / "job.json").read_text())
    (jd / "job.json").write_text(json.dumps({**doc, "request_text": "Please draft responses to the discovery."}))
    r = DraftingRun(jd, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                    seat_factory=lambda: seat_with(standard_docs()), client=client, log=lambda _m: None,
                    readback_pause=0.0)  # fmt: skip
    (r.data / "facts.json").write_text(json.dumps({"client_names": ["Gamma Example"], "matter_number": "100001"}))
    return r


def test_a_job_over_the_firms_cap_stops_before_any_paid_stage(tmp_path, pricing):
    client = Client()
    r = _run(tmp_path, pricing, client, cap=0.05)
    _write_served(r.job.job_dir, [_set("special_interrogatories", 3, "operator")])
    v = r.run()
    assert v.outcome == "failed" and v.reason.startswith("limit: "), (v.stage, v.reason)
    assert client.calls == [] and v.dollars == 0
    st = r._state()
    assert st["scope"]["status"] == "done" and "transcribe" not in st
    est = json.loads((r.data / response_cost.FILE).read_text())
    assert est["over_cap"] and st["expected_finish"] == est["expected_finish"]
    assert est["expected_finish"]["at"].endswith("Z") and est["minutes"]["total"] > 0


def test_a_response_job_composes_from_its_packs_cheapest_first_and_builds_no_digest(tmp_path, pricing):
    client = ResponsesClient(
        by_label={
            "Responses to Special Interrogatories, Set One": _response_doc("SPECIAL INTERROGATORY", "SPECIAL INTERROGATORIES", 22),
            "Responses to Requests for Production, Set One": _response_doc("REQUEST FOR PRODUCTION", "REQUESTS FOR PRODUCTION", 2),
        }
    )  # fmt: skip
    r = _run(tmp_path, pricing, client)
    _write_served(
        r.job.job_dir, [_set("special_interrogatories", 22, "operator"), _set("requests_for_production", 2, "operator")]
    )
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert not any("DIGEST-PROMPT" in json.dumps(c.get("system")) for c in client.calls)  # no whole-file digest
    units = r._state()["units"]
    est = json.loads((r.data / response_cost.FILE).read_text())["units"]
    assert est["set-2"]["usd"] < est["set-1"]["usd"]  # 2 production requests cost less than 22 special
    assert [u["key"] for u in units] == ["set-2", "set-1"]  # so they run first
    special = next(u for u in units if u["variant"] == "special_interrogatories")
    assert "neck pain" in (r.data / special["key"] / rs.PACK).read_text()
    prod = next(u for u in units if u["variant"] == "requests_for_production")
    composed = [json.dumps(c.get("messages")) for c in client.calls if "COMPOSE-PROMPT" in json.dumps(c.get("system"))]
    prod_call = next(m for m in composed if "Requests for Production, Set One," in m)
    assert "THE FILE INVENTORY" in prod_call and "neck pain" not in prod_call
    assert composed.index(prod_call) == 0  # composed first
    assert (r.data / prod["key"] / rs.PACK).is_file()
    rows = json.loads((r.data / "served-outcomes.json").read_text())["sets"]
    assert [x["set"] for x in rows] == ["Responses to Special Interrogatories, Set One",
                                        "Responses to Requests for Production, Set One"]  # fmt: skip


def test_other_classes_still_digest_and_never_scope(tmp_path, pricing):
    from drafting_testkit import ScriptedClient

    client = ScriptedClient()
    make_inputs(tmp_path / "inputs")
    jd = make_job(tmp_path / "job")
    r = DraftingRun(jd, inputs_dir=str(tmp_path / "inputs"), pricing=str(pricing),
                    seat_factory=lambda: seat_with(standard_docs()), client=client, log=lambda _m: None,
                    readback_pause=0.0)  # fmt: skip
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert "scope" not in r._state() and not (r.data / rs.SCOPE).exists()
    assert (r.data / "digest").is_dir() and not list(r.data.rglob(rs.PACK))  # the summarizer's chunks, no pack


# ---- 8. the measured sizes --------------------------------------------------------------------------------


MEASURED = [  # (pages, scan pages, text characters, files)
    (575, 201, 1_400_000, 285),
    (1_367, 468, 4_400_000, 331),
    (1_425, 240, 2_900_000, 300),
]


def _synthetic_rows(pages, scans, chars, files):
    cats = [c for c in rs.CATEGORIES]
    rows, out = [], {}
    for i in range(files):
        n = pages // files + (1 if i < pages % files else 0)
        s = min(n, scans // files + (1 if i < scans % files else 0))
        rid = f"r{i}"
        rows.append({"id": rid, "pages": n, "transcribe": list(range(1, s + 1)), "chars": chars // files})
        out[rid] = cats[i % len(cats)]
    return rows, out


@pytest.mark.parametrize("size", MEASURED)
def test_the_three_measured_sizes_project_and_more_pages_never_cost_less(size, pricing):
    fd, card = firm_data(), load_pricing(pricing)
    plan = _plan(rs.FORM, "special_interrogatories", "requests_for_production")
    rows, cats = _synthetic_rows(*size)
    est = response_cost.project(rows, cats, plan, fd, card)
    assert est["usd"] > 0 and est["minutes"]["total"] > 0 and set(est["units"]) == {"set-1", "set-2", "set-3"}
    for k in ("pages", "characters", "transcription_pages", "usd"):  # what the limit hold's sentence reads
        assert k in est
    last = 0.0
    for scale in (0.5, 1, 2, 4):
        p, s, c, f = size
        more, mcats = _synthetic_rows(int(p * scale), int(s * scale), int(c * scale), f)
        got = response_cost.project(more, mcats, plan, fd, card)["usd"]
        assert got >= last
        last = got
