"""Every site that downloads a firm's file, through every scenario a download meets.

A site is the lane's own entry point to its download (``SITES``); a scenario is
what the seat does when asked (``SCENARIOS``, scripted on ``reader_kit.Seat``).
Each cell records the seat's calls in order (every mint with its ids, every
fetch with the size it was told to expect and what it served or answered),
every sleep, what the site returned, and every file it left in its download
directory (its manifest rows parsed, the downloaded bytes as their length).

The sites, each called as its lane calls it: ``stages.download.run(sr)``,
``stages.msg.pull_containers``, ``stages.update_base._pull``,
``demand.pull.fetch_all``, ``litigation.fetch.fetch_matter``,
``NegotiationRun._fetch``, ``ReviseRun._download`` and
``proof.readback.confirm_files``.

Three files ride every scenario: ``a`` (a typed PDF), ``b`` (a scan), ``c`` (a
text note). The scenarios act on ``a`` or ``b`` as their names say.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import reader_kit as rk
from reader_fixtures import FIXTURES

MID = "11111111-1111-4111-8111-111111111111"
KEYS = {"a": "digital", "b": "scan2", "c": "note_txt"}
#: The file a changed size is served as (the vendor's copy edited after the listing).
EDITED = FIXTURES["typed60"].data[:5000]
EDITED_AGAIN = FIXTURES["typed60"].data[:6000]


def _ids() -> dict[str, str]:
    return {k: FIXTURES[v].id for k, v in KEYS.items()}


def _seat(scenario: str) -> rk.Seat:
    docs, blobs = rk.listing(list(KEYS.values()))
    seat = rk.Seat(docs, blobs, [{"id": "fold-med", "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"}])
    i = _ids()
    if scenario == "mint_fails_once":
        seat.mint_raises = 1
    elif scenario == "mint_fails_twice":
        seat.mint_raises = 2
    elif scenario == "size_changed":
        seat.script[i["a"]] = [("bytes", EDITED)]
    elif scenario == "size_mismatch_twice":
        seat.script[i["a"]] = [("bytes", EDITED), ("bytes", EDITED_AGAIN), ("bytes", EDITED), ("bytes", EDITED_AGAIN)]
    elif scenario == "http_404":
        seat.script[i["b"]] = [("status", 404)]
    elif scenario == "http_403":
        seat.script[i["b"]] = [("status", 403)]
    elif scenario == "http_429_then_ok":
        seat.script[i["b"]] = [("status", 429), "ok"]
    elif scenario == "storage_error_body":
        seat.script[i["b"]] = ["nosuchkey"]
    elif scenario in ("byte_duplicate", "duplicate_chain"):
        twins = ["b"] if scenario == "byte_duplicate" else ["b", "c"]
        for t in twins:
            seat.blobs[i[t]] = seat.blobs[i["a"]]
            next(d for d in seat.docs if d["id"] == i[t])["size"] = len(seat.blobs[i["a"]])
    return seat


SCENARIOS = (
    "clean",
    "resume",
    "mint_fails_once",
    "mint_fails_twice",
    "size_changed",
    "size_mismatch_twice",
    "http_404",
    "http_403",
    "http_429_then_ok",
    "byte_duplicate",
    "duplicate_chain",
    "storage_error_body",
)


def _attempt(fn: Callable[[], Any]) -> Any:
    try:
        return {"returned": fn()}
    except Exception as exc:  # noqa: BLE001 - the ending IS the record: its class and message
        return {"raised": type(exc).__name__, "message": str(exc)[:300]}


def _resume_first(seat: rk.Seat) -> None:
    """The interrupted first attempt of ``resume``: ``c`` could not be minted."""
    seat.blobs["__held"] = seat.blobs.pop(_ids()["c"])


def _resume_second(seat: rk.Seat) -> None:
    seat.blobs[_ids()["c"]] = seat.blobs.pop("__held")


def _twice(scenario: str, seat: rk.Seat, once: Callable[[], Any]) -> list[Any]:
    """One attempt, or for ``resume`` an interrupted attempt and the one after it."""
    if scenario != "resume":
        return [_attempt(once)]
    _resume_first(seat)
    first = _attempt(once)
    _resume_second(seat)
    return [first, _attempt(once)]


# ---- the sites ---------------------------------------------------------------------------
def _medchron_sr(tmp: Path, seat: rk.Seat) -> Any:
    from reader_lanes import _stage_run

    return _stage_run(tmp / "mc", rk.Model(), seat)


def medchron_download(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from jobs.medchron import decisions
    from jobs.stages import download
    from jobs_testkit import seed_seat_files

    sr = _medchron_sr(tmp, seat)
    seed_seat_files(sr.slug_dir.parent, seat)  # type: ignore[arg-type]
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    return _twice(scenario, seat, lambda: download.run(sr)), sr.slug_dir


def msg_containers(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from jobs.stages import msg

    sr = _medchron_sr(tmp, seat)
    targets = [dict(d) for d in seat.docs]
    fpath = {"fold-med": "/MEDICAL"}
    return _twice(scenario, seat, lambda: msg.pull_containers(sr, targets, fpath)), sr.slug_dir


def update_base(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from jobs.stages import update_base as ub

    sr = _medchron_sr(tmp, seat)
    rows = [{"id": d["id"], "name": d["name"] + d["ext"], "size": d["size"]} for d in seat.docs]
    dest = sr.slug_dir / "update-base"
    return _twice(scenario, seat, lambda: ub._pull(sr, rows, dest)), sr.slug_dir


def demand_pull(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from jobs.demand import pull

    data = tmp / "demand"
    targets = [
        {"id": d["id"], "name": d["name"], "ext": d["ext"], "size": d["size"], "folder": "/MEDICAL"} for d in seat.docs
    ]
    return _twice(scenario, seat, lambda: pull.fetch_all(seat, MID, targets, data, lambda _m: None)), data


def litigation_fetch(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from jobs.litigation import fetch

    data = tmp / "lit"
    ids = [d["id"] for d in seat.docs]
    return _twice(scenario, seat, lambda: fetch.fetch_matter(seat, MID, seat.docs, ids, data, lambda _m: None)), data


def negotiation_fetch(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from reader_lanes import _pricing

    from jobs.negotiation.run import NegotiationRun

    jd = tmp / "neg-job"
    (jd / "data").mkdir(parents=True)
    job = {"kind": "negotiation", "job_id": "01KTJ0BX0000000000000000NG", "slug": "example",
           "matter_statuses": ["Open"], "firm_words": ["example"], "per_job_cap_usd": 50.0,
           "monthly_budget_usd": 100.0, "month_cents_used": 0}  # fmt: skip
    (jd / "job.json").write_text(json.dumps(job), encoding="utf-8")
    run = NegotiationRun(jd, state_dir=tmp / "neg-state", pricing=_pricing(tmp), seat_factory=lambda: seat,
                         client=rk.Model(), log=lambda _m: None)  # fmt: skip

    def once() -> list[Any]:
        return [str(run._fetch(MID, dict(d))) for d in seat.docs]

    return _twice(scenario, seat, once), run.data


def revise_download(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    from types import SimpleNamespace

    from jobs.drafting.revise.run import ReviseRun

    run = object.__new__(ReviseRun)
    run.data = tmp / "revise"
    run.job = SimpleNamespace(file_to_id=MID)
    run._seat, run._seat_factory = seat, None
    run.revise = {"files": [{"role": "draft", "file_id": d["id"]} for d in seat.docs[:1]]
                  + [{"role": "attorney_notes", "file_id": d["id"]} for d in seat.docs[1:2]]}  # fmt: skip

    def once() -> list[Any]:
        return [str(run._download(role)) for role in ("draft", "attorney_notes")]

    return _twice(scenario, seat, once), run.data


def readback(tmp: Path, seat: rk.Seat, scenario: str) -> tuple[list[Any], Path]:
    import hashlib

    from jobs.proof import readback as rb

    scratch = tmp / "readback"
    expected = [{"name": d["name"] + d["ext"], "sha256": hashlib.sha256(FIXTURES[k].data).hexdigest(),
                 "bytes": len(FIXTURES[k].data)} for d, k in zip(seat.docs, KEYS.values())]  # fmt: skip

    def once() -> Any:
        return rb.confirm_files(seat, MID, "fold-med", expected, scratch, tries=2, pause=7.0, sleep=time.sleep)

    return _twice(scenario, seat, once), scratch


SITES: dict[str, Callable[[Path, rk.Seat, str], tuple[list[Any], Path]]] = {
    "medchron_download": medchron_download,
    "msg_containers": msg_containers,
    "update_base": update_base,
    "demand_pull": demand_pull,
    "litigation_fetch": litigation_fetch,
    "negotiation_fetch": negotiation_fetch,
    "revise_download": revise_download,
    "readback": readback,
}
#: Every site runs every scenario; none is declared not applicable.
NOT_APPLICABLE: dict[str, str] = {}


def cell(site: str, scenario: str) -> Callable[[Path, Any], Any]:
    def run(tmp: Path, mp: Any) -> Any:
        sleeps = rk.Sleeps(mp)
        seat = _seat(scenario)
        attempts, where = SITES[site](tmp, seat, scenario)
        return rk.scrub(
            {"attempts": attempts, "seat": seat.events, "sleeps": sleeps.calls, "files": rk.tree(where, tmp)}, tmp
        )

    return run


__all__ = ["NOT_APPLICABLE", "SCENARIOS", "SITES", "cell"]
