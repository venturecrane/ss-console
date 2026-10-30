"""A case's breakdown is read once and reused until what would be read changes
(2026-10-30). Before this every breakdown list re-read every litigated case
through all three passes, about $180 a list, though most cases had no new
paper since the last list.

The unit is one CASE. Its saved record is the case's post-gate result as the
last DELIVERED breakdown printed it (``final.json``'s row), with the hand
``kind_hits`` it printed beside. A later job reuses it only when the case's
KEY matches: a digest of exactly what the read would be sent (each paper's
file id, name, saved date, size and text, in order, after the duplicate and
attachment rules of ``run._chunks``) and how it would be read (the three
models, the rules and pass prompts without today's date, both tool schemas,
the chunk size). So an email that ages out of the candidate window without
contributing a paper changes nothing, while a new paper, an edited one, a
deleted one, a new model or a new prompt changes the key and the whole case
is read again (a new response changes entries the old papers made). The doc
numbers are left out: they renumber whenever any file reaches the matter.

The status list's sets are merged in after the read (``complete.merge``), and
SMD's corrections can sit on either side of that merge (2026-10-30: paper
corrections and hand reads before it; a 30-row overlay removing contradicted
status rows after it). So a record also keeps the case's delivered MERGED
rows (``complete``) with a digest of the status values they were merged from
(``prior_hash``): while those values are unchanged the merged rows print as
delivered; once they change, the case is merged again from its saved result.

A case whose saved result holds a "not yet due" response is read again once
that due date has passed or is unknown: the answer was relative to its day.

Records are written only by a delivered run (``run._report``) or the seed
(``seed``: last night's delivered results with the corrections SMD made after
the run). A proof, a held run or a review-held run writes nothing.

Purge one matter: ``python -m medchron.litigation.discovery12.carry purge
--matter <id> [--state-dir <dir>]``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from ..gates import LEAK_PATTERNS
from ..manifest import file_date, full_name, state_dir
from . import batch, sections

#: Bump when a saved record's meaning changes: every case is then read again.
SCHEMA = 1
DIR = "d12"


def _sha(x: Any) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def method(models: dict[str, str]) -> str:
    """How a case is read, without today's date: the models by pass, the rules,
    the pass prompts, the tool schemas and the chunk size."""
    return _sha(
        {
            "schema": SCHEMA,
            "models": {p: models.get(p) or "" for p in batch.PASSES},
            "rules": sections.RULES,
            "tails": sections.TAILS,
            "sections": sections.SECTIONS,
            "tools": [sections.RECORD_RESULT, sections.RECORD_VERDICTS],
            "only_by_tool": [batch.only_by_tool(p) for p in batch.PASSES],
            "chunk": batch.CHUNK_CHARS,
        }
    )


def key(ctx: Any, chunks: list[list[tuple[int, str]]], how: str) -> str:
    """The case's key: every paper the passes would be sent, by identity and
    text (never by doc number), and the read method."""
    parts = []
    for docs in chunks:
        part = []
        for n, text in docs:
            f = ctx.refs[n]
            part.append([str(f.get("id")), full_name(f), file_date(f), f.get("size") or 0, text])
        parts.append(part)
    return _sha({"how": how, "chunks": parts})


def path(state: Path, matter_id: str) -> Path:
    return Path(state) / DIR / f"{matter_id}.json"


def load(state: Path, matter_id: str, log: Callable[[str], None] = print) -> dict[str, Any] | None:
    p = path(state, matter_id)
    if not p.is_file():
        return None
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"[d12 carry] {matter_id}: saved record unreadable ({exc}); the case is read again")
        return None
    return v if isinstance(v, dict) and v.get("schema") == SCHEMA else None


def _stale(final: dict[str, Any], today: dt.date) -> bool:
    """A "not yet due" answer whose due date has passed or is unknown."""
    for rows in (final.get("sections") or {}).values():
        for e in rows or []:
            if ((e.get("response") or {}).get("value")) != "not yet due":
                continue
            due = (e.get("due") or {}).get("date")
            try:
                if dt.date.fromisoformat(str(due)) >= today:
                    continue
            except ValueError:
                pass
            return True
    return False


def reusable(saved: dict[str, Any] | None, k: str, today: dt.date) -> dict[str, Any] | None:
    """The saved record when it may print in place of a read, else None."""
    if not saved or saved.get("key") != k or not isinstance(saved.get("final"), dict):
        return None
    return None if _stale(saved["final"], today) else saved


def save(state: Path, matter_id: str, rec: dict[str, Any]) -> None:
    """Atomic: a crash leaves the old record or the new one, never half."""
    p = path(state, matter_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": SCHEMA, **rec}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


#: The fields of a merged row that print (``final-complete.json``).
MERGED = ("sections", "status_rows", "kind_hits", "completed")


def prior_hash(prior: dict[str, Any] | None) -> str:
    """A digest of the status values ``complete.merge`` reads."""
    from .complete import _DIRS

    return _sha({f: (prior or {}).get(f) for f, _d, _w in _DIRS})


def record(
    k: str,
    final: dict[str, Any],
    kind_hits: Any,
    job_id: str,
    provenance: str,
    complete: dict[str, Any] | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": k,
        "job_id": job_id,
        "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "final": final,
        "kind_hits": kind_hits,
        "complete": {f: complete.get(f) for f in MERGED} if complete else None,
        "prior_hash": prior_hash(prior) if complete else None,
        "provenance": provenance,
    }


def merged(saved: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any] | None:
    """The delivered merged rows, while the status values they came from stand."""
    if not saved or not isinstance(saved.get("complete"), dict) or saved.get("prior_hash") != prior_hash(prior):
        return None
    return saved["complete"]


def purge(state: Path, matter_id: str) -> bool:
    p = path(state, matter_id)
    existed = p.is_file()
    p.unlink(missing_ok=True)
    return existed


# ---- the seed: a delivered job's results, with SMD's corrections ---------------------------------
def problems(rec: Any) -> str | None:
    """Why a supplied (hand-corrected) case record cannot be saved, or None.
    Parsed, never cast: its sections must survive the result shape unchanged,
    it must say it was read, and no internal text may ride along."""
    if not isinstance(rec, dict) or not rec.get("matter_id"):
        return "not a case record"
    if rec.get("declined") or not (rec.get("read") or rec.get("nothing_to_read")):
        return "not read (declined or unread): it is read again on the next list"
    secs = rec.get("sections")
    if not isinstance(secs, dict) or set(secs) - set(sections.KEYS):
        return "sections are not the twelve"
    for k in sections.KEYS:
        rows = secs.get(k) or []
        if not isinstance(rows, list) or any(not isinstance(e, dict) or not e.get("item") for e in rows):
            return f"malformed entries in {k}"
    for what, rx in LEAK_PATTERNS:
        if any(rx.search(t) for t in _printed(rec)):
            return f"carries internal text ({what})"
    return None


def _printed(rec: dict[str, Any]) -> list[str]:
    """The record's text that reaches the page (ids and sources never print)."""
    out = [str(x) for x in rec.get("flags") or []]
    for rows in (rec.get("sections") or {}).values():
        for e in rows or []:
            out += [str(e.get(k) or "") for k in ("item", "party", "notes")]
            out.append(str((e.get("extension") or {}).get("detail") or ""))
    return out


def seed(
    run: Any,
    finals: list[dict[str, Any]],
    provenance: str,
    hand: set[str],
    completes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Save ``finals`` (rows shaped as ``final.json``, corrections made before
    the merge applied) against ``run``'s job: each case's key is computed from
    that job's own texts with today's code. ``completes`` (rows shaped as
    ``final-complete.json``, corrections made after the merge applied) are the
    merged rows as delivered, kept against the lane's current status values.
    ``hand`` names the matter numbers read by hand, which need a provenance
    note. Returns what was saved and every case left out with its reason."""
    from ..manifest import load_prior

    by_mid = {str(c.get("matter_id")): c for c in completes or [] if isinstance(c, dict)}
    planned = run._plan()
    hits = run._json("kind_hits.json") if (run.data / "kind_hits.json").is_file() else {}
    how = method({p: run.firm.model(p) for p in batch.PASSES})
    saved, excluded = [], []
    for rec in finals:
        mid = str((rec or {}).get("matter_id") or "")
        why = problems(rec)
        if why is None and mid not in planned:
            why = "not a case of the source job"
        if why is None and str(rec.get("number")) in hand and not str(rec.get("provenance") or "").strip():
            why = "read by hand without a provenance note"
        if why is not None:
            excluded.append({"number": (rec or {}).get("number"), "why": why})
            continue
        comp = by_mid.get(mid)
        if comp is not None:
            bad = problems({**rec, "sections": comp.get("sections"), "flags": []})
            if bad is not None:
                excluded.append({"number": rec.get("number"), "why": f"merged rows: {bad}"})
                continue
        ctx, chunks = run._chunks(mid)
        k = key(ctx, chunks, how)
        note = str(rec.get("provenance") or provenance)
        prior = load_prior(run.state, mid) if comp is not None else None
        save(run.state, mid, record(k, rec, hits.get(mid), run.job.job_id, note, comp, prior))
        saved.append(rec.get("number"))
    return {"saved": saved, "excluded": excluded}


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.litigation.discovery12.carry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("purge", help="remove one matter's saved breakdown record")
    pg.add_argument("--matter", required=True)
    pg.add_argument("--state-dir", default=None)
    a = ap.parse_args(argv)
    gone = purge(state_dir(a.state_dir), a.matter)
    print(
        json.dumps(
            {"matter": a.matter, "removed": gone, "present_after": path(state_dir(a.state_dir), a.matter).exists()}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
