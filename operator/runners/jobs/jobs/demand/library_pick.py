"""Which Word file a demand is written in: the demand job's one caller of the
library resolver (design 2026-11-06, piece 2).

The demand's writer (``house.py``, ``house_numbered.py``) clones a reference
file: one of the firm's own demands holding one tagged prototype of each
paragraph and table kind. Today that file is a pinned firm input, chosen by
variant and by the matter's responsible attorney (``firm.input_path
("house_reference")`` after ``run._select_format``). This module asks the
resolver first, with the same variant and attorney, and the writer and its
format check are unchanged:

* ``none_accepted`` (every seat today): the pinned reference, exactly as today.
* ``resolved``: the accepted file is the reference, written into the job's data
  folder and handed to the same writer. It must be a "prototypes" file
  (``ledger.PROTOTYPES``), the only shape the writer reads; any other contract
  stops the job. While a pinned reference also exists the accepted file wins,
  and the record names the pin it replaced.
* any other answer: the job stops before anything is spent, failed for SMD
  (``DemandFailed``: resumable, no client message, SMD alerted), with the
  resolver's reason. Never the pinned file in its place.

A variant has its own slot in the ledger (``caller.slot_kind``):
``demand_letter.pre_suit``, ``demand_letter.litigation``, because the two are
written in different reference files.

The record is ``library-resolution.json`` in the job's data folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from operator_connector_sdk.library import (
    NONE_ACCEPTED,
    PROTOTYPES,
    REFUSED,
    RESOLVED,
    Attorney,
    LibraryStore,
    Resolution,
    read_installed,
    resolve,
)
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    DEMAND_JOB,
    NOT_STARTED,
    PINNED,
    BaseUsed,
    LazyStore,
    attorney_key,
    build_record,
    slot_kind,
    write_record,
)
from operator_connector_sdk.library.resolver import replace_status

from .outcome import DemandFailed

KIND = "demand_letter"
#: Where an accepted reference is written for the writer to open.
REFERENCE_NAME = "library-reference.docx"


def spec_dir() -> Path:
    from ..drafting.firm_rules import _spec_dir

    return _spec_dir()


def open_store(seat: Any) -> LibraryStore:
    """The firm's library, over the seat's case-system client. Opened only when
    the ledger names a file; a seat without a client cannot look."""
    from smokeball_connector.library import load_library_config
    from smokeball_connector.library_store import SmokeballLibrary

    return SmokeballLibrary(seat.client, load_library_config())


def _attorneys(firm: Any, who: str | None) -> tuple[Attorney | None, list[Attorney]]:
    known = [Attorney(attorney_key(name), str(row["signer"])) for name, row in firm.data["attorneys"].items()]
    signer = firm.attorney.get("signer")
    matter = Attorney(attorney_key(who), str(signer or who)) if who else None
    return matter, known


def _stop(run: Any, res: Resolution) -> DemandFailed:
    write_record(run.data, build_record(DEMAND_JOB, res, BaseUsed(NOT_STARTED)))
    return DemandFailed(
        f"the firm's document library cannot give this demand's file ({res.status}: {res.reason}); "
        "it was not written on another file. Nothing was spent."
    )


def pick_reference(run: Any) -> Path | None:
    """The reference file the demand is written in, after the variant and the
    attorney are selected. None when the demand is already rendered (a resume
    files the bytes on disk and writes nothing new). Raises ``DemandFailed``."""
    if run._is_done("render"):
        return None
    firm = run.firm
    pinned = firm.input_path("house_reference")
    pinned_used = BaseUsed.of(PINNED, pinned.read_bytes(), name=pinned.name, contract=PROTOTYPES)
    matter, known = _attorneys(firm, run._json("facts.json").get("responsible_attorney"))
    store = LazyStore(lambda: open_store(run.seat))
    res = resolve(
        store,
        read_installed(spec_dir()),
        slot_kind(KIND, firm.variant_name),
        matter_attorney=matter,
        known_attorneys=known,
    )
    if res.status == NONE_ACCEPTED:
        write_record(run.data, build_record(DEMAND_JOB, res, pinned_used))
        return pinned
    if res.status != RESOLVED or res.data is None or res.file is None:
        raise _stop(run, res)
    if res.file.contract != PROTOTYPES:
        raise _stop(
            run,
            replace_status(
                res,
                REFUSED,
                f"the accepted file is a {res.file.contract} file; the demand writer reads only a prototypes file",
            ),
        )
    dest = run.data / REFERENCE_NAME
    part = dest.with_name(f".{dest.name}.tmp")
    part.write_bytes(res.data)
    part.replace(dest)
    used = BaseUsed.of(ACCEPTED, dest.read_bytes(), contract=res.file.contract)
    write_record(run.data, build_record(DEMAND_JOB, res, used, overridden=pinned_used))
    return dest


__all__ = ["KIND", "REFERENCE_NAME", "open_store", "pick_reference", "spec_dir"]
