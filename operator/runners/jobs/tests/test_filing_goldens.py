"""Every way of filing work into a firm's Smokeball, recorded from today's code.

``goldens/filing/cells.json`` is the committed matrix: nine lanes (the filing
call each lane makes, ``filing_scenes.py``) by the endings below, each cell
either recorded in ``goldens/filing/<lane>/<ending>.json`` or declared not
applicable there with its reason. A cell missing from either side fails
``test_every_golden_cell_is_declared_and_recorded``.

Each golden is one run on ``filing_kit.RecordingSeat``: every connector call
(ids, names, the sha256 of any bytes), the send record on disk at each call,
every log line, how it ended, the vendor's final state and the count of each
write. ``crash`` is every call of the clean run in turn: the process killed
right after that call returned, then resumed over the same matter.

``resume/<state>.json`` loads a send record frozen in a state the code writes
(``frozen-*.json``, the matrix's ``frozen`` block names each record and what the
vendor holds) and must make ZERO ``add_file`` calls: a record that names a
send must never send it again.

The goldens hold the code: a change to how a file is sent, recorded, read back
or named must leave every cell equal except the paths
``allowed-changes.json`` names for it (``golden-integrity.py`` regenerates them
from the merge-base). ``JOBS_WRITE_KIT_GOLDENS=1`` rewrites them (then read the
diff); ``JOBS_WRITE_FILING_FROZEN=1`` recaptures the frozen records.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import goldens_kit as gk
from filing_kit import Killed, RecordingSeat, normal
from filing_scenes import CHRON_FILES, LANES, UP_EX2, UP_NEW, Scene
from jobs_testkit import doc_row

ROOT = gk.GOLDENS / "filing"
MATRIX = json.loads((ROOT / "cells.json").read_text(encoding="utf-8"))
ALLOWED = gk.allowed("filing")
ENDINGS: tuple[str, ...] = tuple(MATRIX["endings"])
NOT_APPLICABLE: dict[str, str] = dict(MATRIX["not_applicable"])
FROZEN: dict[str, dict[str, str]] = dict(MATRIX["frozen"])
CELLS: tuple[tuple[str, str], ...] = tuple(
    (lane, e) for lane in MATRIX["lanes"] for e in ENDINGS if f"{lane}/{e}" not in NOT_APPLICABLE
)
CAPTURE_ENV = "JOBS_WRITE_FILING_FROZEN"


# ---- one run, one cell ---------------------------------------------------------------
def _run(sc: Scene) -> dict[str, Any]:
    try:
        return {"returned": gk.plain(sc.run())}
    except Killed as k:
        return {"killed_after": str(k)}
    except Exception as exc:  # noqa: BLE001 - the ending IS the exception: its class and message are recorded
        return {"raised": {"type": type(exc).__name__, "message": str(exc)}}


def _doc(sc: Scene, end: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "events": events,
        "end": end,
        "records": sc.seat._snapshot(),
        "vendor": sc.seat.vendor_state(),
        "writes": dict(sorted(sc.seat.writes.items())),
    }


def _fault(sc: Scene, ending: str) -> None:
    seat = sc.seat
    if ending.startswith("send_raises"):
        seat.write_fault = {"send_raises_placeholder": "placeholder", "send_raises_not_landed": "not_landed"}.get(
            ending, "landed"
        )
        seat.list_fails_after_raise = ending == "send_raises_then_listing_raises"
    if ending == "short_readback":
        seat.lag = seat.version_lag = 10**6
    if ending == "bytes_mismatch":
        seat.corrupt = {sc.first}


def _calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if "call" in e]


def crash_cell(lane: str, root: Path, mp: Any) -> dict[str, Any]:
    """The clean run killed after each of its calls in turn, then resumed."""
    probe = LANES[lane](root / "probe", "clean", mp)
    _run(probe)
    runs = []
    for k in range(1, probe.seat.calls + 1):
        sc = LANES[lane](root / f"k{k:02d}", "clean", mp)
        sc.seat.kill_after = k
        first = _run(sc)
        split = len(sc.seat.events)
        sc.seat._last = None  # the resume's first call shows the record it starts from
        end = _run(sc)
        runs.append({"k": k, "killed": first, **_doc(sc, end, sc.seat.events[split:])})
    return {"calls_in_a_clean_run": probe.seat.calls, "runs": runs}


def cell(lane: str, ending: str, root: Path, mp: Any) -> dict[str, Any]:
    if ending == "crash":
        return crash_cell(lane, root, mp)
    sc = LANES[lane](root, ending, mp)
    _fault(sc, ending)
    end = _run(sc)
    return _doc(sc, end, sc.seat.events)


# ---- frozen records -----------------------------------------------------------------------
def _land(seat: RecordingSeat, fid: str, name: str, folder: str, data: bytes) -> None:
    seat.docs.append(doc_row(fid, name, folder, len(data)))
    seat.blobs[fid] = data


def _frozen_scene(state: str, root: Path, mp: Any) -> Scene:
    """The lane's clean scene with the frozen record on disk, and the vendor
    holding what that record says was sent (``vendor``: ``landed``, or
    ``absent-first`` for a send whose POST never landed)."""
    spec = FROZEN[state]
    sc = LANES[spec["lane"]](root, "clean", mp)
    record = json.loads((ROOT / f"frozen-{spec['record']}.json").read_text(encoding="utf-8"))
    target = sc.seat.watch[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1), encoding="utf-8")
    seat = sc.seat
    if spec["lane"] == "revise":
        seat.land_version("draft-1", (target.parent / "new-draft").read_bytes())
        return sc
    local = _local_bytes(spec["lane"], target.parent)
    rows = record.get("files") if "files" in record else [record]
    for i, row in enumerate(rows):
        if spec["vendor"] == "absent-first" and i == 0:
            continue
        fid = str(row.get("file_id") or f"landed-{i + 1}")
        _land(seat, fid, str(row["name"]), str(record["folder_id"]), local[str(row["name"])])
    if record.get("folder_id") and not any(f["id"] == record["folder_id"] for f in seat.folders):
        seat.folders.append({"id": record["folder_id"], "name": record.get("folder") or "x", "parentId": None})
    if (record.get("replaced") or {}).get("deleted"):
        next(d for d in seat.docs if d["id"] == "c1")["deleted"] = True
    return sc


#: The calls that put a file on the matter.
WRITES = frozenset({"add_file", "add_file_version", "client.add_file"})


def _named(record: str) -> set[str]:
    """The names (and, for versions, the file ids) a frozen record says were sent."""
    doc = json.loads((ROOT / f"frozen-{record}.json").read_text(encoding="utf-8"))
    if "files" in doc:
        return {str(f["name"]) for f in doc["files"]}
    if "name" in doc:
        return {str(doc["name"])}
    ids = {"draft": "draft-1", "attorney_notes": "notes-1"}  # filing_scenes.revise
    return {ids[role] for role, r in doc.items() if r.get("sent")}


def _local_bytes(lane: str, record_dir: Path) -> dict[str, bytes]:
    if lane == "chronology":
        return dict(CHRON_FILES)
    if lane == "update":
        return {UP_NEW: b"new chronology bytes", UP_EX2: b"%PDF new exhibit"}
    return {p.name: p.read_bytes() for p in (record_dir / "out").iterdir()}


# ---- the tests -----------------------------------------------------------------------------
def test_every_golden_cell_is_declared_and_recorded():
    """MUTATION: drop a golden, a lane, an ending or a not-applicable reason and this fails."""
    assert set(MATRIX["lanes"]) == set(LANES)
    assert all(k.split("/")[0] in LANES and k.split("/")[1] in ENDINGS for k in NOT_APPLICABLE)
    assert all(len(why) > 20 for why in NOT_APPLICABLE.values()), "each not-applicable cell says why"
    recorded = {p.relative_to(ROOT).with_suffix("").as_posix() for p in ROOT.glob("*/*.json")}
    want = {f"{lane}/{e}" for lane, e in CELLS} | {f"resume/{s}" for s in FROZEN}
    assert recorded == want
    assert {p.name for p in ROOT.glob("frozen-*.json")} == {f"frozen-{s['record']}.json" for s in FROZEN.values()}
    assert set(ALLOWED) - {"_why"} <= want, "an allowed list names no cell"


@pytest.mark.parametrize("lane,ending", CELLS, ids=[f"{lane}/{e}" for lane, e in CELLS])
def test_a_filing_cell_records_what_its_golden_holds(tmp_path, monkeypatch, lane, ending):
    """MUTATION: change how any lane sends, records, reads back or names a file and its cells fail."""
    got = normal(cell(lane, ending, tmp_path, monkeypatch), tmp_path)
    name = f"{lane}/{ending}"
    assert gk.write_or_compare(ROOT / f"{name}.json", got, ALLOWED.get(name, [])) == []


@pytest.mark.parametrize("state", sorted(FROZEN))
def test_a_frozen_record_loads_and_sends_nothing_again(tmp_path, monkeypatch, state):
    """MUTATION: read a pending, sent or confirmed row as unsent, and a file is filed twice; this fails."""
    sc = _frozen_scene(state, tmp_path, monkeypatch)
    end = _run(sc)
    got = normal(_doc(sc, end, sc.seat.events), tmp_path)
    name = f"resume/{state}"
    assert gk.write_or_compare(ROOT / f"{name}.json", got, ALLOWED.get(name, [])) == []
    named = _named(FROZEN[state]["record"])
    again = [e for e in _calls(sc.seat.events) if e["call"] in WRITES and {e.get("name"), e.get("id")} & named]
    assert again == [], f"{state}: a file its record names was sent again"


# ---- recapturing the frozen records (never run by the integrity job) -------------------------
def _kill_index(lane: str, root: Path, mp: Any, pick: Any, ending: str) -> int:
    probe = LANES[lane](root, ending, mp)
    _fault(probe, ending)
    _run(probe)
    calls = _calls(probe.seat.events)
    return 1 + next(i for i, c in enumerate(calls) if pick(calls, i))


def _after_nth(method: str, n: int = 1) -> Any:
    return lambda calls, i: calls[i]["call"] == method and sum(c["call"] == method for c in calls[: i + 1]) == n


def _first_list_after_last(method: str) -> Any:
    def pick(calls: list[dict[str, Any]], i: int) -> bool:
        last = max(j for j, c in enumerate(calls) if c["call"] == method)
        return i > last and calls[i]["call"] == "list_files"

    return pick


#: record -> (lane, where the run is killed, the ending it runs under).
CAPTURE: dict[str, tuple[str, Any, str]] = {
    "dated-pending": ("chronology", _after_nth("add_file"), "clean"),
    "dated-sent": ("chronology", _first_list_after_last("add_file"), "clean"),
    "dated-confirmed": ("chronology", _after_nth("mint"), "clean"),
    "dated-proven": ("chronology", None, "clean"),
    "standing-pending": ("litigation", _after_nth("add_file"), "clean"),
    "standing-sent": ("litigation", lambda calls, i: i > 0 and calls[i - 1]["call"] == "add_file", "clean"),
    "standing-confirmed": ("litigation", None, "clean"),
    "update-proven": ("update", "delete_file", "clean"),
    "update-retired": ("update", None, "clean"),
    "versions-sent": ("revise", _after_nth("add_file_version"), "clean"),
}
#: The states only the one rule for a send that raises writes (wave 3): a row
#: marked sent once its raised send read back by hash, and an update whose
#: files are proven but left unretired because a send raised. Captured only
#: under ``JOBS_WRITE_FILING_FROZEN=new``.
CAPTURE_RAISED: dict[str, tuple[str, Any, str]] = {
    "dated-raised-sent": ("chronology", _after_nth("add_file", 2), "send_raises_landed"),
    "update-raised-proven": ("update", None, "send_raises_landed"),
}


@pytest.mark.skipif(not os.environ.get(CAPTURE_ENV), reason=f"set {CAPTURE_ENV}=1 to recapture the frozen records")
def test_planted_frozen_records_are_recaptured_from_this_code(tmp_path, monkeypatch):
    """Writes ``frozen-<record>.json``: the record each state's run leaves on
    disk, killed after the picked call, on entry to the named method, or run
    to the end. ``dated-pre-flag`` is ``dated-confirmed`` with its ``sent`` and
    ``pending`` keys removed: the shape the code wrote before ss#2901 and still
    reads. ``JOBS_WRITE_FILING_FROZEN=new`` writes ``frozen-new-<record>.json``
    instead, and the raised-send states too: the records a change writes, which
    the integrity job then loads with the merge-base's code (the rollback)."""
    new = os.environ.get(CAPTURE_ENV) == "new"
    for record, (lane, pick, ending) in {**CAPTURE, **(CAPTURE_RAISED if new else {})}.items():
        sc = LANES[lane](tmp_path / record, ending, monkeypatch)
        _fault(sc, ending)
        if isinstance(pick, str):
            sc.seat.kill_before = pick
        elif pick is not None:
            sc.seat.kill_after = _kill_index(lane, tmp_path / f"{record}-probe", monkeypatch, pick, ending)
        _run(sc)
        doc = json.loads(sc.seat.watch[0].read_text(encoding="utf-8"))
        (ROOT / f"frozen-{'new-' if new else ''}{record}.json").write_text(gk.dump(doc), encoding="utf-8")
        if record == "dated-confirmed" and not new:
            pre = {**doc, "files": [{k: v for k, v in f.items() if k not in ("sent", "pending")} for f in doc["files"]]}
            (ROOT / "frozen-dated-pre-flag.json").write_text(gk.dump(pre), encoding="utf-8")
