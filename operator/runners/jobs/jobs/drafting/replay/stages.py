"""The response path's stages, looked up by name and run on a saved matter.

``EXPECTED`` names the real function each replay stage calls, at its real
signature (``real.py`` makes each call the way the lane makes it); a function
missing from the tree is reported as the module and function the stage needs
instead of failing on an ImportError:

* ``find``: ``served_find.find(data, job_dir, request, reader, *, clients,
  other_side)``, writing ``served.json`` and ``found-report.json`` in
  ``job_dir`` (the replay's work dir). Its model calls go through the reader's
  ``ask``, which is the recorded doorway.
* ``slice``: ``served.numbered(request_text) -> {n: text}``, the requests of a
  served set by number (what the review checks a restated request against).
* ``scope``: ``response_scope.scope(data, firm_data, plan, providers) -> dict``
  on a copy of the saved data; each unit's pack is ``response_scope.pack``.
* ``cost``: ``response_cost.project(rows, cats, plan, firm_data, pricing, *,
  stored_pages, request_counts) -> dict``, ``stored_pages`` from
  ``response_cost.stored_pages`` (the seat's page store, read-only).
* ``form-read``: ``forms.read_stage.pixel_read(pdf) -> {"checked",
  "unregistered", "definition_boxes", "form", "form_revision",
  "unreadable_pages"}``, the pixel checkbox read alone (no model).
* ``exhibits``: ``exhibit_plan.place(files, table, walled) -> {"exhibits",
  "left_out", "walled"}`` over the scope step's record; with a production
  response, ``exhibit_index.index_lines`` gives each index line's request numbers.

A produced body's per-response kind is read only from a JSON body (the answer
stage's own output); a text or .docx body's kinds are reported unread.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: stage -> (module, function).
EXPECTED: dict[str, tuple[str, str]] = {
    "find": ("jobs.drafting.served_find", "find"),
    "slice": ("jobs.drafting.served", "numbered"),
    "scope": ("jobs.drafting.response_scope", "scope"),
    "cost": ("jobs.drafting.response_cost", "project"),
    "form-read": ("jobs.drafting.forms.read_stage", "pixel_read"),
    "exhibits": ("jobs.drafting.exhibit_plan", "place"),
}


class MissingModule(Exception):
    pass


def entry(stage: str) -> Callable[..., Any]:
    mod_name, fn_name = EXPECTED[stage]
    try:
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import - mod_name is only ever a value of the module-constant EXPECTED table above; no input reaches it.
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as exc:
        raise MissingModule(f"needs {mod_name}.{fn_name}; not in this tree: {exc}") from exc
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise MissingModule(f"{mod_name} has no function {fn_name}")
    return fn


def optional(stage: str) -> Callable[..., Any] | None:
    try:
        return entry(stage)
    except MissingModule:
        return None


@dataclass(frozen=True)
class Matter:
    """A saved matter: the job dir and its data dir (the lane's on-disk layout)."""

    job_dir: Path
    data: Path

    @classmethod
    def at(cls, path: Path) -> "Matter":
        if (path / "data" / "manifest.json").is_file():
            return cls(path, path / "data")
        if (path / "manifest.json").is_file():
            return cls(path.parent if path.name == "data" else path, path)
        raise ValueError(f"{path}: no manifest.json here or under data/ (not a saved matter)")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def served_source(matter: Matter, work: Path) -> tuple[Any, Path]:
    """The served sets the later stages use and the directory their paths are
    relative to: the replay's find output, else the job's own served.json."""
    mine = read_json(work / "served.json")
    return (mine, work) if mine else (read_json(matter.job_dir / "served.json"), matter.job_dir)


def served_doc(matter: Matter, work: Path) -> Any:
    return served_source(matter, work)[0]


def slices(served: Any, matter: Matter) -> dict[tuple[str, int], list[str] | None]:
    """Each served set's requests 1..N, by number (``served.numbered``)."""
    out: dict[tuple[str, int], list[str] | None] = {}
    slicer = optional("slice")
    for s in served.get("sets") or [] if isinstance(served, dict) else []:
        if not isinstance(s, dict) or not isinstance(s.get("set_number"), int):
            continue
        k = (str(s.get("set_type") or ""), s["set_number"])
        text = s.get("request_text")
        if not isinstance(text, str) and isinstance(s.get("request_text_path"), str):
            p = matter.job_dir / s["request_text_path"]
            text = p.read_text(encoding="utf-8") if p.is_file() else None
        got = slicer(text) if slicer is not None and isinstance(text, str) else None
        out[k] = [str(got[n]) for n in sorted(got)] if isinstance(got, dict) else None
    return out


def _rows(matter: Matter) -> list[dict[str, Any]]:
    """The saved files' rows: ``corpus.json`` (every lane's pull, the one
    ``forms.read_stage._source_paths`` reads), then the chronology pull's
    ``raw_manifest.jsonl``."""
    corpus = read_json(matter.data / "corpus.json")
    rows = [r for r in corpus if isinstance(r, dict)] if isinstance(corpus, list) else []
    p = matter.data / "raw_manifest.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows += [row] if isinstance(row, dict) else []
    return rows


def file_by_sha(matter: Matter, sha: str) -> Path | None:
    """A saved file by its sha256, through ``corpus.json`` (else ``raw_manifest.jsonl``)."""
    if not sha:
        return None
    for row in _rows(matter):
        if str(row.get("sha256") or "").lower() != sha.lower() or not row.get("path"):
            continue
        path = Path(str(row["path"]))
        for cand in (path, matter.data / path, matter.data / "raw" / path.name):
            if cand.is_file():
                return cand
    return None
