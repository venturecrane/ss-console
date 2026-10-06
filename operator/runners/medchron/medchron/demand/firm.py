"""The demand firm inputs: ``demand-firm.yaml`` and the files it pins.

A separate config from the chronology's ``medchron-firm.yaml`` (review of the
plan, 2026-10-06): a schema miss here defers DEMAND jobs only, and a chronology
seat whose demand inputs are absent keeps running chronologies.

The directory is delivered whole from the firm's vault (``vaults/<slug>/demand/``)
by the entrypoint, root-owned and read-only to the job's uid. Every file the
job reads is named in ``inputs`` with its sha256, so a transport-mangled or
hand-edited copy refuses instead of drafting from something nobody reviewed
(the laptop's ``SMD_VOICE_SHA256`` pin, applied to every input). Closed key set,
as the chronology config: a misspelled key is an error, not a default.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_DIR = "MEDCHRON_DEMAND_INPUTS"
DEFAULT_DIR = "/var/lib/smd-config/demand"
CONFIG_NAME = "demand-firm.yaml"

#: The files a demand job reads. Every one is required; a firm without a voice
#: profile does not get a plain-register demand (the 2026-09-24 lesson).
INPUT_KEYS = (
    "voice_profile",
    "voice_fixed_strings",
    "voice_adjustments",
    "skeleton",
    "drafting_discipline",
    "house_reference",
    "prompt_digest",
    "prompt_condense",
    "prompt_gap_audit",
    "prompt_compose",
    "prompt_audit",
    "prompt_repair",
)
MODEL_KEYS = ("transcription", "digest", "gap_audit", "compose", "audit", "repair")

# section -> {key: (type, required)}
SCHEMA: dict[str, dict[str, tuple[str, bool]]] = {
    "firm": {"slug": ("str", True), "display_name": ("str", True)},
    "models": {k: ("str", True) for k in MODEL_KEYS},
    "budget": {
        "per_job_cap_usd": ("float", True),
        "monthly_budget_usd": ("float", True),
        # The estimate's measured rates (2026-09-01..24 drafting ledger): the
        # record's characters through digest, the scanned pages through
        # transcription, and the fixed drafting tail (compose, audit, repair,
        # reaudit), which does not scale with the record once it is digested.
        "usd_per_million_chars": ("float", True),
        "usd_per_scanned_page": ("float", True),
        "usd_drafting_fixed": ("float", True),
    },
    "privilege": {
        # The firm's own mail domains. Mail between two of them is internal;
        # mail between one of them and the client is privileged. Both are
        # walled off structurally, before any model sees the record.
        "firm_domains": ("list[str]", True),
        # Consumer mailboxes (the client's likely address when the matter's
        # contact carries none). Used only when the client address is unknown.
        "consumer_domains": ("list[str]", True),
    },
    "selection": {
        "doc_extensions": ("list[str]", True),
        "exclude_folder_patterns": ("list[str]", True),
        "exclude_name_patterns": ("list[str]", False),
    },
    "premise": {
        # {class: [regex]} scanned against document names and email subjects.
        "scan": ("map", True),
        # The classes whose hit fails the gate (the demand is not written).
        "fail_on": ("list[str]", True),
        # Text phrases that, inside a carrier's document, state a denial.
        "denial_phrases": ("list[str]", True),
        # Text phrases that identify a carrier claim (G1).
        "carrier_phrases": ("list[str]", True),
    },
    "format": {
        "firm_signature": ("str", True),
        "signer_title": ("str", True),
        "footer_markers": ("list[str]", True),
    },
    "delivery": {
        "folder_template": ("str", True),
        "gap_audit_name_template": ("str", True),
        "coverage_report_name_template": ("str", True),
    },
    "levers": {
        "chunk_chars": ("int", True),
        "concurrency": ("int", True),
        "digest_max_tokens": ("int", True),
        "compose_max_tokens": ("int", True),
        "digest_budget_chars": ("int", True),
    },
    "inputs": {k: ("map", True) for k in INPUT_KEYS},
}


class DemandConfigError(ValueError):
    """The demand inputs are missing, malformed, or do not match their pins."""


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str) and bool(value.strip())
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if kind == "map":
        return isinstance(value, dict)
    return False


def _shape(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["top level must be a map"]
    out = [f"{s}: unknown section (closed key set)" for s in data if s not in SCHEMA]
    for section, keys in SCHEMA.items():
        body = data.get(section)
        if not isinstance(body, dict):
            out.append(f"{section}: required section missing or not a map")
            continue
        out += [f"{section}.{k}: unknown key (closed key set)" for k in body if k not in keys]
        for key, (kind, required) in keys.items():
            if key not in body:
                if required:
                    out.append(f"{section}.{key}: required")
            elif not _type_ok(body[key], kind):
                out.append(f"{section}.{key}: expected {kind}")
    return out


def _semantics(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("per_job_cap_usd", "monthly_budget_usd", "usd_per_million_chars", "usd_per_scanned_page"):
        if float(data["budget"][key]) <= 0:
            out.append(f"budget.{key}: must be > 0")
    if float(data["budget"]["usd_drafting_fixed"]) < 0:
        out.append("budget.usd_drafting_fixed: must be >= 0")
    levers = data["levers"]
    if not 20_000 <= levers["chunk_chars"] <= 200_000:
        out.append("levers.chunk_chars: expected 20000..200000 (dense records truncate above about 120K)")
    if not 1 <= levers["concurrency"] <= 8:
        out.append("levers.concurrency: expected 1..8")
    scan = data["premise"]["scan"]
    for cls, pats in scan.items():
        if not (isinstance(pats, list) and pats and all(isinstance(p, str) for p in pats)):
            out.append(f"premise.scan.{cls}: expected a non-empty list of regex")
            continue
        for p in pats:
            try:
                re.compile(p)
            except re.error as exc:
                out.append(f"premise.scan.{cls}: invalid regex {p!r} ({exc})")
    out += [f"premise.fail_on: {c!r} is not a premise.scan class" for c in data["premise"]["fail_on"] if c not in scan]
    for p in data["selection"]["exclude_folder_patterns"] + list(data["selection"].get("exclude_name_patterns") or []):
        try:
            re.compile(p)
        except re.error as exc:
            out.append(f"selection: invalid regex {p!r} ({exc})")
    for key in INPUT_KEYS:
        entry = data["inputs"][key]
        if set(entry) != {"path", "sha256"} or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256"))):
            out.append(f"inputs.{key}: expected {{path, sha256}} with a 64-hex sha256")
        elif Path(str(entry["path"])).is_absolute() or ".." in Path(str(entry["path"])).parts:
            out.append(f"inputs.{key}.path: must be relative to the inputs directory")
    return out


def validate(data: Any) -> list[str]:
    """Every problem as ``section.key: message``; empty means valid. Shape
    first: the semantic checks assume the shape."""
    problems = _shape(data)
    return problems or _semantics(data)


@dataclass(frozen=True)
class DemandFirm:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, section: str, key: str) -> Any:
        return self.data[section][key]

    def model(self, stage: str) -> str:
        return str(self.data["models"][stage])

    def input_path(self, key: str) -> Path:
        return self.root / str(self.data["inputs"][key]["path"])

    def text(self, key: str) -> str:
        return self.input_path(key).read_text(encoding="utf-8")

    @property
    def firm_domains(self) -> tuple[str, ...]:
        return tuple(d.lower().lstrip("@") for d in self.data["privilege"]["firm_domains"])

    @property
    def consumer_domains(self) -> tuple[str, ...]:
        return tuple(d.lower().lstrip("@") for d in self.data["privilege"]["consumer_domains"])


def check_pins(root: Path, data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in INPUT_KEYS:
        entry = data["inputs"][key]
        p = root / str(entry["path"])
        if not p.is_file():
            out.append(f"inputs.{key}: {entry['path']} is not in {root}")
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != entry["sha256"]:
            out.append(f"inputs.{key}: {entry['path']} sha256 {got[:12]} does not match the pin {entry['sha256'][:12]}")
    return out


def resolve_dir(explicit: str | Path | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def load(explicit: str | Path | None = None) -> DemandFirm:
    root = resolve_dir(explicit)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise DemandConfigError(f"{path} not found (set {ENV_DIR} or deliver vaults/<slug>/demand/); no built-in firm")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DemandConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data)
    if not problems:
        problems = check_pins(root, data)
    if problems:
        raise DemandConfigError(f"{path}: " + "; ".join(problems))
    return DemandFirm(root=root, data=data)


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.demand.firm <dir>``: the provisioning validator."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m medchron.demand.firm <demand inputs dir>", file=sys.stderr)
        return 2
    try:
        firm = load(args[0])
    except DemandConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{firm.root / CONFIG_NAME}: OK ({firm.get('firm', 'slug')}, {len(INPUT_KEYS)} inputs pinned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
