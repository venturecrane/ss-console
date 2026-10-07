"""Deliverable 1, the gap audit, built so a large file's audit always finishes.

A live demand job, 2026-10-06, stopped here: the audit hit its 64,000-token
ceiling and nothing downstream ran. The visible text was about 37,000
characters; the rest of the budget went to thinking over a whole 400K-character
file in one call. Three changes, each closing a different way to run out:

* **The ceiling is the model's own.** ``output_max`` is the documented maximum
  output for the stage's model (128K on the current Opus and Sonnet, streamed),
  not a firm lever: a firm cannot usefully ask for less than the model can say.
* **The output is compact by construction.** The model writes table rows only,
  one per item, under a contract this module states and parses (``CONTRACT``),
  and ends on a sentinel line. Item numbers, section grouping, the readiness
  summary and the list of documents no person read are written here, in code.
* **A large file is audited in provider batches.** The digest's documents are
  grouped by folder (a provider's records share one) and packed, in digest
  order, into batches of at most ``BATCH_DOCS`` documents. Every call sees the
  whole digest (cached, so the repeat costs a cache read) and writes rows only
  for its own documents; the first batch also owns the rows that span the file
  (the treatment timeline, preflight-only lines, held-out candidates). A batch
  that still reaches the ceiling is split in two and rerun, down to a single
  document; the rows are merged and numbered here. Only one document whose own
  rows overflow 128K tokens can still stop the job, and it stops resumably.

Every batch's answer is kept under ``data/gap/``, keyed by its scope and the
prompt it was asked under, so a resume pays only for the batches not yet done.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .summarize import sha

#: The documented maximum output per call, streamed (the Models API's
#: ``max_tokens`` for each id). A model not listed keeps the firm's lever.
OUTPUT_MAX = {
    "claude-opus-5-5": 128_000,
    "claude-opus-5": 128_000,
    "claude-sonnet-5-5": 128_000,
    "claude-sonnet-5": 128_000,
    "claude-fable-5-1": 128_000,
}
#: Documents per gap-audit batch. The 2026-10-06 file had 202 documents in 37
#: folders, the largest folder 30; at this size it is seven or eight calls.
BATCH_DOCS = 30
SENTINEL = "END OF ITEM TABLE"
TRAILERS = ("PREMISE", "PEOPLE-AND-ENTITIES", "FIGURES", "FILES-SEEN")
COLUMNS = ("Provider", "What's missing", "Where the file points to it", "Basis", "Suggested request type", "Priority")

CONTRACT = f"""## OUTPUT CONTRACT (this governs the shape of your answer; it overrides any structure stated above)

Code numbers the items, groups them under the brief's sections, writes the Demand Readiness summary from your
Priority column, and names the documents no person read. You write ONLY table rows, then the sentinel line.

Your entire answer is one table, with this header and separator, then one row per item, then a line reading
exactly `{SENTINEL}`:

| Section | {" | ".join(COLUMNS)} |
|---|---|---|---|---|---|---|

- Section: the brief's section the item comes from, as the brief heads it (for example `A. Referral and order
  trail`), or exactly `Possible, needs paralegal check` for an item only inferable that nothing in the file
  references.
- One row per item. No narrative, no per-provider paragraphs, no headings, no preamble, nothing after the
  sentinel. A received item is not a row: write only what is missing, partial, mismatched, or a flagged interval.
- A treatment interval over the threshold is a row: Provider is the provider of the visit that ends it, What's
  missing states the two dates, the number of days and any reason the records give, Basis `Referenced in record`.
- Keep each cell short: a few words, plus the citation in the Where column. Never put a pipe character in a cell.
- If nothing in your scope is missing, write the header, the separator and the sentinel only.
"""


class GapAuditError(RuntimeError):
    """A batch that could not finish. The job fails resumably; done batches are kept."""


def output_max(model: str, fallback: int) -> int:
    return OUTPUT_MAX.get(model, fallback)


@dataclass(frozen=True)
class Doc:
    name: str
    folder: str


def documents(digest: str) -> list[Doc]:
    """The digest's document headings (``## CLASS | name | date | (folder)``),
    in order, once each; the per-chunk trailer blocks are not documents."""
    out: list[Doc] = []
    seen: set[tuple[str, str]] = set()
    for line in digest.splitlines():
        if not line.startswith("## "):
            continue
        parts = [p.strip() for p in line[3:].split("|")]
        if len(parts) < 2 or parts[0].upper() in TRAILERS:
            continue
        folder = parts[-1].strip("() ") if len(parts) >= 4 else ""
        d = Doc(parts[1], folder or parts[1])
        if (d.name, d.folder) not in seen:
            seen.add((d.name, d.folder))
            out.append(d)
    return out


def batches(docs: list[Doc], size: int = BATCH_DOCS) -> list[list[Doc]]:
    """Folders whole, in digest order, packed to at most ``size`` documents; a
    folder larger than ``size`` is a batch of its own (split later only if it
    overflows)."""
    groups: dict[str, list[Doc]] = {}
    for d in docs:
        groups.setdefault(d.folder, []).append(d)
    out: list[list[Doc]] = []
    cur: list[Doc] = []
    for g in groups.values():
        if cur and len(cur) + len(g) > size:
            out.append(cur)
            cur = []
        cur = cur + g
    if cur:
        out.append(cur)
    return out or [[]]


def halves(batch: list[Doc]) -> list[list[Doc]]:
    """Two pieces, at the folder boundary nearest the middle when there is one."""
    mid = len(batch) // 2
    cuts = [i for i in range(1, len(batch)) if batch[i].folder != batch[i - 1].folder]
    cut = min(cuts, key=lambda c: abs(c - mid)) if cuts else mid
    return [batch[:cut], batch[cut:]]


_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def rows(text: str) -> list[list[str]]:
    """The answer's item rows: seven cells, header and separator dropped."""
    out = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) != 7 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if cells[0].lower() == "section" and cells[1].lower() == "provider":
            continue
        out.append(cells)
    return out


def complete(stop_reason: str, text: str) -> bool:
    return stop_reason != "max_tokens" and any(ln.strip() == SENTINEL for ln in text.splitlines())


def _key(cells: list[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", f"{cells[1]} {cells[2]} {cells[3]}".lower()).strip()


def merge(answers: list[str]) -> list[list[str]]:
    """Every batch's rows, in batch order, an exact repeat dropped."""
    out, seen = [], set()
    for a in answers:
        for r in rows(a):
            k = _key(r)
            if k not in seen:
                seen.add(k)
                out.append(r)
    return out


def _sec_key(label: str) -> str:
    m = re.match(r"\s*([A-Za-z])\s*[.):]", label)
    return m.group(1).upper() if m else label.strip().lower()


def render(merged: list[list[str]], free: dict[str, Any]) -> str:
    """The deliverable: the brief's sections as tables of numbered items, the
    possible list, and the readiness summary, all from the rows."""
    possible = [r for r in merged if "possible" in r[0].lower()]
    items = [r for r in merged if "possible" not in r[0].lower()]
    order: dict[str, str] = {}
    for r in items:
        order.setdefault(_sec_key(r[0]), r[0])
    head = "| Item | " + " | ".join(COLUMNS) + " |\n|" + "---|" * (len(COLUMNS) + 1)
    out = ["# Records and Billing Gap Audit", ""]
    n, blocks, follows = 0, [], []
    for key in sorted(order):
        out += [f"## {order[key]}", "", head]
        for r in (x for x in items if _sec_key(x[0]) == key):
            n += 1
            out.append(f"| {n} | " + " | ".join(r[1:]) + " |")
            (blocks if "block" in r[6].lower() else follows).append(str(n))
        out.append("")
    if not items:
        out += ["The audit found no missing record, bill or flagged interval.", ""]
    out += ["## Possible, needs paralegal check", ""]
    if possible:
        out += ["| " + " | ".join(COLUMNS[:3]) + " | Suggested request type |", "|---|---|---|---|"]
        out += [f"| {r[1]} | {r[2]} | {r[3]} | {r[5]} |" for r in possible]
    else:
        out.append("None.")
    out += ["", "## Demand Readiness", ""]
    out.append("Items that block sending the demand: " + (", ".join(blocks) if blocks else "none") + ".")
    out.append("Items that can follow the demand: " + (", ".join(follows) if follows else "none") + ".")
    unread = [
        str(u.get("name") or "") if isinstance(u, dict) else str(u) for u in free.get("unreadable_documents") or []
    ]
    machine = [str(m) for m in free.get("documents_read_by_machine_transcription") or []]
    out += ["", "Documents that could not be read: " + ("; ".join(unread) if unread else "none") + "."]
    out.append(
        "Documents read by machine transcription, not by a person: " + ("; ".join(machine) if machine else "none") + "."
    )
    return "\n".join(out) + "\n"


def _scope(batch: list[Doc], first: bool, whole: bool) -> str:
    if whole:
        return "THIS CALL'S SCOPE: the whole file. Write every row the audit has."
    names = "\n".join(f"- {d.name} (folder {d.folder})" for d in batch)
    extra = (
        " This call ALSO owns the rows that span the whole file: every treatment-timeline interval, every row "
        "that rests on the preflight alone, and every held-out candidate."
        if first
        else " Treatment-timeline intervals, preflight-only rows and held-out candidates belong to another call."
    )
    return (
        "THIS CALL'S SCOPE (one batch of a large file; other calls cover the rest). Write rows ONLY for items whose "
        "'Where the file points to it' is one of the documents listed here. The whole digest is in the "
        "instruction for cross-reference: a referral in a listed document to any provider is a row here; an item "
        f"found only in an unlisted document is another call's.{extra}\n\nDOCUMENTS IN SCOPE:\n{names}"
    )


class GapAuditor:
    def __init__(self, data: Path, call: Callable[..., Any], model: str, fallback: int, workers: int, log: Any):
        self.dir = data / "gap"
        self.call, self.max = call, output_max(model, fallback)
        self.workers, self.log = workers, log

    def run(self, system: str, free: dict[str, Any], digest: str) -> str:
        docs = documents(digest)
        plan = batches(docs)
        whole = len(plan) == 1
        self.dir.mkdir(parents=True, exist_ok=True)
        ssha = sha(system)[:8]
        first, rest = plan[0], plan[1:]
        answers = [self._batch(system, ssha, first, True, whole)]  # alone: it writes the cache the rest read
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            answers += list(ex.map(lambda b: self._batch(system, ssha, b, False, False), rest))
        if len(plan) > 1:
            self.log(f"  gap audit: {len(docs)} documents in {len(plan)} batches")
        return render(merge(answers), free)

    def _batch(self, system: str, ssha: str, batch: list[Doc], first: bool, whole: bool) -> str:
        user = _scope(batch, first, whole)
        cache = self.dir / f"batch-{ssha}-{sha(user)[:10]}.md"
        if cache.is_file():
            return cache.read_text(encoding="utf-8")
        r = None
        for attempt in (1, 2):
            r = self.call(system, user, self.max, f"gap-{ssha}-{sha(user)[:8]}-{attempt}")
            if complete(r.stop_reason, r.text):
                cache.write_text(r.text, encoding="utf-8")
                return r.text
            if r.stop_reason == "max_tokens":
                cache.with_suffix(".truncated.md").write_text(r.text, encoding="utf-8")  # for diagnosis only
                break
            self.log(f"  gap audit batch of {len(batch)} ended without its sentinel; attempt {attempt} of 2")
        if r is not None and r.stop_reason == "max_tokens" and len(batch) > 1:
            self.log(f"  gap audit batch of {len(batch)} documents reached the ceiling; split in two")
            parts = halves(batch)
            return "\n".join(self._batch(system, ssha, p, first and i == 0, False) for i, p in enumerate(parts))
        what = "reached the output ceiling" if r is not None and r.stop_reason == "max_tokens" else "never finished"
        raise GapAuditError(f"gap audit for {len(batch) or 'the whole'} document(s) {what}; nothing downstream ran")
