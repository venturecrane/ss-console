"""The case file firm inputs: ``casefile-firm.yaml`` plus ``prompts/``.

Delivered from the firm's vault (``vaults/<slug>/casefile/``) into
``$MEDCHRON_CASEFILE_INPUTS_DIR`` by the entrypoint, root-owned and read-only
to the job's uid. A schema miss DEFERS a job once, then fails it
``config_missing`` (the lane); every other lane keeps running.

Everything firm-specific lives here and nowhere in product code: the reader
contract and prompts, the words that put money or a court date on a task, the
statute reminders, which file names read as bills or correspondence, the pack
budgets, the ranking thresholds, the caseload statuses and the models. Closed
key set: a misspelled key is an error, never a default.

The schema::

    firm:                      {slug, display_name}
    models:                    {read, audit, screen?, transcription?}
    per_job_cap_usd:           number > 0
    monthly_budget_usd:        number > 0   (the broker reads this one too)
    at_stake_terms:            [str]        a task naming one is never closed by a reader
    statute_reminder_patterns: [regex]      task subjects that are statute reminders (never closed)
    file_status_folder:        str          the matter folder the File Status is filed in
    # optional
    caseload_statuses:         [str]        (["Open", "Pending"])
    write_postures:            [posture]    postures whose Medicals rows are proposed
    medicals_layout_design:    str          the Medicals tab's layout design id
    pack:                      {budget_chars, fetch_cap, listing_rows_cap,
                                bill_patterns, record_folder_patterns,
                                correspondence_patterns,
                                doc_budgets: {email, bill, record, other}}
    rank:                      {statute_pull_days, statute_week_days,
                                court_pull_days, court_week_days,
                                gov_claim_pull_days, stale_negotiation_days,
                                court_event_patterns, gov_claim_patterns}
    read_workers:              int 1..4     matters read at once (2)
    tool_iterations:           int >= 4     a read's call cap (24)
    chunk_chars:               int >= 5000  one fetch_doc page (20000)
    audit_doc_chars:           int >= 2000  one proof document's text in the audit (40000)
    full_reread_days:          int >= 1     a prior record older than this is read whole (30)
    bulk_recheck_docs:         int >= 1     more changed documents than this is a bulk upload,
                                            read whole rather than re-checked (200)
    validate_retries:          int 0..3     record_review retries on a refused record (2)
    timezone:                  IANA name    the firm's zone (the schedule's calendar too)
    holidays:                  [YYYY-MM-DD] the firm's days off: no scheduled run ([])
    scheduled_cutoff_hour:     int 1..23    firm-local hour a deferred scheduled run stops retrying (14)

``prompts/`` holds ``contract.md`` (the reader contract), ``reader.md`` (the
read's instructions) and ``auditor.md`` (the second reader's). All three are
required and non-empty; they form the fixed, cached system prefix of every
read in a job.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_DIR = "MEDCHRON_CASEFILE_INPUTS_DIR"
DEFAULT_DIR = "/var/lib/smd-config/casefile"
CONFIG_NAME = "casefile-firm.yaml"
PROMPTS = ("contract.md", "reader.md", "auditor.md")
#: Every model role defaults to the read model, so a firm can tier one role at a time.
MODEL_KEYS = ("read", "audit")
OPTIONAL_MODELS = ("screen", "transcription")
POSTURES = (
    "pre_demand",
    "demand_out",
    "offer_pending",
    "settled_open_items",
    "settled_disbursed",
    "litigation",
    "declined",
    "unknown",
)
DEFAULT_WRITE_POSTURES = ["pre_demand", "demand_out", "offer_pending", "litigation"]
PACK_DEFAULTS: dict[str, Any] = {
    "budget_chars": 360_000,
    "fetch_cap": 400,
    "listing_rows_cap": 150_000,
    "bill_patterns": [],
    "record_folder_patterns": [],
    "correspondence_patterns": [],
    "doc_budgets": {"email": 7_000, "bill": 14_000, "record": 1_800, "other": 12_000},
}
RANK_DEFAULTS: dict[str, Any] = {
    "statute_pull_days": 60,
    "statute_week_days": 180,
    "court_pull_days": 14,
    "court_week_days": 45,
    "gov_claim_pull_days": 45,
    "stale_negotiation_days": 30,
    "court_event_patterns": [],
    "gov_claim_patterns": [],
}
REQUIRED: dict[str, str] = {
    "firm": "firm",
    "models": "models",
    "per_job_cap_usd": "pos",
    "monthly_budget_usd": "pos",
    "at_stake_terms": "strs",
    "statute_reminder_patterns": "regexes",
    "file_status_folder": "folder",
}
OPTIONAL: dict[str, tuple[str, Any]] = {
    "caseload_statuses": ("strs", ["Open", "Pending"]),
    "write_postures": ("postures", DEFAULT_WRITE_POSTURES),
    "medicals_layout_design": ("str", "PersonalInjurySettlementDetailsItem"),
    "pack": ("pack", PACK_DEFAULTS),
    "rank": ("rank", RANK_DEFAULTS),
    "read_workers": ("workers", 2),
    "tool_iterations": ("int4", 24),
    "chunk_chars": ("int5000", 20000),
    "audit_doc_chars": ("int2000", 40000),
    "full_reread_days": ("int1", 30),
    "bulk_recheck_docs": ("int1", 200),
    "validate_retries": ("retries", 2),
    "timezone": ("str", "America/Los_Angeles"),
    # The standing schedule (the broker reads these from its copy): the firm's
    # own days off, and the firm-local hour after which a deferred caseload is
    # a missed day (SMD paged) rather than retried.
    "holidays": ("dates", []),
    "scheduled_cutoff_hour": ("hour", 14),
}
MAX_READ_WORKERS = 4
_FOLDER = re.compile(r"^[A-Za-z0-9 ._()-]{1,80}$")


class CasefileConfigError(ValueError):
    """The case file inputs are missing or malformed."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _regexes(key: str, v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        return [f"{key}: expected a list of strings"]
    out = []
    for p in v:
        try:
            re.compile(p)
        except re.error as exc:
            out.append(f"{key}: invalid regex {p!r} ({exc})")
    return out


def _check_pack(key: str, v: Any) -> list[str]:
    if not isinstance(v, dict) or set(v) - set(PACK_DEFAULTS):
        return [f"{key}: expected a map of {sorted(PACK_DEFAULTS)}"]
    out = []
    for k in ("budget_chars", "fetch_cap", "listing_rows_cap"):
        if k in v and not (_is_int(v[k]) and v[k] >= 1):
            out.append(f"{key}.{k}: expected an integer >= 1")
    for k in ("bill_patterns", "record_folder_patterns", "correspondence_patterns"):
        if k in v:
            out += _regexes(f"{key}.{k}", v[k])
    if "doc_budgets" in v:
        b = v["doc_budgets"]
        want = set(PACK_DEFAULTS["doc_budgets"])
        if not (isinstance(b, dict) and set(b) == want and all(_is_int(x) and x >= 200 for x in b.values())):
            out.append(f"{key}.doc_budgets: expected exactly {sorted(want)} as integers >= 200")
    return out


def _check_rank(key: str, v: Any) -> list[str]:
    if not isinstance(v, dict) or set(v) - set(RANK_DEFAULTS):
        return [f"{key}: expected a map of {sorted(RANK_DEFAULTS)}"]
    out = []
    for k, d in RANK_DEFAULTS.items():
        if k not in v:
            continue
        if isinstance(d, list):
            out += _regexes(f"{key}.{k}", v[k])
        elif not (_is_int(v[k]) and v[k] >= 1):
            out.append(f"{key}.{k}: expected an integer >= 1")
    return out


def _check_ident(key: str, v: Any, kind: str) -> list[str]:
    if kind == "firm":
        ok = isinstance(v, dict) and set(v) == {"slug", "display_name"}
        ok = ok and all(isinstance(x, str) and x.strip() for x in v.values())
        return [] if ok else [f"{key}: expected exactly {{slug, display_name}} as strings"]
    ok = isinstance(v, dict) and set(MODEL_KEYS) <= set(v) <= set(MODEL_KEYS) | set(OPTIONAL_MODELS)
    ok = ok and all(isinstance(x, str) and x for x in v.values())
    return [] if ok else [f"{key}: expected {list(MODEL_KEYS)} (and optionally {list(OPTIONAL_MODELS)}) as model ids"]


def _check_number(key: str, v: Any, kind: str) -> list[str]:
    if kind == "pos":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        return [] if ok else [f"{key}: expected a number > 0"]
    if kind == "workers":
        return [] if _is_int(v) and 1 <= v <= MAX_READ_WORKERS else [f"{key}: expected 1..{MAX_READ_WORKERS}"]
    if kind == "retries":
        return [] if _is_int(v) and 0 <= v <= 3 else [f"{key}: expected 0..3"]
    floor = int(kind[3:])
    return [] if _is_int(v) and v >= floor else [f"{key}: expected an integer >= {floor}"]


def _check_text(key: str, v: Any, kind: str) -> list[str]:
    if kind == "str":
        return [] if isinstance(v, str) and v.strip() else [f"{key}: expected a string"]
    if kind == "folder":
        return [] if isinstance(v, str) and _FOLDER.match(v.strip()) else [f"{key}: expected one plain folder name"]
    if kind == "strs":
        ok = isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v)
        return [] if ok else [f"{key}: expected a non-empty list of strings"]
    if kind == "regexes":
        return _regexes(key, v)
    ok = isinstance(v, list) and all(x in POSTURES for x in v)
    return [] if ok else [f"{key}: expected a list drawn from {list(POSTURES)}"]


def _check_dates(key: str, v: Any) -> list[str]:
    """A list of dates (YAML reads an unquoted 2026-11-26 as a date)."""
    import datetime as _dt

    if not isinstance(v, list):
        return [f"{key}: expected a list of YYYY-MM-DD dates"]
    for x in v:
        if isinstance(x, _dt.date):
            continue
        try:
            _dt.date.fromisoformat(str(x).strip())
        except ValueError:
            return [f"{key}: {x!r} is not a YYYY-MM-DD date"]
    return []


_CHECKS = {
    "firm": _check_ident,
    "models": _check_ident,
    "pos": _check_number,
    "int1": _check_number,
    "int4": _check_number,
    "int2000": _check_number,
    "int5000": _check_number,
    "workers": _check_number,
    "retries": _check_number,
    "str": _check_text,
    "folder": _check_text,
    "strs": _check_text,
    "regexes": _check_text,
    "postures": _check_text,
    "dates": lambda key, v, _kind: _check_dates(key, v),
    "hour": lambda key, v, _kind: [] if _is_int(v) and 1 <= v <= 23 else [f"{key}: expected an hour 1..23"],
    "pack": lambda key, v, _kind: _check_pack(key, v),
    "rank": lambda key, v, _kind: _check_rank(key, v),
}


def _check(key: str, v: Any, kind: str) -> list[str]:
    fn = _CHECKS.get(kind)
    return fn(key, v, kind) if fn else [f"{key}: unknown kind {kind}"]


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
    if not out and float(data["monthly_budget_usd"]) < float(data["per_job_cap_usd"]):
        out.append("monthly_budget_usd: must be >= per_job_cap_usd")
    if not out and "timezone" in data:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(str(data["timezone"]))
        except Exception:  # noqa: BLE001 - any failure to resolve the zone is the one finding
            out.append(f"timezone: {data['timezone']!r} is not an IANA time zone")
    return out


@dataclass(frozen=True)
class CasefileFirm:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        if key in self.data:
            return self.data[key]
        if key in OPTIONAL:
            return OPTIONAL[key][1]
        raise KeyError(key)

    def model(self, role: str) -> str:
        models = self.data["models"]
        return str(models.get(role) or models["read"])

    def pack(self, key: str) -> Any:
        return {**PACK_DEFAULTS, **(self.data.get("pack") or {})}[key]

    def rank(self, key: str) -> Any:
        return {**RANK_DEFAULTS, **(self.data.get("rank") or {})}[key]

    def rx(self, values: list[str]) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in values]

    @property
    def at_stake_rx(self) -> re.Pattern[str]:
        """Whole word: not inside a longer word or a hyphen-joined one."""
        words = "|".join(re.escape(w) for w in self.data["at_stake_terms"])
        return re.compile(r"(?<![\w-])(" + words + r")(?![\w-])", re.I)

    @property
    def statute_rx(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.I) for p in self.data["statute_reminder_patterns"]]


def resolve_dir(explicit: str | Path | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def _prompts(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in PROMPTS:
        p = root / "prompts" / name
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise CasefileConfigError(f"{p}: not readable ({type(exc).__name__})") from exc
        if not text.strip():
            raise CasefileConfigError(f"{p}: empty")
        out[name] = text
    return out


def load(explicit: str | Path | None = None) -> CasefileFirm:
    root = resolve_dir(explicit)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise CasefileConfigError(f"{path} not found (set {ENV_DIR} or deliver vaults/<slug>/casefile/)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CasefileConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data)
    if problems:
        raise CasefileConfigError(f"{path}: " + "; ".join(problems))
    return CasefileFirm(root=root, data=data, prompts=_prompts(root))


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.casefile.firm <dir>``: the provisioning validator."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m medchron.casefile.firm <casefile inputs dir>", file=sys.stderr)
        return 2
    try:
        firm = load(args[0])
    except CasefileConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{firm.root / CONFIG_NAME}: OK ({firm.data['firm']['slug']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
