"""The runner-to-broker wire snapshot (``goldens/wire.json``) and how it is fed.

The snapshot has three parts, all from running code, none read from another
golden:

* ``by_cell``: per job host matrix cell, the request and reply shapes of every
  verb its LIVE run sent the real broker (``test_host_matrix_goldens.py``
  checks each cell's own traffic against its row, and writes the row under the
  write switches);
* ``direct``: the verbs no lane host sends (the CLIs), each called on the real
  broker with no payload and with a known job id, recorded BY VALUE
  (normalised the way the matrix normalises);
* ``verbs``: per verb, the union of the two, which the wire census
  (``operator/bin/goldens/wire-census.py``) reads.

Rows are written under a file lock: the matrix's cells run on several workers.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator

WIRE = Path(__file__).resolve().parent / "goldens" / "wire.json"


def shape(v: Any) -> Any:
    """Keys and value types, recursively; a list is the union of its items' shapes."""
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        shapes = sorted({json.dumps(shape(x), sort_keys=True) for x in v})
        return [json.loads(s) for s in shapes]
    if isinstance(v, bool):
        return "bool"
    if v is None:
        return "null"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"


def traffic(steps: list[dict[str, Any]]) -> dict[str, dict[str, list[Any]]]:
    """A cell's live wire, as verb -> sorted request shapes and reply shapes."""
    out: dict[str, dict[str, set[str]]] = {}
    for step in steps:
        for w in step["wire"]:
            row = out.setdefault(w["request"]["action"], {"requests": set(), "replies": set()})
            row["requests"].add(json.dumps(shape(w["request"]), sort_keys=True))
            row["replies"].add(json.dumps(shape(w["reply"]), sort_keys=True))
    return {
        verb: {k: [json.loads(s) for s in sorted(v)] for k, v in sorted(r.items())} for verb, r in sorted(out.items())
    }


def target() -> Path | None:
    """Where a write goes: ``$JOBS_GOLDENS_OUT/wire.json`` (the integrity job),
    the committed file (``JOBS_WRITE_WIRE`` or ``JOBS_WRITE_HOST_GOLDENS``), or
    nowhere (compare)."""
    out = os.environ.get("JOBS_GOLDENS_OUT")
    if out:
        return Path(out) / "wire.json"
    return WIRE if os.environ.get("JOBS_WRITE_WIRE") or os.environ.get("JOBS_WRITE_HOST_GOLDENS") else None


def read(path: Path = WIRE) -> dict[str, Any]:
    if not path.is_file():
        return {"by_cell": {}, "direct": {}, "verbs": {}}
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def locked(path: Path) -> Iterator[dict[str, Any]]:
    """The document at ``path``, read and written back under an exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = read(path)
        yield doc
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    path.with_suffix(".lock").unlink(missing_ok=True)


def union(by_cell: dict[str, Any], direct: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per verb, every request and reply shape the cells and the direct calls carry."""
    out: dict[str, dict[str, set[str]]] = {}
    for rows in by_cell.values():
        for verb, r in rows.items():
            row = out.setdefault(verb, {"requests": set(), "replies": set()})
            row["requests"].update(json.dumps(s, sort_keys=True) for s in r["requests"])
            row["replies"].update(json.dumps(s, sort_keys=True) for s in r["replies"])
    for verb, calls in direct.items():
        row = out.setdefault(verb, {"requests": set(), "replies": set()})
        for c in calls:
            row["requests"].add(json.dumps(shape(c["request"]), sort_keys=True))
            row["replies"].add(json.dumps(shape(c["reply"]), sort_keys=True))
    return {
        verb: {k: [json.loads(s) for s in sorted(v)] for k, v in sorted(r.items())} for verb, r in sorted(out.items())
    }


def check_cell(cell: str, steps: list[dict[str, Any]]) -> list[str]:
    """Write the cell's row under a write switch; otherwise the verbs whose
    live shapes differ from the committed row (empty means equal)."""
    live = traffic(steps)
    dest = target()
    if dest is not None:
        with locked(dest) as doc:
            doc.setdefault("by_cell", {})[cell] = live
        return []
    want = read().get("by_cell", {}).get(cell)
    if want is None:
        return ["<cell missing from wire.json>"]
    return sorted(v for v in set(want) | set(live) if want.get(v) != live.get(v))


__all__ = ["WIRE", "check_cell", "locked", "read", "shape", "target", "traffic", "union"]
