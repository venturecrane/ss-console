"""The firm's installed layout rules, laid over its layout at job start.

WHY (2026-11-05). An attorney's layout rule ("two blank lines before the court
name") was installed to the seat's spec store and nothing that renders the
firm's documents read it: the layout came from ``drafting-firm.yaml`` alone,
which only an SMD release changes. The seat's spec applier now compiles the
clauses of a rule whose words map to ``revise/checklist.LAYOUT_KEYS`` into
``classes/work_product/layout.json`` (``shared/layout_rules.py`` in the
overlay); this module reads that file through ``layout_store``, the reader the
install letter uses, and puts each clause into effect:

* every class: the values laid over ``format.layout.<class>`` before anything
  renders, so the render and ``format_check`` read the same values;
* a class made in the firm's own Word file (``brief_base``): the rendered file
  is edited in place with revise's layout editor (``revise/inplace.py``), and
  the edit is proven against the leaves each key names (``xmlproof``), so the
  base's every other part is as rendered. A key the base owns (``BASE_OWNS``:
  the chart's look) has no in-place applier there, and is recorded unapplied;
* a "more space" clause (``relative``, revise's ``params.relative``): the
  effective value plus one step, capped by the key's range. Resolved once per
  rule and pinned on the volume, because the firm's layout moves under it (an
  SMD release, an adopted base) and it must not grow; the note with the document
  states the value. A later layout already past the pin wins;
* a class on one line pitch (a base, or pleading paper): ``line_spacing`` moves
  paragraphs off the pitch and the format check refuses the whole document, so
  double spacing becomes the pitch keys and anything else is refused here.

BEFORE ANY PAID DRAFT every value is tried: a synthetic brief is laid out with
and without the rules on this job's own base and paper and read by the real
format check (:func:`probe_new_fails`), and a value that adds a finding is
refused, recorded, stated in the note and paged. A job never pays to discover a
rule cannot be laid.

Each rule a job applies, or could not, is one ``RULE_READ`` row in the job's
``rule-reads.jsonl``: the job's own ledger, beside its usage ledger. The rules
are read ONCE per job and frozen in its state, so a resumed job renders and
checks by the rules it started with. An UNUSABLE store is not an answer and is
never frozen: it writes a ``RULE_STORE_UNUSABLE`` row, logs, the daemon pages
(``alert_unusable``), and a resume after the repair reads the rules.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from ..firmbase.base import package
from . import layout_store
from .revise.checklist import LAYOUT_KEYS, _layout_problems

logger = logging.getLogger(__name__)

ENV_DIR = "JOBS_SPEC_DIR"
#: The spec applier's install dir (``spec_applier/__main__.py``).
DEFAULT_DIR = "/var/lib/smd-config/specs"
#: The output class whose rules this lane reads (the overlay's
#: ``shared.layout_rules.LAYOUT_RULE_CLASSES``; parity-tested both sides).
RULE_CLASS = "work_product"
LAYOUT_REL = f"classes/{RULE_CLASS}/layout.json"
SCHEMA_VERSION = layout_store.SCHEMA_VERSION
RELATIVE_STEP = 1
LEDGER = "rule-reads.jsonl"
STATE_KEY = "firm_rules"
#: Where a resolved "more space" stays on the seat's volume (entrypoint-drafting.sh).
PINS_ENV = "JOBS_DRAFTING_RULE_PINS"
PINS_NAME = "pins.json"
#: The two proof alerts that put a rule fault on SMD's desk (``proof/gate.py``).
UNUSABLE_CODE = "rule_store_unusable"
REFUSED_CODE = "rule_value_refused"
#: The pitch "double spaced" means when the firm authored no pleading paper.
DEFAULT_PITCH_PT = 24.0

_LABELS = {
    "front_attorney_gap_lines": "blank lines before the attorney line",
    "front_court_gap_lines": "blank lines before the court's name",
    "front_caption_gap_lines": "blank lines before the caption",
    "signature_gap_lines": "blank lines above the date in the signature block",
    "table_font_pt": "chart font size in points",
}
_SPACING_KEYS = ("line_spacing", "heading_line_spacing")


def _problems(key: str, value: Any) -> list[str]:
    return _layout_problems({key: value})


def _spec_dir(spec_dir: str | Path | None = None) -> Path:
    return Path(spec_dir or os.environ.get(ENV_DIR) or DEFAULT_DIR)


def read(spec_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """The installed rules, oldest first; ``[]`` when there are none to trust."""
    return layout_store.read(_spec_dir(spec_dir), _problems).rules


def _label(key: str) -> str:
    return _LABELS.get(key, key.replace("_", " "))


def _pitch_keys(key: str, pitch: float) -> dict[str, float]:
    if key == "line_spacing":
        return {"line_spacing_pt": pitch}
    return {"heading_line_spacing_pt": pitch, "heading_space_before_pt": 0.0, "heading_space_after_pt": 0.0}


def _absolute(
    rule_id: str, key: str, value: Any, on_base: bool, one_pitch: bool, pitch: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(values to lay, rows)`` for one absolute value on this class."""
    from .brief_base import BASE_OWNS

    row: dict[str, Any] = {"rule_id": rule_id, "key": key, "value": value, "applied": True}
    if key in _SPACING_KEYS and one_pitch:
        if value != 2.0:
            row.update(applied=False, refused=True, reason="this document holds to one line pitch")
            return {}, [row]
        mapped = _pitch_keys(key, pitch)
        return mapped, [{"rule_id": rule_id, "key": k, "value": v, "applied": True} for k, v in mapped.items()]
    if on_base and key in BASE_OWNS:
        row.update(applied=False, reason="the firm's own Word file sets this; no in-place applier")
        return {}, [row]
    return {key: value}, [row]


def _relative(
    rule_id: str,
    cls: str,
    key: str,
    step: int,
    clause: dict[str, Any],
    on_base: bool,
    current: Any,
    pins: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(values to lay, row)`` for one "more" ask: once, from its pinned baseline."""
    from .brief_base import BASE_OWNS

    row: dict[str, Any] = {"rule_id": rule_id, "key": key, "relative": step, "applied": False}
    if clause.get("carried"):
        row["reason"] = "given by hand before relative rules compiled; already in the firm's layout"
        return {}, row
    if on_base and key in BASE_OWNS:
        row["reason"] = "the firm's own Word file sets this; no in-place applier"
        return {}, row
    have = float(current or 0)
    pin_key = f"{rule_id}:{cls}:{key}"
    pin = pins.get(pin_key)
    if isinstance(pin, dict):
        baseline, value = pin.get("baseline"), pin.get("value")
    else:
        hi = LAYOUT_KEYS[key][2]
        baseline, value = current or 0, min(have + step, float(hi))
        if LAYOUT_KEYS[key][0] is int:
            baseline, value = int(have), int(value)
    row.update(baseline=baseline, value=value)
    if float(value) <= have:
        why = "at the limit of what this setting allows" if not pin else "the firm's layout already goes past it"
        row["reason"] = why
        return {}, row
    if _layout_problems({key: value}):
        row.update(refused=True, reason="the resolved value is out of range")
        return {}, row
    pins.setdefault(pin_key, {"baseline": baseline, "value": value, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    row["applied"] = True
    return {key: value}, row


def _notes(rows: list[dict[str, Any]]) -> list[str]:
    """What the note with the document says about the rules: the value a "more"
    ask resolved to, and any rule that could not be laid."""
    out = []
    for r in rows:
        if r.get("relative") and r["applied"]:
            out.append(
                f"Your layout rule {r['rule_id']} asked for more {_label(r['key'])}: "
                f"we used {r['value']:g}, up from {r['baseline']:g}. If you want more, say so."
            )
        elif r.get("refused"):
            out.append(
                f"Your layout rule {r['rule_id']}: we could not apply the {_label(r['key'])} to this "
                "document, because the firm's format check would refuse it. SMD has been told."
            )
    return out


def for_class(
    rules: list[dict[str, Any]],
    cls: str,
    on_base: bool,
    *,
    paper: str = "plain",
    pitch: float | None = None,
    effective: dict[str, Any] | None = None,
    pins: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """``(layout, rows, notes)``: the values this class takes, later rules winning,
    one row per (rule, key) saying whether it was applied, and what the note says.
    ``effective``: the firm's layout for this class, which a relative ask steps
    from. ``pins``: the resolved relative asks, updated in place."""
    layout: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    one_pitch = on_base or paper == "pleading"
    effective, pins = dict(effective or {}), pins if pins is not None else {}
    for r in rules:
        if r["doc_classes"] and cls not in r["doc_classes"]:
            continue
        for clause in r["compiled"]:
            for key, value in clause["layout"].items():
                got, made = _absolute(r["rule_id"], key, value, on_base, one_pitch, pitch or DEFAULT_PITCH_PT)
                layout.update(got)
                rows += made
            for key, step in clause["relative"].items():
                current = layout.get(key, effective.get(key))
                got, row = _relative(r["rule_id"], cls, key, step, clause, on_base, current, pins)
                layout.update(got)
                rows.append(row)
    return layout, rows, _notes(rows)


# ---- the probe: a value the check would refuse never reaches a paid draft ---------------
#: A fictional brief, whole enough for a base's slots and the renderer. Its words
#: are never delivered: the probe's file lives and dies in a temp dir.
PROBE_MD = (
    "Alpha Example, Esq.\nExample Firm, LLP\nOne Example Road\nExampleville, California\n"
    "Telephone: (000) 000-0000\n\n"
    "Attorneys for Plaintiff\nGAMMA EXAMPLE\n\n"
    "**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n\n**COUNTY OF EXAMPLE**\n\n"
    "| | |\n| --- | --- |\n"
    "| GAMMA EXAMPLE, | Case No. CV-0001 |\n"
    "| Plaintiff, | |\n"
    "| v. | **PLAINTIFF'S MEDIATION BRIEF** |\n"
    "| DELTA EXAMPLE, | Mediator: Epsilon Example |\n"
    "| Defendant. | Mediation Date: 1/2/27 |\n"
    "| | Time: 9:00 am |\n"
    "| | Trial Date: 3/4/27 |\n\n"
    "# I. INTRODUCTION\n\nA first paragraph of the example.\n\nA second paragraph of the example.\n\n"
    "| Category | Billed | Howell Amount |\n| --- | --- | --- |\n| Past | $4.00 | $3.00 |\n"
    "| TOTAL | $4.00 | $3.00 |\n\n"
    "| Provider | Dates | Billed | Howell | Status |\n| --- | --- | --- | --- | --- |\n"
    "| Clinic | 1/1/26 | $4.00 | $3.00 | Paid |\n| TOTAL | | $4.00 | $3.00 | |\n\n"
    "# II. CONCLUSION\n\nThe end of the example.\n\n"
    "Dated: __________\n\n**EXAMPLE FIRM, LLP**\n\nBy: ______________________________\n"
    "Alpha Example, Esq.\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n"
)


def _probe_fails(
    where: Path, name: str, cls: str, fmt: dict[str, Any], paper: str, held: Any, layout: dict[str, Any]
) -> list[str]:
    from . import brief_base, format_check

    tried = copy.deepcopy(fmt)
    tried.setdefault("layout", {}).setdefault(cls, {}).update(layout)
    path, _report, _laid, checked = brief_base.lay_out(PROBE_MD, where / name, cls, tried, paper, held, layout)
    return list(format_check.check(path, cls, checked, "", None, paper).fails)


def _shape(finding: str) -> str:
    return re.sub(r"\d+", "#", finding)


def probe_new_fails(
    where: Path, cls: str, fmt: dict[str, Any], paper: str, held: Any, layout: dict[str, Any]
) -> list[str]:
    """The format-check findings ``layout`` adds on this job's own base and paper.
    Compared with the same brief unruled, so a class's content checks, which a
    fictional brief cannot satisfy, say nothing about the rule. Raises when the
    brief cannot be laid out at all: the caller treats that as unproven."""
    with tempfile.TemporaryDirectory(dir=where) as tmp:
        plain = {_shape(f) for f in _probe_fails(Path(tmp), "plain.docx", cls, fmt, paper, held, {})}
        ruled = _probe_fails(Path(tmp), "ruled.docx", cls, fmt, paper, held, layout)
    return [f for f in ruled if _shape(f) not in plain]


def _refuse(rows: list[dict[str, Any]], layout: dict[str, Any], pins: dict[str, Any], key: str, why: str) -> None:
    layout.pop(key, None)
    for r in rows:
        if r["key"] == key and r["applied"]:
            r.update(applied=False, refused=True, reason=why)
            for pin_key in [k for k in pins if k.endswith(f":{key}") and k.startswith(f"{r['rule_id']}:")]:
                pins.pop(pin_key)


def _proven(
    run: Any, layout: dict[str, Any], rows: list[dict[str, Any]], pins: dict[str, Any], paper: str, held: Any
) -> dict[str, Any]:
    """``layout`` less what the format check would refuse; ``rows`` marked."""
    if not layout:
        return layout
    fmt, cls, where = run.firm.data["format"], run.cls, Path(run.data)

    def adds(values: dict[str, Any]) -> bool:
        try:
            return bool(probe_new_fails(where, cls, fmt, paper, held, values))
        except Exception as exc:  # noqa: BLE001 - a rule never costs a job; it is refused
            logger.warning("firm rules: the layout could not be tried (%s)", type(exc).__name__)
            return True

    if not adds(layout):
        return layout
    for key in list(layout):
        if adds({key: layout[key]}):
            _refuse(rows, layout, pins, key, "the firm's format check would refuse it")
    if layout and adds(layout):
        for key in list(layout):
            _refuse(rows, layout, pins, key, "the firm's format check would refuse these together")
    return layout


# ---- pins ---------------------------------------------------------------------------------
def _pins_path() -> Path | None:
    root = os.environ.get(PINS_ENV) or ""
    return Path(root) / PINS_NAME if root else None


def _load_pins() -> dict[str, Any]:
    path = _pins_path()
    if path is None:
        return {}
    try:
        got = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save_pins(pins: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    """Atomic, and only the rules still installed: a pin outlives its rule nowhere."""
    path = _pins_path()
    if path is None:
        return
    live = {f"{r['rule_id']}:" for r in rules}
    kept = {k: v for k, v in pins.items() if any(k.startswith(x) for x in live)}
    part = path.with_name(f".{path.name}.tmp")
    try:
        part.write_text(json.dumps(kept, indent=1, sort_keys=True), encoding="utf-8")
        part.replace(path)
    except OSError as exc:
        logger.error("firm rules: could not write the relative pins (%s)", type(exc).__name__)


# ---- job start ----------------------------------------------------------------------------
def _effective(run: Any, held: Any) -> dict[str, Any]:
    """The value each key has for this class before any rule: the firm's layout,
    else its own Word file for a gap the config does not name, else its font size
    for the chart, which starts from the body."""
    fmt = run.firm.data["format"]
    out = dict((fmt.get("layout") or {}).get(run.cls) or {})
    out.setdefault("table_font_pt", fmt.get("size_pt"))
    if held is not None:
        from .revise import filed

        for key in LAYOUT_KEYS:
            if key.endswith("_gap_lines") and key not in out:
                try:
                    got = filed.value(Path(held[0]), key)
                except (OSError, ValueError, KeyError):
                    continue
                if got is not None:
                    out[key] = got
    return out


def _pitch(fmt: dict[str, Any]) -> float:
    from . import pleading

    spec = pleading.spec_for(fmt)
    return float(spec.pitch_pt) if spec is not None else DEFAULT_PITCH_PT


def start(run: Any) -> dict[str, Any]:
    """At job start: the rules for ``run.cls``, proven, laid over the firm's layout
    and recorded; the layout returned is what a base-rendered file is edited to."""
    st = run._state()
    frozen = st.get(STATE_KEY)
    if not isinstance(frozen, dict):
        store = layout_store.read(_spec_dir(), _problems)
        if store.status == layout_store.UNUSABLE:
            logger.error("firm rules: the installed layout store is unusable (%s)", store.reason)
            _unusable_row(run.data, run.job.job_id, run.cls, store.reason)
            return {}
        held = run.firm.firm_base(run.cls)
        fmt, paper, pins = run.firm.data["format"], run.paper(), _load_pins()
        layout, rows, _n = for_class(
            store.rules,
            run.cls,
            held is not None,
            paper=paper,
            pitch=_pitch(fmt),
            effective=_effective(run, held),
            pins=pins,
        )
        layout = _proven(run, layout, rows, pins, paper, held)
        _save_pins(pins, store.rules)
        frozen = {"layout": layout, "rows": rows, "notes": _notes(rows)}
        run._put(STATE_KEY, frozen)
        _record(run.data, run.job.job_id, run.cls, rows)
    run.rule_notes = list(frozen.get("notes") or [])
    layout = dict(frozen.get("layout") or {})
    if layout:
        fmt = run.firm.data["format"]
        fmt.setdefault("layout", {}).setdefault(run.cls, {}).update(layout)
    return layout


def _record(data: Path, job_id: str, cls: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    with (data / LEDGER).open("a", encoding="utf-8") as fh:
        for row in rows:
            rec = {"action_type": "RULE_READ", "ts": ts, "job_id": job_id, "lane": "drafting", "doc_class": cls, **row}
            fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _unusable_row(data: Path, job_id: str, cls: str, reason: str) -> None:
    rec = {
        "action_type": "RULE_STORE_UNUSABLE",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "job_id": job_id,
        "lane": "drafting",
        "doc_class": cls,
        "reason": reason,
    }
    with (data / LEDGER).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ---- the daemon's half: the child cannot page, so root reads and pages ------------------
def alert_unusable(d: Any, job_id: str) -> None:
    """Before the child runs: a present-but-unusable store goes on SMD's desk."""
    from ..proof import watch

    store = layout_store.read(_spec_dir(), _problems)
    if store.status == layout_store.UNUSABLE:
        watch.alert(d, job_id, UNUSABLE_CODE, store.reason)


def alert_refused(d: Any, job_id: str) -> None:
    """After the child runs: a value the job refused, by rule and key, ids only."""
    from ..proof import watch

    try:
        lines = (d.job_dir(job_id) / "data" / LEDGER).read_text("utf-8").splitlines()
    except (OSError, ValueError):
        return
    refused = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("refused"):
            refused.append(f"{row.get('rule_id')}:{row.get('key')}")
    if refused:
        watch.alert(d, job_id, REFUSED_CODE, ",".join(refused))


def onto_base(path: Path, layout: dict[str, Any]) -> list[str]:
    """Edit the base-rendered file at ``path`` to ``layout`` in place; the
    problems if the edit touched more than its keys name (nothing is written
    then), else ``[]``."""
    if not layout:
        return []
    from .revise import inplace, xmlproof

    rendered = path.read_bytes()
    ed = inplace.open_filed(path)
    before = inplace.snapshot(ed)
    inplace.edit_layout(ed, layout)
    # Every part but the document as rendered, byte for byte: python-docx's own
    # save rewrites the package's parts, which the proof would rightly refuse.
    out = package(rendered, ed.doc.element)
    problems = xmlproof.prove_body(ed, before, rendered, out)
    problems += [f"{name} changed, and no rule names it" for name in xmlproof.parts_differ(rendered, out)]
    if not problems:
        part = path.with_name(f".{path.name}.rules.tmp")
        part.write_bytes(out)
        part.replace(path)
    return problems
