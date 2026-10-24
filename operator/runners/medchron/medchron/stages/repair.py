"""`repair_truncated`: re-run map chunks whose output hit max_tokens as
halves, so the same source is covered by outputs that fit under the ceiling.
Paid when it has work; the always-run $0 safety net otherwise.

A truncated map file is dangerous precisely because it LOOKS fine: a full
## ENTRIES section the assembler parses happily, while the chunk's tail and
its INDEX / BILLING-DATES / FILES-SEEN blocks are gone. That is a silent
under-count of the record. Since the compose window moved to 128k a chunk
rarely reaches the cap, and this stage prints "no unrepaired truncated
chunks" for $0; it repairs at the same window as composition, so a repair
cannot truncate where the original did not. A part that ends cleanly having
produced almost nothing is not repaired, it is EMPTIED (one part returned 112
bytes for 126k chars of input, twenty files including MRI reports), and the
split escalates 2 -> 3 -> 5 rather than leaving the chunk unrepaired.

Decided by rule past the 5-way split (2026-10-24 audit, row 16). A short
part that answers in the house shape is kept: fax covers and legal papers
honestly yield almost nothing. A part that still truncates or answers in no
shape is narrowed the way compose narrows a declined piece (its files one at
a time, then a single file in page halves) down to one page; a single page
that still truncates or answers in no shape is a page nothing read, named in
``unread_pages.json`` and the limitations exactly as compose names one. An
API error is never disclosed: the chunk stays unrepaired and the stage exits
compose.INFRA_EXIT (75), so the daemon can retry later; nothing else fails it.

A part compose wrote after halving an emptied chunk (label "3.2") is found
and repaired the same way as a whole chunk; its pieces take part numbers
after every existing part of the chunk, and only that part's map file is set
aside. Before this, such a part was never repaired and assemble refused.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import llm, prompts
from ..limits import LimitHold
from .base import StageRun, append_jsonl, read_json
from .chunking import MIN_YIELD, split_chunk
from .compose import UNREAD, INFRA_EXIT, narrower, outputs_for, read_usage, record_unread, well_formed


def _run_part(
    sr: StageRun, d: Path, model: str, system: str, text: str, label: str, max_tokens: int
) -> tuple[str | None, str]:
    try:
        r = sr.doorway.call(
            "map-repair",
            model=model,
            system=system,
            messages=[{"role": "user", "content": text}],
            max_tokens=max_tokens,
            effort="high",
            stream=True,
            cache_blocks=("system",),
            custom_id=f"repair-{label}",
        )
    except LimitHold:
        raise  # a limit stops the stage and reaches the driver as a hold (N10)
    except Exception as exc:  # noqa: BLE001 - a doorway failure on one part is recorded as that part's error and the stage continues with the rest
        sr.log(f"  {label}: {str(exc)[:120]}")
        return None, "error"
    append_jsonl(
        d / "usage.jsonl",
        {
            # Prefixed: a bare "3.2" would read as compose's part 3.2, both
            # to truncated_targets below and to assemble's truncation check.
            "chunk": f"repair-{label}",
            "in": r.usage.input_tokens,
            "out": r.usage.output_tokens,
            "stop": r.stop_reason,
            "max_tokens": max_tokens,
        },
    )
    sr.log(f"  {label}: {r.stop_reason} ({r.usage.output_tokens} out)")
    return r.text, r.stop_reason


def _status(body: str | None, stop: str, text: str) -> str:
    """'ok', 'error', 'truncated' or 'empty' (shapeless and implausibly short)."""
    if body is None:
        return "error"
    if stop == "max_tokens":
        return "truncated"
    if len(body) < len(text) * MIN_YIELD and not well_formed(body):
        return "empty"
    return "ok"


class _Repair:
    def __init__(self, sr: StageRun, d: Path, model: str, system: str, max_tokens: int) -> None:
        self.sr, self.d, self.model, self.system, self.max_tokens = sr, d, model, system, max_tokens
        self.calls = 0

    def part(self, text: str, label: str) -> tuple[str | None, str]:
        self.calls += 1
        body, stop = _run_part(self.sr, self.d, self.model, self.system, text, label, self.max_tokens)
        return body, _status(body, stop, text)

    def settle(self, text: str, body: str | None, status: str, label: str) -> list[tuple[str, str | None, str]]:
        """[(text, body, status)] covering `text`: 'ok' with its body, a
        narrowed floor piece as 'truncated'/'empty' (to be named unread), or
        'error' (infrastructure, never disclosed)."""
        if status in ("ok", "error"):
            return [(text, body, status)]
        subs = narrower(text)
        if not subs:
            return [(text, None, status)]
        self.sr.log(f"  part {label} still {status}; narrowing to {len(subs)} pieces")
        out: list[tuple[str, str | None, str]] = []
        for k, s in enumerate(subs, 1):
            b, st = self.part(s, f"{label}.{k}")
            out += self.settle(s, b, st, f"{label}.{k}")
        return out


class _Target:
    """One truncated map file to repair: a whole chunk (label "3", files
    map-03.md / chunk-03.txt) or a part compose wrote after splitting an
    emptied chunk (label "3.2", map-03-2.md / chunk-03-2.txt). Before
    2026-10-24 only whole chunks were found, so a part that truncated was
    never repaired and assemble refused over it."""

    def __init__(self, d: Path, label: str) -> None:
        self.d, self.label = d, label
        c, _, k = label.partition(".")
        self.c, self.k = int(c), (int(k) if k else None)

    @property
    def stem(self) -> str:
        return f"{self.c:02d}-{self.k}" if self.k else f"{self.c:02d}"

    @property
    def map_file(self) -> Path:
        return self.d / f"map-{self.stem}.md"

    @property
    def src_file(self) -> Path:
        return self.d / f"chunk-{self.stem}.txt"

    @property
    def record(self) -> Path:
        """The part numbers this target's repair wrote, so a rerun clears
        exactly those and never a sibling part compose wrote."""
        return self.d / f"repair-{self.stem}.json"


def _clear_previous(t: _Target) -> None:
    """A rerun after an infrastructure exit starts clean: the parts and
    unread rows an earlier attempt at THIS target wrote never sit beside
    this one's. A whole chunk owns every part of its number."""
    d = t.d
    if t.k is None:
        _whole, parts = outputs_for(d, t.c)
        nums = [int(p.stem.rsplit("-", 1)[1]) for p in parts]
    else:
        nums = list(read_json(t.record, []) or [])
    for n in nums:
        (d / f"map-{t.c:02d}-{n}.md").unlink(missing_ok=True)
        (d / f"chunk-{t.c:02d}-{n}.txt").unlink(missing_ok=True)
    gone = {f"{t.c}.{n}" for n in nums}
    p = d / UNREAD
    rows = read_json(p, [])
    keep = [r for r in rows if str(r.get("chunk")) not in gone]
    if len(keep) != len(rows):
        (p.write_text(json.dumps(keep, indent=1), encoding="utf-8") if keep else p.unlink())
    t.record.unlink(missing_ok=True)


def _first_free(t: _Target) -> int:
    """The first part number free for this target's pieces: 1 for a whole
    chunk, after every part of the chunk otherwise (its siblings stay)."""
    if t.k is None:
        return 1
    _whole, parts = outputs_for(t.d, t.c)
    return max([t.k, *(int(p.stem.rsplit("-", 1)[1]) for p in parts)]) + 1


def _repair_chunk(rep: _Repair, t: _Target) -> str:
    """'repaired', or 'infra' when an API error left the target unrepaired."""
    sr, d, lab = rep.sr, rep.d, t.label
    _clear_previous(t)
    src = t.src_file.read_text(encoding="utf-8")
    results: list[tuple[str, str | None, str]] = []
    for nparts in (2, 3, 5):
        parts = split_chunk(src, nparts)
        sr.log(f"chunk {lab}: re-running as {len(parts)} parts ({[len(p) for p in parts]} chars)")
        results = []
        for k, p in enumerate(parts, 1):
            body, status = rep.part(p, f"{lab}.{k}")
            if status == "ok" and body is not None and len(body) < len(p) * MIN_YIELD:
                sr.log(f"  part {lab}.{k} short ({len(body)}B from {len(p)}B) but complete; kept")
            elif status != "ok":
                sr.log(f"  part {lab}.{k} still not clean ({status})")
            results.append((p, body, status))
        if all(st == "ok" for _p, _b, st in results):
            break
        if nparts != 5:
            sr.log(f"  chunk {lab}: splitting further")
    final: list[tuple[str, str | None, str]] = []
    for k, (p, body, status) in enumerate(results, 1):
        final += rep.settle(p, body, status, f"{lab}.{k}")
    infra = any(st == "error" for _p, _b, st in final)
    first = _first_free(t)
    written: list[int] = []
    for n, (p, body, status) in enumerate(final, first):
        (d / f"chunk-{t.c:02d}-{n}.txt").write_text(p, encoding="utf-8")
        name = f"map-{t.c:02d}-{n}.md"
        written.append(n)
        if status == "ok" and body:
            (d / name).write_text(body, encoding="utf-8")
        elif status in ("truncated", "empty") and not infra:
            rows = record_unread(d, f"{t.c}.{n}", p, status, name)
            sr.log(f"  part {t.c}.{n} {status} at one page; named as unread: " + "; ".join(r["file"] for r in rows))
    t.record.write_text(json.dumps(written), encoding="utf-8")
    if infra:
        sr.log(f"  chunk {lab} NOT repaired: an API error outlived its retries (infrastructure; nothing disclosed)")
        return "infra"
    t.map_file.rename(t.map_file.with_name(t.map_file.name + ".truncated"))
    sr.log(f"  chunk {lab} repaired as parts {written[0]}-{written[-1]}; original set aside")
    return "repaired"


_LABEL = re.compile(r"^\d+(\.\d+)?$")


def truncated_targets(d: Path) -> list[_Target]:
    """Every compose label whose LAST recorded stop is max_tokens and whose
    map file is still live: whole chunks and compose's parts alike. Repair's
    own calls are recorded as `repair-<label>`, so they never read as a
    compose part."""
    last: dict[str, str] = {}
    for u in read_usage(d):
        label = str(u.get("chunk"))
        if _LABEL.match(label) and u.get("stop"):
            last[label] = str(u["stop"])
    out = []
    for label, stop in last.items():
        t = _Target(d, label)
        # Parts of a whole chunk already repaired are repair's own output
        # (an older run recorded them unprefixed), never compose's.
        repaired_whole = t.k is not None and (d / f"map-{t.c:02d}.md.truncated").exists()
        if stop == "max_tokens" and not repaired_whole and t.map_file.is_file() and t.src_file.is_file():
            out.append(t)
    return sorted(out, key=lambda t: (t.c, t.k or 0))


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    targets = truncated_targets(d)
    if not targets:
        sr.log("no unrepaired truncated chunks")
        return 0
    sr.log(f"truncated chunks: {[t.label for t in targets]}")
    max_tokens = int(sr.cfg.get("levers", "compose_max_tokens", 128000))
    rep = _Repair(sr, d, llm.model_for(sr.cfg, "composition"), prompts.load("map-system", sr.cfg), max_tokens)
    outcomes = [_repair_chunk(rep, t) for t in targets]
    # An unrepaired chunk blocks assembly (which refuses over it), so say so
    # here with the exit code rather than a line only a person reads.
    return INFRA_EXIT if "infra" in outcomes else 0
