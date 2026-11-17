"""The ops workspace replays a recorded update review under today's $0 rules
on a copy of a held job, read only (2026-11-17, ``update/review_replay``).

WHY. The class 2 rule and the bounded re-check were built from one held
update. Before a seat runs them, the replay reads that job's recorded
findings and says, per finding, whether the rule closes it (and by which
entry), whether the re-check would send it, and what the re-check would now
show and cost beside what it did. It calls no model and writes nothing. Every
name and value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron import rehearsal
from jobs.medchron.update import review_replay as RPL
from test_update_decide_rehearse import _Forbidden, _held_job, _snapshot, scene  # noqa: F401 - scene is a pytest fixture

REVERTED = "reverted: the audit did not find the edited sentence on its page"


def _record_two_rounds(sd: Path) -> None:
    """The held record as a two-round review leaves it: round 1's must-fix
    frozen with round 1's outcomes, round 2 having overwritten the rows."""
    p = sd / "runs" / "alpha" / "update" / "review_repair.json"
    rec = json.loads(p.read_text())
    omitted = dict(rec["open"][0])
    value = {**omitted, "klass": "1 wrong value", "anchor": "N2.c1", "page": 2, "problem": "the size is misread"}
    r1 = [{**omitted, "outcome": "declined by the finisher: it is on the page"}, {**value, "outcome": REVERTED}]
    rec["frozen"] = [
        {"key": "x", "dates": [], "finding": {**omitted, "outcome": "confirmed on re-check; declined"}},
        {"key": "y", "dates": [], "finding": {**value, "outcome": "confirmed on re-check; declined"}},
    ]
    rec["rounds"] = [{"round": 1, "findings": r1}, {"round": 2, "calls": 3, "usage": []}]
    p.write_text(json.dumps(rec))


def test_the_replay_reports_each_finding_and_the_recheck_and_writes_nothing(
    scene, data_root, firm_config_path, pricing_path, monkeypatch, capsys
) -> None:
    job_dir, sd = _held_job(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    copy = rehearsal.prepare(job_dir)
    _record_two_rounds(copy / "data" / "example-matter")
    before = _snapshot(copy)
    assert RPL.main([str(copy), "--firm-config", str(firm_config_path), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert _snapshot(copy) == before  # read only
    omitted, value = r["frozen"]
    assert value["replay"] == "sent to the re-check" and value["page"] == "Exhibit 2 p. 2"
    # The page carries a finding-clause word ("in the morning") the entry left out:
    # strict by design, the replay names what keeps it open.
    assert omitted["replay"] == (
        "left open (holds): not re-checked: an omission is settled by rule when its content is composed "
        "elsewhere, else it holds; N1 lacks term morning"
    )
    (held,) = r["held"]
    assert held == {"anchor": "N1", "klass": "2 omitted content", "page": "Exhibit 2 p. 1"} | {
        "replay": "left open: N1 lacks term morning"
    }
    now, then = r["recheck_now"], r["recheck_recorded"]
    assert now == {"findings": 1, "parts": 1, "pages": 2, "projected_usd": now["projected_usd"], "cap_usd": 5.0}
    assert then["pages_shown"] == 2 and then["calls"] == 3
    assert RPL.main([str(copy), "--firm-config", str(firm_config_path)]) == 0
    text = capsys.readouterr().out
    assert "RE-CHECK NOW: 1 finding(s), 1 part(s), 2 page(s)" in text
    assert "HELD FINDINGS THE CLASS 2 RULE CLOSES: 0 of 1" in text
