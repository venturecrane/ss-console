"""The response path's stages, imported lazily and run on a saved matter.

The modules below arrive on separate branches (design section J, PR1-PR4);
this harness was built before they were combined, so it names exactly what it
calls (``EXPECTED``) and, when one is absent, reports the module and function
it needs instead of failing on an ImportError. Each entry point is called with
the positional arguments shown and only those keyword arguments its signature
accepts (``doorway``, ``out_dir``, ``log``, ``served``, ``data_dir``):

* ``find(data_dir, out_dir, *, doorway, log)``: writes ``out_dir/served.json``
  (today's schema, ``served.py``, plus the form keys) and
  ``out_dir/found-report.json``. Its model calls go through ``doorway``.
* ``slice_requests(request_text, set_type) -> list[str]``: requests 1..N of a
  set, used when a served set carries no ``requests`` list of its own.
* ``scope(data_dir, served, *, out_dir, log) -> dict``: ``{"categories":
  {name: {files, pages, scan_pages, chars}}, "packs": {unit: {chars, cap,
  left_out}}, "unread": [...]}``; None means read ``out_dir/scope.json``.
* ``project(scope, *, served, data_dir) -> dict``: ``{"dollars", "minutes",
  "lines": [{"item", "dollars", "minutes"}]}``.
* ``pixel_read(pdf_path) -> dict``: ``{"checked": [numbers], "unregistered":
  [numbers], "form", "form_revision"}``, the pixel checkbox read alone (no
  model).
* ``kind_of(response_text) -> str | None``: one response's kind (``key.KINDS``).
"""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: stage -> (module, function, the branch it comes from).
EXPECTED: dict[str, tuple[str, str, str]] = {
    "find": ("jobs.drafting.served_find", "find", "feat/served-find"),
    "slice": ("jobs.drafting.served_find", "slice_requests", "feat/served-find"),
    "scope": ("jobs.drafting.response_scope", "scope", "feat/response-scope-cost"),
    "cost": ("jobs.drafting.response_cost", "project", "feat/response-scope-cost"),
    "form-read": ("jobs.drafting.forms.read_stage", "pixel_read", "feat/forms-foundation-landed"),
    "kind": ("jobs.drafting.forms.answer_stage", "kind_of", "feat/form-answers"),
}


class MissingModule(Exception):
    pass


def entry(stage: str) -> Callable[..., Any]:
    mod_name, fn_name, branch = EXPECTED[stage]
    try:
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import - mod_name is only ever a value of the module-constant EXPECTED table above; no input reaches it.
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as exc:
        raise MissingModule(f"needs {mod_name}.{fn_name} (branch {branch}); not in this tree: {exc}") from exc
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise MissingModule(f"{mod_name} has no function {fn_name} (expected from branch {branch})")
    return fn


def optional(stage: str) -> Callable[..., Any] | None:
    try:
        return entry(stage)
    except MissingModule:
        return None


def call(fn: Callable[..., Any], *args: Any, **optional_kw: Any) -> Any:
    """``fn(*args, **those of optional_kw its signature accepts)``."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **optional_kw)
    filled = set(list(params)[: len(args)])  # a name the positionals already gave is never passed twice
    optional_kw = {k: v for k, v in optional_kw.items() if k not in filled}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **optional_kw)
    return fn(*args, **{k: v for k, v in optional_kw.items() if k in params})


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


def served_doc(matter: Matter, work: Path) -> Any:
    """The served sets the later stages use: the replay's find output, else the job's own."""
    return read_json(work / "served.json") or read_json(matter.job_dir / "served.json")


def slices(served: Any, matter: Matter) -> dict[tuple[str, int], list[str] | None]:
    """Each served set's requests 1..N: its ``requests`` list, else the finder's slicer."""
    out: dict[tuple[str, int], list[str] | None] = {}
    slicer = optional("slice")
    for s in served.get("sets") or [] if isinstance(served, dict) else []:
        if not isinstance(s, dict) or not isinstance(s.get("set_number"), int):
            continue
        k = (str(s.get("set_type") or ""), s["set_number"])
        reqs = s.get("requests")
        if isinstance(reqs, list):
            out[k] = [r.get("text", "") if isinstance(r, dict) else str(r) for r in reqs]
            continue
        text = s.get("request_text")
        if not isinstance(text, str) and isinstance(s.get("request_text_path"), str):
            p = matter.job_dir / s["request_text_path"]
            text = p.read_text(encoding="utf-8") if p.is_file() else None
        out[k] = [str(x) for x in slicer(text, k[0])] if slicer is not None and isinstance(text, str) else None
    return out


def file_by_sha(matter: Matter, sha: str) -> Path | None:
    """A saved file by its sha256, from the pull's ``raw_manifest.jsonl``."""
    p = matter.data / "raw_manifest.jsonl"
    if not sha or not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or str(row.get("sha256") or "").lower() != sha or not row.get("path"):
            continue
        path = Path(str(row["path"]))
        for cand in (path, matter.data / "raw" / path.name):
            if cand.is_file():
                return cand
    return None
