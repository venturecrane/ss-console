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
"""

from __future__ import annotations

from pathlib import Path

from .. import llm, prompts
from ..limits import LimitHold
from .base import StageRun, append_jsonl
from .chunking import MIN_YIELD, split_chunk
from .compose import INFRA_EXIT, clear_unread, narrower, outputs_for, read_usage, record_unread, well_formed


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
            "chunk": label,
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


def _repair_chunk(rep: _Repair, c: int, src: str) -> str:
    """'repaired', or 'infra' when an API error left the chunk unrepaired."""
    sr, d = rep.sr, rep.d
    # A rerun after an infrastructure exit starts clean: parts and unread rows
    # of the earlier attempt never sit beside this one's.
    _whole, stale = outputs_for(d, c)
    for p in stale:
        p.unlink()
    for p in d.glob(f"chunk-{c:02d}-*.txt"):
        p.unlink()
    clear_unread(d, c)
    results: list[tuple[str, str | None, str]] = []
    for nparts in (2, 3, 5):
        parts = split_chunk(src, nparts)
        sr.log(f"chunk {c}: re-running as {len(parts)} parts ({[len(p) for p in parts]} chars)")
        results = []
        for k, p in enumerate(parts, 1):
            body, status = rep.part(p, f"{c}.{k}")
            if status == "ok" and body is not None and len(body) < len(p) * MIN_YIELD:
                sr.log(f"  part {c}.{k} short ({len(body)}B from {len(p)}B) but complete; kept")
            elif status != "ok":
                sr.log(f"  part {c}.{k} still not clean ({status})")
            results.append((p, body, status))
        if all(st == "ok" for _p, _b, st in results):
            break
        if nparts != 5:
            sr.log(f"  chunk {c}: splitting further")
    final: list[tuple[str, str | None, str]] = []
    for k, (p, body, status) in enumerate(results, 1):
        final += rep.settle(p, body, status, f"{c}.{k}")
    infra = any(st == "error" for _p, _b, st in final)
    for k, (p, body, status) in enumerate(final, 1):
        (d / f"chunk-{c:02d}-{k}.txt").write_text(p, encoding="utf-8")
        name = f"map-{c:02d}-{k}.md"
        if status == "ok" and body:
            (d / name).write_text(body, encoding="utf-8")
        elif status in ("truncated", "empty") and not infra:
            rows = record_unread(d, f"{c}.{k}", p, status, name)
            sr.log(f"  part {c}.{k} {status} at one page; named as unread: " + "; ".join(r["file"] for r in rows))
    if infra:
        sr.log(f"  chunk {c} NOT repaired: an API error outlived its retries (infrastructure; nothing disclosed)")
        return "infra"
    (d / f"map-{c:02d}.md").rename(d / f"map-{c:02d}.md.truncated")
    sr.log(f"  chunk {c} repaired; original set aside")
    return "repaired"


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    truncated = sorted(
        {
            u["chunk"]
            for u in read_usage(d)
            if u.get("stop") == "max_tokens"
            and isinstance(u.get("chunk"), int)
            and not (d / f"map-{u['chunk']:02d}.md.truncated").exists()
        }
    )
    if not truncated:
        sr.log("no unrepaired truncated chunks")
        return 0
    sr.log(f"truncated chunks: {truncated}")
    max_tokens = int(sr.cfg.get("levers", "compose_max_tokens", 128000))
    rep = _Repair(sr, d, llm.model_for(sr.cfg, "composition"), prompts.load("map-system", sr.cfg), max_tokens)
    outcomes = [_repair_chunk(rep, c, (d / f"chunk-{c:02d}.txt").read_text(encoding="utf-8")) for c in truncated]
    # An unrepaired chunk blocks assembly (which refuses over it), so say so
    # here with the exit code rather than a line only a person reads.
    return INFRA_EXIT if "infra" in outcomes else 0
