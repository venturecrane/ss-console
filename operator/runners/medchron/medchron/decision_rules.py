"""The shared parts of the decision hooks, and the two hooks that are pure page
and folder rules: the record-control page and the unit-folder resolution.

Split out of ``decisions.py`` (2026-10-24) to keep that file under its
500-line ceiling; ``decisions`` re-exports every name, so callers and the
HOOKS tables are unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import FirmConfig
from .job import Job, Unit


@dataclass
class Decision:
    hook: str
    artifact: Path | None
    payload: Any
    holds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def held(self) -> bool:
        return bool(self.holds)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write(path: Path | None, payload: Any, *, dry_run: bool) -> None:
    """Write a hook's authored artifact atomically. Every hook that writes names
    its artifact; a hook without one (``Decision.artifact`` is None) reaching
    here is a programming error, raised rather than silently skipped."""
    if dry_run:
        return
    if path is None:
        raise ValueError("decision hook wrote with no artifact path")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.I) for p in patterns]


# ---- unit folders --------------------------------------------------------------
def _folded(name: str) -> str:
    """A folder name with case and punctuation ignored."""
    return re.sub(r"[^0-9a-z]+", "", name.casefold())


def _tops(slug_dir: Path) -> list[str]:
    folders = _read_json(slug_dir / "folders.json", [])
    return sorted({f["path"].split("/")[1] for f in folders if f.get("path", "").count("/") >= 1})


def resolve_prefix(prefix: str, tops: list[str]) -> str | None:
    """The real top-level folder a unit's ``folder_prefix`` names, as
    ``/<folder>``: the exact folder, else the ONE folder that matches with case
    and punctuation ignored (2026-10-24: a firm renamed "Alpha_Example" to
    "ALPHA EXAMPLE" and the run held). None when there is no such folder, or
    more than one, which still holds: guessing between two folders could pull
    another client's records."""
    bare = prefix.strip("/")
    if bare in tops:
        return f"/{bare}"
    hits = [t for t in tops if _folded(t) == _folded(bare)]
    return f"/{hits[0]}" if len(hits) == 1 and _folded(bare) else None


# ---- control page ------------------------------------------------------------
def control(job: Job, cfg: FirmConfig, slug_dir: Path, unit: Unit, *, dry_run: bool) -> Decision:
    """`record_control[-unit].json`: a page that is a record by construction,
    the planted control the scanned-page classifier is falsified against.

    `page_map.json` is the exhibit list with each source file's page range;
    `extracted.jsonl` carries native-text chars and pages per file. The control
    is the second page of the exhibited file with the densest native text (at
    least 800 chars a page across two or more pages): dense native text is a
    record, and the second page skips a cover sheet."""
    page_map = _read_json(slug_dir / "out" / unit.unit / "page_map.json", [])
    density: dict[str, float] = {}
    for row in _read_jsonl(slug_dir / "extracted.jsonl"):
        pages, chars = int(row.get("pages") or 0), int(row.get("chars") or 0)
        if pages >= 1 and chars:
            density[str(row.get("name") or "")] = chars / pages
    # (dense, chars per page, exhibit, page): the dense rule first, then the
    # best text page anywhere in the exhibits.
    best: tuple[bool, float, int, int] | None = None
    for ex in page_map if isinstance(page_map, list) else []:
        for f in ex.get("files") or []:
            name = str(f.get("file") or "")
            dens = density.get(name.rsplit(".", 1)[0]) or density.get(name)
            pages = int(f.get("pages") or 0)
            if not dens or pages < 1:
                continue
            start = int(f.get("start_page") or 1)
            cand = (pages >= 2 and dens >= 800, dens, int(ex.get("exhibit") or 0), start + 1 if pages >= 2 else start)
            if best is None or cand[:2] > best[:2]:
                best = cand
    d = Decision(
        "control", slug_dir / (f"record_control-{unit.unit}.json" if job.joint else "record_control.json"), None
    )
    if best is None:
        # With no native text anywhere in the exhibits (an all-fax matter) no
        # page is a record by construction, so the scanned-page classifier has
        # nothing to be falsified against. Until 2026-10-24 that held the run;
        # it is a legitimate record set, so the classifier is skipped instead
        # and the strip keeps every scanned page (keeping a page is harmless).
        why = "no exhibited file carries native text, so no page is a record by construction"
        d.payload = {"skip": True, "_decided": {"by": "medchron.decisions.control", "reason": why}}
        d.notes.append(f"record control: {why}; the scanned-page classifier is skipped and scanned pages are kept")
        _write(d.artifact, d.payload, dry_run=dry_run)
        return d
    if not best[0]:
        # Before 2026-10-23 a matter with no file of 800+ chars a page over
        # two pages held here for SMD; the best text page is still a record
        # page the classifier must call a record, so it serves.
        d.notes.append(
            f"no exhibited file has dense native text; the record control is the best text page "
            f"({round(best[1])} chars a page)"
        )
    d.payload = {
        "exhibit": best[2],
        "page": best[3],
        "_decided": {"by": "medchron.decisions.control", "chars_per_page": round(best[1]), "dense": best[0]},
    }
    _write(d.artifact, d.payload, dry_run=dry_run)
    return d
