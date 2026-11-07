"""Two attorneys, two accepted files of their own: two demands, two looks, two
signatures (design 2026-11-06, piece 8, "an attorney's own file for any
attorney a firm sets up").

End to end through the real ``DemandRun``: the seat's ledger accepts one
prototypes file per attorney (``ledger`` attorney slot, the key the resolver
files a matter's attorney under), each with a header of its own; the demand
inputs sign each attorney in their own words. The responsible attorney on the
matter is the only thing that differs between the two jobs, and each demand is
written in that attorney's own file and signed by that attorney.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import docx
import pytest

import library_testkit as lt
from demand_testkit import ScriptedClient, firm_data, make_inputs, make_job, seat_with, standard_docs
from jobs.demand import library_pick
from jobs.demand.run import DemandRun
from jobs.drafting import firm_rules
from jobs_testkit import PRICING
from operator_connector_sdk.library import PROTOTYPES, MemoryLibrary
from test_demand_library_pick import _demand, _reference

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
SLOT = "demand_letter.pre_suit"
SIGNS = {
    "Example Lawyer": {"signer": "Example A. Lawyer", "title": "Attorney at Law", "initials": "EAL/dm"},
    "Second Attorney": {"signer": "Second B. Attorney", "title": "Trial Counsel", "initials": "SBA/dm"},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    (tmp_path / "specs").mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))


def _run(root: Path, attorney: str):
    root.mkdir(parents=True, exist_ok=True)
    pricing = root / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs", attorneys={**firm_data()["attorneys"], **SIGNS})
    seat = seat_with(standard_docs())
    seat.facts = {**seat.facts, "responsible_attorney": attorney}
    r = DemandRun(
        make_job(root / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=ScriptedClient(),
        log=lambda m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run()


def test_two_attorneys_accepted_files_make_two_demands_with_two_looks_and_two_signatures(tmp_path, monkeypatch):
    lib = MemoryLibrary()
    lt.install(
        tmp_path / "specs",
        lt.entry(
            lib,
            "own-first",
            SLOT,
            _reference("FIRST ATTORNEY'S OWN FILE"),
            attorney="example lawyer",
            contract=PROTOTYPES,
        ),
        lt.entry(
            lib,
            "own-second",
            SLOT,
            _reference("SECOND ATTORNEY'S OWN FILE"),
            attorney="second attorney",
            contract=PROTOTYPES,
        ),
    )
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    made = {}
    for who in SIGNS:
        r, v = _run(tmp_path / who.split()[0].lower(), who)
        assert v.outcome == "delivered", (who, v.reason)
        doc = docx.Document(io.BytesIO(_demand(r)))
        rec = lt.record(r.data)
        made[who] = (doc.sections[0].header.paragraphs[0].text, "\n".join(p.text for p in doc.paragraphs), rec)
    first, second = made["Example Lawyer"], made["Second Attorney"]
    assert first[0] == "FIRST ATTORNEY'S OWN FILE" and second[0] == "SECOND ATTORNEY'S OWN FILE"  # two looks
    assert "Example A. Lawyer" in first[1] and "Second B. Attorney" not in first[1]  # two signatures
    assert "Second B. Attorney" in second[1] and "Trial Counsel" in second[1] and "Example A. Lawyer" not in second[1]
    assert (
        first[2]["resolution"]["file"]["file_id"] == "own-first"
        and second[2]["resolution"]["file"]["file_id"] == "own-second"
    )
    assert first[2]["resolution"]["file"]["scope"] == second[2]["resolution"]["file"]["scope"] == "attorney"
