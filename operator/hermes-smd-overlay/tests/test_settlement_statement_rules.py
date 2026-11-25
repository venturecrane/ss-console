"""A firm's wording rule for its settlement statements (2026-11-25).

Settlement statements are made in a chat turn by the settlement-statement-feeder
skill (``operator/contracts/output-classes.yaml``: artifact
``settlement_statement``, class ``work_product``), not by a queued job. A rule
recorded for ``settlement_statement`` installs through the real spec applier,
reaches the class spec the chat turn reads before it delivers
(``classes/work_product/<property>.md``), and reaches no discovery response,
which the drafting lane makes and selects rules for by kind from
``wording.json``. Every name and sentence here is invented."""

from __future__ import annotations

import io
import json

from shared import rule_reach, wording_rules, wording_rules_store
from spec_applier.applier import SCHEMA_VERSION, apply

RULE = "5e7a0c11"
TEXT = "On every settlement statement, put the client's acknowledgment block on its own last page."


def _install(tmp_path, monkeypatch, prop: str = "format"):
    adj = {
        "id": RULE,
        "text": TEXT,
        "instructed_by": "attorney@firm.example",
        "at": "2026-11-25T17:00:00Z",
        "doc_kinds": ["settlement_statement"],
    }
    doc = {
        "schema_version": SCHEMA_VERSION,
        "classes": {"work_product": {prop: {"adjustments": [adj]}}},
    }

    class S3:
        def get_object(self, **_k):
            return {"Body": io.BytesIO(json.dumps(doc).encode())}

    spec_dir = tmp_path / "specs"
    result = apply(s3_client=S3(), bucket="b", slug="s", spec_dir=spec_dir)
    monkeypatch.setenv("SMD_SPEC_DIR", str(spec_dir))
    return result, spec_dir


def test_settlement_statement_is_a_kind_a_rule_may_name():
    fields, errors = wording_rules.reach_fields({"doc_kinds": ["settlement_statement"]}, "subject")
    assert errors == [] and fields["doc_kinds"] == ["settlement_statement"]
    assert wording_rules_store.KIND_WORDS["settlement_statement"] == (
        "settlement statement",
        "settlement statements",
    )
    assert rule_reach.kinds_named("Please do this on our settlement statements.") == (
        "settlement_statement",
    )


def test_a_settlement_statement_rule_reaches_the_statement_and_no_discovery_response(
    tmp_path, monkeypatch
):
    result, spec_dir = _install(tmp_path, monkeypatch)
    assert result.applied
    # The chat turn that renders the statement reads its class's spec: the rule is in it.
    spec = (spec_dir / "classes/work_product/format.md").read_text("utf-8")
    assert f"[rule {RULE}]" in spec and TEXT in spec
    store = wording_rules_store.read(spec_dir)
    assert store.status == wording_rules_store.OK
    assert [
        r["rule_id"] for r in wording_rules_store.applicable(store.rules, "settlement_statement")
    ] == [RULE]
    # The drafting lane selects by kind: a discovery response never gets it.
    assert wording_rules_store.applicable(store.rules, "discovery_response") == []
    # No queued job makes a settlement statement, so no lane is said to read it.
    assert wording_rules.lane_reads("work_product", ["settlement_statement"], on=True) == ()
    assert "settlement_statement" in wording_rules_store.CHAT_KINDS
    assert "settlement_statement" not in wording_rules_store.LANE_KINDS
