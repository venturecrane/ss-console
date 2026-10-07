"""`medchron draft`: one drafting job end to end against a fake seat and a
scripted model, and the outcome classes (delivered, held, failed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.drafting import run as run_mod
from medchron.drafting.run import DraftingRun


CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, seat, client, **job_kw):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = make_job(tmp_path / "job", **job_kw)
    log: list[str] = []
    r = DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=log.append,
        readback_pause=0.0,
    )
    return r, r.run(), log


def test_a_mediation_brief_runs_pull_to_read_back(tmp_path, pricing):
    seat, client = seat_with(standard_docs()), ScriptedClient()
    r, v, log = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert [f["role"] for f in v.files] == ["draft", "attorney_notes"]
    assert v.files[0]["name"] == "Mediation Brief - 100001 - " + r.date_stamp + ".docx"
    assert seat.created[0]["name"].endswith(f"(Operator {r.job.job_id[-6:]})")
    assert v.document_class == "mediation_brief" and v.dollars > 0
    # the charges table reached compose, computed by code, with its source
    compose = next(
        c
        for c in client.calls
        if "COMPOSE-PROMPT" in json.dumps(c["system"]) and "REPAIR" not in json.dumps(c["system"])
    )
    user = compose["messages"][0]["content"]
    assert "THE CHARGES TABLE" in user and "$1,200.00 (source: ER bill 1-15-26)" in user
    assert "THE ATTORNEY'S REQUEST" in user and "THE CAPTION (verbatim from Complaint 2-1-26" in user
    # the caption diff: the record's case number and the plaintiff's spelling, the missing email
    fields = {d["field"] for d in v.caption_discrepancies}
    assert fields == {"case_number", "plaintiff", "attorney_email"}
    # the settlement marker stands
    assert any(m["kind"] == "ATTORNEY" for m in v.markers)


def test_a_resume_after_delivery_pays_for_nothing_twice(tmp_path, pricing):
    seat, client = seat_with(standard_docs()), ScriptedClient()
    _run(tmp_path, pricing, seat, client)
    paid = len(client.calls)
    _r, v, _ = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered" and len(client.calls) == paid


def test_the_drafters_refusal_holds_with_its_sentence(tmp_path, pricing):
    client = ScriptedClient(draft="=== NEEDS THE ATTORNEY ===\nThe request does not name the deponent.")
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), client, cls="depo_outline")
    assert v.outcome == "held" and "does not name the deponent" in v.reason


def test_a_format_refusal_of_our_own_render_fails_and_files_nothing(tmp_path, pricing, monkeypatch):
    from medchron.drafting import format_check

    monkeypatch.setattr(format_check, "check", lambda *a, **k: format_check.Result(fails=["font: forced"]))
    seat = seat_with(standard_docs())
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "failed" and "format check" in v.reason
    assert seat.sent == []


def test_a_destination_that_is_not_the_matter_or_a_rehearsal_matter_holds(tmp_path, pricing):
    seat = seat_with(standard_docs())
    seat.numbers[next(k for k, v in seat.numbers.items() if v == "OPS-LIBRARY")] = "200002"
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient(), file_to=True)
    assert v.outcome == "held" and "filing destination" in v.reason
    assert v.dollars == 0


def test_a_rehearsal_files_to_the_authored_library_matter(tmp_path, pricing):
    seat = seat_with(standard_docs())
    _r, v, _ = _run(
        tmp_path,
        pricing,
        seat,
        ScriptedClient(draft="# Question\n\nA memo (ER record 1-15-26, p. 1)."),
        cls="memo",
        file_to=True,
    )
    assert v.outcome == "delivered", v.reason


def test_an_unreadable_matter_record_fails_never_holds(tmp_path, pricing):
    seat = seat_with(standard_docs())
    seat.facts = {**seat.facts, "errors": ["matter: RuntimeError: timed out"]}
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "failed" and v.stage == "facts"


def test_the_verdict_is_the_lanes_contract(tmp_path, pricing):
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    d = v.to_list()[0]
    assert d["unit"] == "drafting" and d["kind"] == "drafting"
    assert set(d) >= {"outcome", "stage", "reason", "dollars", "folder_id", "files", "caption_discrepancies", "markers"}


def test_the_cli_draft_command_writes_the_verdict(tmp_path, pricing, monkeypatch, capsys):
    from medchron import __main__ as cli

    monkeypatch.setattr(run_mod.DraftingRun, "run", lambda self: run_mod.Verdict("failed", stage="pull", reason="x"))
    make_inputs(tmp_path / "inputs")
    jd = make_job(tmp_path / "job")
    code = cli.main(
        ["draft", str(jd), "--inputs", str(tmp_path / "inputs"), "--pricing", str(pricing), "--redo", "pull"]
    )
    assert code == 1
    assert json.loads((jd / "verdict.json").read_text())[0]["outcome"] == "failed"


DISCOVERY_DRAFT = """| | |
| --- | --- |
| PROPOUNDING PARTY: | GAMMA EXAMPLE |
| RESPONDING PARTY: | DELTA EXAMPLE |
| SET NO.: | ONE |

# DEFINITIONS

1. "INCIDENT" means the collision on January 15, 2026 (ER record 1-15-26, p. 1).

# SPECIAL INTERROGATORIES

SPECIAL INTERROGATORY NO. 1

IDENTIFY each PERSON who witnessed the INCIDENT.

SPECIAL INTERROGATORY NO. 2

State all facts supporting YOUR contention that the INCIDENT was not YOUR fault.

Dated: {{ATTORNEY: date}}

=== ATTORNEY NOTES ===

## REQUEST BASIS

None.
"""


def test_a_discovery_set_ends_at_the_signature_and_the_job_appends_the_proof_of_service(tmp_path, pricing):
    import docx

    from medchron.demand import deliver

    seat = seat_with(standard_docs())
    r, v, _log = _run(tmp_path, pricing, seat, ScriptedClient(draft=DISCOVERY_DRAFT), cls="discovery_set")
    assert v.outcome == "delivered", (v.stage, v.reason)
    path = deliver.out_dir(r.data) / v.files[0]["name"]
    text = [p.text for p in docx.Document(str(path)).paragraphs if p.text.strip()]
    assert text[-2] == "PROOF OF SERVICE" and "at service" in text[-1]
    assert not any("DECLARATION" in t for t in text)  # 2 special interrogatories, no prior sets
    assert not any("ATTORNEY NOTES" in t for t in text)


# ---- every recorded reason leads with its code ------------------------------------------

CODE = __import__("re").compile(r"^[a-z_]+: ")


def test_every_reason_literal_in_the_runner_leads_with_a_known_code():
    """Static: each raise of a hold or failure, and each Verdict reason the
    runner builds, starts with one of REASON_CODES."""
    import re

    src = Path(run_mod.__file__).read_text(encoding="utf-8")
    starts = re.findall(r"raise (?:DraftingHold|DraftingFailed|exc)\(\s*f?\"([^\"]*)", src)
    starts += re.findall(r"reason=f?\"([^\"]*)", src)
    assert len(starts) >= 12
    for s in starts:
        m = run_mod.REASON.match(s)
        assert m, f"reason does not lead with a code: {s[:60]!r}"
    lane = (Path(run_mod.__file__).parents[1] / "drafting_lane.py").read_text(encoding="utf-8")
    assert '"reason": f"no_verdict: ' in lane


def _failing(monkeypatch, attr, exc):
    def boom(self, *a, **k):
        raise exc

    monkeypatch.setattr(run_mod.DraftingRun, attr, boom)


@pytest.mark.parametrize(
    "case",
    ["destination", "facts", "format", "needs", "unexpected", "limit", "stage"],
)
def test_every_outcome_reason_matches_the_code_shape(tmp_path, pricing, monkeypatch, case):
    from medchron import limits as limits_mod
    from medchron.drafting import compose, format_check

    seat, client, kw = seat_with(standard_docs()), ScriptedClient(), {}
    if case == "destination":
        seat.numbers[next(k for k, v in seat.numbers.items() if v == "OPS-LIBRARY")] = "200002"
        kw["file_to"] = True
    elif case == "facts":
        seat.facts = {**seat.facts, "errors": ["matter: RuntimeError: timed out"]}
    elif case == "format":
        monkeypatch.setattr(format_check, "check", lambda *a, **k: format_check.Result(fails=["font: x"]))
    elif case == "needs":
        client = ScriptedClient(draft="=== NEEDS THE ATTORNEY ===\nName the deponent.")
    elif case == "unexpected":
        _failing(monkeypatch, "_preflight", ValueError("Secret Client file.pdf could not be parsed"))
    elif case == "limit":
        _failing(monkeypatch, "_estimate", limits_mod.LimitHold("per_job_cap_usd", "per_job_cap_usd: over"))
    elif case == "stage":
        _failing(monkeypatch, "_digest", compose.DraftingError("compose output still unfinished"))
    _r, v, _ = _run(tmp_path, pricing, seat, client, **kw)
    assert v.outcome in ("held", "failed")
    assert CODE.match(v.reason or ""), v.reason
    assert run_mod.REASON.match(v.reason or ""), v.reason
    assert not (v.reason or "").startswith("Secret")
