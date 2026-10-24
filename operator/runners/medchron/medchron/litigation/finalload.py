"""`baseline final`: a delivered job's values into the lane's state, per group.

WHY (2026-10-24). Until this loader, ``report`` was the only writer of lane
state (``run.py``), so a list a person finished by hand (a held job corrected
and filed) left the next run's baseline where the last automatic delivery put
it: the corrected values were bought, delivered, and then read again at full
price. This loader is the second writer and the last step of a hand finish
(``docs/handbook/incident-response.md``): the finished job's
``data/final.json`` goes into ``matters/`` ("last delivered"), the same day,
dry run first.

The rules, each pinned by a test:

* **Per group, never wholesale.** A group (``vocab.GROUP_FIELDS``) the source
  carries (in its ``fields_read``) with every value cited replaces the stored
  group; a group it does not carry is kept. Within defendants, a stored
  ``sent_for_service`` survives when the source's defendant (``identity.
  same_person``) has no such key. ``fields_read`` is the union of the stored
  groups and the groups replaced, less the groups refused (read again next run).
  Provenance advances and the manifest is committed only when a group was
  actually replaced.
* **Refused, not guessed.** A group with a dated value that cites no file id,
  a status with no source, a date not ``YYYY-MM-DD``, or a defendant that is
  not a record is refused: the stored values stand, the group leaves
  ``fields_read`` so the next run reads it, and the refusal is counted. Only the ``final.json``
  shape is read; any other file is refused whole.
* **Em dashes are normalized** (U+2014 to ", ", in every text value outside a
  source) and counted; the gates refuse them in a workbook.
* **Newer state wins.** A matter whose stored ``provenance.read_at`` is after
  the source's is not touched, nor one read the same day by a different job
  (a later same-day delivery is never overwritten).
* **The dry run writes nothing** and reports the counts, plus the
  ``seedcheck.gaps`` the would-be state still has against the job's listing.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import gates, identity, manifest, seedcheck, vocab

DROP = ("overturns", "responsible", "integrity", "provenance")
_DASH = re.compile(r"\s*—\s*")


class FinalShapeError(ValueError):
    """The file is not a job's ``final.json``."""


def _norm(obj: Any, n: list[int]) -> Any:
    """``obj`` with every em dash outside a ``source`` made ", "; ``n[0]`` counts."""
    if isinstance(obj, str):
        n[0] += obj.count("—")
        return _DASH.sub(", ", obj)
    if isinstance(obj, list):
        return [_norm(x, n) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k == "source" else _norm(v, n)) for k, v in obj.items()}
    return obj


def _cited(v: Any) -> str | None:
    """None when a dated value is fit to store, else the refusal's reason."""
    if not isinstance(v, dict):
        return None
    d = v.get("date")
    if d is None or d == "":
        return None
    if not (isinstance(d, str) and gates.DATE.match(d)):
        return "non_iso_date"
    src = v.get("source")
    return None if isinstance(src, dict) and src.get("file_id") else "uncited"


def _values(m: dict[str, Any], g: str) -> list[Any]:
    if g == vocab.GROUP_CASE:
        return [m.get("complaint_filed"), m.get("next_court_date")]
    if g == vocab.GROUP_DEFENDANTS:
        return [d.get(f) for d in m.get("defendants") or [] for f, _k in identity.DATED_FIELDS]
    rows = [*(m.get("discovery_propounded") or []), *(m.get("discovery_served_on_client") or [])]
    return rows + [r.get("responses_served") for r in m.get("discovery_served_on_client") or []]


def refusal(m: dict[str, Any], g: str) -> str | None:
    """Why the source's group ``g`` may not replace the stored one, or None."""
    if any(k not in m for k in vocab.GROUP_FIELDS[g]):
        return "incomplete"
    if g == vocab.GROUP_CASE:
        cs = m.get("case_status")
        if not isinstance(cs, dict) or not cs.get("value"):
            return "uncited"
        if cs.get("value") != vocab.UNCLEAR and not (
            isinstance(cs.get("source"), dict) and cs["source"].get("file_id")
        ):
            return "uncited"
    if g == vocab.GROUP_DEFENDANTS and not isinstance(m.get("defendants"), list):
        return "incomplete"
    if g == vocab.GROUP_DEFENDANTS and not all(isinstance(d, dict) for d in m["defendants"]):
        return "malformed"
    return next((r for r in map(_cited, _values(m, g)) if r), None)


def _carried(src: dict[str, Any]) -> list[str]:
    marked = src.get("fields_read")
    if isinstance(marked, list):
        return [g for g in vocab.GROUPS if g in marked]
    return [g for g in vocab.GROUPS if all(k in src for k in vocab.GROUP_FIELDS[g])]


def _keep_sent(stored: list[Any], fresh: list[Any]) -> int:
    """A stored ``sent_for_service`` onto the fresh defendant that lacks the
    key (the same person, one match only); the count kept."""
    n = 0
    old = [d for d in stored if isinstance(d, dict) and "sent_for_service" in d]
    for d in fresh:
        if not isinstance(d, dict) or "sent_for_service" in d:
            continue
        hits = [o for o in old if identity.same_person(str(o.get("name") or ""), str(d.get("name") or ""))]
        if len(hits) == 1:
            d["sent_for_service"] = copy.deepcopy(hits[0]["sent_for_service"])
            n += 1
    return n


def merge(stored: dict[str, Any] | None, src: dict[str, Any], out: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(the would-be stored matter, the groups replaced); ``out`` gathers the counts."""
    base = copy.deepcopy(stored) if stored else {}
    replaced, refused = [], []
    for g in vocab.GROUPS:
        why = refusal(src, g) if g in _carried(src) else "not_carried"
        if why == "not_carried":
            out["kept"] += 1
            continue
        if why:
            out["refused"][why] = out["refused"].get(why, 0) + 1
            out["kept"] += 1
            refused.append(g)
            continue
        fresh = {k: copy.deepcopy(src[k]) for k in vocab.GROUP_FIELDS[g]}
        if g == vocab.GROUP_DEFENDANTS:
            out["sent_for_service_kept"] += _keep_sent(base.get("defendants") or [], fresh["defendants"])
        base.update(fresh)
        replaced.append(g)
    if replaced:  # the matter's own notes and flags come with a delivered read, never alone
        group_keys = {k for ks in vocab.GROUP_FIELDS.values() for k in ks}
        for k, v in src.items():
            if k not in group_keys and k not in DROP and k != "fields_read":
                base[k] = copy.deepcopy(v)
    out["groups_replaced"] += len(replaced)
    base["fields_read"] = sorted((set(base.get("fields_read") or []) | set(replaced)) - set(refused))
    return base, replaced


def _read_at(final_json: Path) -> str | None:
    st = manifest._read(final_json.parent / "state.json")
    return str(st.get("today")) if isinstance(st, dict) and st.get("today") else None


def _job_id(final_json: Path) -> str:
    jj = manifest._read(final_json.parent.parent / "job.json")
    return str(jj.get("job_id") or "") if isinstance(jj, dict) else ""


def _matters(final_json: Path) -> list[dict[str, Any]]:
    rows = manifest._read(final_json)
    if not isinstance(rows, list) or not all(isinstance(m, dict) and m.get("matter_id") for m in rows):
        raise FinalShapeError(f"{final_json}: not a job's final.json (a list of matters with matter_id)")
    return rows


def _gaps(out: dict[str, Any], m: dict[str, Any], files: list[dict[str, Any]], today: dt.date) -> None:
    g = seedcheck.gaps(m, files, today)
    for grp in g["hard"]:
        out["gaps"]["hard"][grp] = out["gaps"]["hard"].get(grp, 0) + 1
    for grp, pairs in g["soft"].items():
        out["gaps"]["soft"][grp] = out["gaps"]["soft"].get(grp, 0) + len(pairs)


def load_final(
    state: Path,
    final_json: Path,
    files_dir: Path,
    job_id: str | None = None,
    dry_run: bool = False,
    read_at: str | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    state, final_json, files_dir = Path(state), Path(final_json), Path(files_dir)
    src_at = read_at or _read_at(final_json)
    if not src_at or not gates.DATE.match(src_at):
        raise FinalShapeError("no read date: the job's data/state.json carries no 'today' (pass --read-at)")
    job = job_id or _job_id(final_json) or "unknown"
    out: dict[str, Any] = {
        "loaded": 0,
        "unchanged": 0,
        "refused_only": 0,
        "skipped_newer": 0,
        "skipped_no_listing": 0,
        "groups_replaced": 0,
        "kept": 0,
        "sent_for_service_kept": 0,
        "refused": {},
        "normalized": 0,
        "gaps": {"hard": {}, "soft": {}},
        "dry_run": dry_run,
    }
    for raw in _matters(final_json):
        _one(state, raw, files_dir, job, src_at, dry_run, today or dt.date.today(), out)
    return out


def _one(
    state: Path, raw: dict[str, Any], files_dir: Path, job: str, src_at: str, dry: bool, today: dt.date, out: dict
) -> None:
    mid = str(raw["matter_id"])
    stored = manifest.load_prior(state, mid)
    prov = (stored or {}).get("provenance") or {}
    at, by = str(prov.get("read_at") or ""), str(prov.get("job_id") or "")
    if at > src_at or (at == src_at and by and by != job):
        out["skipped_newer"] += 1
        return
    files = manifest._read(files_dir / f"{mid}.json")
    if not isinstance(files, list):
        out["skipped_no_listing"] += 1
        return
    n = [0]
    src = _norm({k: v for k, v in raw.items() if k not in DROP}, n)
    out["normalized"] += n[0]
    new, replaced = merge(stored, src, out)
    if not replaced:  # nothing delivered here is fit to store: only a refused group's mark changes
        if stored:
            _gaps(out, new, files, today)
        marked = stored is not None and new != stored
        out["refused_only" if marked else "unchanged"] += 1
        if marked and not dry:
            manifest._atomic(state / "matters" / f"{mid}.json", new)
        return
    new["provenance"] = {"job_id": job, "read_at": src_at, "loaded_from": "final"}
    _gaps(out, new, files, today)
    unchanged = new == stored and manifest.load_manifest(state, mid) == manifest.current_manifest(files)
    out["unchanged" if unchanged else "loaded"] += 1
    if not dry and not unchanged:
        manifest.commit(state, mid, new, files)


def main(args: list[str]) -> int:
    """``final <final.json> <files_dir> <state> [--job-id X] [--read-at YYYY-MM-DD] [--dry-run]``."""
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in _VALUED)]
    if len(pos) != 4 or pos[0] != "final":
        print(f"usage: python -m medchron.litigation.baseline {main.__doc__}", file=sys.stderr)
        return 2
    opt = {k: args[args.index(k) + 1] for k in _VALUED if k in args}
    try:
        rep = load_final(
            Path(pos[3]),
            Path(pos[1]),
            Path(pos[2]),
            job_id=opt.get("--job-id"),
            dry_run="--dry-run" in args,
            read_at=opt.get("--read-at"),
        )
    except FinalShapeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(rep))
    return 0


_VALUED = ("--job-id", "--read-at")
