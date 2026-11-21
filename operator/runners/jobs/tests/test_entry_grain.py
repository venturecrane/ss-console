"""The writer's entry grain is a firm setting (`chronology.entry_detail`).

`full` is the form every chronology before 2026-11-21 was written in, one entry
per signing clinician per date; a firm without the key must get that prompt
byte for byte, so nothing changes for a firm that has not chosen. `standard` is
one entry per facility per date, the form the firm asked for. These tests go
through the real config loader and the real prompt loader.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from jobs.medchron import config as config_mod
from jobs.medchron import prompts

TOKENS = ("{{NOTE_AUTHOR}}", "{{ENTRY_GRAIN}}", "{{PROVIDER_RULE}}")
# The two sentences the setting exists to change, as they stood before it.
FULL_GRAIN = "One entry per provider per date of service."
FULL_PROVIDER = "The provider is the clinician who signed or authored the record."


def _cfg(firm_config_path, tmp_path, detail):
    data = yaml.safe_load(firm_config_path.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    if detail is None:
        data["chronology"].pop("entry_detail", None)
    else:
        data["chronology"]["entry_detail"] = detail
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config_mod.load(str(p))


@pytest.mark.parametrize("name", ["map-system"])
def test_every_token_is_filled_in_both_forms(name, firm_config_path, tmp_path):
    for detail in ("full", "standard"):
        text = prompts.load(name, _cfg(firm_config_path, tmp_path, detail))
        assert [t for t in TOKENS if t in text] == []
        assert "{{WORDING:" not in text


def test_an_absent_key_writes_the_full_form(firm_config_path, tmp_path):
    absent = prompts.load("map-system", _cfg(firm_config_path, tmp_path, None))
    full = prompts.load("map-system", _cfg(firm_config_path, tmp_path, "full"))
    assert absent == full
    assert FULL_GRAIN in absent and FULL_PROVIDER in absent
    assert "names its author as the provider" in absent


def test_standard_writes_one_entry_per_facility_and_keeps_every_date(firm_config_path, tmp_path):
    text = prompts.load("map-system", _cfg(firm_config_path, tmp_path, "standard"))
    assert FULL_GRAIN not in text and FULL_PROVIDER not in text
    assert "One entry per facility or practice per date of service" in text
    assert "never group visits on different dates" in text
    # The depth rule is the same text in both forms: the setting changes how
    # many entries there are, never how much an entry states.
    assert prompts.depth_rule() in text


def test_an_update_always_writes_the_full_form(firm_config_path, tmp_path):
    """Every delivered chronology is full form, and the update's repeat and
    finish steps match by provider; a standard-form update would repeat
    delivered visits. The writer and its repair both go through
    ``compose.writer_system``."""
    from types import SimpleNamespace

    from jobs.stages.compose import writer_system

    cfg = _cfg(firm_config_path, tmp_path, "standard")
    sr = SimpleNamespace(slug_dir=tmp_path / "slug", unit=SimpleNamespace(unit="u1"), cfg=cfg)
    run = sr.slug_dir / "runs" / "u1"
    run.mkdir(parents=True)
    assert "One entry per facility or practice per date of service" in writer_system(sr)
    (run / "update").mkdir()
    (run / "update" / "update_plan.json").write_text("{}", encoding="utf-8")
    text = writer_system(sr)
    assert FULL_GRAIN in text and FULL_PROVIDER in text
    assert "One entry per facility or practice" not in text


def test_a_value_outside_the_two_is_refused(firm_config_path, tmp_path):
    data = yaml.safe_load(firm_config_path.read_text(encoding="utf-8"))
    data["chronology"]["entry_detail"] = "short"
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception) as err:
        config_mod.load(str(p))
    assert "entry_detail" in str(err.value)
