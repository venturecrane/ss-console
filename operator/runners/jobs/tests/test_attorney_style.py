"""The drafting style file scoped to the attorney whose instructions it is
(design 2026-11-06, piece 8; ``drafting/style_scope.py``).

Real ``DraftingRun`` jobs over made-up inputs, each run twice and compared
request for request (the section audits run concurrently, so as sets):

* switched off, or no wording store installed, or no ``style_attorney``: every
  request is today's, whoever's name is on the document;
* switched on with the store installed: a document in HIS name sends today's
  requests exactly; a document in ANOTHER attorney's name sends today's with
  THE HOUSE STYLE block removed from every compose, and nothing else changed,
  for every drafting kind;
* a scope naming someone the roster does not list as an attorney stops the job.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from jobs import firm_wording, wording_rules_store
from jobs.drafting import style_scope
from jobs.drafting.run import DraftingRun
from jobs_testkit import PRICING
from operator_connector_sdk.library import roster as roster_mod

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
HIS = "Example Lawyer"  # the kits' responsible attorney
OTHER = "Second Attorney"
STYLE = "Times New Roman 12. Roman numerals centered and bold only."  # the kit's style file
BLOCK = "## THE HOUSE STYLE (the attorney's own drafting instructions; binding)\n\n" + STYLE + "\n\n---\n\n"
KINDS = style_scope.KINDS


@pytest.fixture(autouse=True)
def _seat(tmp_path, monkeypatch, _spec_store):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))


def _switch(tmp_path: Path, monkeypatch, on: bool) -> None:
    cy = tmp_path / "customer.yaml"
    cy.write_text("logic_in_code:\n  wording_rules: enforce\n" if on else "customer_id: x\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cy))


def _store(spec: Path, rules: list[dict] | None = None) -> None:
    path = spec / wording_rules_store.WORDING_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"schema_version": wording_rules_store.SCHEMA_VERSION, "rules": rules or []}).encode()
    path.write_bytes(blob)
    manifest = {
        wording_rules_store.MANIFEST_KEY: {
            wording_rules_store.WORDING_REL: {"sha256": hashlib.sha256(blob).hexdigest()}
        }
    }
    (spec / "manifest.json").write_text(json.dumps(manifest), "utf-8")


def _run(root: Path, cls: str, attorney: str, scoped: bool):
    root.mkdir(parents=True, exist_ok=True)
    pricing = root / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs", **({style_scope.KEY: HIS} if scoped else {}))
    seat = seat_with(standard_docs())
    seat.facts = {**seat.facts, "responsible_attorney": attorney}
    client = ScriptedClient()
    r = DraftingRun(
        make_job(root / "job", cls=cls),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
    )
    return r, r.run(), client


def _sent(client) -> list[str]:
    return sorted(json.dumps(c, sort_keys=True) for c in client.calls)


def _without_style(client) -> list[str]:
    """Today's requests with THE HOUSE STYLE block taken out of every system prompt."""
    out = []
    for call in client.calls:
        c = copy.deepcopy(call)
        for part in c.get("system") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = part["text"].replace(BLOCK, "", 1)
        out.append(json.dumps(c, sort_keys=True))
    return sorted(out)


def _composes_with_style(client) -> int:
    return sum(BLOCK in json.dumps(c.get("system")).replace("\\n", "\n") for c in client.calls)


@pytest.mark.parametrize("kind", KINDS)
def test_switched_on_his_documents_are_todays_and_anothers_lose_exactly_his_style(
    tmp_path, monkeypatch, _spec_store, kind
):
    """Each run is compared with today's run on the same matter in the same
    attorney's name (the name is in the matter record the model reads)."""
    _switch(tmp_path, monkeypatch, on=True)
    _store(_spec_store)
    _, v0, today_his = _run(tmp_path / "today-his", kind, HIS, scoped=False)
    his, v1, c1 = _run(tmp_path / "his", kind, HIS, scoped=True)
    _, v2, today_other = _run(tmp_path / "today-other", kind, OTHER, scoped=False)
    other, v3, c3 = _run(tmp_path / "other", kind, OTHER, scoped=True)
    assert v0.outcome == v1.outcome == v2.outcome == v3.outcome, (v0.reason, v1.reason, v2.reason, v3.reason)
    assert _composes_with_style(today_his) > 0 and _composes_with_style(today_other) > 0
    assert _sent(c1) == _sent(today_his)  # in his name: byte for byte today's
    assert _sent(c3) == _without_style(today_other)  # another's: today's minus THE HOUSE STYLE, nothing else
    assert _sent(c3) != _sent(today_other) and _composes_with_style(c3) == 0
    frozen = json.loads((other.data / firm_wording.FILE).read_text())[firm_wording.HOUSE_STYLE]
    assert frozen["applies"] is False and frozen["rule"]["attorney"] == HIS
    assert json.loads((his.data / firm_wording.FILE).read_text())[firm_wording.HOUSE_STYLE]["applies"] is True


def test_his_name_is_matched_with_case_and_spacing_folded(tmp_path, monkeypatch, _spec_store):
    _switch(tmp_path, monkeypatch, on=True)
    _store(_spec_store)
    r, v, c = _run(tmp_path / "his", "memo", "example  LAWYER", scoped=True)
    assert json.loads((r.data / firm_wording.FILE).read_text())[firm_wording.HOUSE_STYLE]["applies"] is True
    assert _composes_with_style(c) > 0


@pytest.mark.parametrize("why", ["switched off", "no store installed"])
def test_off_or_without_a_store_every_document_gets_the_style_file_as_today(tmp_path, monkeypatch, _spec_store, why):
    _switch(tmp_path, monkeypatch, on=why != "switched off")
    if why == "switched off":
        _store(_spec_store)
    _, v0, c0 = _run(tmp_path / "today", "memo", "Second Attorney", scoped=False)
    _, v1, c1 = _run(tmp_path / "scoped", "memo", "Second Attorney", scoped=True)
    assert v0.outcome == v1.outcome
    assert _sent(c1) == _sent(c0) and _composes_with_style(c1) > 0


def test_a_scope_naming_someone_the_roster_does_not_list_stops_the_job(tmp_path, monkeypatch, _spec_store):
    _switch(tmp_path, monkeypatch, on=True)
    _store(_spec_store)
    folder = tmp_path / "roster"
    folder.mkdir()
    doc = {
        "schema_version": 1,
        "attorneys": [{"name": "Second Attorney", "emails": []}, {"name": "Third One", "emails": []}],
    }
    (folder / roster_mod.FILE_NAME).write_text(json.dumps(doc), "utf-8")
    monkeypatch.setenv(roster_mod.DIR_ENV, str(folder))
    _, v, client = _run(tmp_path / "job", "memo", "Second Attorney", scoped=True)
    assert v.outcome == "failed" and v.reason.startswith("config_missing: "), v.reason
    assert client.calls == []


def test_the_scope_is_validated_with_the_inputs(tmp_path):
    from jobs.drafting import firm as firm_mod

    with pytest.raises(firm_mod.DraftingConfigError, match=style_scope.KEY):
        firm_mod.load(str(make_inputs(tmp_path / "bad", **{style_scope.KEY: "  "})))
    assert (
        firm_mod.load(str(make_inputs(tmp_path / "ok", **{style_scope.KEY: "Example  Lawyer"}))).style_attorney == HIS
    )
    assert firm_mod.load(str(make_inputs(tmp_path / "none"))).style_attorney is None
