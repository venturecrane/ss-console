"""The firm's authored attorney block (drafting/attorneys.py): validated with the
firm config, chosen per job (responsible attorney, then requester), and handed to
compose, audit and the drafting gate as a source. Fictional throughout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.drafting import attorneys, firm as firm_mod, gate
from medchron.drafting.run import DraftingRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
LEAD = {
    "email": "Lead@Firm.example",
    "block": [
        "Lead Attorney, Esq. (SBN 123456)",
        "EXAMPLE & EXAMPLE, LLP",
        "100 Main Street",
        "Exampletown, California 90000",
        "Telephone: (555) 555-0100",
        "Email: lead@firm.example",
    ],
}
ADMIN = {"email": "admin@firm.example", "block": ["Admin Attorney (SBN 654321)", "Email: admin@firm.example"]}


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def test_a_valid_section_loads_and_a_bad_one_refuses_the_whole_config(tmp_path):
    assert attorneys.validate([LEAD, ADMIN]) == []
    assert firm_mod.load(str(make_inputs(tmp_path / "ok", attorneys=[LEAD]))).attorneys == [LEAD]
    assert firm_mod.load(str(make_inputs(tmp_path / "none"))).attorneys == []
    with pytest.raises(firm_mod.DraftingConfigError, match="attorneys"):
        firm_mod.load(str(make_inputs(tmp_path / "bad", attorneys=[{"email": "x", "block": []}])))


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"email": "not-an-email", "block": ["x"]}],
        [{"email": "a@b.example", "block": []}],
        [{"email": "a@b.example", "block": ["ok", ""]}],
        [{"email": "a@b.example", "block": ["x"], "extra": 1}],
        [{"email": "a@b.example", "block": ["x"]}, {"email": "A@B.example", "block": ["y"]}],
    ],
)
def test_a_malformed_section_refuses(bad):
    assert attorneys.validate(bad)


def test_the_responsible_attorney_wins_then_the_requester_then_none():
    entries = [LEAD, ADMIN]
    assert attorneys.pick(entries, "lead@firm.example", "admin@firm.example")["email"] == "lead@firm.example"
    assert attorneys.pick(entries, "someone@firm.example", "Admin@Firm.example")["email"] == "admin@firm.example"
    assert attorneys.pick(entries, "", "nobody@firm.example") is None
    assert attorneys.pick([], "lead@firm.example", None) is None


def _run(tmp_path: Path, attys: list | None, responsible: str):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    make_inputs(inputs, **({"attorneys": attys} if attys is not None else {}))
    jd = make_job(tmp_path / "job")
    seat, client = seat_with(standard_docs()), ScriptedClient()
    seat.record = {**seat.record, "attorney_email": responsible}
    r = DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
    )
    return r, r.run(), client


def _calls(client, marker: str) -> list[str]:
    return [json.dumps(c) for c in client.calls if marker in json.dumps(c.get("system"))]


def test_the_responsible_attorneys_block_reaches_compose_audit_and_the_gate(tmp_path):
    r, v, client = _run(tmp_path, [LEAD, ADMIN], "lead@firm.example")
    assert v.outcome == "delivered", (v.stage, v.reason)
    cap = json.loads((r.data / "caption.json").read_text())
    assert cap["attorney_block"]["email"] == "lead@firm.example"
    for marker in ("COMPOSE-PROMPT", "AUDIT-PROMPT"):
        sent = _calls(client, marker)
        assert sent and all("THE ATTORNEY BLOCK" in s and "Email: lead@firm.example" in s for s in sent), marker
    sources, _vision, _held = gate.collect(r.data, firm_mod.load(str(tmp_path / "inputs")), "mediation_brief")
    assert ("firm attorney block", "\n".join(LEAD["block"])) in sources


def test_the_requesters_block_is_used_when_the_responsible_attorney_has_none(tmp_path):
    r, v, _client = _run(tmp_path, [ADMIN], "")
    assert json.loads((r.data / "caption.json").read_text())["attorney_block"]["email"] == "admin@firm.example"


def test_a_firm_without_attorneys_drafts_exactly_as_before(tmp_path):
    r, v, client = _run(tmp_path, None, "lead@firm.example")
    assert v.outcome == "delivered"
    assert json.loads((r.data / "caption.json").read_text())["attorney_block"] is None
    assert not any("THE ATTORNEY BLOCK" in s for s in _calls(client, "COMPOSE-PROMPT"))
    sources, _v, _h = gate.collect(r.data, firm_mod.load(str(tmp_path / "inputs")), "mediation_brief")
    assert not any(name == "firm attorney block" for name, _t in sources)
