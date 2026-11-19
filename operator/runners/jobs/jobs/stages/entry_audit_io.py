"""The entry audit's ($0) plumbing: its units, its answer files, its replay
seam and its projected cost (``stages/entry_audit.py`` is the stage).

ANSWER FILES. Every model reply is saved under
``runs/<unit>/entry_read/<name>.<kind>.json`` before anything acts on it:

    {"hash": "<sha256 of the request>", "text": "<the reply>",
     "stop_reason": "end_turn", "model": "<model id>"}

`name` is the unit's key (``N3``, ``N3-2``, ``PRE``; a unit split after two
unparsed replies reads each cited page as ``N3~s1``, ``N3~s2``...) for a read,
and ``<read name>.f<i>`` for the confirm of that read's i-th finding (from 0).
`kind` is ``read1``/``read2`` (the first read and its one retry) or
``confirm1``/``confirm2``. A file whose hash matches the request is the
answer: a resumed stage never pays for it again.

REPLAY ($0). With ``ENTRY_AUDIT_REPLAY=1`` in the environment (read only by
this stage; a real run never sets it) a missing answer is never called for:
the request is written beside where its answer goes,
``<name>.<kind>.request.json``:

    {"name": ..., "kind": ..., "hash": ..., "model": ..., "system": "<prompt>",
     "content": [{"type": "text", "text": ...},
                 {"type": "image", "exhibit": E, "page": P}, ...]}

(a picture is named by its exhibit and page, never embedded), and the stage
exits ``EXIT_PENDING`` once every request of the current phase is written.
An answer is written as the answer file above, with the request's hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import budget, llm
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.page_mode import Modes
from ..medchron.update import body as B, entry_read as ER, index_gaps as IG
from ..medchron.update import review_pages as RV
from .base import StageRun, read_json
from .update_base import headings
from .update_review_io import page_counts

REPLAY_ENV = "ENTRY_AUDIT_REPLAY"
DIR = "entry_read"
RECORD = "entry_read.json"
STAGE = "audit"  # the ledger's stage name: the stage keeps its name
MAX_TOKENS = 16_000
#: The projection's figures: findings confirmed per entry (each confirm shows
#: the entry's pages again), the prompt, and a reply's output.
CONFIRMS_PER_UNIT = 2
SYSTEM_TOKENS = 3_000
OUT_TOKENS = 2_000
_LABEL = re.compile(r"^Exhibit (\d+) p\. (\d+)")


class Pending(Exception):
    """An answer the replay has not written yet (only under REPLAY_ENV)."""


def replaying() -> bool:
    return os.environ.get(REPLAY_ENV) == "1"


def model(cfg: Any) -> str:
    return llm.model_for(cfg, "judgment")


def serial(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request content with each picture named by the page its label gives."""
    out: list[dict[str, Any]] = []
    at: tuple[int, int] | None = None
    for b in content:
        if b.get("type") == "image":
            out.append({"type": "image", "exhibit": at[0], "page": at[1]} if at else {"type": "image"})
            continue
        text = str(b.get("text", ""))
        m = _LABEL.match(text)
        if m:
            at = (int(m.group(1)), int(m.group(2)))
        out.append({"type": "text", "text": text})
    return out


def request_hash(mdl: str, system: str, content: list[dict[str, Any]]) -> str:
    blob = json.dumps({"model": mdl, "system": system, "content": serial(content)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(path)


@dataclass
class Asker:
    """One question, answered once: from its answer file, else (replay) a
    request file and Pending, else one paid call saved before it returns."""

    sr: StageRun
    model: str
    d: Path
    replay: bool
    calls: int = 0
    pending: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def ask(self, name: str, kind: str, system: str, content: list[dict[str, Any]]) -> tuple[str, str]:
        h = request_hash(self.model, system, content)
        f = self.d / f"{name}.{kind}.json"
        got = read_json(f, {}) if f.is_file() else {}
        if isinstance(got, dict) and got.get("hash") == h and isinstance(got.get("text"), str):
            return got["text"], str(got.get("stop_reason") or "end_turn")
        if self.replay:
            req = {"name": name, "kind": kind, "hash": h, "model": self.model, "system": system}
            _write(self.d / f"{name}.{kind}.request.json", req | {"content": serial(content)})
            with self._lock:
                self.pending.append(f"{name}.{kind}")
            raise Pending(f"{name}.{kind}")
        r = self.sr.doorway.call(
            STAGE,
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": content}],
            max_tokens=MAX_TOKENS,
            stream=True,
            effort="high",
            custom_id=re.sub(r"[^A-Za-z0-9_-]", "-", f"{STAGE}-{name}-{kind}")[:64],
        )
        _write(f, {"hash": h, "text": r.text, "stop_reason": r.stop_reason, "model": self.model})
        with self._lock:
            self.calls += 1
        return r.text, str(r.stop_reason)


# ---- the units --------------------------------------------------------------------
@dataclass
class Plan:
    units: list[ER.Unit]
    gaps: list[IG.Gap]
    malformed: int
    keep: set[int]
    heads: set[str]


def plan(slug_dir: Path, unit: str, cfg: Any, md: str) -> Plan:
    """The document's units, each carrying the writer's index gaps for it."""
    out = slug_dir / "out" / unit
    keep, heads = set(exhibit_paths(out)), headings(cfg)
    units = ER.plan(md, keep, heads, page_counts(out))
    ents = B.entries_of(md)[1]
    gaps: list[IG.Gap] = []
    bad = 0
    for m in sorted((slug_dir / "runs" / unit).glob("map-*.md")):
        g, b = IG.scan(m.read_text(encoding="utf-8"), ents)
        gaps += g
        bad += b
    IG.attach(units, gaps)
    return Plan(units, gaps, bad, keep, heads)


# ---- the projection ---------------------------------------------------------------
def _tokens(u: ER.Unit, modes: Modes | None) -> int:
    return (
        SYSTEM_TOKENS
        + len(u.text) // RV.CHARS_PER_TOKEN
        + sum(RV.mode_tokens(modes(x, p) if modes else None) for x, p in u.pages)
    )


def tokens(slug_dir: Path, unit: str, cfg: Any) -> tuple[int, int, int, int] | None:
    """(units, units already read, input tokens, output tokens) still to pay
    for; None before the chronology exists."""
    doc = slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file():
        return None
    units = plan(slug_dir, unit, cfg, doc.read_text(encoding="utf-8")).units
    d = slug_dir / "runs" / unit / DIR
    todo = [u for u in units if not (d / f"{u.key}.read1.json").is_file()]
    modes = Modes.open(slug_dir, unit)
    try:
        tin = sum(_tokens(u, modes) * (1 + CONFIRMS_PER_UNIT) for u in todo)
    finally:
        modes.close()
    return len(units), len(units) - len(todo), tin, len(todo) * (1 + CONFIRMS_PER_UNIT) * OUT_TOKENS


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What the stage is projected to add, for the driver's limits."""
    got = tokens(slug_dir, unit, cfg)
    if got is None:
        return None
    rate = pricing.rate_for(model(cfg))
    return (got[2] * rate.input_per_million_cents + got[3] * rate.output_per_million_cents) / 1e8


def projection_lines(sr: StageRun) -> list[str]:
    from .update_review import _dollars

    got = tokens(sr.slug_dir, sr.unit.unit, sr.cfg)
    if got is None:
        return ["no built chronology yet (build_doc has not run); the entry read is unprojectable"]
    n, done, tin, tout = got
    usd = _dollars([{"model": model(sr.cfg), "in": tin, "out": tout}])
    return [
        f"{n} entr(ies) to read from the built chronology, {done} already read; {tin} input and {tout} output "
        f"tokens (each read plus {CONFIRMS_PER_UNIT} confirms per entry) on {model(sr.cfg)} = ~{usd}"
    ]
