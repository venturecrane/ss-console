"""The drafting firm inputs: ``drafting-firm.yaml`` and the files it pins.

A config of its own, beside ``demand-firm.yaml`` and the chronology's
``medchron-firm.yaml``: a schema miss here DEFERS drafting jobs only, and a
seat whose drafting inputs are absent keeps running chronologies and demands.

Delivered whole from the firm's vault (``vaults/<slug>/drafting/``) by the
entrypoint, root-owned and read-only to the job's uid. Every file a job reads
is named in ``inputs`` (relative path -> sha256), so a transport-mangled or
hand-edited copy refuses instead of drafting from something nobody reviewed.
Closed key set: a misspelled key is an error, never a default.

The schema (the engagements author writes to exactly this)::

    firm:        {slug, display_name}
    models:      {transcription, digest, compose, audit, repair}
    budget:      {per_job_cap_usd, monthly_budget_usd, usd_per_million_chars,
                  usd_per_scanned_page, usd_drafting_fixed}
    privilege:   {firm_domains, consumer_domains}
    selection:   {doc_extensions, exclude_folder_patterns, exclude_name_patterns}
    style:       <path of the house-style markdown>
    format:      {font, size_pt, mediation_brief_sections: [11 titles],
                  discovery_label_style, depo_outline_page_numbers,
                  plain_letter_paper?: bool,
                  layout?: {<class>: {line_spacing, line_spacing_pt, justify,
                            first_line_indent_in, heading_indent_in: [3],
                            heading_underline: [3], item_line_spacing,
                            item_space_after_pt, centered_court_lines,
                            bold_italic_heading_indent_in, page_numbers_always,
                            footer_title}}   every layout key optional
    classes:     {<class>: {skeleton, prompts: {digest, compose, audit, repair},
                  exemplars: [paths]}}   one entry for each of the five classes
    attachments: {decl_2030_050, pos}
    inputs:      {<relative path>: <sha256>}   every file referenced above
    delivery:    {rehearsal_matters: [matter numbers]}   OPTIONAL

``delivery`` is the one optional section: the matter numbers a job may file to
when they are not the matter it read (the firm's Operator library matter, for
a rehearsal). Absent, a job files only to the matter it read.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_DIR = "MEDCHRON_DRAFTING_INPUTS"
DEFAULT_DIR = "/var/lib/smd-config/drafting"
CONFIG_NAME = "drafting-firm.yaml"

CLASSES = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")
MODEL_KEYS = ("transcription", "digest", "compose", "audit", "repair")
PROMPT_KEYS = ("digest", "compose", "audit", "repair")
LABEL_STYLES = ("caps_bold_underline", "all caps, bold, underlined")
MEDIATION_SECTIONS = 11
#: format.layout.<class> keys and their types (closed).
LAYOUT_KEYS = {
    "line_spacing": "num",  # a multiple: 2.0 is double
    "line_spacing_pt": "num",  # exact points: wins over line_spacing
    "justify": "bool",
    "first_line_indent_in": "num",
    "heading_indent_in": "num3",
    "heading_underline": "bool3",
    "item_line_spacing": "num",
    "item_space_after_pt": "num",
    "centered_court_lines": "bool",
    "bold_italic_heading_indent_in": "num",
    "page_numbers_always": "bool",
    "footer_title": "str",
}

# section -> {key: (type, required)}; None: a free-keyed map checked by its own rules
SCHEMA: dict[str, dict[str, tuple[str, bool]] | None | str] = {
    "firm": {"slug": ("str", True), "display_name": ("str", True)},
    "models": {k: ("str", True) for k in MODEL_KEYS},
    "budget": {
        "per_job_cap_usd": ("float", True),
        "monthly_budget_usd": ("float", True),
        "usd_per_million_chars": ("float", True),
        "usd_per_scanned_page": ("float", True),
        "usd_drafting_fixed": ("float", True),
    },
    "privilege": {"firm_domains": ("list[str]", True), "consumer_domains": ("list[str]", True)},
    "selection": {
        "doc_extensions": ("list[str]", True),
        "exclude_folder_patterns": ("list[str]", True),
        "exclude_name_patterns": ("list[str]", True),
    },
    "style": "path",
    "format": {
        "font": ("str", True),
        "size_pt": ("float", True),
        "mediation_brief_sections": ("list[str]", True),
        "discovery_label_style": ("str", True),
        "depo_outline_page_numbers": ("bool", True),
        # Optional: plain US Letter, line numbering removed, for every class.
        "plain_letter_paper": ("bool", False),
        # Optional: {<class>: {LAYOUT_KEYS}}, the attorney's per-class layout
        # (house.py turns it into the renderer's HouseStyle override).
        "layout": ("map", False),
    },
    "classes": None,
    "attachments": {"decl_2030_050": ("str", True), "pos": ("str", True)},
    "inputs": None,
}
OPTIONAL = {"delivery": {"rehearsal_matters": ("list[str]", True)}}


class DraftingConfigError(ValueError):
    """The drafting inputs are missing, malformed, or do not match their pins."""


def _type_ok(value: Any, kind: str) -> bool:
    if kind in ("str", "path"):
        return isinstance(value, str) and bool(value.strip())
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "map":
        return isinstance(value, dict)
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)
    return False


def _section(name: str, body: Any, keys: dict[str, tuple[str, bool]]) -> list[str]:
    if not isinstance(body, dict):
        return [f"{name}: required section missing or not a map"]
    out = [f"{name}.{k}: unknown key (closed key set)" for k in body if k not in keys]
    for key, (kind, required) in keys.items():
        if key not in body:
            if required:
                out.append(f"{name}.{key}: required")
        elif not _type_ok(body[key], kind):
            out.append(f"{name}.{key}: expected {kind}")
    return out


def _shape(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["top level must be a map"]
    out = [f"{s}: unknown section (closed key set)" for s in data if s not in SCHEMA and s not in OPTIONAL]
    for name, keys in SCHEMA.items():
        if keys == "path":
            if not _type_ok(data.get(name), "path"):
                out.append(f"{name}: required, a path in inputs")
        elif keys is None:
            if not isinstance(data.get(name), dict):
                out.append(f"{name}: required section missing or not a map")
        else:
            out += _section(name, data.get(name), keys)  # type: ignore[arg-type]
    for name, keys in OPTIONAL.items():
        if name in data:
            out += _section(name, data[name], keys)
    return out


def _class_entry(cls: str, entry: Any) -> list[str]:
    where = f"classes.{cls}"
    if not isinstance(entry, dict) or set(entry) != {"skeleton", "prompts", "exemplars"}:
        return [f"{where}: expected exactly {{skeleton, prompts, exemplars}}"]
    out = [] if _type_ok(entry["skeleton"], "path") else [f"{where}.skeleton: expected a path"]
    prompts = entry["prompts"]
    if not isinstance(prompts, dict) or set(prompts) != set(PROMPT_KEYS):
        out.append(f"{where}.prompts: expected exactly {list(PROMPT_KEYS)}")
    else:
        out += [f"{where}.prompts.{k}: expected a path" for k in PROMPT_KEYS if not _type_ok(prompts[k], "path")]
    if not isinstance(entry["exemplars"], list) or not all(_type_ok(x, "path") for x in entry["exemplars"]):
        out.append(f"{where}.exemplars: expected a list of paths")
    return out


def _num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _layout_value_ok(value: object, kind: str) -> bool:
    num = _num
    if kind == "num":
        return num(value)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "str":
        return isinstance(value, str) and bool(value.strip())
    if kind == "num3":
        return isinstance(value, list) and len(value) == 3 and all(num(v) for v in value)
    if kind == "bool3":
        return isinstance(value, list) and len(value) == 3 and all(isinstance(v, bool) for v in value)
    return False


def _layout(layout: dict) -> list[str]:
    out = []
    for cls, body in layout.items():
        where = f"format.layout.{cls}"
        if cls not in CLASSES:
            out.append(f"{where}: unknown class (expected {list(CLASSES)})")
            continue
        if not isinstance(body, dict):
            out.append(f"{where}: expected a map")
            continue
        out += [f"{where}.{k}: unknown key (closed key set)" for k in body if k not in LAYOUT_KEYS]
        out += [
            f"{where}.{k}: expected {LAYOUT_KEYS[k]}"
            for k, v in body.items()
            if k in LAYOUT_KEYS and not _layout_value_ok(v, LAYOUT_KEYS[k])
        ]
    return out


def _models(models: dict) -> list[str]:
    """Every configured model must have a known output maximum: a model the
    table does not know would silently get a small ceiling and truncate."""
    from ..demand.gapaudit import OUTPUT_MAX

    return [
        f"models.{stage}: {m!r} has no known output maximum (medchron.demand.gapaudit.OUTPUT_MAX)"
        for stage, m in models.items()
        if m not in OUTPUT_MAX
    ]


def referenced_paths(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(where, path) for every file the config names outside ``inputs``."""
    refs = [("style", data["style"])]
    refs += [(f"attachments.{k}", v) for k, v in data["attachments"].items()]
    for cls, entry in data["classes"].items():
        refs.append((f"classes.{cls}.skeleton", entry["skeleton"]))
        refs += [(f"classes.{cls}.prompts.{k}", v) for k, v in entry["prompts"].items()]
        refs += [(f"classes.{cls}.exemplars[{i}]", v) for i, v in enumerate(entry["exemplars"])]
    return refs


def _semantics(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("per_job_cap_usd", "monthly_budget_usd", "usd_per_million_chars", "usd_per_scanned_page"):
        if float(data["budget"][key]) <= 0:
            out.append(f"budget.{key}: must be > 0")
    if float(data["budget"]["usd_drafting_fixed"]) < 0:
        out.append("budget.usd_drafting_fixed: must be >= 0")
    fmt = data["format"]
    if len(fmt["mediation_brief_sections"]) != MEDIATION_SECTIONS:
        out.append(f"format.mediation_brief_sections: expected exactly {MEDIATION_SECTIONS} section titles")
    if fmt["discovery_label_style"] not in LABEL_STYLES:
        out.append(f"format.discovery_label_style: expected one of {list(LABEL_STYLES)}")
    if not 8 <= float(fmt["size_pt"]) <= 16:
        out.append("format.size_pt: expected 8..16")
    out += _layout(fmt.get("layout") or {})
    out += _models(data["models"])
    for p in data["selection"]["exclude_folder_patterns"] + data["selection"]["exclude_name_patterns"]:
        try:
            re.compile(p)
        except re.error as exc:
            out.append(f"selection: invalid regex {p!r} ({exc})")
    classes = data["classes"]
    out += [f"classes.{c}: unknown class (expected {list(CLASSES)})" for c in classes if c not in CLASSES]
    out += [f"classes.{c}: required" for c in CLASSES if c not in classes]
    for cls in CLASSES:
        if cls in classes:
            out += _class_entry(cls, classes[cls])
    if out:
        return out
    inputs = data["inputs"]
    for path, digest in inputs.items():
        if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
            out.append(f"inputs.{path}: must be a path relative to the inputs directory")
        elif not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            out.append(f"inputs.{path}: expected a 64-hex sha256")
    out += [f"{where}: {p} is not pinned in inputs" for where, p in referenced_paths(data) if p not in inputs]
    return out


def validate(data: Any) -> list[str]:
    """Every problem as ``section.key: message``; empty means valid."""
    return _shape(data) or _semantics(data)


@dataclass(frozen=True)
class DraftingFirm:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, section: str, key: str) -> Any:
        return self.data[section][key]

    def model(self, stage: str) -> str:
        return str(self.data["models"][stage])

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    @property
    def style(self) -> str:
        return self.read(self.data["style"])

    def skeleton(self, cls: str) -> str:
        return self.read(self.data["classes"][cls]["skeleton"])

    def prompt(self, cls: str, stage: str) -> str:
        return self.read(self.data["classes"][cls]["prompts"][stage])

    def exemplar_paths(self, cls: str) -> list[Path]:
        return [self.root / p for p in self.data["classes"][cls]["exemplars"]]

    def attachment(self, key: str) -> str:
        return self.read(self.data["attachments"][key])

    @property
    def rehearsal_matters(self) -> tuple[str, ...]:
        return tuple((self.data.get("delivery") or {}).get("rehearsal_matters") or ())

    @property
    def firm_domains(self) -> tuple[str, ...]:
        return tuple(d.lower().lstrip("@") for d in self.data["privilege"]["firm_domains"])


def check_pins(root: Path, data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rel, digest in data["inputs"].items():
        p = root / rel
        if not p.is_file():
            out.append(f"inputs.{rel}: not in {root}")
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != digest:
            out.append(f"inputs.{rel}: sha256 {got[:12]} does not match the pin {digest[:12]}")
    return out


def resolve_dir(explicit: str | Path | None = None) -> Path:
    return Path(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def load(explicit: str | Path | None = None) -> DraftingFirm:
    root = resolve_dir(explicit)
    path = root / CONFIG_NAME
    if not path.is_file():
        raise DraftingConfigError(
            f"{path} not found (set {ENV_DIR} or deliver vaults/<slug>/drafting/); no built-in firm"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DraftingConfigError(f"{path}: not valid YAML ({exc})") from exc
    problems = validate(data) or check_pins(root, data)
    if problems:
        raise DraftingConfigError(f"{path}: " + "; ".join(problems))
    return DraftingFirm(root=root, data=data)


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.drafting.firm <dir>``: the provisioning validator."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m medchron.drafting.firm <drafting inputs dir>", file=sys.stderr)
        return 2
    try:
        firm = load(args[0])
    except DraftingConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"{firm.root / CONFIG_NAME}: OK ({firm.get('firm', 'slug')}, {len(firm.data['inputs'])} inputs pinned, "
        f"{len(firm.data['classes'])} classes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
