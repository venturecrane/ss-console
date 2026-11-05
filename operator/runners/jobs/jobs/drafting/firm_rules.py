"""The firm's installed layout rules, laid over its layout at job start.

WHY (2026-11-05). An attorney's layout rule ("two blank lines before the court
name") was installed to the seat's spec store and nothing that renders the
firm's documents read it: the layout came from ``drafting-firm.yaml`` alone,
which only an SMD release changes. The seat's spec applier now compiles a rule
whose words map to one of ``revise/checklist.LAYOUT_KEYS`` into
``classes/work_product/layout.json`` (``shared/layout_rules.py`` in the
overlay); this module reads that file and puts each rule into effect:

* every class: the rule's values laid over ``format.layout.<class>`` before
  anything renders, so the render and ``format_check`` read the same values;
* a class made in the firm's own Word file (``brief_base``): the rendered file
  is edited in place with revise's layout editor (``revise/inplace.py``), and
  the edit is proven against the leaves each key names (``xmlproof``), so the
  base's every other part is as rendered. A key the base owns (``BASE_OWNS``:
  the chart's look) has no in-place applier there, and is recorded unapplied.

Each rule a job applies, or could not, is one ``RULE_READ`` row in the job's
``rule-reads.jsonl``: the job's own ledger, beside its usage ledger. The rules
are read ONCE per job and frozen in its state, so a resumed job renders and
checks by the rules it started with.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from ..firmbase.base import package
from .revise.checklist import _layout_problems

ENV_DIR = "JOBS_SPEC_DIR"
#: The spec applier's install dir (``spec_applier/__main__.py``).
DEFAULT_DIR = "/var/lib/smd-config/specs"
#: The output class whose rules this lane reads (the overlay's
#: ``shared.layout_rules.LAYOUT_RULE_CLASSES``; parity-tested both sides).
RULE_CLASS = "work_product"
LAYOUT_REL = f"classes/{RULE_CLASS}/layout.json"
SCHEMA_VERSION = 1
LEDGER = "rule-reads.jsonl"
STATE_KEY = "firm_rules"


def read(spec_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """The installed compiled rules, oldest first; ``[]`` when there are none.

    The file must match the digest root wrote into the manifest beside it; a
    torn or unlisted file applies nothing (a rule half-read is a rule guessed),
    and every rule is checked again by the lane's own validator.
    """
    root = Path(spec_dir or os.environ.get(ENV_DIR) or DEFAULT_DIR)
    path = root / LAYOUT_REL
    try:
        blob = path.read_bytes()
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    entry = (manifest.get("layouts") or {}).get(LAYOUT_REL) if isinstance(manifest, dict) else None
    if not isinstance(entry, dict) or entry.get("sha256") != hashlib.sha256(blob).hexdigest():
        return []
    try:
        doc = json.loads(blob.decode("utf-8"))
    except ValueError:
        return []
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        return []
    out = []
    for r in doc.get("rules") or []:
        if isinstance(r, dict) and isinstance(r.get("rule_id"), str) and not _layout_problems(r.get("layout")):
            out.append(
                {"rule_id": r["rule_id"], "layout": r["layout"], "doc_classes": list(r.get("doc_classes") or [])}
            )
    return out


def for_class(rules: list[dict[str, Any]], cls: str, on_base: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(layout, rows)``: the values this class takes, later rules winning, and
    one row per (rule, key) saying whether it was applied."""
    from .brief_base import BASE_OWNS

    layout: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for r in rules:
        if r["doc_classes"] and cls not in r["doc_classes"]:
            continue
        for key, value in r["layout"].items():
            row = {"rule_id": r["rule_id"], "key": key, "value": value, "applied": True}
            if on_base and key in BASE_OWNS:
                row.update(applied=False, reason="the firm's own Word file sets this; no in-place applier")
            else:
                layout[key] = value
            rows.append(row)
    return layout, rows


def start(run: Any) -> dict[str, Any]:
    """At job start: the rules for ``run.cls``, laid over the firm's layout and
    recorded; the layout returned is what a base-rendered file is edited to."""
    st = run._state()
    frozen = st.get(STATE_KEY)
    if not isinstance(frozen, dict):
        on_base = run.firm.firm_base(run.cls) is not None
        layout, rows = for_class(read(), run.cls, on_base)
        frozen = {"layout": layout, "rows": rows}
        run._put(STATE_KEY, frozen)
        _record(run.data, run.job.job_id, run.cls, rows)
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
