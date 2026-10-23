"""The audit's concurrency is a firm lever, bounded, defaulting to 4.

WHY (2026-10-22). The Curtis chronology reached its audit with 4,902 claims
and a client waiting; the audit waits on the model, so its time is close to
claims / workers, and the 4 was a constant no one could change for one firm.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from medchron import config as config_mod


def _firm(tmp_path: Path, firm_config_path: Path, workers: object) -> Path:
    data = yaml.safe_load(firm_config_path.read_text())
    if workers is not None:
        data.setdefault("levers", {})["audit_workers"] = workers
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


@pytest.mark.parametrize("workers", [None, 1, 8, 12])
def test_an_in_range_value_or_none_loads(tmp_path: Path, firm_config_path: Path, workers: object) -> None:
    cfg = config_mod.load(str(_firm(tmp_path, firm_config_path, workers)))
    assert cfg.get("levers", "audit_workers") == workers


@pytest.mark.parametrize("workers", [0, 13])
def test_an_out_of_range_value_refuses_to_load(tmp_path: Path, firm_config_path: Path, workers: int) -> None:
    with pytest.raises(Exception, match="audit_workers"):
        config_mod.load(str(_firm(tmp_path, firm_config_path, workers)))
