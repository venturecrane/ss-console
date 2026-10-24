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
split escalates 2 -> 3 -> 5 rather than leaving the chunk unrepaired. A short
part that answers in the house shape is NOT emptied: a part of fax covers
and legal papers honestly yields almost nothing, and below MIN_YIELD it was
re-split until the stage failed (2026-10-24 audit, row 16). Only a part that
still truncates, errors, or answers in no shape at every split fails it.
"""

from __future__ import annotations

from pathlib import Path

from .. import llm, prompts
from ..limits import LimitHold
from .base import StageRun, append_jsonl
from .chunking import MIN_YIELD, split_chunk
from .compose import read_usage, well_formed


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
    system = prompts.load("map-system", sr.cfg)
    model = llm.model_for(sr.cfg, "composition")
    max_tokens = int(sr.cfg.get("levers", "compose_max_tokens", 128000))
    unrepaired = 0
    for c in truncated:
        src = (d / f"chunk-{c:02d}.txt").read_text(encoding="utf-8")
        ok, parts, bodies = False, [], []
        for nparts in (2, 3, 5):
            parts = split_chunk(src, nparts)
            sr.log(f"chunk {c}: re-running as {len(parts)} parts ({[len(p) for p in parts]} chars)")
            ok, bodies = True, []
            for k, p in enumerate(parts, 1):
                body, stop = _run_part(sr, d, model, system, p, f"{c}.{k}", max_tokens)
                if body is None or stop == "max_tokens":
                    ok = False
                    sr.log(f"  part {c}.{k} still not clean (stop={stop})")
                elif len(body) < len(p) * MIN_YIELD and not well_formed(body):
                    ok = False
                    sr.log(f"  part {c}.{k} produced {len(body)}B from {len(p)}B of source; implausibly empty")
                elif len(body) < len(p) * MIN_YIELD:
                    # Fax covers and legal papers honestly yield nothing: a short
                    # answer in the house shape is the part's account, kept
                    # (2026-10-24 audit, row 16; compose keeps the same).
                    sr.log(f"  part {c}.{k} short ({len(body)}B from {len(p)}B) but complete; kept")
                bodies.append((k, body, stop))
            if ok:
                break
            sr.log(f"  chunk {c}: splitting further")
        for k, body, _stop in bodies:
            if body:
                (d / f"map-{c:02d}-{k}.md").write_text(body, encoding="utf-8")
                (d / f"chunk-{c:02d}-{k}.txt").write_text(parts[k - 1], encoding="utf-8")
        if ok:
            (d / f"map-{c:02d}.md").rename(d / f"map-{c:02d}.md.truncated")
            sr.log(f"  chunk {c} repaired; original set aside")
        else:
            unrepaired += 1
            sr.log(f"  chunk {c} NOT fully repaired")
    # An unrepaired chunk blocks assembly (which refuses over it), so say so
    # here with the exit code rather than a line only a person reads.
    return 1 if unrepaired else 0
