"""The entry audit's ($0) plumbing: its units, its answer files, its replay
seam and its projected cost (``stages/entry_audit.py`` is the stage).

ANSWER FILES. Every model reply is saved under
``runs/<unit>/entry_read/<name>.<kind>.json`` before anything acts on it:

    {"hash": "<sha256 of the request>", "text": "<the reply>",
     "stop_reason": "end_turn", "model": "<model id>"}

`name` is the unit's key (``N3``, ``N3-2``, ``PRE``; a unit split after two
unparsed replies, or a request the API refused as too large, reads each cited
page as ``N3~s1``, ``N3~s2``...) for a read, and ``<read name>.f<i>`` for the
confirm of that read's i-th finding (from 0). `kind` is ``read1``/``read2``
(the first read and its one retry) or ``confirm1``/``confirm2``. A file is
the answer only when its hash matches the request, it did not run out
(``stop_reason`` max_tokens) and it parses for its kind: anything else is
asked again, so a resumed hold asks afresh for exactly what was not read and
pays for nothing that was.

REPLAY ($0). With ``ENTRY_AUDIT_REPLAY=1`` in the environment (read only by
this stage; a real run never sets it) a missing answer is never called for:
the request is written beside where its answer goes,
``<name>.<kind>.request.json``:

    {"name": ..., "kind": ..., "hash": ..., "model": ..., "system": "<prompt>",
     "content": [{"type": "text", "text": ...},
                 {"type": "image", "exhibit": E, "page": P}, ...]}

(a picture is named by its exhibit and page, never embedded), and the stage
exits ``EXIT_PENDING`` once every request of the current phase is written.
An answer is written as the answer file above, with the request's hash; under
replay a saved answer that does not parse is taken as given (the retry asks
for the next attempt), so a bad answer cannot loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import budget, llm, paid
from ..medchron import job as job_mod
from ..medchron.audit.page_text import exhibit_paths
from ..medchron.page_mode import Modes
from ..medchron.update import body as B, entry_confirm as EC, entry_read as ER, index_gaps as IG
from ..medchron.update import review_pages as RV
from .base import StageRun, read_json
from .update_base import headings
from .update_review_io import page_counts

REPLAY_ENV = "ENTRY_AUDIT_REPLAY"
DIR = "entry_read"
RECORD = "entry_read.json"
RELEASE = "tripwire-release.json"
STAGE = "audit"  # the ledger's stage name: the stage keeps its name
MAX_TOKENS = 16_000  # a read's output budget
CONFIRM_MAX_TOKENS = 4_000  # a confirm answers one small JSON object
#: The projection's figures before anything is read: confirms per entry, the
#: pages each shows, and the prompt.
CONFIRMS_PER_UNIT = 3
SYSTEM_TOKENS = 3_000
_LABEL = re.compile(r"^Exhibit (\d+) p\. (\d+)")
Valid = Callable[[str], bool]


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
    """One question, answered once: from a good answer file, else (replay) a
    request file and Pending, else one paid call saved before it returns."""

    sr: StageRun
    model: str
    d: Path
    replay: bool
    calls: int = 0
    pending: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _saved(self, name: str, kind: str, h: str, valid: Valid | None) -> str | None:
        f = self.d / f"{name}.{kind}.json"
        got = read_json(f, {}) if f.is_file() else {}
        if not isinstance(got, dict) or got.get("hash") != h or not isinstance(got.get("text"), str):
            return None
        if self.replay:
            return got["text"]
        if got.get("stop_reason") == "max_tokens" or (valid is not None and not valid(got["text"])):
            return None  # a bad answer is never reused: it is asked again
        return got["text"]

    def peek(self, name: str, kind: str, system: str, content: list[dict[str, Any]], valid: Valid | None) -> str | None:
        return self._saved(name, kind, request_hash(self.model, system, content), valid)

    def ask(
        self,
        name: str,
        kind: str,
        system: str,
        content: list[dict[str, Any]],
        valid: Valid | None = None,
        max_tokens: int = MAX_TOKENS,
    ) -> tuple[str, str]:
        h = request_hash(self.model, system, content)
        got = self._saved(name, kind, h, valid)
        if got is not None:
            return got, "end_turn"
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
            max_tokens=max_tokens,
            stream=True,
            effort="high",
            custom_id=re.sub(r"[^A-Za-z0-9_-]", "-", f"{STAGE}-{name}-{kind}")[:64],
        )
        _write(
            self.d / f"{name}.{kind}.json",
            {"hash": h, "text": r.text, "stop_reason": r.stop_reason, "model": self.model},
        )
        with self._lock:
            self.calls += 1
        return r.text, str(r.stop_reason)


@dataclass
class Read:
    unit: ER.Unit
    status: str = "unread"  # read | unread | pending
    got: list[tuple[str, ER.Unit, ER.Parsed]] = field(default_factory=list)
    why: str = ""  # an unread unit's reason


# ---- the units --------------------------------------------------------------------
@dataclass
class Plan:
    units: list[ER.Unit]
    gaps: list[IG.Gap]
    malformed: int
    keep: set[int]
    heads: set[str]
    npages: Callable[[int], int]


def plan(slug_dir: Path, unit: str, cfg: Any, md: str) -> Plan:
    """The document's units, each carrying the writer's index gaps for it."""
    out = slug_dir / "out" / unit
    keep, heads, npages = set(exhibit_paths(out)), headings(cfg), page_counts(out)
    units = ER.plan(md, keep, heads, npages)
    ents = B.entries_of(md)[1]
    gaps: list[IG.Gap] = []
    bad = 0
    for m in sorted((slug_dir / "runs" / unit).glob("map-*.md")):
        g, b = IG.scan(m.read_text(encoding="utf-8"), ents)
        gaps += g
        bad += b
    IG.attach(units, gaps)
    return Plan(units, gaps, bad, keep, heads, npages)


# ---- the projection ---------------------------------------------------------------
def _page(modes: Modes | None, x: int, p: int) -> int:
    return RV.mode_tokens(modes(x, p) if modes else None)


def unit_tokens(u: ER.Unit, modes: Modes | None) -> tuple[int, int]:
    """(input, output) tokens of one unit: its read, every shown page as
    text or picture, and CONFIRMS_PER_UNIT confirms of up to 8 pages each at
    the unit's own mean page cost; output at each call's real maximum."""
    pages = [_page(modes, x, p) for x, p in u.pages]
    mean = sum(pages) // max(len(pages), 1)
    confirm = SYSTEM_TOKENS + mean * min(EC.CONFIRM_PAGES, max(len(pages), 1))
    tin = SYSTEM_TOKENS + len(u.text) // RV.CHARS_PER_TOKEN + sum(pages) + CONFIRMS_PER_UNIT * confirm
    return tin, MAX_TOKENS + CONFIRMS_PER_UNIT * CONFIRM_MAX_TOKENS


def tokens(slug_dir: Path, unit: str, cfg: Any) -> tuple[int, int, int, int] | None:
    """(units, units already read, input tokens, output tokens) still to pay
    for; None before the chronology exists."""
    doc = slug_dir / "runs" / unit / "final-chronology.md"
    if not doc.is_file():
        return None
    units = plan(slug_dir, unit, cfg, doc.read_text(encoding="utf-8")).units
    d = slug_dir / "runs" / unit / DIR
    todo = [u for u in units if not any((d / f"{u.key}.read{a}.json").is_file() for a in (1, 2))]
    modes = Modes.open(slug_dir, unit)
    try:
        got = [unit_tokens(u, modes) for u in todo]
    finally:
        modes.close()
    return len(units), len(units) - len(todo), sum(t for t, _ in got), sum(o for _, o in got)


def priced(pricing: budget.Pricing, cfg: Any, tin: int, tout: int) -> float:
    rate = pricing.rate_for(model(cfg))
    return (tin * rate.input_per_million_cents + tout * rate.output_per_million_cents) / 1e8


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What the stage is projected to add, for the driver's limits."""
    got = tokens(slug_dir, unit, cfg)
    return None if got is None else priced(pricing, cfg, got[2], got[3])


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


def confirm_usd(sr: StageRun, modes: Modes | None, items: list[tuple[ER.Unit, ER.ReadFinding]]) -> float | None:
    """The confirm phase priced from the real findings and the real pages
    each confirm shows; None when no rate card can be read."""
    try:
        pricing = paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return None
    tin = sum(SYSTEM_TOKENS + sum(_page(modes, x, p) for x, p in EC.confirm_pages(f, u)) for u, f in items)
    return priced(pricing, sr.cfg, tin, len(items) * CONFIRM_MAX_TOKENS)


def cap_room(sr: StageRun) -> float | None:
    """What the job's cap leaves, as the driver reckons it: the firm's cap,
    lowered by the envelope's, less the job's spend so far."""
    try:
        pricing = paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return None
    firm = float(sr.cfg.per_job_cap_usd)
    cap = firm if sr.job.cap_usd is None else min(float(sr.job.cap_usd), firm)
    spent = paid.budget(pricing, cap, job_mod.usage_ledgers(sr.job)).refresh()
    return cap - spent
