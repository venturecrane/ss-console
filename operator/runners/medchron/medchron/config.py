"""The per-firm config: every firm-specific fact the pipeline used to carry as a
constant, loaded from a YAML file that lives in the private engagements repo and
reaches the seat as config-as-data (slice 5 delivers it beside customer.yaml).

Closed key set. An unknown key is an error, not a warning: a misspelled key that
silently falls back to a default is how a firm's alias table stops applying
without anyone noticing. The same rule the console validator applies to
`customer.yaml` blocks (customer-yaml-blocks.yaml).

Resolution order: `MEDCHRON_FIRM_CONFIG` env, else the fixed root-owned path the
seat's other config-as-data uses. A missing file is a refusal; there is no
built-in firm.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_PATH = "MEDCHRON_FIRM_CONFIG"
DEFAULT_PATH = "/var/lib/smd-config/medchron-firm.yaml"


class ConfigError(ValueError):
    """The firm config is missing, malformed, or carries a key this runner
    does not know. Every message names the key path."""


# ---- schema: section -> {key: (type, required)} -----------------------------
# Types: "str", "int", "float", "bool", "list[str]", "list[map]", "map".
SCHEMA: dict[str, dict[str, tuple[str, bool]]] = {
    "firm": {
        "slug": ("str", True),
        "display_name": ("str", True),
    },
    "selection": {
        # Every top-level folder is pulled unless its name matches one of these
        # classes (vendor chronologies, correspondence, photos, ...). The
        # sixteen delivered matters had sixteen folder vocabularies, so an
        # allowlist of "medical" folder names is the wrong shape; exclusion by
        # class plus disclosure is what generalises.
        "exclude_folder_classes": ("list[str]", True),
        "root_pdfs": ("bool", True),
        "doc_extensions": ("list[str]", True),
        # Names of shared folders on a joint matter (records for every client
        # arrive here besides the per-client folders).
        "shared_folder_classes": ("list[str]", False),
    },
    "folders": {
        # The firm's records-chase vocabulary carried in subfolder names
        # ("- received", "- need bill"); stripped from provider lane names.
        "status_suffix_regex": ("str", True),
    },
    "providers": {
        "aliases": ("list[map]", True),  # [{match, label}]
        "department_policy": ("map", False),  # {label: merge|split}
        "unresolved_label": ("str", True),
    },
    "coverage": {
        "exclusions": ("list[map]", True),  # [{match, reason}]
    },
    "billing": {
        "name_patterns": ("list[str]", True),
        "provider_match": ("map", False),  # {label: [regex]}
        "suspect_amount_cents": ("int", True),
    },
    "nonrecord": {
        "page_classes": ("list[map]", True),  # [{name, patterns}]
    },
    "units": {
        "exclude_name_patterns": ("list[str]", True),
    },
    "format": {
        "subsections": ("list[str]", True),
        "font": ("str", True),
        "citation_pattern": ("str", False),
    },
    "delivery": {
        "folder_template": ("str", True),
        "chronology_name_template": ("str", True),
        "worksheet_glob": ("str", True),
    },
    "models": {
        "tiers": ("map", True),  # {transcription, mechanical, composition, audit, judgment}
    },
    "levers": {
        # `audit` is never a legal member: its cache design needs live calls
        # (RUNBOOK). Enforced below, not by default value.
        "batch_stages": ("list[str]", True),
        "audit_mode": ("str", True),
        "cache": ("bool", True),
        "compose_max_tokens": ("int", True),
    },
    "chronology": {
        "treatment_gap_days": ("int", True),
        "pre_incident_history": ("str", True),  # include | summarize_only
    },
    "budget": {
        "per_job_cap_usd": ("float", True),
        "usd_per_million_chars": ("float", True),
        # The routine-11 cost controls (2026-09-09). All four are REQUIRED, not
        # defaulted: a stale firm.yaml that predates them must refuse to run
        # rather than run unmetered, which is exactly the state a 3,312-page
        # package ran in when it crossed no limit anything enforced.
        "monthly_budget_usd": ("float", True),
        "single_matter_page_threshold": ("int", True),
        # The measured rates the pre-stage projections are built from: the
        # transcription cost of one scanned page, and the audit cost of one
        # claim. Firm posture, so they live here and never on the seat.
        "usd_per_scanned_page": ("float", True),
        "usd_per_audit_claim": ("float", True),
    },
    "pipeline": {
        "python": ("str", False),
    },
}

BATCHABLE_STAGES = {"vision", "billing", "compose"}
AUDIT_MODES = {"image", "text"}
PRE_INCIDENT_POLICIES = {"include", "summarize_only"}


@dataclass(frozen=True)
class FirmConfig:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name) or {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return (self.data.get(section) or {}).get(key, default)

    # Convenience accessors used by the driver and decisions.
    @property
    def slug(self) -> str:
        return str(self.get("firm", "slug"))

    @property
    def batch_stages(self) -> list[str]:
        return list(self.get("levers", "batch_stages") or [])

    @property
    def per_job_cap_usd(self) -> float:
        return float(self.get("budget", "per_job_cap_usd"))

    @property
    def monthly_budget_usd(self) -> float:
        return float(self.get("budget", "monthly_budget_usd"))

    @property
    def single_matter_page_threshold(self) -> int:
        return int(self.get("budget", "single_matter_page_threshold"))

    @property
    def usd_per_scanned_page(self) -> float:
        return float(self.get("budget", "usd_per_scanned_page"))

    @property
    def usd_per_audit_claim(self) -> float:
        return float(self.get("budget", "usd_per_audit_claim"))

    def compiled(self, section: str, key: str) -> list[tuple[re.Pattern[str], str]]:
        """Compile a [{match, label|reason}] list once; the second field is
        whichever of label/reason the entry carries."""
        out: list[tuple[re.Pattern[str], str]] = []
        for entry in self.get(section, key) or []:
            out.append((re.compile(entry["match"], re.I), entry.get("label") or entry.get("reason") or ""))
        return out


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if kind == "list[map]":
        return isinstance(value, list) and all(isinstance(x, dict) for x in value)
    if kind == "map":
        return isinstance(value, dict)
    return False


def validate(data: Any, *, path: str = "<memory>") -> list[str]:
    """Return every problem as `section.key: message`. Empty means valid."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return [f"{path}: top level must be a map"]
    for section in data:
        if section not in SCHEMA:
            problems.append(f"{section}: unknown section (closed key set)")
    for section, keys in SCHEMA.items():
        body = data.get(section)
        if body is None:
            if any(req for _, req in keys.values()):
                problems.append(f"{section}: required section missing")
            continue
        if not isinstance(body, dict):
            problems.append(f"{section}: must be a map")
            continue
        for key in body:
            if key not in keys:
                problems.append(f"{section}.{key}: unknown key (closed key set)")
        for key, (kind, required) in keys.items():
            if key not in body:
                if required:
                    problems.append(f"{section}.{key}: required")
                continue
            if not _type_ok(body[key], kind):
                problems.append(f"{section}.{key}: expected {kind}")
    problems.extend(_semantic_checks(data))
    return problems


def _semantic_checks(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    levers = data.get("levers") or {}
    for stage in levers.get("batch_stages") or []:
        if stage == "audit":
            out.append("levers.batch_stages: `audit` is never batchable (its cache design needs live calls)")
        elif stage not in BATCHABLE_STAGES:
            out.append(f"levers.batch_stages: `{stage}` is not a batchable stage {sorted(BATCHABLE_STAGES)}")
    if levers.get("audit_mode") not in (None, *AUDIT_MODES):
        out.append(f"levers.audit_mode: expected one of {sorted(AUDIT_MODES)}")
    chron = data.get("chronology") or {}
    if chron.get("pre_incident_history") not in (None, *PRE_INCIDENT_POLICIES):
        out.append(f"chronology.pre_incident_history: expected one of {sorted(PRE_INCIDENT_POLICIES)}")
    tiers = (data.get("models") or {}).get("tiers") or {}
    for tier in ("transcription", "mechanical", "composition", "audit", "judgment"):
        if tiers and tier not in tiers:
            out.append(f"models.tiers.{tier}: required")
    for section, key in (("providers", "aliases"), ("coverage", "exclusions")):
        for i, entry in enumerate((data.get(section) or {}).get(key) or []):
            if "match" not in entry:
                out.append(f"{section}.{key}[{i}]: `match` required")
                continue
            try:
                re.compile(entry["match"])
            except re.error as exc:
                out.append(f"{section}.{key}[{i}].match: invalid regex ({exc})")
    budget = data.get("budget") or {}
    if budget and float(budget.get("per_job_cap_usd", 0)) <= 0:
        out.append("budget.per_job_cap_usd: must be > 0 (a zero cap refuses every paid stage)")
    # A zero or negative control is not a control: it either refuses every job
    # or meters nothing. Each is named separately so the validator's message
    # points at the key the firm has to fix.
    for key, what in (("monthly_budget_usd", "a zero budget refuses every job"),
                      ("single_matter_page_threshold", "a zero threshold refuses every matter"),
                      ("usd_per_scanned_page", "a zero rate projects every page at no cost"),
                      ("usd_per_audit_claim", "a zero rate projects every claim at no cost")):
        if key in budget and float(budget.get(key) or 0) <= 0:
            out.append(f"budget.{key}: must be > 0 ({what})")
    return out


def resolve_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_PATH) or DEFAULT_PATH)


def load(explicit: str | None = None) -> FirmConfig:
    path = resolve_path(explicit)
    if not path.is_file():
        raise ConfigError(
            f"firm config not found at {path} (set {ENV_PATH} or deliver it to {DEFAULT_PATH}); "
            "there is no built-in firm"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data, path=str(path))
    if problems:
        raise ConfigError(f"{path}: " + "; ".join(problems))
    return FirmConfig(path=path, data=data)
