"""The litigation firm inputs: ``litigation-firm.yaml`` (one file, no pins).

Delivered from the firm's vault (``vaults/<slug>/litigation/``) into
``$MEDCHRON_LITIGATION_INPUTS`` by the entrypoint, root-owned and read-only to
the job's uid. A schema miss DEFERS a litigation job once, then fails it
``config_missing`` (the lane); every other lane keeps running.

Everything firm-specific lives here and nowhere in product code: which file
names read as court papers, the court's form numbers and what they mean, the
process servers the firm uses, the words a settlement is written in, the caps
and the models. Closed key set: a misspelled key is an error, never a default.

The schema::

    firm:                  {slug, display_name}
    models:                {read, verify, audit, transcription?}
    per_job_cap_usd:       number > 0
    monthly_budget_usd:    number > 0      (the broker reads this one too)
    court_paper_patterns:  [regex]         file names that read as court papers
    process_server_names:  [str]           a file name carrying one is a service record
    settlement_terms:      [regex]         the settlement scan's words
    form_hints:            {form number: what it is and where its facts sit}
    email_recent_n:        int >= 1        the newest emails every read opens (15)
    case_statuses:         [str]           every case AND defendant status, exactly
                                           the product's vocabulary (vocab.py)
    # optional
    attorneys:             {display name: staff uuid}   the broker's scope resolver
    discovery_patterns:    [regex]         file names that read as discovery papers
    email_months:          int >= 1        older emails are not candidates (12)
    min_court_hits:        int >= 1        court-named files a matter needs (1)
    matter_statuses:       [str]           Smokeball statuses listed (["Open"])
    tool_iterations:       int >= 4        the extract read's call cap (10); verify and audit get half
    fetch_caps:            {court, server, discovery, email, total: int}   per matter
    concurrency:           int >= 1        parallel model calls in extract (5)
    context_chars:         int >= 5000     document text a read is handed up front (60000)
    doc_context_chars:     int >= 1        of which one document at most (6000)
    chunk_chars:           int >= 5000     one fetch_doc page (20000)
    timezone:              IANA name       "today" for the date gates
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import vocab

ENV_DIR = "MEDCHRON_LITIGATION_INPUTS"
DEFAULT_DIR = "/var/lib/smd-config/litigation"
CONFIG_NAME = "litigation-firm.yaml"
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

REQUIRED: dict[str, str] = {
    "firm": "firm",
    "models": "models",
    "per_job_cap_usd": "pos",
    "monthly_budget_usd": "pos",
    "court_paper_patterns": "regexes",
    "process_server_names": "strs",
    "settlement_terms": "regexes",
    "form_hints": "strmap",
    "email_recent_n": "int1",
    "case_statuses": "strs",
}
OPTIONAL: dict[str, tuple[str, Any]] = {
    "attorneys": ("attorneys", {}),
    "discovery_patterns": ("regexes", []),
    "email_months": ("int1", 12),
    "min_court_hits": ("int1", 1),
    "matter_statuses": ("strs", ["Open"]),
    "tool_iterations": ("int4", 10),
    "chunk_chars": ("int5000", 20000),
    "timezone": ("str", "America/Los_Angeles"),
    "fetch_caps": ("caps", {}),
    "concurrency": ("int1", 5),
    "context_chars": ("int5000", 60000),
    "doc_context_chars": ("int1", 6000),
}
MODEL_KEYS = ("read", "verify", "audit")
#: transcription: the vision OCR model (defaults to models.read).
OPTIONAL_MODELS = ("transcription",)
FETCH_CAP_KEYS = ("court", "server", "discovery", "email", "total")


class LitigationConfigError(ValueError):
    """The litigation inputs are missing or malformed."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_struct(key: str, v: Any, kind: str) -> list[str]:
    if kind == "firm":
        if (
            not isinstance(v, dict)
            or set(v) != {"slug", "display_name"}
            or not all(isinstance(x, str) and x.strip() for x in v.values())
        ):
            return [f"{key}: expected exactly {{slug, display_name}} as strings"]
        return []
    if kind == "models":
        if (
            not isinstance(v, dict)
            or not set(MODEL_KEYS) <= set(v) <= set(MODEL_KEYS) | set(OPTIONAL_MODELS)
            or not all(isinstance(x, str) and x for x in v.values())
        ):
            return [f"{key}: expected {list(MODEL_KEYS)} (and optionally {list(OPTIONAL_MODELS)}) as model ids"]
        return []
    if kind == "caps":
        ok = isinstance(v, dict) and all(k in FETCH_CAP_KEYS and _is_int(x) and x >= 1 for k, x in v.items())
        return [] if ok else [f"{key}: expected a map of {list(FETCH_CAP_KEYS)} to integers >= 1"]
    return []


def _check_scalar(key: str, v: Any, kind: str) -> list[str]:
    if kind == "pos":
        return (
            []
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else [f"{key}: expected a number > 0"]
        )
    if kind in ("int1", "int4", "int5000"):
        floor = int(kind[3:])
        return [] if _is_int(v) and v >= floor else [f"{key}: expected an integer >= {floor}"]
    if kind == "str":
        return [] if isinstance(v, str) and v.strip() else [f"{key}: expected a string"]
    return []


def _check_collection(key: str, v: Any, kind: str) -> list[str]:
    if kind in ("strs", "regexes"):
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            return [f"{key}: expected a list of strings"]
        if kind == "regexes":
            out = []
            for p in v:
                try:
                    re.compile(p)
                except re.error as exc:
                    out.append(f"{key}: invalid regex {p!r} ({exc})")
            return out
        return []
    if kind == "strmap":
        ok = isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) and x.strip() for k, x in v.items())
        return [] if ok else [f"{key}: expected a map of string to string"]
    if kind == "attorneys":
        if not isinstance(v, dict):
            return [f"{key}: expected a map of display name to staff uuid"]
        return [
            f"{key}.{k}: expected a staff uuid" for k, x in v.items() if not (isinstance(x, str) and _UUID.match(x))
        ]
    return []


def _check(key: str, v: Any, kind: str) -> list[str]:
    if kind in ("firm", "models", "caps"):
        return _check_struct(key, v, kind)
    if kind in ("pos", "int1", "int4", "int5000", "str"):
        return _check_scalar(key, v, kind)
    if kind in ("strs", "regexes", "strmap", "attorneys"):
        return _check_collection(key, v, kind)
    return [f"{key}: unknown kind {kind}"]


def validate(data: Any) -> list[str]:
    """Every problem as ``key: message``; empty means valid."""
    if not isinstance(data, dict):
        return ["top level must be a map"]
    out = [f"{k}: unknown key (closed key set)" for k in data if k not in REQUIRED and k not in OPTIONAL]
    for key, kind in REQUIRED.items():
        out += [f"{key}: required"] if key not in data else _check(key, data[key], kind)
    for key, (kind, _default) in OPTIONAL.items():
        if key in data:
            out += _check(key, data[key], kind)
    if not out:
        if float(data["monthly_budget_usd"]) < float(data["per_job_cap_usd"]):
            out.append("monthly_budget_usd: must be >= per_job_cap_usd")
        want = set(vocab.CASE_STATUSES) | set(vocab.DEFENDANT_STATUSES)
        got = set(data["case_statuses"])
        out += [f"case_statuses: {s!r} is not a product status" for s in sorted(got - want)]
        out += [f"case_statuses: missing {s!r}" for s in sorted(want - got)]
        if "timezone" in data:
            out += _tz(str(data["timezone"]))
    return out


def _tz(name: str) -> list[str]:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure to resolve the zone is the one finding
        return [f"timezone: {name!r} is not an IANA time zone"]
    return []


@dataclass(frozen=True)
class LitigationFirm:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        if key in self.data:
            return self.data[key]
        if key in OPTIONAL:
            return OPTIONAL[key][1]
        raise KeyError(key)

    def model(self, role: str) -> str:
        models = self.data["models"]
        if role == "transcription" and role not in models:
            return str(models["read"])
        return str(models[role])

    @property
    def court_rx(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in self.data["court_paper_patterns"]]

    @property
    def discovery_rx(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in self.get("discovery_patterns")]

    @property
    def settlement_rx(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in self.data["settlement_terms"]]

    @property
    def process_servers(self) -> list[str]:
        return [s.lower() for s in self.data["process_server_names"]]


def resolve_dir(explicit: str | Path | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def load(explicit: str | Path | None = None) -> LitigationFirm:
    root = resolve_dir(explicit)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise LitigationConfigError(f"{path} not found (set {ENV_DIR} or deliver vaults/<slug>/litigation/)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LitigationConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data)
    if problems:
        raise LitigationConfigError(f"{path}: " + "; ".join(problems))
    return LitigationFirm(root=root, data=data)


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.litigation.firm <dir>``: the provisioning validator."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m medchron.litigation.firm <litigation inputs dir>", file=sys.stderr)
        return 2
    try:
        firm = load(args[0])
    except LitigationConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{firm.root / CONFIG_NAME}: OK ({firm.data['firm']['slug']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
