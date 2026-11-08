"""Reader as an input (design 2026-11-06, piece 7): each kind's reader from the
document-kind registry, the wording-rules store keyed by kind, attorney and
reader, and the record beside each document naming the reader and the profile
in effect (``operator_connector_sdk.library.readers``).

The proofs, through the real lanes over made-up inputs:

* a demand and a memo on ONE matter, with one store: each job's instructions
  carry its own reader's rule and not the other's, and the two rule blocks
  differ by exactly that, in both directions;
* with no reader-scoped rule, every lane sends what it sent when no caller
  passed a reader (the parent commit's behaviour): the drafting job, the
  evidence-first brief and the demand, request for request;
* the record names the reader and the existing profile that carried it, and a
  reader with no profile of its own is carried by the path's base, said so.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import library_testkit as lt
from operator_connector_sdk.library import readers
from operator_connector_sdk.library.caller import record_problems

from jobs import firm_wording, wording_rules_store
from jobs_testkit import PRICING

RUNNER = Path(__file__).resolve().parents[1]
CHECKER = RUNNER.parents[1] / "templates" / "drafting" / "drafting_gate_check.py"
KINDS = ["demand_letter", "memo"]
UNSCOPED = "Never call the client a claimant."
FOR_ADJUSTER = "Name the policy limits in the opening paragraph."
FOR_INTERNAL = "Open with the bottom line in one sentence."


def _rule(rule_id: str, words: str, reader: str | None = None) -> dict:
    return {
        "rule_id": rule_id,
        "text": words,
        "wording": [words],
        "doc_kinds": KINDS,
        "attorney": None,
        "reader": reader,
        "property": "voice",
        "at": "2026-12-01T00:00:00Z",
        "instructed_by": "admin@firm.example",
        "applied_by": "admin@firm.example",
    }


RULES = [
    _rule("aaaa1111", UNSCOPED),
    _rule("bbbb2222", FOR_ADJUSTER, "adjuster"),
    _rule("cccc3333", FOR_INTERNAL, "internal"),
]


@pytest.fixture
def seat(tmp_path, monkeypatch, _spec_store):
    """The wording rules switched on, and the store the root applier would install."""
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    cy = tmp_path / "customer.yaml"
    cy.write_text("logic_in_code:\n  wording_rules: enforce\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cy))

    def install(rules: list[dict]) -> None:
        path = _spec_store / wording_rules_store.WORDING_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps({"schema_version": wording_rules_store.SCHEMA_VERSION, "rules": rules}).encode()
        path.write_bytes(blob)
        manifest = {
            wording_rules_store.MANIFEST_KEY: {
                wording_rules_store.WORDING_REL: {"sha256": hashlib.sha256(blob).hexdigest()}
            }
        }
        (_spec_store / "manifest.json").write_text(json.dumps(manifest), "utf-8")

    return install


def _demand(root: Path):
    from demand_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
    from jobs.demand.run import DemandRun

    root.mkdir(parents=True, exist_ok=True)
    pricing = root / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    client = ScriptedClient()
    r = DemandRun(
        make_job(root / "job"),
        inputs_dir=str(make_inputs(root / "inputs")),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run(), client


def _drafting(root: Path, cls: str):
    from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
    from jobs.drafting.run import DraftingRun

    root.mkdir(parents=True, exist_ok=True)
    pricing = root / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    client = ScriptedClient()
    r = DraftingRun(
        make_job(root / "job", cls=cls),
        inputs_dir=str(make_inputs(root / "inputs")),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
    )
    return r, r.run(), client


def _sent(client) -> list[str]:
    return sorted(json.dumps(c, sort_keys=True) for c in client.calls)


def _composed(client, marker: str) -> str:
    return "\n".join(json.dumps(c.get("system")) for c in client.calls if marker in json.dumps(c.get("system")))


# ---- the registry's reader, and the record --------------------------------------------------


@pytest.mark.parametrize(
    "path,kind,variant,want",
    [
        (readers.DEMAND_JOB, "demand_letter", "pre_suit", ("adjuster", "registry", "adjuster")),
        (readers.DEMAND_JOB, "demand_letter", "litigation", ("opposing_counsel", "variant", "firm_base")),
        (readers.DRAFTING_JOB, "mediation_brief", None, ("mediator", "registry", "court")),
        (readers.DRAFTING_JOB, "discovery_set", None, ("opposing_counsel", "registry", "court")),
        (readers.DRAFTING_JOB, "discovery_response", None, ("opposing_counsel", "registry", "court")),
        (readers.DRAFTING_JOB, "memo", None, ("internal", "registry", "internal")),
        (readers.DRAFTING_JOB, "depo_outline", None, ("internal", "registry", "internal")),
        (readers.CHAT_TOOL, "memo", None, ("internal", "registry", "firm_base")),
        (readers.CHAT_TOOL, "letter", None, (None, "addressee_unknown", "firm_base")),
    ],
)
def test_each_kind_has_its_registry_reader_and_only_an_existing_profile(path, kind, variant, want):
    got = readers.record(path, kind, variant)
    assert (got["reader"], got["reader_from"], got["profile"]) == want
    assert readers.record_problems(got) == []
    assert got["profile_lives_in"]


def test_a_letter_takes_its_addressees_reader_when_the_roles_name_one():
    got = readers.record(readers.CHAT_TOOL, "letter", addressee="adjuster")
    assert (got["reader"], got["reader_from"], got["profile"]) == ("adjuster", "addressee_role", "firm_base")
    assert readers.record(readers.CHAT_TOOL, "letter", addressee="nobody")["reader"] is None


# ---- one matter, a demand and a memo: different readers, nothing else different ---------------


def test_a_demand_and_a_memo_on_one_matter_differ_by_their_reader_and_nothing_else(tmp_path, seat):
    """FALSIFIER: pass no reader (as every caller did before) and neither job gets
    its reader's rule; pass one reader to both and the two blocks are equal."""
    seat(RULES)
    dr, dv, dclient = _demand(tmp_path / "demand")
    mr, mv, mclient = _drafting(tmp_path / "memo", "memo")
    assert dv.outcome == "delivered" and mv.outcome == "delivered", (dv.reason, mv.reason)
    demand_block, memo_block = firm_wording.block_for(dr.data), firm_wording.block_for(mr.data)
    a = "- [rule bbbb2222] " + FOR_ADJUSTER
    i = "- [rule cccc3333] " + FOR_INTERNAL
    # Exactly the reader's rule differs, in both directions.
    assert demand_block.replace(a, i) == memo_block and memo_block.replace(i, a) == demand_block
    assert UNSCOPED in demand_block and UNSCOPED in memo_block
    # What each job sent the model: its own reader's rule, never the other's.
    sent_demand, sent_memo = _composed(dclient, "COMPOSE-PROMPT"), _composed(mclient, "COMPOSE-PROMPT")
    assert FOR_ADJUSTER in sent_demand and FOR_INTERNAL not in sent_demand
    assert FOR_INTERNAL in sent_memo and FOR_ADJUSTER not in sent_memo
    # The records name each reader and the profile that carried it.
    for run, reader, profile in ((dr, "adjuster", "adjuster"), (mr, "internal", "internal")):
        rec = lt.record(run.data)
        assert record_problems(rec) == [] and rec["reader"]["reader"] == reader
        assert rec["reader"]["profile"] == profile
        assert json.loads((run.data / firm_wording.FILE).read_text())["reader"] == reader


# ---- no reader-scoped rule: every lane sends exactly what it sent before --------------------


def _as_before(monkeypatch) -> None:
    """The parent commit: no caller passed a reader."""
    monkeypatch.setattr(readers, "reader_for", lambda kind, variant=None: None)


@pytest.mark.parametrize("cls", ["memo", "mediation_brief", "discovery_set"])
def test_with_no_reader_scoped_rule_a_drafting_job_sends_todays_requests(tmp_path, seat, monkeypatch, cls):
    seat([_rule("aaaa1111", UNSCOPED) | {"doc_kinds": list(wording_rules_store.KINDS)}])
    new, v_new, c_new = _drafting(tmp_path / "new", cls)
    _as_before(monkeypatch)
    old, v_old, c_old = _drafting(tmp_path / "old", cls)
    assert v_new.outcome == v_old.outcome, (v_new.reason, v_old.reason)
    assert _sent(c_new) == _sent(c_old)


def test_with_no_reader_scoped_rule_a_demand_sends_todays_requests(tmp_path, seat, monkeypatch):
    seat([_rule("aaaa1111", UNSCOPED)])
    new, v_new, c_new = _demand(tmp_path / "new")
    _as_before(monkeypatch)
    old, v_old, c_old = _demand(tmp_path / "old")
    assert v_new.outcome == v_old.outcome == "delivered", (v_new.reason, v_old.reason)
    assert _sent(c_new) == _sent(c_old)
