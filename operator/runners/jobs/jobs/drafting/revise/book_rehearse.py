"""The $0 rehearsal of a set revision on the firm-rules path (``book_run``):
the same functions the job runs, over saved filed documents, with every model
answer from RECORDINGS (``replay/recorded.py``), scored against SMD's hand
finals per response, per Index of Exhibits line and per kind of change.

``set_rehearse.py --book phrase-book.json`` runs this instead of the
quoted-words path. A unit folder holds ``base.docx`` (as filed), ``key.docx``
(as revised by hand) and optionally ``set_type``; an index file is a unit too.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ... import firm_wording
from ..outcome import DraftingFailed, DraftingHold
from ..replay import recorded
from . import book_run, responses as rs, score, set_units as su
from .prior import docx_to_md


class RehearsalRun:
    """The slice of ``ReviseSetRun`` ``book_run`` reads, over folders on disk."""

    cls = "discovery_response"

    def __init__(self, units_dir: Path, work: Path, request: str, book_path: Path, ask: Any, fmt: Any) -> None:
        self.data, self.log, self._ask_fn, self._fmt_v = work / "data", (lambda _m: None), ask, fmt
        self.data.mkdir(parents=True, exist_ok=True)
        self.dirs = sorted(d for d in units_dir.iterdir() if (d / "base.docx").is_file())
        self.units = [{"name": f"{d.name}.docx", "file_id": d.name} for d in self.dirs]
        self.job = SimpleNamespace(request_text=request)
        self.revise: dict[str, Any] = {}
        text = book_path.read_text(encoding="utf-8")
        data = {"phrase_book": {"path": str(book_path), "stages": ["revise"]}}
        self.firm = SimpleNamespace(data=data, read=lambda _p: text, model=lambda _role: "recorded")

    def _ask(self, _model: str) -> Any:
        return self._ask_fn

    def _fmt(self) -> Any:
        return self._fmt_v

    def _udir(self, k: int) -> Path:
        d = self.data / "units" / f"{k:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ujson(self, k: int, name: str) -> Any:
        p = self._udir(k) / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _uwrite(self, k: int, name: str, obj: Any) -> None:
        (self._udir(k) / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _write(self, name: str, obj: Any) -> None:
        (self.data / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")

    def _hint(self, k: int) -> str | None:
        p = self.dirs[k] / "set_type"
        return (p.read_text(encoding="utf-8").strip() or None) if p.is_file() else None

    def _scoped(self) -> list[int]:
        return [k for k in range(len(self.units)) if (self._ujson(k, "base.json") or {}).get("in_scope")]

    def _rules_in_force(self) -> list[dict[str, Any]]:
        return list(self._json("rules-in-force.json")["rules"])

    def base(self, rules: list[dict[str, Any]]) -> None:
        (self.data / firm_wording.FILE).write_text(json.dumps({"rules": rules}), encoding="utf-8")
        self._write("rules-in-force.json", {"rules": rules, "without_wording": []})
        for k, d in enumerate(self.dirs):
            shutil.copyfile(d / "base.docx", self._udir(k) / "filed.docx")
            md, doc = su.read_base(self._udir(k) / "filed.docx", self._udir(k), self._hint(k))
            (self._udir(k) / "base.md").write_text(md, encoding="utf-8")
            on = not doc.is_index and su.in_scope(self.job.request_text, [doc.set_type])[0]
            self._uwrite(k, "base.json", {"set_type": doc.set_type, "numbers": doc.numbers, "in_scope": on,
                                          "index": doc.is_index})  # fmt: skip
        book_run.pair_indexes(self)


def _scores(run: RehearsalRun, bk: Any) -> list[dict[str, Any]]:
    out = []
    for k, d in enumerate(run.dirs):
        hint = run._hint(k)
        base = rs.read((run._udir(k) / "base.md").read_text(encoding="utf-8"), hint)
        key = rs.read(docx_to_md(d / "key.docx"), hint)
        rec = run._ujson(k, "render.json") or {}
        done = rs.read(docx_to_md(Path(rec["out"])), hint) if rec.get("out") else base
        out.append({
            "document": d.name, "changed": rec.get("changed") or [], "left": rec.get("left") or [],
            "responses": score.responses(base, done, key), "index": score.index(base, done, key),
            "kinds": score.kinds(base, done, key, bk),
        })  # fmt: skip
    return out


def rehearse(
    units_dir: Path, request: str, rules: list[dict[str, Any]], recordings: dict[str, Any], book_path: Path,
    work: Path, fmt: Any = None,
) -> dict[str, Any]:  # fmt: skip
    client = recorded.RecordedClient(recordings)
    door = recorded.doorway(work, client)

    def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
        client.stage = stage
        msgs = [{"role": "user", "content": user}]
        return door.call(stage, model="recorded", system=system, messages=msgs, max_tokens=max_tokens).text

    run = RehearsalRun(units_dir, work, request, book_path, ask, fmt)
    outcome = {"outcome": "delivered", "reason": ""}
    try:
        run.base(rules)
        book_run.values(run)
        book_run.render_set(run)
        if not any((run._ujson(k, "render.json") or {}).get("out") for k in range(len(run.units))):
            outcome = {"outcome": "held", "reason": "revise_unchanged: no filed response would change"}
    except recorded.NeedsModelCall as exc:
        outcome = {"outcome": "incomplete", "reason": str(exc)}
    except (DraftingFailed, DraftingHold, su.BaseUnproven) as exc:
        outcome = {"outcome": "failed" if isinstance(exc, DraftingFailed) else "held", "reason": str(exc)[:400]}
    docs = _scores(run, book_run.book(run)) if outcome["outcome"] != "incomplete" else []
    kinds = {kind: {c: sum(d["kinds"][kind][c] for d in docs) for c in ("correct", "missed", "extra")}
             for kind in score.KINDS}  # fmt: skip
    return {
        "request": outcome,
        "documents": docs,
        "totals": {
            "responses": score.totals([d["responses"] for d in docs]),
            "index": score.totals([d["index"] for d in docs]),
            "kinds": kinds,
        },  # fmt: skip
        "needed": client.needed,
    }


__all__ = ["RehearsalRun", "rehearse"]
