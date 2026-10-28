"""The cycle page allowance is checked per UNIT, and a shared file counts once.

Before 2026-10-28 the driver asked the page question once per process
(``_first_paid_checked``), and one process walks every unit of a job. So a
two-client job's second unit was never page-checked: pages read after the
first unit's check (a later unit's own reading) went unmetered against the
allowance. Every unit reports the JOB's whole read (the matter's one extracted
file), and the job is debited it once (``daemon._report``), so each unit's
check is that total against what remained at submission; a shared file never
counts twice.

The sequence is stubbed to the two paid stages that matter (vision, slug scope;
map, unit scope) so each unit's first paid stage is observable without the rest
of the DAG.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from medchron import dag, driver as driver_mod, job as job_mod
from medchron_testkit import FakeSeat, job_yaml


class _NoNetwork:
    def __getattr__(self, name):  # pragma: no cover - a stage that reached the model is the defect
        raise AssertionError(f"a test stage reached the model ({name})")


def _seed_extracted(data_root: Path, pages: int) -> None:
    (data_root / "example-matter" / "extracted.jsonl").write_text(
        json.dumps({"id": "f1", "name": "f1", "pages": pages, "chars": 0}) + "\n", encoding="utf-8"
    )


def _joint_job(tmp_path: Path, data_root: Path, *, remaining: int, name: str = "job") -> Path:
    jd = tmp_path / name
    jd.mkdir()
    body = yaml.safe_load(job_yaml(data_root, joint=True))
    body.update(allowance_remaining_pages=remaining, month_cents_used=0, allowance_month="2026-09")
    (jd / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return jd


def _two_paid_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = [dag.BY_NAME["vision"], dag.BY_NAME["map"]]
    monkeypatch.setattr(driver_mod.dag, "stages_from", lambda *_a, **_k: list(seq))


def _driver(jd: Path, firm_config_path: Path, pricing_path: Path) -> driver_mod.Driver:
    job_mod.load(jd)
    d = driver_mod.Driver(
        jd,
        firm_config=str(firm_config_path),
        pricing=str(pricing_path),
        log=lambda *_: None,
        seat_factory=FakeSeat,
        client=_NoNetwork(),
    )
    d._runner_override = {"vision": lambda _sr: 0, "map": lambda _sr: 0}
    return d


def _grow_at_map(data_root: Path, by: int):
    """A stage that reads more of the matter: the first unit's map adds pages
    to the job's file, as any later read the job pays for would."""

    def run(_sr) -> int:
        p = data_root / "example-matter" / "extracted.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "f2", "name": "f2", "pages": by, "chars": 0}) + "\n")
        return 0

    fired = {"n": 0}

    def once(sr) -> int:
        fired["n"] += 1
        return run(sr) if fired["n"] == 1 else 0

    return once


def test_the_second_unit_is_page_checked(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """15 pages remain and the file is 10 when unit one is checked; unit one's
    work brings the job's read to 20 before unit two's first paid stage.

    Falsifier: restore the once-per-process ``_first_paid_checked`` flag and
    both units deliver, 20 pages read against a 15-page remainder."""
    _two_paid_stages(monkeypatch)
    _seed_extracted(data_root, 10)
    d = _driver(_joint_job(tmp_path, data_root, remaining=15), firm_config_path, pricing_path)
    d._runner_override["map"] = _grow_at_map(data_root, 10)
    outs = d.run()
    assert [o.outcome for o in outs] == ["delivered", "held"], [o.sentence() for o in outs]
    second = outs[1]
    assert second.stage == "map"
    assert second.reason.startswith("chronology_package_page_allowance_per_month: ")
    assert "20 pages and 15 pages remain" in second.reason
    assert second.allowance == {"pages": 20, "remaining": 15, "over": 5, "projected_cents": 0}


def test_a_shared_file_never_holds_the_second_unit(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both clients' chronologies come from one 10-page file and exactly 10
    remain: the job reads 10 pages, not 20, so neither unit holds."""
    _two_paid_stages(monkeypatch)
    _seed_extracted(data_root, 10)
    outs = _driver(_joint_job(tmp_path, data_root, remaining=10), firm_config_path, pricing_path).run()
    assert [o.outcome for o in outs] == ["delivered", "delivered"], [o.sentence() for o in outs]
    assert [o.pages for o in outs] == [10, 10]  # each reports the job's one file


def test_a_file_larger_than_the_remainder_holds_the_first_unit(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_paid_stages(monkeypatch)
    _seed_extracted(data_root, 11)
    outs = _driver(_joint_job(tmp_path, data_root, remaining=10), firm_config_path, pricing_path).run()
    assert outs[0].outcome == "held" and outs[0].stage == "vision"
    assert outs[0].allowance == {"pages": 11, "remaining": 10, "over": 1, "projected_cents": 0}


def test_an_approved_overage_lets_the_job_through_and_no_further(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SMD's approval (``medchron.allowance approve``) adds exactly its pages to
    the page check; one page short of the overrun still holds."""
    from medchron import allowance

    _two_paid_stages(monkeypatch)
    _seed_extracted(data_root, 15)
    short = _joint_job(tmp_path, data_root, remaining=10, name="short")
    allowance.write_approval(short, pages=4, by="smd", attempt=1)
    assert _driver(short, firm_config_path, pricing_path).run()[0].outcome == "held"
    enough = _joint_job(tmp_path, data_root, remaining=10, name="enough")
    allowance.write_approval(enough, pages=5, by="smd", attempt=1)
    assert [o.outcome for o in _driver(enough, firm_config_path, pricing_path).run()] == ["delivered", "delivered"]


def test_a_single_unit_job_is_checked_once_as_before(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unit, two paid stages: the page question is asked at vision only;
    map is a dollar check alone."""
    _two_paid_stages(monkeypatch)
    _seed_extracted(data_root, 10)
    jd = tmp_path / "single"
    jd.mkdir()
    body = yaml.safe_load(job_yaml(data_root))
    body.update(allowance_remaining_pages=10, month_cents_used=0, allowance_month="2026-09")
    (jd / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    outs = _driver(jd, firm_config_path, pricing_path).run()
    assert [o.outcome for o in outs] == ["delivered"], [o.sentence() for o in outs]
