"""The rate card a stop prices with is the one the seat has (2026-11-24).

On ashton-price, ``python -m jobs.stop`` could not price the job it stopped:
"[Errno 2] No such file or directory: '/opt/smd/cost_telemetry/anthropic_pricing.json'".
The jobs bundle ships the card at ``/opt/jobs/anthropic_pricing.json``
(``templates/jobs-runtime.sh``, ``seat_layout.JOBS_PRICING``); only the daemon,
launched with ``JOBS_PRICING_JSON``, found it. A root command run from a shell
has no such env and fell back to a default no seat has ever had. And the stop
then dropped the figure silently: the ledger row carried no cents and no word
of why.

Now the default is the seat path, a figure that still cannot be read is said by
name in the stop's ledger reason, its state file and its printout, and the seat
probe checks the card is present at boot. Each test fails if the line it
defends is removed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import budget, seat_layout, smd_stop, spend, stop
from jobs_testkit import PRICING

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "seat-layout.yaml"
ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "read"}


def test_the_default_rate_card_is_the_one_the_jobs_bundle_ships():
    assert budget.PRICING_DEFAULT == seat_layout.JOBS_PRICING


def test_the_seat_probe_checks_the_rate_card_is_on_every_lane_seat():
    entry = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["paths"]["jobs_pricing"]
    assert entry["expect"] == "present" and entry["kind"] == "file" and entry["when"] == "lanes"
    assert entry["env"] == [budget.PRICING_ENV]  # the daemon's env must name the same file


def _job(tmp_path: Path) -> Path:
    jd = tmp_path / "jobs" / "01JOB"
    (jd / "data").mkdir(parents=True)
    (jd / "data" / spend.LEDGER_NAME).write_text(json.dumps(ROW) + "\n", encoding="utf-8")
    return jd


def test_a_card_that_will_not_load_is_named_not_dropped(tmp_path, monkeypatch):
    missing = tmp_path / "absent.json"
    monkeypatch.setenv(budget.PRICING_ENV, str(missing))
    cents, why = spend.priced(_job(tmp_path), "drafting")
    assert cents is None
    assert why.startswith(f"{spend.UNKNOWN_PRICE_TABLE}: {missing}: ") and "No such file" in why
    assert spend.priced_cents(_job(tmp_path / "b"), "drafting") is None


def test_a_job_that_made_no_paid_call_spent_nothing_whatever_the_card(tmp_path, monkeypatch):
    monkeypatch.setenv(budget.PRICING_ENV, str(tmp_path / "absent.json"))
    jd = tmp_path / "jobs" / "01JOB"
    jd.mkdir(parents=True)
    assert spend.priced(jd, "drafting") == (0, None)


def test_a_card_that_loads_prices_the_jobs_ledger(tmp_path, monkeypatch):
    card = tmp_path / "pricing.json"
    card.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(card))
    assert spend.priced(_job(tmp_path), "drafting") == (500, None)


class _Lane:
    LANE = "drafting"

    def __init__(self, jd: Path) -> None:
        self.jd = jd
        self.state: dict[str, Any] = {}
        self.records: list[tuple[str, str, dict[str, Any]]] = []
        self.broker = self
        self.clock = lambda: 100.0

    def job_dir(self, _job_id: str) -> Path:
        return self.jd

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.records.append((job_id, state, fields))
        return {}

    def _write_state(self, _job_id: str, **kw: Any) -> None:
        self.state.update(kw)


def test_a_stop_whose_spend_cannot_be_priced_says_so_in_the_ledger_and_the_state(tmp_path, monkeypatch):
    monkeypatch.setenv(budget.PRICING_ENV, str(tmp_path / "absent.json"))
    jd = _job(tmp_path)
    smd_stop.request(jd, by="scott", why="over the cap SMD set", at=1.0)
    lane = _Lane(jd)
    monkeypatch.setattr(smd_stop.run_record, "fields_for", lambda *_a, **_k: {})
    assert smd_stop.finish(lane, "01JOB", -15, "") is True
    ((_jid, state, fields),) = lane.records
    assert state == "failed" and "cents" not in fields
    assert fields["reason"].startswith("stopped_by_smd: by scott: over the cap SMD set; spend unknown: ")
    assert spend.UNKNOWN_PRICE_TABLE in fields["reason"]
    assert lane.state["stopped_cents"] is None
    assert lane.state["stopped_spend_unknown"].startswith(spend.UNKNOWN_PRICE_TABLE)


def test_a_priced_stop_reads_as_it_always_did(tmp_path, monkeypatch):
    card = tmp_path / "pricing.json"
    card.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(card))
    jd = _job(tmp_path)
    smd_stop.request(jd, by="scott", why="duplicate job", at=1.0)
    lane = _Lane(jd)
    monkeypatch.setattr(smd_stop.run_record, "fields_for", lambda *_a, **_k: {})
    assert smd_stop.finish(lane, "01JOB", -15, "") is True
    ((_jid, _state, fields),) = lane.records
    assert fields == {"reason": "stopped_by_smd: by scott: duplicate job", "cents": 500}
    assert "stopped_spend_unknown" not in lane.state


def test_the_cli_prints_why_the_spend_is_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(budget.PRICING_ENV, str(tmp_path / "absent.json"))
    jobs_name, _pid = stop.LANES["drafting"]
    jd = tmp_path / "run" / jobs_name / "01JOB"
    (jd / "data").mkdir(parents=True)
    (jd / "data" / spend.LEDGER_NAME).write_text(json.dumps(ROW) + "\n", encoding="utf-8")
    (jd / "daemon.json").write_text(json.dumps({"state": "running", "claimed": True, "attempts": 1}))
    argv = ["drafting", "01JOB", "--by", "scott", "--why", "cap", "--run-dir", str(tmp_path / "run"), "--wait", "1"]
    assert stop.main(argv, euid=lambda: 0, clock=lambda: 1.0, sleep=lambda _s: None) == 3
    out = capsys.readouterr().out
    assert f"spend=unknown ({spend.UNKNOWN_PRICE_TABLE}: {tmp_path / 'absent.json'}: " in out


@pytest.mark.parametrize("unknown, said", [(None, "unknown (not read)"), ("why", "unknown (why)")])
def test_the_cli_spend_words(unknown, said):
    assert stop._spend(None, unknown) == said
    assert stop._spend(5664) == "$56.64"
