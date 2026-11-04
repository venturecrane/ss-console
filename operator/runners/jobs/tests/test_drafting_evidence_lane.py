"""The ledger path through the drafting job (``evidence_lane.py``), end to end
on a synthetic matter against a fake seat and a model scripted by prompt. No
network, no spend; every name and figure is invented.

Each test is built to fail on the property it names: the filed brief's every
cite, quote, figure and date traces to a verified fact (``assert_spans``); a
hand-typed cite the one repair does not remove fails the job; a checker that
confirms everything fails it; a class the ledger path may not draft is a
schema error; the digest path is byte-identical with or without the section;
a resume pays for no paid stage twice.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from drafting_testkit import (
    COURT,
    SECTIONS,
    ScriptedClient,
    firm_data,
    make_inputs,
    make_job,
    seat_with,
    standard_docs,
)
from jobs_testkit import PRICING, make_pdf
from jobs.drafting import evidence_config, firm as firm_mod
from jobs.drafting.evidence import render as ev_render
from jobs.drafting.evidence.shapes import Ledger, Span
from jobs.drafting.run import DraftingRun
from test_drafting_verify_check import Model

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
LEDGER = {"mode": "ledger"}

#: Corpus names: the seat's file names without their extension.
ER = "ER record 1-15-26"
ORTHO = "Ortho notes 2-10-26"
BILL = "ER bill 1-15-26"
PAGES = {
    ER: "Patient seen 01/15/2026 after a rear-end collision.\nComplained of neck pain radiating to the left shoulder.",
    ORTHO: "Orthopedic evaluation found persistent cervical radiculopathy.\nRecommended an epidural steroid injection.",
    BILL: "Exampletown ER itemized statement.\nTotal charges billed: $1,200.00 for emergency services.",
}
#: What the scripted extract read proposes for each document (the page's own words).
CANDIDATES = {
    ER: [
        {
            "page": 1,
            "kind": "date",
            "excerpt": "Patient seen 01/15/2026",
            "claim": "seen at the ER",
            "value": "01/15/2026",
        },
        {
            "page": 1,
            "kind": "fact",
            "excerpt": "Complained of neck pain radiating to the left shoulder.",
            "claim": "neck pain radiating to the shoulder",
            "tags": ["neck"],
        },
    ],
    ORTHO: [
        {
            "page": 1,
            "kind": "fact",
            "excerpt": "Orthopedic evaluation found persistent cervical radiculopathy.",
            "claim": "radiculopathy",
            "tags": ["neck"],
        },
        {"page": 1, "kind": "fact", "excerpt": "Recommended an epidural steroid injection.", "claim": "injection"},
    ],
    BILL: [
        {
            "page": 1,
            "kind": "figure",
            "role": "billed",
            "excerpt": "Total charges billed: $1,200.00 for emergency services.",
            "claim": "the ER billed",
            "value": "$1,200.00",
        }
    ],
}


def docs() -> list[tuple[str, str, bytes, str]]:
    complaint = next(d for d in standard_docs() if d[1].startswith("Complaint"))
    return [
        ("d1", f"{ER}.pdf", make_pdf([PAGES[ER]]), "f-med"),
        ("d2", f"{ORTHO}.pdf", make_pdf([PAGES[ORTHO]]), "f-med"),
        ("d4", f"{BILL}.pdf", make_pdf([PAGES[BILL]]), "f-med"),
        complaint,
    ]


def index_ids(user: str) -> dict[str, str]:
    """excerpt start -> fact id, read from THE FACT INDEX compose was sent."""
    out = {}
    for m in re.finditer(r"(?m)^(F\d{4}) \|.*?\| excerpt: (.*?)(?: \| |$)", user):
        out[m.group(2)[:20]] = m.group(1)
    return out


def brief(ids: dict[str, str], typed: str = "") -> str:
    def i(start: str) -> str:
        return next(v for k, v in ids.items() if k.startswith(start[:20]))

    date, neck, radic, inj, bill = (
        i("Patient seen"),
        i("Complained of neck"),
        i("Orthopedic evaluation"),
        i("Recommended an"),
        i("Total charges"),
    )
    body = {
        "IV. STATEMENT OF FACTS": (
            f"Plaintiff was seen at the emergency room on {{{{D:{date}}}}} {{{{F:{date}}}}}. "
            f"The emergency physicians recorded neck pain radiating to the left shoulder {{{{F:{neck}}}}}. {typed}"
        ),
        "VI. DAMAGES": (
            f"The emergency room billed {{{{N:{bill}}}}} for emergency services {{{{F:{bill}}}}}.\n\n{{{{T:charges}}}}"
        ),
        "VII. CAUSATION": (
            f"The orthopedist found persistent cervical radiculopathy {{{{F:{radic}}}}} and recommended an epidural "
            f"steroid injection {{{{F:{inj}}}}}. In the physician's words, {{{{Q:{neck}|neck pain radiating to the left "
            f"shoulder}}}}. Recovery is governed by {{{{L:?|Howell v. Hamilton Meats (2011) 52 Cal.4th 541}}}}."
        ),
    }
    parts = [f"# {s}\n\n{body.get(s, 'Plaintiff relies on the record of this matter.')}" for s in SECTIONS]
    return COURT + "\n\n".join(parts) + "\n\n=== ATTORNEY NOTES ===\n\n## NOT IN RECORD\n\nNone.\n"


def _text(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "\n".join(_text(b.get("text", "")) if isinstance(b, dict) else str(b) for b in x)
    return json.dumps(x)


class LedgerClient(ScriptedClient):
    """The drafting client plus the ledger path's calls: the window read, the
    page-image question, the markup compose and the source check."""

    def __init__(self, checker: Any = None, typed: str = "", lint_fix: bool = False, **kw: Any):
        super().__init__(**kw)
        self.checker = checker or Model()
        self.typed, self.lint_fix = typed, lint_fix

    def _answer(self, params: dict[str, Any]) -> str:
        system = _text(params.get("system") or "")
        user = _text(params["messages"][-1]["content"])
        if system.startswith("You read a window of pages"):
            name = re.match(r"DOCUMENT: (.*)", user).group(1).strip()  # type: ignore[union-attr]
            return json.dumps([{"file": name, **c} for c in CANDIDATES.get(name, [])])
        if not system and isinstance(params["messages"][-1]["content"], list):
            return json.dumps({"answer": "yes", "reading": "as printed"})
        if system.startswith("SOURCE CHECK"):
            return self.checker(system, user)
        if "LINT REPAIR" in user:
            if not self.lint_fix:
                return "\n\n".join(re.split(r"## SECTIONS TO REPAIR\n\n", user, maxsplit=1)[1:])
            return re.split(r"## SECTIONS TO REPAIR\n\n", user, maxsplit=1)[1].replace(self.typed, "")
        if "HOW EVERY FACT IS WRITTEN" in system and "REPAIR-PROMPT" not in system and "THE FACT INDEX" in user:
            return brief(index_ids(user), self.typed)
        return super()._answer(params)


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, client: Any, evidence: dict | None = LEDGER, documents=None, **job_kw: Any):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs, **({"evidence": evidence} if evidence is not None else {}))
    seat = seat_with(documents or docs())
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
    return r, r.run(), log, seat


def _stage_calls(client: Any, needle: str) -> int:
    return sum(1 for c in client.calls if _text(c.get("system") or "").startswith(needle))


def test_the_ledger_path_files_a_brief_whose_every_span_traces_to_a_verified_fact(tmp_path, pricing):
    client = LedgerClient()
    r, v, log, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason, log[-8:])
    st = r._state()
    for s in ("evidence-extract", "evidence-verify", "evidence-images", "index", "check", "gate"):
        assert st[s]["status"] == "done", s
    final = (r.data / "draft-final.md").read_text(encoding="utf-8")
    doc = final.split("=== ATTORNEY NOTES ===")[0]
    # code wrote every cite, quote, figure and date; no token survives render
    assert "{{F:" not in doc and "{{N:" not in doc and "{{Q:" not in doc
    assert f"(FILE: {ER}, p. 1)" in doc and "$1,200.00" in doc and "January 15, 2026" in doc
    assert '"neck pain radiating to the left shoulder"' in doc
    # the span proof, re-run here from the job's own artifacts
    led = Ledger.from_jsonl((r.data / "evidence" / "ledger.jsonl").read_text(encoding="utf-8"))
    spans = [Span.from_json(x) for x in json.loads((r.data / "evidence-spans.json").read_text())]
    table = json.loads((r.data / "howell.json").read_text())["table"]
    rep = ev_render.assert_spans(doc, spans, led, table, None, r.exempt(), r.store())
    assert rep.ok and rep.traced == rep.spans == len(spans) > 5, rep.problems
    # the fixers never ran on this path; the check's notes reached the attorney notes
    assert json.loads((r.data / "gate.json").read_text())["repairs"] == []
    assert "## Source check" in (r.data / "notes-final.md").read_text(encoding="utf-8")
    wanted = json.loads((r.data / "authorities-wanted.json").read_text())["wanted"]
    assert [w["reason"] for w in wanted] == ["not_on_file"]
    # the digest path's summarizer never ran; the estimate is the ledger projection
    assert not any("DIGEST-PROMPT" in _text(c.get("system") or "") for c in client.calls)
    # nor the digest audit and its repair rounds: the source check is this path's audit
    assert not any("AUDIT-PROMPT" in _text(c.get("system") or "") for c in client.calls)
    assert not (r.data / "audit-v1.md").exists()
    est = json.loads((r.data / "preflight.json").read_text())["estimate"]
    assert set(est["tail"]["parts"]) >= {"extract", "images", "compose", "check"}
    assert seat.created, "filed"


def test_a_hand_typed_cite_the_one_lint_repair_leaves_fails_the_job(tmp_path, pricing):
    typed = f"Plaintiff treated thereafter (FILE: {ORTHO}, p. 1)."
    client = LedgerClient(typed=typed)
    r, v, log, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("markup_lint: "), (v.reason, log[-5:])
    assert "hand_cite" in v.reason and not seat.created
    lint = json.loads((r.data / "lint-compose.json").read_text())
    assert lint["before"]["hand_cite"] >= 1 and lint["after"]["hand_cite"] >= 1


def test_a_hand_typed_cite_the_lint_repair_removes_drafts_on(tmp_path, pricing):
    typed = f"Plaintiff treated thereafter (FILE: {ORTHO}, p. 1)."
    client = LedgerClient(typed=typed, lint_fix=True)
    r, v, log, _seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert (r.data / "draft-v1.unlinted.md").is_file()
    assert typed not in (r.data / "draft-final.md").read_text(encoding="utf-8")


def test_a_checker_that_confirms_everything_fails_and_files_nothing(tmp_path, pricing):
    stamp = Model(grader=lambda s, ev: ("confirmed", [ev[0][0]] if ev else []))
    _r, v, log, seat = _run(tmp_path, pricing, LedgerClient(checker=stamp))
    assert v.outcome == "failed" and v.reason.startswith("check_invalid: "), (v.reason, log[-5:])
    assert not seat.created


def test_an_edit_to_a_rendered_span_after_render_is_a_gate_failure(tmp_path, pricing, monkeypatch):
    real = DraftingRun._render

    def edited(self, u, md):
        out = real(self, u, md).replace("$1,200.00", "$1,300.00", 1)
        (u.data / "draft-final.md").write_text(out, encoding="utf-8")
        return out

    monkeypatch.setattr(DraftingRun, "_render", edited)
    _r, v, log, seat = _run(tmp_path, pricing, LedgerClient())
    assert v.outcome == "failed" and v.reason.startswith("gate_refused: "), (v.reason, log[-5:])
    assert "span" in v.reason and not seat.created


def test_a_resume_after_a_failure_pays_for_no_finished_stage_twice(tmp_path, pricing):
    # The first attempt stops at the source check (a checker that confirms
    # everything), after every paid stage before it finished.
    stamp = Model(grader=lambda s, ev: ("confirmed", [ev[0][0]] if ev else []))
    first = LedgerClient(checker=stamp)
    _r, v, _log, _seat = _run(tmp_path, pricing, first)
    assert v.outcome == "failed" and v.reason.startswith("check_invalid: ")
    assert _stage_calls(first, "You read a window") == 4  # three records and the complaint
    again = LedgerClient()
    r, v, log, _seat = _run(tmp_path, pricing, again)
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    paid_before_check = ("You read a window", "You read ONE billing document", "COPY THE CAPTION", "## THE HOUSE STYLE")
    assert [n for n in paid_before_check if _stage_calls(again, n)] == []
    assert not any(isinstance(c["messages"][-1]["content"], list) for c in again.calls)  # no page image twice
    assert not any("AUDIT-PROMPT" in _text(c.get("system") or "") for c in again.calls)


def test_the_digest_path_is_byte_identical_with_or_without_the_section(tmp_path, pricing):
    outs = []
    for k, ev in enumerate((None, {"mode": "digest"})):
        base = tmp_path / str(k)
        base.mkdir()
        client = ScriptedClient()
        r, v, log, _seat = _run(base, pricing, client, evidence=ev, documents=standard_docs())
        assert v.outcome == "delivered", (v.reason, log[-5:])
        assert not (r.data / "evidence").exists()
        # every prompt the model was sent, in a stable order (the reads run in parallel)
        prompts = sorted(
            (json.dumps(c.get("system")) + json.dumps(c.get("messages"))).replace(str(base), "<job>")
            for c in client.calls
        )
        outs.append(((r.data / "draft-final.md").read_bytes(), prompts, r._json("preflight.json")["estimate"]))
    assert outs[0] == outs[1]
    assert "tail" not in outs[0][2]


@pytest.mark.parametrize(
    "body, problem",
    [
        ({"mode": "ledger", "classes": ["discovery_set"]}, "cannot draft on the ledger path"),
        ({"mode": "ledger", "classes": ["memo"]}, "cannot draft on the ledger path"),
        ({"mode": "fast"}, "evidence.mode: expected one of"),
        ({"mode": "ledger", "page_window": 0}, "evidence.page_window"),
        ({"mode": "ledger", "extra": 1}, "evidence.extra: unknown key"),
        ({"mode": "ledger", "prompts": {"extract": "evidence/nope.md"}}, "is not pinned in inputs"),
    ],
)
def test_the_schema_refuses_what_the_ledger_path_may_not_draft(tmp_path, body, problem):
    inputs = make_inputs(tmp_path / "in", evidence=body)
    with pytest.raises(firm_mod.DraftingConfigError) as e:
        firm_mod.load(inputs)
    assert problem in str(e.value)


def test_the_ledger_path_is_chosen_only_for_its_class_and_digest_is_the_default():
    assert not evidence_config.settings(firm_data(), "mediation_brief").ledger
    assert evidence_config.settings(firm_data(evidence=LEDGER), "mediation_brief").ledger
    assert not evidence_config.settings(firm_data(evidence=LEDGER), "discovery_set").ledger
    assert firm_mod.validate(firm_data(evidence={"mode": "digest", "classes": ["discovery_set"]})) == [
        "inputs: required section missing or not a map"
    ]


def _pricing(path: Path) -> Any:
    from jobs import budget

    return budget.Pricing.load(path)


def test_the_cost_projection_reads_real_pages_and_its_cli_prices_a_job_dir(tmp_path, pricing, capsys):
    from jobs.drafting import evidence_cost

    rows = [
        {"pages": 25, "chars": 50_000, "transcribe": [3, 4], "text_path": "x"},
        {"pages": 4, "chars": 0, "transcribe": "all", "text_path": "y"},
        {"pages": 0, "unextractable": "no extractor"},
    ]
    c = evidence_cost.corpus_counts(rows, 10)
    assert c == {"pages": 29, "windows": 4, "chars": 50_000 + 6 * evidence_cost.TRANSCRIBED_CHARS}
    data = firm_data(evidence=LEDGER)
    small = evidence_cost.project(rows, data, _pricing(pricing), "mediation_brief")
    big = evidence_cost.project(
        [{**rows[0], "pages": 2500, "chars": 5_000_000}], data, _pricing(pricing), "mediation_brief"
    )
    assert big["parts"]["extract"] > 50 * small["parts"]["extract"] > 0, "the read scales with the file"
    r, v, _log, _seat = _run(tmp_path, pricing, LedgerClient())
    assert v.outcome == "delivered"
    capsys.readouterr()
    argv = [str(r.job.job_dir), "--inputs", str(tmp_path / "inputs"), "--pricing", str(pricing)]
    assert evidence_cost.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pages"] == 4 and out["windows"] == 4 and out["total_usd"] >= out["usd"] > 0


OPERATOR = Path(__file__).resolve().parents[3]


def test_the_estimator_reads_every_rate_card_the_tree_keeps():
    from medchron import budget
    from medchron.drafting import evidence_cost

    cards = [
        OPERATOR / "templates" / "drafting" / "rate-card.json",
        OPERATOR / "hermes-smd-overlay" / "shared" / "anthropic_pricing.json",
        OPERATOR / "adapter" / "cost_telemetry" / "anthropic_pricing.json",
    ]
    for card in cards[:2]:
        with pytest.raises(budget.BudgetError):
            budget.Pricing.load(card)  # the lane's own loader refuses these two shapes
    for card in cards:
        p = evidence_cost.load_pricing(card)
        assert p.rate_for("claude-opus-5-5").input_per_million_cents > 0, card
    assert evidence_cost.TREE_PRICING == cards[2] and evidence_cost.pricing_path(None).is_file()


def test_the_estimate_is_a_range_with_notes_and_flags_the_cap():
    from medchron.drafting import evidence_cost

    rows = [{"pages": 3600, "chars": 9_000_000, "transcribe": [], "text_path": "x"}]
    card = evidence_cost.load_pricing(OPERATOR / "adapter" / "cost_telemetry" / "anthropic_pricing.json")
    data = firm_data(evidence=LEDGER)
    est = evidence_cost.project(rows, data, card, "mediation_brief")
    assert est["chars_per_token"] == 2.0 and "notes" in est["parts"] and "audit" not in est["parts"]
    assert 0 < est["low_usd"] < est["high_usd"] == est["usd"]
    assert est["over_cap"] is (est["high_usd"] > 100.0)
    tight = {**data, "budget": {**data["budget"], "per_job_cap_usd": 1.0}}
    assert evidence_cost.project(rows, tight, card, "mediation_brief")["over_cap"] is True
    measured = evidence_cost.project(rows, data, card, "mediation_brief", chars_per_token=4.0)
    assert measured["parts"]["extract"] < est["parts"]["extract"]


def test_the_cost_cli_runs_without_the_firms_pinned_files(tmp_path, pricing, capsys):
    from medchron.drafting import evidence_cost

    r, v, _log, _seat = _run(tmp_path, pricing, LedgerClient())
    assert v.outcome == "delivered"
    (tmp_path / "inputs" / "pos.md").unlink()  # a pinned file absent off the seat, as the exemplars are
    with pytest.raises(firm_mod.DraftingConfigError):
        firm_mod.load(tmp_path / "inputs")
    capsys.readouterr()
    assert evidence_cost.main([str(r.job.job_dir), "--inputs", str(tmp_path / "inputs")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total_usd"] >= out["total_low_usd"] > 0 and out["rate_card"].endswith("anthropic_pricing.json")
