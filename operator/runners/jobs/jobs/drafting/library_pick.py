"""Which Word file a drafted document is made in: the drafting job's one caller
of the library resolver (design 2026-11-06, piece 2).

Today the drafting job lays a document in one of two places: the firm's own
Word file for its class when the firm's pinned inputs name one
(``format.firm_bases``, a "marked" file, laid by ``base_lay`` through the class's
plan), else SMD's starter (``render.render`` with no base). This module asks the
resolver first, with the class and the matter's responsible attorney:

* ``none_accepted`` (every seat today): exactly today's choice, by today's code:
  the pinned firm base when there is one, else the starter.
* ``resolved``, a "styles" file: it is the base the renderer opens in place of
  the starter (``render.render(base=...)``); a pinned firm base is not used.
* ``resolved``, a "marked" file: it goes where the pinned firm base goes today
  (``base_lay.lay_out``'s ``held``), for a class that has a plan for one; on a
  class with no plan, or on the placing path (which lays no firm base), the job
  stops.
* ``resolved``, any other contract, and every other answer: the job stops
  (``firm_base_unfit``, the code the lane already stops with for a firm file it
  cannot use: failed, resumable, no client message, SMD alerted), with the
  resolver's reason. Never the pinned file or the starter in its place.

While both an accepted file and a pinned base exist for the class, the
accepted file wins and the record names the pin it replaced. The record is
``library-resolution.json`` in the job's data folder. The answer is asked once
per job run and kept on the run, so every unit of a job is made in one file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import (
    MARKED,
    NONE_ACCEPTED,
    REFUSED,
    RESOLVED,
    STYLES,
    Attorney,
    LibraryStore,
    Resolution,
    read_installed,
    resolve,
)
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    DRAFTING_JOB,
    NOT_STARTED,
    PINNED,
    STARTER,
    BaseUsed,
    LazyStore,
    attorney_key,
    build_record,
    write_record,
)
from operator_connector_sdk.library.resolver import replace_status

from .brief_base import unfit
from .firm_rules import _spec_dir

#: Where an accepted marked file is written for the class's plan to open.
BASE_NAME = "library-base.docx"
_MEMO = "_library_base"


@dataclass(frozen=True)
class DraftBase:
    """What the document is made in: ``styles`` for the renderer, or ``held``
    (path, source) for the class's firm-base plan, or neither (the starter)."""

    resolution: Resolution
    styles: bytes | None = None
    held: tuple[Path, dict[str, str]] | None = None


def open_store(seat: Any) -> LibraryStore:
    """The firm's library, over the seat's case-system client. Opened only when
    the ledger names a file; a seat without a client cannot look."""
    from smokeball_connector.library import load_library_config
    from smokeball_connector.library_store import SmokeballLibrary

    return SmokeballLibrary(seat.client, load_library_config())


def _matter_attorney(run: Any) -> Attorney | None:
    p = run.data / "facts.json"
    facts = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    who = str(facts.get("responsible_attorney") or "").strip()
    return Attorney(attorney_key(who), who) if who else None


def _pinned(run: Any) -> tuple[tuple[Path, dict[str, str]] | None, BaseUsed | None]:
    held = run.firm.firm_base(run.cls)
    if held is None:
        return None, None
    return held, BaseUsed.of(PINNED, held[0].read_bytes(), name=held[1].get("name", ""), contract=MARKED)


def _stop(run: Any, res: Resolution) -> Exception:
    write_record(run.data, build_record(DRAFTING_JOB, res, BaseUsed(NOT_STARTED)))
    return unfit(f"the firm's document library cannot give this document's file ({res.status}: {res.reason})")


def _accepted(run: Any, res: Resolution, marked_ok: bool) -> DraftBase:
    from .base_lay import PLANS

    f, data = res.file, res.data
    if f is None or data is None:
        raise _stop(run, res)
    if f.contract == STYLES:
        return DraftBase(res, styles=data)
    if f.contract == MARKED and marked_ok and run.cls in PLANS:
        dest = run.data / BASE_NAME
        part = dest.with_name(f".{dest.name}.tmp")
        part.write_bytes(data)
        part.replace(dest)
        source = {
            "matter_id": "",
            "file_id": f.file_id,
            "name": f"the firm's accepted library file {f.file_id}",
            "sha256": f.sha256,
            "version": f.version,
            "accepted": f.accepted_by,
        }
        return DraftBase(res, held=(dest, source))
    why = (
        "this class has no plan for a marked file"
        if f.contract == MARKED
        else "the drafting job reads only styles or marked files"
    )
    raise _stop(run, replace_status(res, REFUSED, f"the accepted file is a {f.contract} file; {why}"))


def pick_base(run: Any, *, marked_ok: bool = True) -> DraftBase:
    """The file this job's document is made in. ``marked_ok`` is False on the
    placing path, which lays no firm base. Raises ``DraftingFailed``."""
    kept = getattr(run, _MEMO, None)
    if kept is not None:
        return kept
    held, pinned = _pinned(run)
    store = LazyStore(lambda: open_store(run.seat))
    res = resolve(store, read_installed(_spec_dir()), run.cls, matter_attorney=_matter_attorney(run))
    if res.status == NONE_ACCEPTED:
        today = held if marked_ok else None
        used = pinned if today is not None and pinned is not None else BaseUsed(STARTER)
        write_record(run.data, build_record(DRAFTING_JOB, res, used))
        out = DraftBase(res, held=today)
    elif res.status == RESOLVED and res.file is not None and res.data is not None:
        out = _accepted(run, res, marked_ok)
        used = BaseUsed.of(ACCEPTED, res.data, contract=res.file.contract)
        write_record(run.data, build_record(DRAFTING_JOB, res, used, overridden=pinned if marked_ok else None))
    else:
        raise _stop(run, res)
    setattr(run, _MEMO, out)
    return out


__all__ = ["BASE_NAME", "DraftBase", "open_store", "pick_base"]
