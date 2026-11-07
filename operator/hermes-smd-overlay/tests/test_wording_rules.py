"""A firm's confirmed wording rule takes effect where the document is made (2026-11-06).

Three halves, each with the input the absent control would have waved through:

* **The record.** The spec applier writes each ``work_product`` rule that carries
  a reach, and only those, to ``wording.json`` under the root manifest; the
  intake and the applier refuse a malformed reach.
* **The reach.** A proposal covers only the kinds the requester's own words
  name, and an attorney only when the requester is or names them. With the seat
  switched on a wider reach is refused; off, it is cut to the words, never widened.
* **The letter.** "In effect" for a kind a job lane reads, with the switch on;
  "recorded" with it off, for a letter, and for a record product.

Fixture names are made up.
"""

from __future__ import annotations

import io
import json

import pytest

from establish_intake.intake import EstablishIntake
from shared import rule_dispatch, rule_reach, wording_rules, wording_rules_store
from shared.inbound import SESSION_INBOUND_ORIGIN, SESSION_TAINT
from spec_applier.applier import SCHEMA_VERSION, apply
from tests.conftest import load_plugin

RULE = "5e1f00aa"
ADMIN = "admin@firm.example"
ATTORNEY = "Dana Whitlock"


def _seat(tmp_path, monkeypatch, on: bool):
    path = tmp_path / "customer.yaml"
    path.write_text(
        "logic_in_code:\n  wording_rules: enforce\n" if on else "customer_id: x\n", "utf-8"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _install(tmp_path, monkeypatch, adjustments, prop="voice", output_class="work_product"):
    doc = {
        "schema_version": SCHEMA_VERSION,
        "classes": {output_class: {prop: {"adjustments": adjustments}}},
    }

    class S3:
        def get_object(self, **_k):
            return {"Body": io.BytesIO(json.dumps(doc).encode())}

    spec_dir = tmp_path / "specs"
    result = apply(s3_client=S3(), bucket="b", slug="s", spec_dir=spec_dir)
    monkeypatch.setenv("SMD_SPEC_DIR", str(spec_dir))
    return result, spec_dir


def _adj(text="Write persuasively, never conversationally.", **reach):
    return {"id": RULE, "text": text, "instructed_by": ADMIN, "at": "2026-12-01T00:00:00Z", **reach}


# ---- the record ----------------------------------------------------------------------------
def test_the_applier_writes_the_reach_under_the_manifest(tmp_path, monkeypatch):
    result, spec_dir = _install(
        tmp_path, monkeypatch, [_adj(doc_kinds=["mediation_brief"], attorney=ATTORNEY)]
    )
    assert result.applied
    store = wording_rules_store.read(spec_dir)
    assert store.status == wording_rules_store.OK
    (rule,) = store.rules
    assert rule["doc_kinds"] == ["mediation_brief"]
    assert rule["attorney"] == ATTORNEY
    assert rule["wording"] == ["Write persuasively, never conversationally."]
    manifest = json.loads((spec_dir / "manifest.json").read_text())
    assert wording_rules_store.WORDING_REL in manifest["wordings"]


def test_the_voice_file_is_unchanged_by_a_reach(tmp_path, monkeypatch):
    """Chat turns read the .md: the reach lives in wording.json only."""
    _, with_reach = _install(tmp_path, monkeypatch, [_adj(doc_kinds=["memo"])])
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    _, without = _install(plain_dir, monkeypatch, [_adj()])
    rel = "classes/work_product/voice.md"
    assert (with_reach / rel).read_bytes() == (without / rel).read_bytes()


def test_a_rule_with_no_recorded_reach_writes_no_wording_file(tmp_path, monkeypatch):
    _, spec_dir = _install(tmp_path, monkeypatch, [_adj()])
    assert not (spec_dir / wording_rules_store.WORDING_REL).exists()
    assert wording_rules_store.read(spec_dir).status == wording_rules_store.NONE


def test_a_layout_clause_is_not_said_to_the_model_twice(tmp_path, monkeypatch):
    text = "Never use the word plainly; two blank lines before the court name."
    _, spec_dir = _install(
        tmp_path, monkeypatch, [_adj(text=text, doc_kinds=["memo"])], prop="format"
    )
    (rule,) = wording_rules_store.read(spec_dir).rules
    assert rule["wording"] == ["Never use the word plainly"]


@pytest.mark.parametrize(
    "reach",
    [
        {"doc_kinds": []},
        {"doc_kinds": ["chronology"]},
        {"doc_kinds": ["memo", "memo"]},
        {"attorney": ATTORNEY},
        {"doc_kinds": ["memo"], "attorney": " "},
        {"doc_kinds": ["memo"], "reader": "jury"},
        {"reader": "mediator"},
    ],
)
def test_a_malformed_reach_refuses_the_whole_document(tmp_path, monkeypatch, reach):
    result, _ = _install(tmp_path, monkeypatch, [_adj(**reach)])
    assert not result.applied
    record, problems = EstablishIntake._verify_adjustment(
        {**_adj(**reach), "sha256": "x", "applied_by": ADMIN}
    )
    assert record is None and any(
        "doc_kinds" in p or "attorney" in p or "reader" in p for p in problems
    )


def test_the_intake_carries_the_reach_it_verified():
    from shared.ids import sha256

    raw = _adj(doc_kinds=["demand_letter"], attorney=ATTORNEY)
    raw["sha256"] = sha256(raw["text"].encode("utf-8"))
    record, problems = EstablishIntake._verify_adjustment(raw)
    assert problems == []
    assert record["doc_kinds"] == ["demand_letter"] and record["attorney"] == ATTORNEY


def test_a_torn_store_is_unusable_never_empty(tmp_path, monkeypatch):
    _, spec_dir = _install(tmp_path, monkeypatch, [_adj(doc_kinds=["memo"])])
    (spec_dir / wording_rules_store.WORDING_REL).write_text('{"schema_version": 1, "rules": []}')
    assert wording_rules_store.read(spec_dir).status == wording_rules_store.UNUSABLE


# ---- the selection, in code ----------------------------------------------------------------
def test_the_reader_is_recorded_and_round_trips_through_the_installed_file(tmp_path, monkeypatch):
    _, spec_dir = _install(
        tmp_path, monkeypatch, [_adj(doc_kinds=["mediation_brief"], reader="mediator")]
    )
    (rule,) = wording_rules_store.read(spec_dir).rules
    assert rule["reader"] == "mediator"
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    _, spec_dir = _install(plain_dir, monkeypatch, [_adj(doc_kinds=["memo"])])
    (rule,) = wording_rules_store.read(spec_dir).rules
    assert rule["reader"] is None


def test_a_reader_scoped_rule_never_reaches_a_document_whose_reader_is_unknown():
    """No lane knows its document's reader yet: callers pass none, and a rule
    limited to one reader must then never be selected."""
    rules = [
        {"rule_id": "a", "wording": ["x"], "doc_kinds": ["mediation_brief"], "attorney": None},
        {
            "rule_id": "m",
            "wording": ["y"],
            "doc_kinds": ["mediation_brief"],
            "attorney": None,
            "reader": "mediator",
        },
    ]
    pick = wording_rules_store.applicable
    assert [r["rule_id"] for r in pick(rules, "mediation_brief", None)] == ["a"]
    assert [r["rule_id"] for r in pick(rules, "mediation_brief", None, None)] == ["a"]
    assert [r["rule_id"] for r in pick(rules, "mediation_brief", None, "court")] == ["a"]
    assert [r["rule_id"] for r in pick(rules, "mediation_brief", None, "mediator")] == ["a", "m"]


def test_a_reader_stands_only_on_the_requesters_words_and_is_never_invented():
    own = "In briefs for the mediator, lead with the damages."
    ok = rule_reach.check(_subject(doc_kinds=["mediation_brief"], reader="mediator"), own, None)
    assert ok.refusal == "" and ok.reader == "mediator"
    bare = "In briefs, lead with the damages."
    refused = rule_reach.check(
        _subject(doc_kinds=["mediation_brief"], reader="mediator"), bare, None
    )
    assert "does not say the rule is for documents written to mediator" in refused.refusal
    # off: dropping only the reader would widen it to every reader, so nothing is recorded
    assert (
        rule_reach.narrowed(_subject(doc_kinds=["mediation_brief"], reader="mediator"), bare, None)
        == {}
    )
    assert rule_reach.narrowed(_subject(doc_kinds=["mediation_brief"]), bare, None) == {
        "doc_kinds": ["mediation_brief"]
    }


def test_a_rule_is_selected_only_for_its_kind_and_its_attorney():
    rules = [
        {"rule_id": "a", "wording": ["x"], "doc_kinds": ["memo"], "attorney": None},
        {"rule_id": "b", "wording": ["y"], "doc_kinds": ["memo"], "attorney": ATTORNEY},
    ]
    pick = wording_rules_store.applicable
    assert [r["rule_id"] for r in pick(rules, "memo", "dana  WHITLOCK")] == ["a", "b"]
    assert [r["rule_id"] for r in pick(rules, "memo", "Rae Ostrander")] == ["a"]
    assert [r["rule_id"] for r in pick(rules, "memo", None)] == ["a"]
    assert pick(rules, "mediation_brief", ATTORNEY) == []


# ---- the reach -----------------------------------------------------------------------------
_OWN = "From now on, in our mediation briefs, write persuasively."


@pytest.mark.parametrize(
    "words, kinds",
    [
        ("In every mediation brief, open with the posture.", ("mediation_brief",)),
        ("Memos and depo outlines get a summary up top.", ("memo", "depo_outline")),
        ("Dana's demands close with Cordially.", ("demand_letter",)),
        ("In all our documents, no contractions.", wording_rules_store.KINDS),
        ("Double space the headings.", ()),
    ],
)
def test_kinds_are_read_from_the_words_against_a_fixed_list(words, kinds):
    assert rule_reach.kinds_named(words) == kinds


def _subject(**over):
    return {"output_class": "work_product", "property": "voice", **over}


def test_a_reach_beyond_the_requesters_words_is_refused():
    """Answer key row 131: a briefs rule stored for memos and discovery too."""
    verdict = rule_reach.check(
        _subject(doc_kinds=["mediation_brief", "memo", "discovery_set"]), _OWN, None
    )
    assert verdict.refusal and "mediation briefs" in verdict.refusal


def test_a_reach_the_requester_named_stands():
    own = "Briefs and memos: write persuasively."
    verdict = rule_reach.check(_subject(doc_kinds=["mediation_brief", "memo"]), own, None)
    assert verdict.refusal == ""
    assert verdict.kinds == ("mediation_brief", "memo")


def test_a_rule_naming_no_kind_is_sent_back_to_ask():
    verdict = rule_reach.check(_subject(), "Double space the headings.", None)
    assert "ask them which documents" in verdict.refusal


def test_an_attorney_stands_only_when_the_requester_is_or_names_them():
    own = "Dana's demands close with Cordially."
    ok = rule_reach.check(_subject(doc_kinds=["demand_letter"], attorney=ATTORNEY), own, None)
    assert ok.refusal == ""
    other = "Demands close with Cordially."
    refused = rule_reach.check(
        _subject(doc_kinds=["demand_letter"], attorney=ATTORNEY), other, None
    )
    assert "does not name" in refused.refusal
    mine = rule_reach.check(
        _subject(doc_kinds=["demand_letter"], attorney=ATTORNEY), other, ATTORNEY
    )
    assert mine.refusal == ""


def test_off_the_reach_is_cut_to_the_words_and_never_widened():
    wide = _subject(doc_kinds=["mediation_brief", "memo"])
    assert rule_reach.narrowed(wide, _OWN, None) == {"doc_kinds": ["mediation_brief"]}
    unsaid = _subject(doc_kinds=["memo"], attorney=ATTORNEY)
    # dropping only the attorney would widen it to everyone's memos
    assert rule_reach.narrowed(unsaid, "Memos get a summary.", None) == {}


def test_the_readback_states_the_reach_only_when_on():
    subject = {"doc_kinds": ["mediation_brief"], "attorney": ATTORNEY}
    on = rule_reach.readback_with_reach("[rule 5e1f00aa] x", subject, True)
    assert on.endswith(
        f"(This rule covers mediation briefs, in {ATTORNEY}'s name, for any reader, "
        "and nothing else.)"
    )
    assert (
        rule_reach.readback_with_reach("[rule 5e1f00aa] x", subject, False) == "[rule 5e1f00aa] x"
    )


# ---- the reach through the plugin ----------------------------------------------------------
class _Cfg:
    admins = [ADMIN]
    connectors: dict = {}

    def sender_is_admin(self, sender):
        return sender == ADMIN

    def sender_on_roster(self, _sender):
        return True

    def authored_person_name(self, _address):
        return None


class _CfgCls:
    @classmethod
    def from_volume(cls, path=None):
        return _Cfg()


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-establishment")
    requests: list[dict] = []

    def broker(payload):
        requests.append(payload)
        return {
            "ok": True,
            "proposal_id": RULE,
            "subject": payload.get("subject"),
            "readback": f"[rule {RULE}] {payload.get('text')}",
        }

    monkeypatch.setattr(mod, "_broker_request", broker)
    monkeypatch.setattr(mod, "CustomerConfig", _CfgCls)
    monkeypatch.setattr(mod, "_possession_gate", lambda *_a, **_k: None)
    for store in (mod._ADMIN_STASH, mod._CONFIRMED_STASH, mod._READBACK_OWED):
        store.clear()
    SESSION_INBOUND_ORIGIN._origins.clear()
    SESSION_TAINT._tainted.clear()
    yield mod, requests
    SESSION_TAINT._tainted.clear()


def _args(kinds):
    return {
        "scope": "firm_adjust",
        "subject": _subject(doc_kinds=kinds),
        "text": "In mediation briefs, write persuasively.",
        "instructed_by": ADMIN,
        "source_ref": "msg-1",
    }


def test_on_the_plugin_refuses_a_wider_reach_and_states_the_allowed_one(
    plugin, tmp_path, monkeypatch
):
    mod, _ = plugin
    _seat(tmp_path, monkeypatch, on=True)
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message=_OWN)
    verdict = mod.on_pre_tool_call(
        tool_name=mod.TOOL_PROPOSE, session_id="s1", args=_args(["mediation_brief", "memo"])
    )
    assert verdict["action"] == "block" and "mediation briefs" in verdict["message"]
    assert (
        mod.on_pre_tool_call(
            tool_name=mod.TOOL_PROPOSE, session_id="s1", args=_args(["mediation_brief"])
        )
        is None
    )


def test_on_the_broker_records_the_reach_and_the_readback_states_it(plugin, tmp_path, monkeypatch):
    mod, requests = plugin
    _seat(tmp_path, monkeypatch, on=True)
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message=_OWN)
    out = json.loads(mod._propose(_args(["mediation_brief"]), session_id="s1"))
    assert requests[-1]["subject"]["doc_kinds"] == ["mediation_brief"]
    assert "covers mediation briefs, for any attorney, for any reader" in out["readback"]


def test_off_nothing_is_refused_and_the_recorded_reach_is_cut(plugin, tmp_path, monkeypatch):
    mod, requests = plugin
    _seat(tmp_path, monkeypatch, on=False)
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message=_OWN)
    wide = _args(["mediation_brief", "memo"])
    assert mod.on_pre_tool_call(tool_name=mod.TOOL_PROPOSE, session_id="s1", args=wide) is None
    out = json.loads(mod._propose(wide, session_id="s1"))
    assert requests[-1]["subject"]["doc_kinds"] == ["mediation_brief"]
    assert out["readback"] == f"[rule {RULE}] In mediation briefs, write persuasively."


def test_a_reach_never_rides_on_a_rule_for_another_class(plugin, tmp_path, monkeypatch):
    mod, requests = plugin
    _seat(tmp_path, monkeypatch, on=True)
    mod.on_pre_llm_call(session_id="s1", sender_id=ADMIN, user_message="Client letters: shorter.")
    args = _args(["letter"])
    args["subject"]["output_class"] = "outbound_client"
    mod._propose(args, session_id="s1")
    assert "doc_kinds" not in requests[-1]["subject"]


# ---- the letter ----------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"sent": True, "reason": "", "outcome_unknown": False})()


@pytest.fixture
def pages(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(rule_dispatch, "_page_unread_rule", lambda p, c: seen.append((p, c)))
    return seen


def _letter(output_class="work_product"):
    send = _Recorder()
    rule_dispatch.notify_outcome(
        kind="installed",
        proposal_id=RULE,
        text="Write persuasively, never conversationally.",
        requester=ADMIN,
        by=ADMIN,
        send=send,
        output_class=output_class,
    )
    (call,) = send.calls
    return call


def test_on_a_lane_kind_is_in_effect(tmp_path, monkeypatch, pages):
    _install(tmp_path, monkeypatch, [_adj(doc_kinds=["mediation_brief"], attorney=ATTORNEY)])
    _seat(tmp_path, monkeypatch, on=True)
    call = _letter()
    assert call["subject"] == f"Your rule is in effect [rule {RULE}]"
    assert f"in effect from the next mediation brief in {ATTORNEY}'s name" in call["text"]
    assert "not yet in effect" not in call["text"]
    assert "\u2014" not in call["text"]
    assert pages == []


def test_off_a_lane_kind_is_still_recorded(tmp_path, monkeypatch, pages):
    _install(tmp_path, monkeypatch, [_adj(doc_kinds=["mediation_brief"])])
    _seat(tmp_path, monkeypatch, on=False)
    call = _letter()
    assert call["subject"] == f"Your rule is recorded [rule {RULE}]"
    assert "not yet in effect" in call["text"]
    assert pages == [(RULE, "work_product")]


def test_on_a_kind_no_lane_reads_is_still_recorded(tmp_path, monkeypatch, pages):
    _install(tmp_path, monkeypatch, [_adj(doc_kinds=["letter"])])
    _seat(tmp_path, monkeypatch, on=True)
    assert _letter()["subject"] == f"Your rule is recorded [rule {RULE}]"


def test_on_a_record_product_is_still_recorded(tmp_path, monkeypatch, pages):
    _install(tmp_path, monkeypatch, [_adj(doc_kinds=["memo"])], output_class="record")
    _seat(tmp_path, monkeypatch, on=True)
    assert _letter("record")["subject"] == f"Your rule is recorded [rule {RULE}]"


def test_on_a_mixed_reach_is_partly_in_effect_and_pages(tmp_path, monkeypatch, pages):
    _install(tmp_path, monkeypatch, [_adj(doc_kinds=["memo", "letter"])])
    _seat(tmp_path, monkeypatch, on=True)
    call = _letter()
    assert call["subject"] == f"Your rule is partly in effect [rule {RULE}]"
    effect, recorded = call["text"].split("For letters")
    assert "in effect from the next memo" in effect
    assert "not yet in effect" in recorded
    assert pages == [(RULE, "work_product")]


def test_on_a_torn_store_is_recorded(tmp_path, monkeypatch, pages):
    _, spec_dir = _install(tmp_path, monkeypatch, [_adj(doc_kinds=["memo"])])
    _seat(tmp_path, monkeypatch, on=True)
    (spec_dir / "manifest.json").write_text("{")
    assert wording_rules.lane_truth(RULE, "work_product") is None
    assert _letter()["subject"] == f"Your rule is recorded [rule {RULE}]"
