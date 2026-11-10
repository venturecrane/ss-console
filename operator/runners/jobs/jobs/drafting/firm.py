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
    models:      {transcription, digest, compose, audit, repair, review?}
    budget:      {per_job_cap_usd, monthly_budget_usd, usd_per_million_chars,
                  usd_per_scanned_page, usd_drafting_fixed,
                  repair_rounds?, repair_cap_usd?}
    privilege:   {firm_domains, consumer_domains}
    selection:   {doc_extensions, exclude_folder_patterns, exclude_name_patterns}
    style:       <path of the house-style markdown>
    format:      {font, size_pt, mediation_brief_sections: [11 titles],
                  discovery_label_style, depo_outline_page_numbers,
                  plain_letter_paper?: bool,
                  pleading?: {lines, line_pitch_pt, margins_in: [4]},
                  limits?: {<class>: {<section title>: max pages}},
                  firm_bases?: {<class>: {path, contract?, source: {matter_id, file_id, name,
                  sha256, version?, accepted?}}}   contract "marked" (the default:
                  mediation_brief|memo, filled through the class's plan) or "styles"
                  (any class: the firm's page setup, headers, footers and styles, the
                  body written fresh by the renderer),
                  layout?: {<class>: {line_spacing, line_spacing_pt, justify,
                            first_line_indent_in, heading_indent_in: [3],
                            heading_underline: [3], item_line_spacing,
                            item_space_after_pt, centered_court_lines,
                            bold_italic_heading_indent_in, page_numbers_always,
                            footer_title, heading_line_spacing,
                            heading_line_spacing_pt,
                            heading_space_before_pt, heading_space_after_pt,
                            table_font_pt, table_header_fill, table_total_fill,
                            table_border_pt}}   every layout key optional
    classes:     {<class>: {skeleton, prompts: {digest, compose, audit, repair},
                  exemplars: [paths]}}   one entry for each of the five classes
    attachments: {decl_2030_050, pos}
    inputs:      {<relative path>: <sha256>}   every file referenced above
    delivery:    {rehearsal_matters: [matter numbers]}   OPTIONAL
    evidence:    {mode, classes, page_window, image_pages, prompts}   OPTIONAL,
                 the brief's drafting path (evidence_config.py); default digest
    verify:      {enabled, classes, max_loops, model, parallelism, authorities}
                 OPTIONAL, the source check on the ledger path (verify/config.py)
    attorneys:   [{email, block: [lines]}]   OPTIONAL, the caption's attorney block
                 per attorney (attorneys.py)
    citations:   {<class>: {allow: [kinds], forbid?: [kinds]}}   OPTIONAL, which
                 files a document may cite (citations.py)
    opening_demand: {<class>: {marker, chart, column, sentence}}   OPTIONAL, the
                 demand code computes from the damages chart (opening_demand.py)

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

from ..firm_inputs import pin

from . import attorneys as attorneys_mod, citations, evidence_config, opening_demand, page_limits
from .verify import config as verify_config

ENV_DIR = "JOBS_DRAFTING_INPUTS"
DEFAULT_DIR = "/var/lib/smd-config/drafting"
CONFIG_NAME = "drafting-firm.yaml"

CLASSES = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")
MODEL_KEYS = ("transcription", "digest", "compose", "audit", "repair")
#: Optional: the independent pre-delivery review's model (``review.py``).
#: Absent, the review uses ``audit``, never ``compose``: on 2026-10-12 the
#: review ran on the drafter's own model and the drafter's blind spot was its.
OPTIONAL_MODEL_KEYS = ("review",)
PROMPT_KEYS = ("digest", "compose", "audit", "repair")
LABEL_STYLES = ("caps_bold_underline", "all caps, bold, underlined")
MEDIATION_SECTIONS = 11
#: The classes whose first draft can be made in a firm's MARKED Word file (brief_base.py, memo_base.py).
FIRM_BASE_CLASSES = ("mediation_brief", "memo")
#: ``format.firm_bases.<class>.contract``: a marked file is filled through the
#: class's plan (``base_lay.PLANS``); a styles file is the renderer's base, its
#: body cleared and written fresh (``render.render(base=...)``), so any class
#: may have one. Absent means marked, the only contract before 2026-11-09.
MARKED_BASE, STYLES_BASE = "marked", "styles"
BASE_CONTRACTS = (MARKED_BASE, STYLES_BASE)
STYLES_BASE_CLASSES = CLASSES
SOURCE_KEYS = ("matter_id", "file_id", "name", "sha256")
ACCEPTANCE_KEYS = ("version", "accepted")
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
    # The attorney's spacing of headings and his charts (2026-11-04, Chris
    # Price: double spaced including the headings; charts boxed, larger, with
    # some colour).
    "heading_line_spacing": "num",
    "heading_line_spacing_pt": "num",  # exact points: wins over the multiple
    "heading_space_before_pt": "num",
    "heading_space_after_pt": "num",
    "table_font_pt": "pt",  # every table but the caption
    "table_header_fill": "hex",  # RRGGBB
    "table_total_fill": "hex",  # a row whose first cell starts with "Total"
    "table_border_pt": "border",
    # The first page's gaps, in blank lines (frontpage.py; A&P, 2026-11-04).
    "front_attorney_gap_lines": "lines",
    "front_caption_gap_lines": "lines",
    # The first page single spaced, a rule under the caption, the date beside
    # the "By:" line (briefpage.py; A&P, 2026-11-04 3:28 PM).
    "front_single_spaced": "bool",
    "caption_rule_below": "rule",  # true (full width) or "left" (to the caption's vertical line)
    "signature_date_beside": "bool",
    # Chris, 2026-11-04 5:03 PM: the Dated/By and name lines single spaced, the
    # blank signature lines above them, two blank lines from "Attorneys for"
    # to the court (frontpage.py).
    "signature_single_spaced": "bool",
    "signature_gap_lines": "lines",
    "front_court_gap_lines": "lines",
}

#: Where each firm-authored format key belongs (ADR 0083 amendment 2026-11-10).
#: ``template``: a Word paragraph, character, table or section style can carry
#: it, so it belongs in the firm's template (item 1); the yaml copy is a known
#: violation that shrinks as the styles base (#3905) takes it over.
#: ``firm_yaml``: no style can express it, because it depends on the document's
#: structure (blank lines between front-page blocks, a row picked by its text,
#: one block placed beside another, a rule drawn to the caption grid) or is
#: per-document text; it stays in drafting-firm.yaml, listed here (item 4).
#: Rule-derived layout (pleading paper, line pitch) is not here: it is code in
#: ``pleading.py`` (item 2).
FORMAT_KEY_HOME: dict[str, str] = {
    "format.font": "template",
    "format.size_pt": "template",
    **{
        f"format.layout.{k}": "template"
        for k in (
            "line_spacing",
            "line_spacing_pt",
            "justify",
            "first_line_indent_in",
            "heading_indent_in",
            "heading_underline",
            "item_line_spacing",
            "item_space_after_pt",
            "centered_court_lines",
            "bold_italic_heading_indent_in",
            "page_numbers_always",
            "heading_line_spacing",
            "heading_line_spacing_pt",
            "heading_space_before_pt",
            "heading_space_after_pt",
            "table_font_pt",
            "table_header_fill",
            "table_border_pt",
            "front_single_spaced",
            "signature_single_spaced",
        )
    },
    **{
        f"format.layout.{k}": "firm_yaml"
        for k in (
            "footer_title",  # per-document text, not a style
            "table_total_fill",  # a row chosen by its first cell's text
            "front_attorney_gap_lines",  # blank lines between front-page blocks
            "front_caption_gap_lines",
            "front_court_gap_lines",
            "signature_gap_lines",
            "caption_rule_below",  # a rule drawn to the caption grid's vertical line
            "signature_date_beside",  # one block placed beside another
        )
    },
}

# section -> {key: (type, required)}; None: a free-keyed map checked by its own rules
SCHEMA: dict[str, dict[str, tuple[str, bool]] | None | str] = {
    "firm": {"slug": ("str", True), "display_name": ("str", True)},
    "models": {**{k: ("str", True) for k in MODEL_KEYS}, **{k: ("str", False) for k in OPTIONAL_MODEL_KEYS}},
    "budget": {
        "per_job_cap_usd": ("float", True),
        "monthly_budget_usd": ("float", True),
        "usd_per_million_chars": ("float", True),
        "usd_per_scanned_page": ("float", True),
        "usd_drafting_fixed": ("float", True),
        # Optional: a served set's repair budget (repair_budget.py); defaults
        # there, 8 rounds and a quarter of per_job_cap_usd.
        "repair_rounds": ("float", False),
        "repair_cap_usd": ("float", False),
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
        # Optional: the firm's pleading paper, used ONLY when a request asks
        # for it (pleading.py): {lines, line_pitch_pt, margins_in: [top, right,
        # bottom, left]}. Absent, a pleading request is held for SMD.
        "pleading": ("map", False),
        # Optional: {<class>: {<section title>: max pages}}, measured on the
        # rendered pages (page_limits.py).
        "limits": ("map", False),
        # Optional: the classes filed on the firm's pleading paper by default,
        # asked for or not (finish.paper); needs format.pleading.
        "pleading_classes": ("list[str]", False),
        # Optional: {<class>: {path, source: {matter_id, file_id, name, sha256, version?,
        # accepted?}, contract?}}, the attorney's accepted save or a firm's past file,
        # blanked: the Word file a first draft is made in. A marked file (the default)
        # is filled by the class's plan (firmbase, brief_base.py, memo_base.py) and its
        # layout outranks layout.<class>; a styles file is the renderer's base, and
        # layout.<class> is laid over it as authored.
        "firm_bases": ("map", False),
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
    out = [
        f"{s}: unknown section (closed key set)"
        for s in data
        if s not in SCHEMA
        and s not in OPTIONAL
        and s not in (attorneys_mod.KEY, evidence_config.KEY, verify_config.KEY, citations.KEY, opening_demand.KEY)
    ]
    if opening_demand.KEY in data:
        out += opening_demand.validate(data[opening_demand.KEY], CLASSES)
    if citations.KEY in data:
        out += citations.validate(data[citations.KEY], CLASSES)
    if attorneys_mod.KEY in data:
        out += attorneys_mod.validate(data[attorneys_mod.KEY])
    if evidence_config.KEY in data:
        out += evidence_config.validate(data[evidence_config.KEY], CLASSES)
    if verify_config.KEY in data:
        out += verify_config.validate(data[verify_config.KEY], CLASSES, None)
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
    if kind == "pt":
        return num(value) and 8 <= float(value) <= 20  # type: ignore[arg-type]
    if kind == "border":
        return num(value) and 0.25 <= float(value) <= 6  # type: ignore[arg-type]
    if kind == "rule":
        return isinstance(value, bool) or value == "left"
    if kind == "lines":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6
    if kind == "hex":
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9A-Fa-f]{6}", value))
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


PLEADING_KEYS = ("lines", "line_pitch_pt", "margins_in")


def _pleading(p: dict) -> list[str]:
    where = "format.pleading"
    out = [f"{where}.{k}: unknown key (closed key set)" for k in p if k not in PLEADING_KEYS]
    out += [f"{where}.{k}: required" for k in PLEADING_KEYS if k not in p]
    if out:
        return out
    if not (isinstance(p["lines"], int) and not isinstance(p["lines"], bool) and 20 <= p["lines"] <= 40):
        out.append(f"{where}.lines: expected an integer 20..40")
    if not (_num(p["line_pitch_pt"]) and 12 <= p["line_pitch_pt"] <= 36):
        out.append(f"{where}.line_pitch_pt: expected 12..36")
    m = p["margins_in"]
    if not (isinstance(m, list) and len(m) == 4 and all(_num(x) and x <= 3 for x in m)):
        out.append(f"{where}.margins_in: expected [top, right, bottom, left] in inches")
    elif 11.0 - m[0] - m[2] < p["lines"] * float(p["line_pitch_pt"]) / 72.0:
        out.append(f"{where}: {p['lines']} lines at {p['line_pitch_pt']} point do not fit between the margins")
    return out


def _models(models: dict) -> list[str]:
    """Every configured model must have a known output maximum: a model the
    table does not know would silently get a small ceiling and truncate."""
    from ..demand.gapaudit import OUTPUT_MAX

    return [
        f"models.{stage}: {m!r} has no known output maximum (jobs.demand.gapaudit.OUTPUT_MAX)"
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
    bases = data["format"].get("firm_bases") or {}
    refs += [(f"format.firm_bases.{c}", b["path"]) for c, b in bases.items()]
    return refs + evidence_config.referenced_paths(data)


def _format(fmt: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if len(fmt["mediation_brief_sections"]) != MEDIATION_SECTIONS:
        out.append(f"format.mediation_brief_sections: expected exactly {MEDIATION_SECTIONS} section titles")
    if fmt["discovery_label_style"] not in LABEL_STYLES:
        out.append(f"format.discovery_label_style: expected one of {list(LABEL_STYLES)}")
    if not 8 <= float(fmt["size_pt"]) <= 16:
        out.append("format.size_pt: expected 8..16")
    out += _layout(fmt.get("layout") or {}) + (_pleading(fmt["pleading"]) if "pleading" in fmt else [])
    if "limits" in fmt:
        out += page_limits.validate(fmt["limits"], CLASSES, fmt["mediation_brief_sections"])
    bad = [c for c in fmt.get("pleading_classes") or [] if c not in CLASSES]
    if bad or (fmt.get("pleading_classes") and "pleading" not in fmt):
        out.append(f"format.pleading_classes: classes from {list(CLASSES)}, and format.pleading authored")
    out += _firm_bases(fmt.get("firm_bases") or {})
    return out


def _firm_bases(bases: dict[str, Any]) -> list[str]:
    out = []
    for c, b in bases.items():
        where = f"format.firm_bases.{c}"
        contract = b.get("contract", MARKED_BASE) if isinstance(b, dict) else MARKED_BASE
        if contract not in BASE_CONTRACTS:
            out.append(f"{where}.contract: expected one of {list(BASE_CONTRACTS)}")
        elif contract == MARKED_BASE and c not in FIRM_BASE_CLASSES:
            out.append(f"{where}: only {list(FIRM_BASE_CLASSES)} are made in a firm's marked file")
        elif contract == STYLES_BASE and c not in STYLES_BASE_CLASSES:
            out.append(f"{where}: a styles file is for one of {list(STYLES_BASE_CLASSES)}")
        elif not isinstance(b, dict) or not _type_ok(b.get("path"), "path"):
            out.append(f"{where}: expected a path")
        elif not set(SOURCE_KEYS) <= set(b.get("source") or {}) <= {*SOURCE_KEYS, *ACCEPTANCE_KEYS}:
            out.append(
                f"{where}.source: expected {list(SOURCE_KEYS)}, and for an accepted save {list(ACCEPTANCE_KEYS)}"
            )
        elif not all(_type_ok(v, "str") or (k == "name" and v == "") for k, v in b["source"].items()):
            # name may be "": a filed document's name carries the client's (firmbase/hunt.py)
            out.append(f"{where}.source: every value is a string")
        elif bool(b["source"].get("version")) != bool(b["source"].get("accepted")):
            out.append(f"{where}.source: an accepted save records both {list(ACCEPTANCE_KEYS)}")
    return out


def _semantics(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("per_job_cap_usd", "monthly_budget_usd", "usd_per_million_chars", "usd_per_scanned_page"):
        if float(data["budget"][key]) <= 0:
            out.append(f"budget.{key}: must be > 0")
    if float(data["budget"]["usd_drafting_fixed"]) < 0:
        out.append("budget.usd_drafting_fixed: must be >= 0")
    for key in ("repair_rounds", "repair_cap_usd"):
        if key in data["budget"] and float(data["budget"][key]) <= 0:
            out.append(f"budget.{key}: must be > 0")
    if "repair_rounds" in data["budget"] and float(data["budget"]["repair_rounds"]) != int(
        data["budget"]["repair_rounds"]
    ):
        out.append("budget.repair_rounds: a whole number")
    out += _format(data["format"])
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


def _store_pins(data: dict[str, Any]) -> list[str]:
    """The source check's authority store has pinned files (verify/config.py)."""
    if verify_config.KEY not in data:
        return []
    return [
        e for e in verify_config.validate(data[verify_config.KEY], CLASSES, data["inputs"]) if "no pinned file" in e
    ]


def validate(data: Any) -> list[str]:
    """Every problem as ``section.key: message``; empty means valid."""
    return _shape(data) or _semantics(data) or _store_pins(data)


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

    def _pinned_base(self, cls: str, contract: str) -> tuple[Path, dict[str, str]] | None:
        b = (self.data["format"].get("firm_bases") or {}).get(cls)
        if not b or b.get("contract", MARKED_BASE) != contract:
            return None
        return self.root / b["path"], dict(b["source"])

    def firm_base(self, cls: str) -> tuple[Path, dict[str, str]] | None:
        """(the firm's MARKED Word file this class is made in, where it came from);
        None: no marked file (the config's layout, or a styles file: ``styles_base``)."""
        return self._pinned_base(cls, MARKED_BASE)

    def styles_base(self, cls: str) -> tuple[Path, dict[str, str]] | None:
        """(the firm's pinned STYLES file this class is rendered on, where it came
        from); None when the class pins none."""
        return self._pinned_base(cls, STYLES_BASE)

    def attachment(self, key: str) -> str:
        return self.read(self.data["attachments"][key])

    @property
    def rehearsal_matters(self) -> tuple[str, ...]:
        return tuple((self.data.get("delivery") or {}).get("rehearsal_matters") or ())

    @property
    def attorneys(self) -> list[dict[str, Any]]:
        """The firm's authored attorney blocks (``attorneys.py``); empty when none."""
        return list(self.data.get(attorneys_mod.KEY) or [])

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
    # Resolved once: the live path is a symlink the applier swaps (jobs/firm_inputs.py).
    return pin(explicit or os.environ.get(ENV_DIR) or DEFAULT_DIR)


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
    """``python -m jobs.drafting.firm <dir>``: the provisioning validator."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m jobs.drafting.firm <drafting inputs dir>", file=sys.stderr)
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
