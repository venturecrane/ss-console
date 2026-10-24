"""The sent-out-for-service backfill (2026-10-24): one field merged onto the
lane's stored state, matched through ``identity``, every miss counted.

Every name and id here is invented."""

from __future__ import annotations

import json
from pathlib import Path

from medchron.litigation import backfill, baseline, book, vocab

SRC = {"file_id": "req-1", "name": "Service request.msg", "doc_date": "2026-03-20"}


def _defendant(name: str) -> dict:
    return {
        "name": name,
        "status": vocab.SERVED_NO_ANSWER,
        "served": {"date": "2026-04-10", "method": None, "source": {"file_id": "pos-1"}},
        "answered": {"date": None, "source": None},
        "flags": [],
    }


def _state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    (state / "matters").mkdir(parents=True)
    m1 = {"case_name": "Gamma v. Delta", "defendants": [_defendant("Delta Example"), _defendant("Zeta Corp")]}
    # Two defendants one row could mean: a one-letter typo of either given name.
    m2 = {"defendants": [_defendant("Marisol Vance"), _defendant("Marisel Vance"), _defendant("Omega LLC")]}
    (state / "matters" / "m1.json").write_text(json.dumps(m1))
    (state / "matters" / "m2.json").write_text(json.dumps(m2))
    return state


def _load(state: Path, mid: str) -> dict:
    return json.loads((state / "matters" / f"{mid}.json").read_text())


ROWS = [
    {"matter_id": "m1", "defendant": "DELTA EXAMPLE", "date": "2026-03-20", "source": SRC},
    {"matter_id": "m1", "defendant": "Zeta Corp.", "date": None, "source": None},  # read, none found
    {"matter_id": "m1", "defendant": "Nobody Here", "date": "2026-03-20", "source": SRC},  # no such defendant
    {"matter_id": "m2", "defendant": "Marisal Vance", "date": "2026-03-20", "source": SRC},  # two could match
    {"matter_id": "m2", "defendant": "Omega LLC", "date": "2026-03-20", "source": None},  # uncited date
    {"matter_id": "m9", "defendant": "Delta Example", "date": "2026-03-20", "source": SRC},  # no stored matter
]


def test_the_backfill_adds_only_the_sent_date_and_counts_every_miss(tmp_path):
    state = _state(tmp_path)
    before = _load(state, "m1")
    counts = baseline.merge_sent_for_service(state, ROWS)
    assert counts == {
        "merged": 2,
        "skipped_unmatched": 2,
        "skipped_ambiguous": 1,
        "skipped_uncited": 1,
        "already_read": 0,
    }
    after = _load(state, "m1")
    delta, zeta = after["defendants"]
    assert delta["sent_for_service"] == {"date": "2026-03-20", "source": SRC}
    assert zeta["sent_for_service"] == {"date": None, "source": None}
    # nothing else on the matter moved
    for d0, d1 in zip(before["defendants"], after["defendants"]):
        assert {k: v for k, v in d1.items() if k != "sent_for_service"} == d0
    assert {k: v for k, v in after.items() if k not in ("defendants", "provenance")} == {
        k: v for k, v in before.items() if k != "defendants"
    }
    assert all("sent_for_service" not in d for d in _load(state, "m2")["defendants"])  # ambiguous, uncited
    # rendered: a date, "Not in file" where the hand run found none, "Not read yet" where nobody read
    cells = [book.sent_cells(d)[0] for d in after["defendants"] + _load(state, "m2")["defendants"]]
    assert cells == ["2026-03-20", "Not in file", "Not read yet", "Not read yet", "Not read yet"]


def test_a_rerun_leaves_values_already_read_alone_and_a_second_row_for_one_defendant_is_ambiguous(tmp_path):
    state = _state(tmp_path)
    baseline.merge_sent_for_service(state, ROWS)
    again = baseline.merge_sent_for_service(state, ROWS[:2])
    assert again["merged"] == 0 and again["already_read"] == 2
    state2 = _state(tmp_path / "two")
    twice = [ROWS[0], {**ROWS[0], "date": "2026-03-01"}]
    assert baseline.merge_sent_for_service(state2, twice)["skipped_ambiguous"] == 1
    assert _load(state2, "m1")["defendants"][0]["sent_for_service"]["date"] == "2026-03-20"


def test_a_dry_run_writes_nothing(tmp_path):
    state = _state(tmp_path)
    raw = (state / "matters" / "m1.json").read_text()
    assert baseline.merge_sent_for_service(state, ROWS, dry_run=True)["merged"] == 2
    assert (state / "matters" / "m1.json").read_text() == raw


def test_the_cli_reads_a_rows_file_and_prints_the_counts(tmp_path, capsys):
    state = _state(tmp_path)
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps(ROWS))
    assert backfill.main(["--state", str(state), "--rows", str(rows), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["merged"] == 2 and out["dry_run"] is True and out["rows"] == len(ROWS)
    assert "sent_for_service" not in _load(state, "m1")["defendants"][0]
    assert backfill.main(["--state", str(state), "--rows", str(rows)]) == 0
    assert _load(state, "m1")["defendants"][0]["sent_for_service"]["date"] == "2026-03-20"
    assert backfill.main(["--state", str(tmp_path / "nowhere"), "--rows", str(rows)]) == 2
