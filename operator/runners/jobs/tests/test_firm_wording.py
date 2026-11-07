"""A firm's confirmed wording rules reach what a job sends the model (jobs/firm_wording.py).

The proofs, each able to fail:

* with a stored rule for kind A, the compose instructions for a job of kind A
  differ from the no-rule instructions by exactly that rule's block, and kind B's
  are byte-identical to no-rule; the same for the attorney;
* with the seat switch off, every lane's instructions for every kind are byte
  identical to the instructions the code built before rules were read (the
  reference builders below are that code, copied from the parent commit);
* an untrustworthy store holds the job before anything is paid, switched on only.

Names are made up.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from jobs import firm_wording, wording_rules_store
from jobs.demand import draft as demand_draft
from jobs.drafting import compose, evidence_lane
from jobs.drafting.evidence import prompts as ev_prompts

SEP = "\n\n---\n\n"
ATTORNEY = "Dana Whitlock"
DRAFTING_KINDS = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")


# ---- the seat --------------------------------------------------------------------------------
def _switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text("logic_in_code:\n  wording_rules: enforce\n" if on else "customer_id: x\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _rule(rule_id: str, kinds: list[str], attorney: str | None = None, words: str = "") -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "text": words or f"Rule {rule_id} words.",
        "wording": [words or f"Rule {rule_id} words."],
        "doc_kinds": kinds,
        "attorney": attorney,
        "property": "voice",
        "at": "2026-12-01T00:00:00Z",
        "instructed_by": "admin@firm.example",
        "applied_by": "admin@firm.example",
    }


def _store(spec_dir: Path, rules: list[dict[str, Any]]) -> None:
    """An installed store as the root applier commits it: file, then manifest."""
    path = spec_dir / wording_rules_store.WORDING_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"schema_version": wording_rules_store.SCHEMA_VERSION, "rules": rules}).encode()
    path.write_bytes(blob)
    manifest = {
        wording_rules_store.MANIFEST_KEY: {
            wording_rules_store.WORDING_REL: {"sha256": hashlib.sha256(blob).hexdigest()}
        }
    }
    (spec_dir / "manifest.json").write_text(json.dumps(manifest), "utf-8")


# ---- the drafters, and the code they ran before this ----------------------------------------
class _DraftingFirm:
    style = "HOUSE STYLE TEXT"

    def prompt(self, cls: str, stage: str) -> str:
        return f"PROMPT {cls} {stage}"

    def exemplar_paths(self, cls: str) -> list[Path]:
        return []


class _DemandFirm:
    def text(self, key: str) -> str:
        return f"TEXT {key}"


def _drafting(tmp: Path, kind: str, cls: type = compose.Drafter) -> Any:
    d = cls(tmp, None, _DraftingFirm(), kind, "the request", [], 1, print, skeleton=f"SKELETON {kind}")
    if cls is evidence_lane.MarkupDrafter:
        d.compose_prompt = f"EVIDENCE PROMPT {kind}"
    return d


def _demand(tmp: Path) -> Any:
    d = demand_draft.Drafter.__new__(demand_draft.Drafter)
    d.data, d.firm, d.brief = tmp, _DemandFirm(), "THE BRIEF"
    return d


def _drafting_before(d: Any) -> str:
    """``compose.Drafter.compose_system`` at the parent commit."""
    blocks = [
        "## THE HOUSE STYLE (the attorney's own drafting instructions; binding)\n\n" + d.firm.style,
        "## THE SKELETON (structure is fixed; fill it)\n\n" + d.skeleton(),
        d.firm.prompt(d.cls, "compose"),
    ]
    ex = d.exemplars()
    if ex:
        blocks.append("## EXEMPLARS\n\n" + ex)
    return SEP.join(blocks)


def _markup_before(d: Any) -> str:
    """``evidence_lane.MarkupDrafter.compose_system`` at the parent commit."""
    blocks = [
        "## THE HOUSE STYLE (the attorney's own drafting instructions; binding)\n\n" + d.firm.style,
        "## THE SKELETON (structure is fixed; fill it)\n\n" + d.skeleton(),
        d.compose_prompt or d.firm.prompt(d.cls, "compose"),
    ]
    ex = d.exemplars()
    if ex:
        blocks.append("## EXEMPLARS (their FORMAT and VOICE; their cites are not this brief's)\n\n" + ex)
    return SEP.join([*blocks, ev_prompts.MARKUP_BLOCK])


def _demand_before(d: Any) -> str:
    """``demand.draft.Drafter.compose_system`` at the parent commit."""
    f = d.firm
    blocks = [
        demand_draft.discipline_part_one(f.text("drafting_discipline")),
        "## THE SKELETON (structure is fixed; fill it)\n\n" + f.text("skeleton"),
        demand_draft.voice_block(f.text("voice_profile"), f.text("voice_adjustments")),
        "## FIRM FIXED STRINGS (use verbatim where the skeleton calls for them)\n\n" + f.text("voice_fixed_strings"),
        f.text("prompt_compose"),
        demand_draft.FIRM_READS,
        d.brief,
    ]
    return SEP.join(blocks)


def _job(tmp_path: Path, name: str, kind: str, attorney: str | None) -> Path:
    data = tmp_path / name
    data.mkdir()
    firm_wording.freeze(data, kind, attorney)
    return data


EVERY_RULE = [_rule("a1a1a1a1", list(wording_rules_store.KINDS)), _rule("b2b2b2b2", ["memo"], ATTORNEY)]


# ---- off: today's bytes, every lane and kind ------------------------------------------------
@pytest.mark.parametrize("kind", DRAFTING_KINDS)
def test_off_every_drafting_kind_sends_exactly_todays_instructions(tmp_path, monkeypatch, _spec_store, kind):
    _store(_spec_store, EVERY_RULE)
    _switch(tmp_path, monkeypatch, on=False)
    data = _job(tmp_path, "job", kind, ATTORNEY)
    assert not (data / firm_wording.FILE).exists()
    for cls, before in ((compose.Drafter, _drafting_before), (evidence_lane.MarkupDrafter, _markup_before)):
        d = _drafting(data, kind, cls)
        d.wording = firm_wording.block_for(data)
        assert d.compose_system() == before(d)


def test_off_a_demand_sends_exactly_todays_instructions(tmp_path, monkeypatch, _spec_store):
    _store(_spec_store, EVERY_RULE)
    _switch(tmp_path, monkeypatch, on=False)
    data = _job(tmp_path, "job", "demand_letter", ATTORNEY)
    d = _demand(data)
    d.wording = firm_wording.block_for(data)
    assert d.compose_system() == _demand_before(d)


def test_off_an_untrustworthy_store_is_not_even_read(tmp_path, monkeypatch, _spec_store):
    _store(_spec_store, EVERY_RULE)
    (_spec_store / "manifest.json").write_text("{", "utf-8")
    _switch(tmp_path, monkeypatch, on=False)
    assert firm_wording.freeze(tmp_path, "memo", None) == []


# ---- on: exactly the rule in scope -------------------------------------------------------------
def _diff(with_rule: str, without: str, block: str) -> None:
    """``with_rule`` is ``without`` plus exactly one ``block``, and nothing else."""
    assert block and block in with_rule
    assert with_rule.replace(SEP + block, "", 1) == without
    assert len(with_rule) == len(without) + len(SEP + block)


@pytest.mark.parametrize("cls", [compose.Drafter, evidence_lane.MarkupDrafter])
def test_on_kind_a_gets_exactly_its_rule_and_kind_b_gets_nothing(tmp_path, monkeypatch, _spec_store, cls):
    _store(_spec_store, [_rule("c3c3c3c3", ["mediation_brief"], words="Write persuasively.")])
    _switch(tmp_path, monkeypatch, on=True)
    brief = _job(tmp_path, "brief", "mediation_brief", None)
    memo = _job(tmp_path, "memo", "memo", None)
    block = firm_wording.block_for(brief)
    assert "[rule c3c3c3c3] Write persuasively." in block
    a = _drafting(brief, "mediation_brief", cls)
    a.wording = block
    _diff(a.compose_system(), _drafting(brief, "mediation_brief", cls).compose_system(), block)
    b = _drafting(memo, "memo", cls)
    b.wording = firm_wording.block_for(memo)
    assert b.wording == ""
    assert b.compose_system() == _drafting(memo, "memo", cls).compose_system()


def test_on_an_attorneys_rule_reaches_only_documents_in_their_name(tmp_path, monkeypatch, _spec_store):
    _store(_spec_store, [_rule("d4d4d4d4", ["memo"], ATTORNEY, words="Open with the bottom line.")])
    _switch(tmp_path, monkeypatch, on=True)
    theirs = _job(tmp_path, "theirs", "memo", "dana   whitlock")
    other = _job(tmp_path, "other", "memo", "Rae Ostrander")
    unknown = _job(tmp_path, "unknown", "memo", None)
    block = firm_wording.block_for(theirs)
    a = _drafting(theirs, "memo")
    a.wording = block
    _diff(a.compose_system(), _drafting(theirs, "memo").compose_system(), block)
    for data in (other, unknown):
        b = _drafting(data, "memo")
        b.wording = firm_wording.block_for(data)
        assert b.compose_system() == _drafting(data, "memo").compose_system()


def test_on_a_demand_gets_its_rule_in_compose_and_repair(tmp_path, monkeypatch, _spec_store):
    _store(
        _spec_store,
        [_rule("e5e5e5e5", ["demand_letter"], ATTORNEY, "Close with Cordially."), _rule("f6f6f6f6", ["memo"])],
    )
    _switch(tmp_path, monkeypatch, on=True)
    data = _job(tmp_path, "demand", "demand_letter", ATTORNEY)
    block = firm_wording.block_for(data)
    assert "e5e5e5e5" in block and "f6f6f6f6" not in block
    d = _demand(data)
    d.wording = block
    _diff(d.compose_system(), _demand(data).compose_system(), block)


def test_on_a_reader_scoped_rule_reaches_only_a_document_for_that_reader(tmp_path, monkeypatch, _spec_store):
    """A caller that passes no reader still selects none (a scoped rule never
    leaks into a document that cannot say it is in scope); the lanes now pass
    the registry's reader (piece 7), and the brief's mediator gets the rule."""
    scoped = {**_rule("aaaa0004", ["mediation_brief"]), "reader": "mediator"}
    _store(_spec_store, [scoped, _rule("aaaa0005", ["mediation_brief"])])
    _switch(tmp_path, monkeypatch, on=True)
    assert wording_rules_store.read(_spec_store).rules[0]["reader"] == "mediator"
    data = _job(tmp_path, "job", "mediation_brief", None)
    block = firm_wording.block_for(data)
    assert "aaaa0005" in block and "aaaa0004" not in block
    read = tmp_path / "for-the-mediator"
    read.mkdir()
    firm_wording.freeze(read, "mediation_brief", None, "mediator")
    assert "aaaa0004" in firm_wording.block_for(read) and "aaaa0005" in firm_wording.block_for(read)
    assert json.loads((read / firm_wording.FILE).read_text())["reader"] == "mediator"


def test_on_the_selection_is_frozen_for_a_resumed_job(tmp_path, monkeypatch, _spec_store):
    _store(_spec_store, [_rule("a7a7a7a7", ["memo"])])
    _switch(tmp_path, monkeypatch, on=True)
    data = _job(tmp_path, "job", "memo", None)
    _store(_spec_store, [_rule("b8b8b8b8", ["memo"])])
    assert [r["rule_id"] for r in firm_wording.freeze(data, "memo", None)] == ["a7a7a7a7"]
    assert "a7a7a7a7" in firm_wording.block_for(data)


# ---- an untrustworthy store holds the job, switched on ----------------------------------------
@pytest.mark.parametrize("tear", ["manifest", "digest", "schema"])
def test_on_an_untrustworthy_store_raises_before_anything_is_paid(tmp_path, monkeypatch, _spec_store, tear):
    _store(_spec_store, [_rule("a9a9a9a9", ["memo"])])
    path = _spec_store / wording_rules_store.WORDING_REL
    if tear == "manifest":
        (_spec_store / "manifest.json").write_text("{", "utf-8")
    elif tear == "digest":
        path.write_text(path.read_text("utf-8") + " ", "utf-8")
    else:
        _store(_spec_store, [{**_rule("a9a9a9a9", ["memo"]), "doc_kinds": ["chronology"]}])
    _switch(tmp_path, monkeypatch, on=True)
    with pytest.raises(firm_wording.StoreUnusable):
        firm_wording.freeze(tmp_path, "memo", None)
    assert not (tmp_path / firm_wording.FILE).exists()


def test_the_drafting_lane_holds_on_an_untrustworthy_store(tmp_path, monkeypatch, _spec_store):
    from jobs.drafting.outcome import DraftingHold
    from jobs.drafting.run import DraftingRun

    _store(_spec_store, [_rule("aaaa0001", ["memo"])])
    (_spec_store / "manifest.json").write_text("{", "utf-8")
    _switch(tmp_path, monkeypatch, on=True)
    with pytest.raises(DraftingHold, match="^rule_store_unusable: "):
        firm_wording.freeze_or_hold(tmp_path, "memo", {"responsible_attorney": ATTORNEY}, DraftingHold)
    assert "self.data, self.cls, f, DraftingHold, reader, self._style_rule()" in inspect.getsource(DraftingRun)


def test_the_demand_lane_holds_on_an_untrustworthy_store(tmp_path, monkeypatch, _spec_store):
    from jobs.demand.outcome import DemandHold
    from jobs.demand.run import DemandRun

    _store(_spec_store, [_rule("aaaa0002", ["demand_letter"])])
    (_spec_store / "manifest.json").write_text("{", "utf-8")
    _switch(tmp_path, monkeypatch, on=True)
    with pytest.raises(DemandHold, match="^rule_store_unusable: "):
        firm_wording.freeze_or_hold(tmp_path, firm_wording.DEMAND_KIND, {"responsible_attorney": ATTORNEY}, DemandHold)
    src = inspect.getsource(DemandRun)
    assert 'firm_wording.freeze_or_hold(self.data, kind, self._json("facts.json"), DemandHold, reader)' in src
    # frozen after the variant is read and before anything is paid
    assert (
        src.index("self._select_format()\n        #")
        < src.index("self._freeze_wording()")
        < src.index("return self._paid()")
    )


def test_the_daemon_pages_an_untrustworthy_store_only_when_switched_on(tmp_path, monkeypatch, _spec_store):
    from jobs.proof import watch

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(watch, "alert", lambda d, job_id, code, detail="", **_k: seen.append((code, job_id)))
    _store(_spec_store, [_rule("aaaa0003", ["memo"])])
    (_spec_store / "manifest.json").write_text("{", "utf-8")
    _switch(tmp_path, monkeypatch, on=False)
    firm_wording.alert_unusable(object(), "J1")
    _switch(tmp_path, monkeypatch, on=True)
    firm_wording.alert_unusable(object(), "J2")
    assert seen == [("rule_store_unusable", "J2")]


@pytest.mark.parametrize("kind", DRAFTING_KINDS)
@pytest.mark.parametrize(
    "cls,before", [(compose.Drafter, _drafting_before), (evidence_lane.MarkupDrafter, _markup_before)]
)
def test_a_style_file_out_of_scope_takes_out_the_house_style_block_and_nothing_else(tmp_path, kind, cls, before):
    """``house_style`` False (drafting/style_scope.py: the style file is another
    attorney's) removes exactly THE HOUSE STYLE block from both drafters' compose."""
    d = _drafting(tmp_path, kind, cls)
    d.house_style = False
    block = "## THE HOUSE STYLE (the attorney's own drafting instructions; binding)\n\n" + d.firm.style + SEP
    assert block in before(d) and d.compose_system() == before(d).replace(block, "", 1)
