"""What the status derivation would have done, measured on real requests.

While the seat's ``status_from_events`` switch observes, the firm's list is what
it always was: the read states each status, and every hold fires as before. The
read also reports the dated events, which ride apart from the list's values
(``shadow`` on a defendant, dropped before the gates by ``LitigationRun.final``).

After the gates, ``record`` builds the list the derivation would have made, runs
that copy through the real gates and parity, and writes one file per run:

    <state dir>/status_shadow/runs/<job id>.json

``counts`` holds the run's rate (defendants read, how many differ, how many would
have held). ``rows`` holds one row per defendant on the list: matter id and
number, the index, printed status, derived status, the deciding rule, whether it
differs, whether it would have held and why. A row holds no name and no document
text; ``why`` is a gate's own wording. Read it with:

    python -m jobs.litigation.statusshadow [state dir]

The events themselves are kept per matter in ``status_shadow/events/<matter>.json``
so a later update read, which sees only the arrived papers, is judged on all of
them. They commit with a delivered list, as the matter's values do.

A record that cannot be made is logged and written as an error. It never changes
a verdict: it measures, and a measuring tool must not hold a firm's list.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import defstatus, gates, inventory, manifest, parity, passes, update, vocab
from .tools import dump

_DIR = "status_shadow"
_FIELDS = (*vocab.SHADOW_EVENTS, "role")
_RUN_EVENTS = "status_shadow_events.json"


def _key(d: dict[str, Any]) -> str:
    return parity.party_key(str(d.get("name") or ""))


def _stored_events(state: Path, mid: str) -> dict[str, dict[str, Any]]:
    got = manifest._read(Path(state) / _DIR / "events" / f"{mid}.json")
    return got if isinstance(got, dict) else {}


def _with_events(d: dict[str, Any], *sources: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(d)
    out.pop("shadow", None)
    for src in sources:
        out.update({f: copy.deepcopy(src[f]) for f in _FIELDS if f in src})
    return out


def _derived(d: dict[str, Any], today: dt.date) -> dict[str, Any]:
    d["status_from"] = vocab.FROM_DERIVED
    d["status"] = defstatus.derive(d, today)
    return d


def _read_result(r: Any, mid: str) -> dict[str, Any] | None:
    rec = r._mjson(mid, "read3.json") or r._mjson(mid, "read2.json")
    return rec["result"] if rec else None


def _flat_prior(r: Any, mid: str, stored: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    prior = manifest.load_prior(r.state, mid)
    if prior is None:
        return None
    return {**prior, "defendants": [_with_events(d, stored.get(_key(d), {})) for d in prior.get("defendants") or []]}


def _shadow_matter(r: Any, m: dict[str, Any], p: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The matter as the derivation would have it, and whether its defendants
    were read this run. One this run did not read prints as stored either way."""
    mid = m["matter_id"]
    result = _read_result(r, mid)
    if result is None or vocab.GROUP_DEFENDANTS not in (p.get("read_groups") or []):
        return m, False
    stored = _stored_events(r.state, mid)
    shadows = {_key(d): d.get("shadow") or {} for d in result.get("defendants") or []}
    ds = [_with_events(d, stored.get(_key(d), {}), shadows.get(_key(d), {})) for d in m.get("defendants") or []]
    ds = [_derived(d, r.today) for d in ds]
    out = {**m, "defendants": ds}
    if p.get("mode") == update.MODE:
        prior = _flat_prior(r, mid, stored) or {}
        out, _log = update.guard(
            prior,
            out,
            [vocab.GROUP_DEFENDANTS],
            p["trigger_files"],
            passes.texts(r, mid),
            today=r.today,
            enforcing=True,
        )
    return out, True


def _new_items(now: dict[str, list[Any]], was: dict[str, list[Any]]) -> dict[str, list[Any]]:
    return {k: [x for x in v if x not in was.get(k, [])] for k, v in now.items()}


def _gate_reasons(
    new: dict[str, list[Any]], ms: list[dict[str, Any]]
) -> tuple[dict[tuple[str, int], list[str]], list[str]]:
    """Each newly refused item against its defendant, in the gate's own wording."""
    index = {
        (str(m.get("number") or m.get("matter_id")), str(d.get("name"))): i
        for m in ms
        for i, d in enumerate(m.get("defendants") or [])
    }
    per: dict[tuple[str, int], list[str]] = {}
    matter: list[str] = []
    for rule, items in new.items():
        for item in items:
            number, who = (str(item[0]), str(item[1])) if isinstance(item, tuple) and len(item) > 1 else (str(item), "")
            name, _sep, field = who.partition(": ")
            i = index.get((number, name))
            if i is None:
                matter.append(rule)
            else:
                per.setdefault((number, i), []).append(f"{rule}: {field}" if field else rule)
    return per, sorted(set(matter))


def _parity_reasons(r: Any, m: dict[str, Any], s: dict[str, Any], p: dict[str, Any]) -> dict[int, list[str]]:
    """Each value parity would newly have held on, against its defendant."""
    mid = m["matter_id"]
    prior = _flat_prior(r, mid, _stored_events(r.state, mid))
    kw: dict[str, Any] = {
        "moved_files": set(p.get("moved") or []),
        "today": r.today,
        "unsupported": r._unsupported(p),
        "on_matter": {str(f["id"]) for f in inventory.files_of(r.data, mid)},
    }
    shadow_overturns = [
        {"path": f"defendants[{d.get('name')}].{f}", "source": {"file_id": "check-pass"}}
        for d in (_read_result(r, mid) or {}).get("defendants") or []
        for f in (d.get("shadow") or {}).get("overturned") or []
    ]
    main = parity.compare(manifest.load_prior(r.state, mid), m, overturns=m.get("overturns") or [], **kw)
    shown = parity.compare(prior, s, overturns=[*(m.get("overturns") or []), *shadow_overturns], **kw)
    was = {c["path"] for c in parity.unexplained(main)}
    keys = {_key(d): i for i, d in enumerate(s.get("defendants") or [])}
    out: dict[int, list[str]] = {}
    for c in parity.unexplained(shown):
        if c["path"] in was or c["path"].startswith(vocab.DISCOVERY_PATHS):
            continue
        name, _sep, field = c["path"].removeprefix("defendants[").partition("].")
        out.setdefault(keys.get(name, -1), []).append(f"parity: {field or c['path']} changed with no document cited")
    return out


def _row(
    m: dict[str, Any], s: dict[str, Any], i: int, read_now: bool, why: list[str], today: dt.date
) -> dict[str, Any]:
    printed = (m.get("defendants") or [])[i].get("status")
    d = (s.get("defendants") or [])[i]
    derived = d.get("status") if read_now else printed
    return {
        "matter_id": m.get("matter_id"),
        "number": m.get("number"),
        "index": i,
        "read": read_now,
        "printed": printed,
        "derived": derived,
        "rule": defstatus.decide(d, today, defstatus.stored_input(d)).rule if read_now else None,
        "differs": read_now and derived != printed,
        "would_hold": bool(why),
        "why": sorted(set(why)),
    }


def _counts(rows: list[dict[str, Any]], list_why: list[str], main_held: bool) -> dict[str, Any]:
    read = [x for x in rows if x["read"]]
    return {
        "defendants_on_list": len(rows),
        "defendants_read": len(read),
        "differ": sum(1 for x in read if x["differs"]),
        "would_hold": sum(1 for x in read if x["would_hold"]),
        "list_would_hold": bool(list_why) or any(x["would_hold"] for x in rows),
        "list_held_by_the_gates_as_they_are": main_held,
        "reasons": dict(Counter(w for x in rows for w in x["why"]) + Counter(list_why)),
    }


def _build(
    r: Any,
    ms: list[dict[str, Any]],
    g: dict[str, Any],
    files: dict[str, set[str]],
    saved: dict[str, Any],
    hits: dict[str, list[str]],
    discovery: bool,
) -> dict[str, Any]:
    plan = r._plan()
    pairs = [(m, *_shadow_matter(r, m, plan.get(m["matter_id"]) or {})) for m in ms]
    shadow_ms = [s for _m, s, _read in pairs]
    run = gates.run(
        shadow_ms, today=r.today, files=files, saved=saved, text_of=r._text_of, hits=hits, discovery=discovery
    )
    per, list_why = _gate_reasons(_new_items(run["issues"], g["issues"]), shadow_ms)
    rows, events = [], {}
    for m, s, read_now in pairs:
        p = plan.get(m["matter_id"]) or {}
        held = _parity_reasons(r, m, s, p) if read_now else {}
        list_why += held.get(-1, [])
        number = str(m.get("number") or m.get("matter_id"))
        for i in range(len(m.get("defendants") or [])):
            rows.append(_row(m, s, i, read_now, [*per.get((number, i), []), *held.get(i, [])], r.today))
        if read_now:
            events[m["matter_id"]] = {_key(d): {f: d[f] for f in _FIELDS if f in d} for d in s.get("defendants") or []}
    dump(r.data / _RUN_EVENTS, events)
    return {
        "job_id": r.job.job_id,
        "today": r.today.isoformat(),
        "mode": "observe",
        "counts": _counts(rows, list_why, not g["passed"]),
        "rows": rows,
    }


def _run_file(state: Path, job_id: str) -> Path:
    return Path(state) / _DIR / "runs" / f"{job_id}.json"


def record(
    r: Any,
    ms: list[dict[str, Any]],
    g: dict[str, Any],
    files: dict[str, set[str]],
    saved: dict[str, Any],
    hits: dict[str, list[str]],
    discovery: bool,
) -> None:
    """One run's file. Nothing here can change the verdict the caller is about
    to reach; a failure to measure is written down instead."""
    try:
        doc = _build(r, ms, g, files, saved, hits, discovery)
    except Exception as exc:  # noqa: BLE001 - a measurement must not hold a firm's list
        doc = {"job_id": r.job.job_id, "today": r.today.isoformat(), "mode": "observe", "error": repr(exc)[:300]}
        r.log(f"[{_DIR}] no record for this run: {type(exc).__name__}")
    path = _run_file(r.state, r.job.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest._atomic(path, doc)


def commit_events(r: Any) -> None:
    """A delivered list's events, for the next update to be judged on."""
    got = manifest._read(r.data / _RUN_EVENTS)
    for mid, events in (got if isinstance(got, dict) else {}).items():
        manifest._atomic(Path(r.state) / _DIR / "events" / f"{mid}.json", events)


def rates(state: Path) -> dict[str, Any]:
    """Every run's rate and the running totals."""
    runs = []
    for p in sorted((Path(state) / _DIR / "runs").glob("*.json")):
        doc = manifest._read(p)
        if isinstance(doc, dict):
            runs.append({k: doc.get(k) for k in ("job_id", "today", "error")} | (doc.get("counts") or {}))
    total = {k: sum(int(x.get(k) or 0) for x in runs) for k in ("defendants_read", "differ", "would_hold")}
    total["lists_that_would_have_held"] = sum(1 for x in runs if x.get("list_would_hold"))
    total["runs"] = len(runs)
    return {"total": total, "runs": runs}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    print(json.dumps(rates(Path(args[0]) if args else manifest.state_dir()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
