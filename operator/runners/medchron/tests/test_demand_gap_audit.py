"""A large file's gap audit always finishes (a live demand job, 2026-10-06,
stopped at its 64K-token ceiling with nothing downstream run): the stage
model's own output maximum, table rows only, provider batches merged in code,
a batch at the ceiling split and rerun, and compose continued rather than cut.
A resume reuses the paid digest and every finished batch."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import demand_testkit
from demand_testkit import GAP, ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.demand import gapaudit, vendor
from medchron.demand.run import DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
HEADER = (
    "| Section | Provider | What's missing | Where the file points to it | Basis | Suggested request type | Priority |\n"
    "|---|---|---|---|---|---|---|\n"
)


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path, pricing, client, **job_kw):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = make_job(tmp_path / "job", **job_kw)
    log: list[str] = []
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=log.append,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run(), log


def big_digest(folders: int = 7, per: int = 10) -> str:
    out = []
    for f in range(folders):
        for k in range(per):
            out.append(
                f"## MEDICAL | Provider{f} visit {k} | 01/{k + 1:02d}/2026 | (/Medical/Provider{f})\nnote (FILE: x)\n"
            )
        out.append("## FIGURES\n- $1 | bill | x\n\n## FILES-SEEN\n- x digested\n")
    return "\n".join(out)


def _row(section: str, provider: str, missing: str, where: str, priority: str = "Strengthens demand") -> str:
    return f"| {section} | {provider} | {missing} | {where} | Referenced in record | records | {priority} |\n"


def scoped_gap(params):
    """One row per document in the call's scope; the file-wide owner adds one interval row."""
    user = params["messages"][-1]["content"]
    if "SCOPE: the whole file" in user:
        return HEADER + _row(
            "B. Completeness", "Exampletown ER", "radiology bill", "ER record"
        ) + "END OF ITEM TABLE\n", "end_turn"
    docs = re.findall(r"^- (.+?) \(folder ", user, flags=re.M)
    body = "".join(_row("B. Completeness", d.split(" visit")[0], f"bill for {d}", d) for d in docs)
    if "ALSO owns" in user:
        body += _row("D. Treatment timeline", "Provider0", "no visit 01/01 to 03/01 (59 days)", "Provider0 visit 0")
    return HEADER + body + "END OF ITEM TABLE\n", "end_turn"


# ---- the ceiling ------------------------------------------------------------------------
def test_the_ceiling_is_the_models_documented_maximum_not_the_firm_lever():
    assert gapaudit.output_max("claude-opus-5-5", 64_000) == 128_000
    assert gapaudit.output_max("claude-sonnet-5", 64_000) == 128_000
    assert gapaudit.output_max("some-future-model", 64_000) == 64_000


def test_every_drafting_call_asks_for_the_models_maximum(tmp_path, pricing):
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    for c in client.calls:
        s = json.dumps(c.get("system"))
        if any(k in s for k in ("GAP-PROMPT", "COMPOSE-PROMPT")):
            assert c["max_tokens"] == 128_000  # the firm lever in the fixture is 64,000


# ---- compact by construction ------------------------------------------------------------
def test_the_audit_is_rendered_from_rows_numbered_and_summarized_in_code():
    md = gapaudit.render(
        gapaudit.merge(
            [GAP + "| Possible, needs paralegal check | Lakeside Clinic | prior records | none | x | records | x |\n"]
        ),
        {
            "unreadable_documents": [{"name": "scan.pdf", "why": "blank"}],
            "documents_read_by_machine_transcription": ["fax.pdf"],
        },
    )
    heads = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert heads == [
        "## A. Referral and order trail",
        "## B. Provider-by-provider completeness",
        "## C. Billing-to-record reconciliation",
        "## Possible, needs paralegal check",
        "## Demand Readiness",
    ]
    assert "| 1 | Northfield Imaging |" in md and "| 4 | Exampletown ER | ED physician bill |" in md
    assert "Items that block sending the demand: 3, 4." in md
    assert "Items that can follow the demand: 1, 2." in md
    assert "scan.pdf" in md and "fax.pdf" in md
    # the possible list is never looked up as a confirmed missing provider
    assert vendor.missing_providers(md) == ["Northfield Imaging", "Ridgeview PT", "Exampletown ER"]


def test_the_contract_rides_last_in_the_gap_audit_instruction(tmp_path, pricing):
    client = ScriptedClient()
    _run(tmp_path, pricing, client)
    gap = next(c for c in client.calls if "GAP-PROMPT" in json.dumps(c.get("system")))
    system = gap["system"][0]["text"] if isinstance(gap["system"], list) else gap["system"]
    assert system.rstrip().endswith(gapaudit.CONTRACT.rstrip())
    assert "THE RECORD DIGEST" in system  # in the cached block, shared by every batch


# ---- batches ----------------------------------------------------------------------------
def test_a_large_file_is_audited_in_folder_batches_and_merged(tmp_path, pricing, monkeypatch):
    monkeypatch.setattr(demand_testkit, "DIGEST", big_digest())
    client = ScriptedClient(gap_fn=scoped_gap)
    _r, v, log = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    gaps = [c for c in client.calls if "GAP-PROMPT" in json.dumps(c.get("system"))]
    assert len(gaps) == 3  # 70 documents, folders of 10, at most 30 a batch
    assert len({json.dumps(c["system"]) for c in gaps}) == 1  # one cached instruction for every batch
    scopes = [re.findall(r"^- (.+?) \(folder ", c["messages"][-1]["content"], flags=re.M) for c in gaps]
    # The first batch runs alone; the rest run in a pool, so their CALL order is
    # completion order. The merged audit follows batch order (ex.map), checked below.
    assert len(scopes[0]) == 30 and sorted(len(s) for s in scopes[1:]) == [10, 30]
    assert all(" visit " in d and d.split(" visit")[0] in ("Provider0", "Provider1", "Provider2") for d in scopes[0])
    assert ["ALSO owns" in c["messages"][-1]["content"] for c in gaps] == [True, False, False]
    md = (tmp_path / "job" / "data" / "gap-audit.md").read_text()
    numbers = [int(m) for m in re.findall(r"^\| (\d+) \|", md, flags=re.M)]
    assert numbers == list(range(1, 72))  # 70 documents + one interval row, numbered once across batches
    assert "## D. Treatment timeline" in md


def test_a_batch_at_the_ceiling_is_split_and_rerun_never_raised(tmp_path, pricing, monkeypatch):
    monkeypatch.setattr(demand_testkit, "DIGEST", big_digest())

    def gap(params):
        user = params["messages"][-1]["content"]
        if len(re.findall(r"^- ", user, flags=re.M)) > 15:
            return HEADER + _row("B. x", "P", "partial", "w"), "max_tokens"
        return scoped_gap(params)

    client = ScriptedClient(gap_fn=gap)
    _r, v, log = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    assert any("split in two" in ln for ln in log)
    md = (tmp_path / "job" / "data" / "gap-audit.md").read_text()
    assert len(re.findall(r"^\| \d+ \|", md, flags=re.M)) == 71  # nothing lost, nothing doubled
    assert "| partial |" not in md  # a cut-off answer contributes no rows


def test_halves_cut_at_a_folder_boundary():
    docs = gapaudit.documents(big_digest(3, 10))
    a, b = gapaudit.halves(docs)
    assert len(a) in (10, 20) and {d.folder for d in a}.isdisjoint({d.folder for d in b})


def test_an_answer_without_its_sentinel_is_asked_again_once():
    calls = []

    def call(system, user, mx, cid):
        calls.append(cid)
        text = HEADER + _row("B. x", "P", "m", "w") + ("END OF ITEM TABLE\n" if len(calls) == 2 else "")
        return SimpleNamespace(text=text, stop_reason="end_turn")

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        g = gapaudit.GapAuditor(Path(d), call, "claude-opus-5-5", 64_000, 2, lambda m: None)
        md = g.run("S", {}, "## MEDICAL | a | 1/1 | (/M)\nx\n")
    assert len(calls) == 2 and "| 1 | P | m |" in md


# ---- failure stays resumable, and a resume never repays -------------------------------------
def test_one_document_still_over_the_ceiling_fails_resumably_and_the_resume_repays_nothing(tmp_path, pricing):
    state = {"fixed": False}

    def gap(params):
        if not state["fixed"]:
            return HEADER, "max_tokens"
        return GAP, "end_turn"

    client = ScriptedClient(gap_fn=gap)
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and "gap audit" in v.reason  # failed is resumable; held would be final
    paid = client.stages()
    assert paid.count("DIGEST") == 1
    state["fixed"] = True
    _r, v2, _ = _run(tmp_path, pricing, client)
    assert v2.outcome == "delivered", v2.reason
    again = client.stages()[len(paid) :]
    assert "DIGEST" not in again and "VISION" not in again  # the paid digest and transcription are reused
    assert again[0] == "GAP"  # the resume starts at the gap audit


def test_a_resume_pays_only_for_the_batches_not_yet_done(tmp_path, pricing, monkeypatch):
    monkeypatch.setattr(demand_testkit, "DIGEST", big_digest())
    state = {"fail_last": True}

    def gap(params):
        user = params["messages"][-1]["content"]
        if state["fail_last"] and "Provider6 visit" in user:
            return HEADER, "max_tokens"
        return scoped_gap(params)

    client = ScriptedClient(gap_fn=gap)
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "failed"
    before = len(client.calls)
    state["fail_last"] = False
    _r, v2, _ = _run(tmp_path, pricing, client)
    assert v2.outcome == "delivered", v2.reason
    regap = [c for c in client.calls[before:] if "GAP-PROMPT" in json.dumps(c.get("system"))]
    assert len(regap) == 1 and "Provider6 visit" in regap[0]["messages"][-1]["content"]


# ---- compose has the same exposure -----------------------------------------------------------
def test_a_compose_at_the_ceiling_is_continued_not_cut(tmp_path, pricing):
    client = ScriptedClient(compose_stops=["max_tokens", "max_tokens", "end_turn"])
    _r, v, log = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    composes = [
        c
        for c in client.calls
        if "COMPOSE-PROMPT" in json.dumps(c.get("system")) and "REPAIR-PROMPT" not in json.dumps(c.get("system"))
    ]
    assert len(composes) == 3
    assert "=== ANSWER SO FAR ===" in composes[2]["messages"][-1]["content"]
    assert (tmp_path / "job" / "data" / "draft-v1.md").read_text() == demand_testkit.DRAFT


def test_a_compose_unfinished_after_its_continuations_fails_resumably(tmp_path, pricing):
    client = ScriptedClient(compose_stops=["max_tokens"] * 3)
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and "continuations" in v.reason
    assert not (tmp_path / "job" / "data" / "draft-v1.md").exists()
    assert (tmp_path / "job" / "data" / "gap-audit.md").exists()  # deliverable 1 is kept for the resume
