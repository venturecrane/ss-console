"""The records lane's firm inputs: ``records-firm.yaml``.

Delivered from the firm's vault (``vaults/<slug>/records/``) into
``$MEDCHRON_RECORDS_INPUTS_DIR`` by ``entrypoint-records.sh``, root-owned and
read-only to the job's uid. A schema miss DEFERS a job once, then fails it
``config_missing`` (the lane), the case file lane's shape.

Closed key set, every key required; a misspelled key is an error, never a
default::

    per_job_cap_usd:     number > 0   one job's spend stops here (every paid call)
    monthly_budget_usd:  number > 0   the month's records spend stops here (the broker reads it too)
    usd_per_page:        number > 0   the pre-flight's price of one unread page

The model is never authored here: each paid request names its own, and the
spend is priced from that name (``budget.py``), so a model change cannot slip
under the cap.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_DIR = "MEDCHRON_RECORDS_INPUTS_DIR"
DEFAULT_DIR = "/var/lib/smd-config/records"
CONFIG_NAME = "records-firm.yaml"
KEYS = ("per_job_cap_usd", "monthly_budget_usd", "usd_per_page")


class RecordsConfigError(ValueError):
    """The records inputs are missing or malformed."""


def _positive(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def validate(data: Any) -> list[str]:
    """Every problem as ``key: message``; empty means valid."""
    if not isinstance(data, dict):
        return ["top level must be a map"]
    out = [f"{k}: unknown key (closed key set)" for k in data if k not in KEYS]
    for key in KEYS:
        if key not in data:
            out.append(f"{key}: required")
        elif not _positive(data[key]):
            out.append(f"{key}: expected a number > 0")
    if not out and float(data["monthly_budget_usd"]) < float(data["per_job_cap_usd"]):
        out.append("monthly_budget_usd: must be >= per_job_cap_usd")
    return out


@dataclass(frozen=True)
class RecordsFirm:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def per_job_cap_usd(self) -> float:
        return float(self.data["per_job_cap_usd"])

    @property
    def monthly_budget_usd(self) -> float:
        return float(self.data["monthly_budget_usd"])

    @property
    def usd_per_page(self) -> float:
        return float(self.data["usd_per_page"])


def resolve_dir(explicit: str | Path | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def load(explicit: str | Path | None = None) -> RecordsFirm:
    root = resolve_dir(explicit)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise RecordsConfigError(f"{path} not found (set {ENV_DIR} or deliver vaults/<slug>/records/)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RecordsConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data)
    if problems:
        raise RecordsConfigError(f"{path}: " + "; ".join(problems))
    return RecordsFirm(root=root, data=data)


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.records.firm <dir>``: the boot and provisioning validator."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m medchron.records.firm <records inputs dir>", file=sys.stderr)
        return 2
    try:
        firm = load(args[0])
    except RecordsConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{firm.root / CONFIG_NAME}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CONFIG_NAME", "DEFAULT_DIR", "ENV_DIR", "KEYS", "RecordsConfigError", "RecordsFirm", "load", "validate"]
