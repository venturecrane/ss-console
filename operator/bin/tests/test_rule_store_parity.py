"""rule-store-parity: a firm rule that lives only in a drafting prompt, a stored
rule with no document kind, and an owed rule nobody recorded are each named,
and no rule text is printed. Every rule, id and name here is invented."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rule_store_parity", _BIN / "rule-store-parity.py")
assert _spec and _spec.loader
parity = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = parity
_spec.loader.exec_module(parity)

R_INDEX = "Every set of exhibits ends with an index listing each exhibit letter and its documents."
R_PHONE = "When a request asks for the whole phone bill, say subject to the objections no documents will be produced."
R_SALAD = "Always spell the client's street name in capital letters on the caption page of the response."
R_PROP = "Propounded special interrogatories never exceed thirty-five numbered items without a declaration."

PROMPT = f"""## COMPOSITION INSTRUCTION: AN INVENTED FORMAT

RULE 1. Stated 2026-11-19, 2:57 PM MST. Covers: responses to requests for production. His words:
    {R_INDEX}

RULE 2. Stated 2026-11-20, 4:03 PM MST (then 4:05 PM: "make it a rule"). Covers: responses to requests for production. His words:
    {R_PHONE}
    This should be a rule.

RULE 3. Stated 2026-11-21, 9:28 AM MST. Covers: every response set. His words:
    {R_SALAD}

RULE 4. Stated 2026-11-21, 9:40 AM MST. Covers: PROPOUNDED sets, not responses. His words:
    {R_PROP}

MECHANICS OF HIS RULES (layout only).
"""


def _seat(tmp: Path, pending: list[tuple], wording: list[dict], owed: list[tuple]) -> Path:
    db = tmp / "audit.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE pending_rules (proposal_id TEXT, kind TEXT, subject_json TEXT, text TEXT, "
        "consumed_at REAL, declined_at REAL, installed_at REAL)"
    )
    con.executemany("INSERT INTO pending_rules VALUES (?,?,?,?,?,?,?)", pending)
    con.execute("CREATE TABLE smd_owed (id TEXT, created_at TEXT, kind TEXT, detail TEXT, closed_at TEXT)")
    con.executemany("INSERT INTO smd_owed VALUES (?,?,?,?,?)", owed)
    con.commit()
    con.close()
    specs = tmp / "specs"
    (specs / "classes" / "work_product").mkdir(parents=True)
    (specs / parity.WORDING_REL).write_text(json.dumps({"schema_version": 1, "rules": wording}))
    layout = tmp / "layout.json"
    layout.write_text(json.dumps({"paths": {"audit_volume_db": {"path": str(db)}, "specs": {"path": str(specs)}}}))
    return layout


def _subject(*kinds: str) -> str:
    return json.dumps({"output_class": "work_product", "doc_kinds": list(kinds)} if kinds else {})


def _run(tmp: Path, layout: Path, capsys, monkeypatch) -> tuple[int, str]:
    monkeypatch.setenv(parity.LAYOUT_ENV, str(layout))
    assert parity.main(["--seat-snapshot"]) == 0
    snap = tmp / "snap.json"
    snap.write_text(capsys.readouterr().out)
    prompts = tmp / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "compose-discovery-response.md").write_text(PROMPT)
    (prompts / "audit-discovery-response.md").write_text(PROMPT)  # the same block, counted once
    code = parity.main(["--snapshot", str(snap), "--prompts", str(prompts), "--seat", "pilot-example"])
    return code, capsys.readouterr().out


def test_every_gap_is_named_and_no_rule_text_leaves_either_side(tmp_path, capsys, monkeypatch) -> None:
    """FALSIFIER: count a prompt rule as stored because SMD pasted it into the
    prompt, and the job lanes never apply it."""
    layout = _seat(
        tmp_path,
        pending=[
            # Installed: its wording rule carries the id prefix.
            ("01JRULEINDEXAAAA-p", "rule", _subject("discovery_response"), R_INDEX, 1.0, None, 2.0),
            # Committed with no kind: never installed, never read by a job.
            ("01JRULEPHONEBBBB-p", "rule", _subject(), R_PHONE, 1.0, None, None),
            # Declined: not in the store.
            ("01JRULESALADCCCC-p", "rule", _subject("discovery_response"), R_SALAD, None, 3.0, None),
            # Stored under the response kind, though he said propounded sets.
            ("01JRULEPROPDDDDD-p", "rule", _subject("discovery_response"), R_PROP, 1.0, None, None),
        ],
        wording=[{"rule_id": "01JRULEINDEXAAAA", "text": R_INDEX, "doc_kinds": ["discovery_response"]}],
        owed=[
            ("01JOWEDRULEEEEEE", "2026-11-20T18:00:00Z", "rule_stated", "rule stated; from a@firm.example", None),
            ("01JOWEDHELPFFFFF", "2026-11-20T18:00:00Z", "help_question", "q", None),
            ("01JOWEDDONEGGGGG", "2026-11-20T18:00:00Z", "rule_stated", "done", "2026-11-21T00:00:00Z"),
        ],
    )
    code, out = _run(tmp_path, layout, capsys, monkeypatch)
    snap_text = (tmp_path / "snap.json").read_text()
    for words in (R_INDEX, R_PHONE, R_SALAD, R_PROP):
        for piece in (words[:30], words[-25:]):
            assert piece not in out and piece not in snap_text
    assert "a@firm.example" not in snap_text
    assert code == 1
    assert "prompts: 4 distinct rules; store: 1 installed, 2 committed and not installed" in out
    assert "IN PROMPTS, NOT IN STORE: 1" in out and "RULE 3 stated 2026-11-21 kind discovery_response" in out
    assert "audit-discovery-response.md, compose-discovery-response.md" in out
    assert "IN STORE UNDER ANOTHER KIND: 1" in out and "RULE 4 stated 2026-11-21 kind discovery_set" in out
    assert "STORE ROWS WITH NO DOCUMENT KIND: 1" in out and "01JRULEPHONEBBBB-p (committed)" in out
    assert "OPEN OWED RULES: 1" in out and "01JOWEDRULEEEEEE kind rule_stated age" in out
    assert "01JOWEDHELPFFFFF" not in out and "01JOWEDDONEGGGGG" not in out
    assert "RESULT: 4 GAPS" in out


def test_a_store_that_holds_every_rule_with_its_kind_is_clean(tmp_path, capsys, monkeypatch) -> None:
    layout = _seat(
        tmp_path,
        pending=[("01JRULEPROPDDDDD-p", "rule", _subject("discovery_set"), R_PROP, 1.0, None, None)],
        wording=[
            {"rule_id": "01JRULEINDEXAAAA", "text": R_INDEX, "doc_kinds": ["discovery_response"]},
            # Recorded from his email without the closing "This should be a rule."
            {"rule_id": "01JRULEPHONEBBBB", "text": R_PHONE, "doc_kinds": ["discovery_response"]},
            {"rule_id": "01JRULESALADCCCC", "text": R_SALAD, "doc_kinds": ["discovery_response", "memo"]},
        ],
        owed=[],
    )
    code, out = _run(tmp_path, layout, capsys, monkeypatch)
    assert code == 0 and out.rstrip().endswith("RESULT: CLEAN"), out


def test_a_paragraph_only_one_of_whose_rules_was_recorded_is_reported() -> None:
    """His email often states several rules at once; recording one is not
    recording the paragraph."""
    entry = parity.parse_prompt(
        "RULE 7. Stated 2026-11-23, 1:00 PM MST. Covers: responses. His words:\n"
        f"    {R_INDEX} {R_PHONE} {R_SALAD} {R_PROP}\n",
        Path("compose-memo.md"),
    )[0]
    entry["runs"] = parity.runs(R_INDEX + " " + R_PHONE + " " + R_SALAD + " " + R_PROP)
    snap = {"installed": [{"id": "x", "doc_kinds": ["discovery_response"], "runs": sorted(parity.runs(R_INDEX))}]}
    result = parity.compare([entry], snap)
    assert len(result["missing"]) == 1 and result["missing"][0]["cover"] < 0.5


def test_a_read_that_failed_is_never_clean(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv(parity.LAYOUT_ENV, str(tmp_path / "absent.json"))
    assert parity.main(["--seat-snapshot"]) == 3
    assert "READ FAILED" in capsys.readouterr().out
    snap = tmp_path / "s.json"
    snap.write_text(parity.SNAPSHOT_TAG + json.dumps({"schema": 1, "pending": [], "installed": [], "owed": []}))
    empty = tmp_path / "p.md"
    empty.write_text("no rules here\n")
    assert parity.main(["--snapshot", str(snap), "--prompts", str(empty)]) == 3
    assert parity.main(["--snapshot", str(snap), "--prompts", str(tmp_path / "gone")]) == 3


@pytest.mark.parametrize(
    "covers,kind",
    [
        ("PROPOUNDED sets (special interrogatories), not responses", "discovery_set"),
        ("responses to form interrogatories", "discovery_response"),
        ("every set of discovery", "memo"),  # falls back to the file's kind
    ],
)
def test_the_kind_comes_from_what_he_said_it_covers(covers: str, kind: str) -> None:
    assert parity.kind_of(covers, parity._file_kind(Path("repair-memo.md"))) == kind
