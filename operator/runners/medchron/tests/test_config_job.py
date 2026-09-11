from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from medchron import config as config_mod, job as job_mod
from medchron_testkit import FIRM_CONFIG, job_yaml


def test_valid_config_loads(firm_config_path: Path) -> None:
    cfg = config_mod.load(str(firm_config_path))
    assert cfg.slug == "example-firm"
    assert cfg.per_job_cap_usd == 150.0
    assert cfg.compiled("providers", "aliases")[0][1] == "Example Clinic"


def test_unknown_key_is_refused(tmp_path: Path) -> None:
    data = copy.deepcopy(FIRM_CONFIG)
    data["budget"]["per_matter_cap"] = 400  # not a key; a typo must not silently vanish
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="budget.per_matter_cap: unknown key"):
        config_mod.load(str(p))


def test_unknown_section_is_refused(tmp_path: Path) -> None:
    data = copy.deepcopy(FIRM_CONFIG)
    data["extras"] = {"x": 1}
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="extras: unknown section"):
        config_mod.load(str(p))


def test_audit_is_never_batchable(tmp_path: Path) -> None:
    data = copy.deepcopy(FIRM_CONFIG)
    data["levers"]["batch_stages"] = ["vision", "audit"]
    p = tmp_path / "f.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="audit"):
        config_mod.load(str(p))


def test_missing_file_is_a_refusal_not_a_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config_mod.ENV_PATH, raising=False)
    with pytest.raises(config_mod.ConfigError, match="no built-in firm"):
        config_mod.load(str(tmp_path / "absent.yaml"))


def test_bad_regex_is_named(tmp_path: Path) -> None:
    data = copy.deepcopy(FIRM_CONFIG)
    data["coverage"]["exclusions"].append({"match": "(", "reason": "x"})
    problems = config_mod.validate(data)
    assert any("coverage.exclusions[2].match: invalid regex" in p for p in problems)


def test_job_loads(job_dir: Path) -> None:
    j = job_mod.load(job_dir)
    assert j.slug == "example-matter"
    assert j.incident_date == "2026-01-15"
    assert not j.joint
    assert j.cap_usd is None
    # The laptop identity: controls and ICD tables beside the matters.
    assert j.install_root == j.data_root


def test_job_install_root_is_honoured_when_the_daemon_names_one(tmp_path: Path, data_root: Path) -> None:
    body = yaml.safe_load(job_yaml(data_root, install_root=tmp_path / "run"))
    j = job_mod.parse(body, path=tmp_path / "job.yaml")
    assert j.install_root == tmp_path / "run" and j.data_root == data_root
    body["install_root"] = 7
    with pytest.raises(job_mod.JobError, match="install_root"):
        job_mod.parse(body, path=tmp_path / "job.yaml")


def test_job_joint_requires_folder_prefix(tmp_path: Path, data_root: Path) -> None:
    body = yaml.safe_load(job_yaml(data_root, joint=True))
    del body["units"][1]["folder_prefix"]
    with pytest.raises(job_mod.JobError, match="folder_prefix"):
        job_mod.parse(body, path=tmp_path / "job.yaml")


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda b: b["incident"].__setitem__("date", "01/15/2026"), "YYYY-MM-DD"),
        (lambda b: b["incident"].__setitem__("source", "guessed"), "incident.source"),
        (lambda b: b["units"][0].__setitem__("dob", "1970-01-01"), "MM/DD/YYYY"),
        (lambda b: b.__setitem__("cap_usd", 0), "cap_usd"),
        (lambda b: b.pop("data_root"), "data_root"),
        # The month state the daemon stamps. A malformed count is a refusal,
        # never a coerced zero: "nothing remains" and "unknown" are opposite
        # answers to the allowance question.
        (lambda b: b.__setitem__("allowance_remaining_pages", -1), "allowance_remaining_pages"),
        (lambda b: b.__setitem__("month_cents_used", "1200"), "month_cents_used"),
        (lambda b: b.__setitem__("allowance_pages", True), "allowance_pages"),
        (lambda b: b.__setitem__("allowance_month", "2026-9"), "allowance_month"),
    ],
)
def test_job_refuses_the_shapes_it_must_not_guess(tmp_path: Path, data_root: Path, mutate, msg: str) -> None:
    body = yaml.safe_load(job_yaml(data_root))
    mutate(body)
    with pytest.raises(job_mod.JobError, match=msg):
        job_mod.parse(body, path=tmp_path / "job.yaml")


# ---- the routine-11 cost controls (2026-09-09; per-matter line removed 09-10) -
COST_KEYS = ("monthly_budget_usd", "usd_per_scanned_page", "usd_per_audit_claim")


@pytest.mark.parametrize("key", COST_KEYS)
def test_each_cost_control_is_required_not_defaulted(tmp_path: Path, key: str) -> None:
    """A firm.yaml that predates these keys must REFUSE to run, not run
    unmetered. Defaulting them would let a stale config produce a package
    nobody was metering, which is the state routine 11 was in."""
    data = copy.deepcopy(FIRM_CONFIG)
    del data["budget"][key]
    p = tmp_path / f"missing-{key}.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match=f"budget.{key}: required"):
        config_mod.load(str(p))


@pytest.mark.parametrize("key", COST_KEYS)
def test_a_zero_cost_control_is_refused(tmp_path: Path, key: str) -> None:
    """Zero is not "no limit": a zero budget or threshold refuses every job,
    and a zero rate projects every page and every claim at no cost, so the
    projection can never trip anything."""
    data = copy.deepcopy(FIRM_CONFIG)
    data["budget"][key] = 0
    p = tmp_path / f"zero-{key}.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match=f"budget.{key}: must be > 0"):
        config_mod.load(str(p))


def test_the_cost_controls_are_read_back_as_typed_values(firm_config_path: Path) -> None:
    cfg = config_mod.load(str(firm_config_path))
    assert cfg.monthly_budget_usd == 500.0
    assert cfg.usd_per_scanned_page == 0.03 and cfg.usd_per_audit_claim == 0.06


def test_the_month_state_round_trips_off_the_envelope(tmp_path: Path, data_root: Path) -> None:
    body = yaml.safe_load(job_yaml(data_root))
    body.update(allowance_pages=15000, allowance_remaining_pages=1200, month_pages_used=13800,
                month_cents_used=4310, allowance_month="2026-09", allowance_remaining_documents=1200)
    job = job_mod.parse(body, path=tmp_path / "job.yaml")
    assert (job.allowance_pages, job.allowance_remaining_pages) == (15000, 1200)
    assert (job.month_pages_used, job.month_cents_used) == (13800, 4310)
    assert job.allowance_month == "2026-09" and job.allowance_remaining_documents == 1200
    # A laptop envelope carries none of them and that is not an error.
    bare = job_mod.parse(yaml.safe_load(job_yaml(data_root)), path=tmp_path / "job.yaml")
    assert bare.allowance_remaining_pages is None and bare.month_cents_used is None
