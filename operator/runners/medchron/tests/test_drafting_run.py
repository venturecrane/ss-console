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


# ---- the second-review rules ---------------------------------------------------------------


def test_a_content_finding_gets_one_repair_pass_then_files(tmp_path, pricing, monkeypatch):
    from medchron.drafting import compose, format_check

    real = format_check.check
    calls = {"check": 0, "repair": 0}

    def check(*a, **k):
        calls["check"] += 1
        if calls["check"] == 1:
            r = format_check.Result()
            r.model("sections: 'VII. CAUSATION' missing or out of the authored order")
            return r
        return real(*a, **k)

    def repair_format(self, doc, findings):
        calls["repair"] += 1
        assert findings == ["sections: 'VII. CAUSATION' missing or out of the authored order"]
        return doc

    monkeypatch.setattr(format_check, "check", check)
    monkeypatch.setattr(compose.Drafter, "repair_format", repair_format)
    r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    assert v.outcome == "delivered", v.reason
    assert calls == {"check": 2, "repair": 1} and r._is_done("format_repair")


def test_a_content_finding_that_survives_its_repair_fails_with_format_check(tmp_path, pricing, monkeypatch):
    from medchron.drafting import compose, format_check

    def check(*a, **k):
        r = format_check.Result()
        r.model("definitions: no Definitions section")
        return r

    calls = []
    monkeypatch.setattr(format_check, "check", check)
    monkeypatch.setattr(compose.Drafter, "repair_format", lambda self, doc, f: calls.append(f) or doc)
    seat = seat_with(standard_docs())
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "failed" and v.reason.startswith("format_check: ") and len(calls) == 1
    assert seat.sent == []


def test_our_render_defect_fails_at_once_without_a_repair(tmp_path, pricing, monkeypatch):
    from medchron.drafting import compose, format_check

    monkeypatch.setattr(format_check, "check", lambda *a, **k: format_check.Result(fails=["font: 3 run(s)"]))
    monkeypatch.setattr(compose.Drafter, "repair_format", lambda *a: pytest.fail("no repair for our own render"))
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    assert v.outcome == "failed" and v.reason.startswith("format_check: ")


def test_a_billing_read_that_does_not_finish_fails_resumably(tmp_path, pricing, monkeypatch):
    from medchron.drafting import howell

    def boom(*a, **k):
        raise howell.ExtractionError("the billing read of a bill stopped at its output ceiling")

    monkeypatch.setattr(howell, "extract", boom)
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    assert v.outcome == "failed" and v.reason.startswith("stage_unfinished: howell")


def test_a_privilege_wall_refusal_holds_with_a_fixed_sentence_only(tmp_path, pricing, monkeypatch):
    from medchron.drafting import gate

    monkeypatch.setattr(
        gate,
        "run",
        lambda *a, **k: {
            "passed": False,
            "disposition": "refused",
            "refusals": ["[1] leaked from held-out Client to firm re settlement.msg"],
            "warnings": [],
        },
    )
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient())
    assert v.outcome == "held" and v.reason == f"gate_refused: {run_mod.WALL_SENTENCE}"
    assert "msg" not in v.reason


def test_a_limit_reason_carries_the_estimate_made_before_anything_was_paid(tmp_path, pricing):
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), ScriptedClient(), cents=74_999_00)
    assert v.outcome == "failed" and v.reason.startswith("limit: ")
    assert "estimate before anything was paid:" in v.reason and "USD" in v.reason


def test_compose_refuses_an_input_over_the_context_ceiling_before_any_call(tmp_path, pricing, monkeypatch):
    from medchron.drafting import compose

    monkeypatch.setattr(compose, "COMPOSE_INPUT_MAX_CHARS", 1_000)
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), client)
    assert v.outcome == "failed" and "over 1,000" in v.reason and v.stage == "compose"
    assert "COMPOSE" not in client.stages()


def test_the_caption_notes_never_say_agrees_when_nothing_was_compared(tmp_path, pricing):
    seat = seat_with(
        standard_docs(),
        record={"case_number": None, "court": None, "plaintiffs": [], "defendants": [], "attorney_email": None},
    )
    r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "delivered"
    notes = r._notes_md([], [])
    assert "Fields compared: none" in notes and "agrees" not in notes


def test_an_unreadable_caption_record_fails_with_record_unreadable(tmp_path, pricing):
    seat = seat_with(standard_docs())
    seat.caption_record = lambda _m: None  # type: ignore[method-assign]
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "failed" and v.reason.startswith("record_unreadable: ")


def test_every_artifact_a_resume_trusts_is_written_atomically():
    import re

    src = Path(run_mod.__file__).read_text(encoding="utf-8")
    direct = [m.group(0) for m in re.finditer(r"[a-zA-Z_./()\"]+\.write_text\(", src)]
    assert direct == ["tmp.write_text(", "tmp.write_text("], direct  # state.json and _atomic, each via .tmp
    from medchron.drafting import howell, render

    for mod in (howell, render):
        body = Path(mod.__file__).read_text(encoding="utf-8")
        assert ".replace(" in body and "tmp" in body
