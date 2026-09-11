"""`condense`: routine visits get the short form the firm's own format calls
for. Paid (mechanical tier), one call per routine entry.

Measured against the firm's exemplar: median 93 words per entry, average
232, max 1812. Major encounters run long and the many routine visits are
brief. This pass classifies each entry by WHAT THE VISIT WAS, from its own
header, and condenses only the routine ones; it never recomposes from
records, so no new fact can enter. Invariants enforced mechanically after
the model returns: no citation appears that the original did not carry, at
least one survives, the date and provider lines are unchanged, the result is
shorter. An entry failing any of these keeps its long form. Pre-incident
routine entries are never condensed: the scope stage drops them or the
summary collapses them.

entries_condensed.md is written unconditionally, byte for byte the source
when nothing condensed: a stale file from an earlier run once carried old
exhibit numbering into every stage after it.
"""

from __future__ import annotations

import re
import time

from .. import llm, prompts
from .base import StageRun

ENTRY = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
CITE = re.compile(r"\(Exhibit \d+(?: - p\. [0-9,\s\-]+)?\)")
SERIAL_PROVIDER = re.compile(r"(?i)physical therapy|chiropractic|\bPT\b|neurofeedback|massage|acupuncture|rehab")
MAJOR_HEADER = re.compile(
    r"(?i)\b(emergency|\bED\b|admission|admitted|initial evaluation|initial consultation|consultation|imaging|"
    r"radiology|MRI|CT\b|x-?ray|EEG|qEEG|surgery|surgical|operative|procedure|injection|nerve block|ambulance|"
    r"hospital|neuropsych|discharge)\b"
)
MAJOR_BODY = re.compile(
    r"(?i)\b(initial evaluation|new patient|re-?evaluation|discharge summary|impression:|operative report|admitted to)\b"
)
MIN_WORDS = 90
PAUSE_SECONDS = 0.4


def is_routine(entry: str) -> bool:
    lines = entry.splitlines()
    header = lines[1] if len(lines) > 1 else ""
    body = "\n".join(lines[2:])
    if MAJOR_HEADER.search(header) or MAJOR_BODY.search(body[:1200]):
        return False
    return bool(SERIAL_PROVIDER.search(header))


def split_entries(src: str) -> tuple[str, list[str]]:
    """(preamble, entries): anything before the first dated entry is the Prior
    Medical History block and the scope note, which must survive."""
    parts = ENTRY.split(src)
    preamble = parts[0].strip() if parts and not re.match(r"\d{2}/\d{2}/\d{4}", parts[0].strip()) else ""
    entries = [e.strip() for e in parts if e.strip() and re.match(r"\d{2}/\d{2}/\d{4}", e.strip())]
    return preamble, entries


def iso_of(e: str) -> str:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", e)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def accept(orig: str, new: str) -> str | None:
    """None when accepted, else the reason. Containment, not equality:
    condensing removes administrative content and the citations that
    supported it; what must never happen is an invented citation."""
    ol, nl = orig.splitlines(), new.splitlines()
    if set(CITE.findall(new)) - set(CITE.findall(orig)):
        return "invented citation"
    if not CITE.findall(new):
        return "no citation left"
    if len(nl) < 2 or nl[0].strip() != ol[0].strip() or nl[1].strip() != ol[1].strip():
        return "header changed"
    if len(new.split()) >= len(orig.split()):
        return "not shorter"
    return None


def condense(sr: StageRun, src: str, incident: str, pause: float = PAUSE_SECONDS) -> tuple[str, dict]:
    preamble, entries = split_entries(src)
    routine = [i for i, e in enumerate(entries) if is_routine(e) and len(e.split()) > MIN_WORDS]
    before_n = len(routine)
    routine = [i for i in routine if iso_of(entries[i]) >= incident]
    sr.log(
        f"{sr.unit.unit}: {len(entries)} entries, {len(routine)} routine and long enough to condense, "
        f"{before_n - len(routine)} pre-incident routine skipped"
    )
    out = list(entries)
    model, system = llm.model_for(sr.cfg, "mechanical"), prompts.load("condense-system", sr.cfg)
    kept_long = condensed = failed = 0
    for n, i in enumerate(routine, 1):
        orig = entries[i]
        try:
            r = sr.doorway.call(
                "condense",
                model=model,
                system=system,
                messages=[{"role": "user", "content": orig}],
                max_tokens=2000,
                custom_id=f"condense-{i}",
            )
            new = r.text.strip()
        except Exception as exc:  # noqa: BLE001 - one entry keeps its long form
            sr.log(f"  [{n}] error: {str(exc)[:90]}")
            failed += 1
            continue
        why = accept(orig, new)
        if why is None:
            out[i] = new
            condensed += 1
        else:
            kept_long += 1
            sr.log(f"  [{n}] kept long ({why}): {orig.splitlines()[0]}")
        if pause:
            time.sleep(pause)
    body = "\n\n".join(([preamble] if preamble else []) + out) if condensed else src
    lens = sorted(len(e.split()) for e in out)
    stats = {
        "entries": len(entries),
        "routine": len(routine),
        "condensed": condensed,
        "kept_long": kept_long,
        "failed": failed,
        "words_before": sum(len(e.split()) for e in entries),
        "words_after": sum(lens),
        "lens": lens,
    }
    return body, stats


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    src_p = d / "entries_scoped_final.md"
    if not src_p.is_file():
        src_p = d / "entries_final.md"
    src = src_p.read_text(encoding="utf-8")
    body, s = condense(sr, src, sr.job.incident_date)
    (d / "entries_condensed.md").write_text(body, encoding="utf-8")
    sr.log(f"condensed {s['condensed']}, kept long {s['kept_long']}, failed {s['failed']}")
    if s["words_before"] and s["lens"]:
        sr.log(
            f"words {s['words_before']} -> {s['words_after']} ({s['words_after'] / s['words_before'] * 100:.0f}%); "
            f"words/entry median {s['lens'][len(s['lens']) // 2]}, max {s['lens'][-1]}"
        )
    else:
        sr.log("no dated entries in the source; entries_condensed.md is a verbatim copy")
    return 0
