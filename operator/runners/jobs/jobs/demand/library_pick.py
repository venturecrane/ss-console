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
* any other answer (``cannot_look``, ``integrity_failure``,
  ``version_unavailable``, ``copy_missing``, ``refused``): the job stops before anything is spent, failed for SMD
  (``DemandFailed``: resumable, no client message, SMD alerted), with the
  resolver's reason. Never the pinned file in its place.

A variant has its own slot in the ledger (``ledger.slot_name``):
``demand_letter.pre_suit``, ``demand_letter.litigation``, because the two are
written in different reference files.

The accepted bytes come from the seat's own kept copy (``copies_dir``); the
library is asked only whether a newer version exists.

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
    copies_dir,
    read_installed,
    resolve,
    slot_name,
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
    write_record,
)
from operator_connector_sdk.library.resolver import replace_status

from .outcome import DemandFailed, DemandHold

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


def select(run: Any, variant: str) -> tuple[Any, Path | None]:
    """The firm selected for the matter's responsible attorney and ``variant``,
    and the reference the demand is written in. The attorney is chosen in code
    from the matter record; an unmapped attorney, or a variant with no format
    for them, holds the job (``DemandHold``) before anything is spent."""
    firm = run.firm
    who = run._json("facts.json").get("responsible_attorney")
    signs = firm.attorney_for(who)
    # The format is the responsible attorney's when their row names one
    # (2026-10-14); unmapped, the firm's default.
    if variant not in firm.variants_for(signs):
        whose = f"{who}'s demands" if (signs or {}).get("format") else "the firm"
        raise DemandHold(
            f"this is a {variant.replace('_', '-')} demand and no {variant.replace('_', '-')} format is "
            f"authored for {whose}. Nothing was spent."
        )
    if not signs:
        raise DemandHold(
            f"no signature is authored for the responsible attorney ({who or 'none on the matter'}); "
            "a demand is never signed for an attorney who has not been mapped. Nothing was spent."
        )
    run.firm = firm.select(variant, signs)
    return run.firm, pick_reference(run)


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
    spec = spec_dir()
    res = resolve(
        store,
        read_installed(spec),
        slot_name(KIND, firm.variant_name),
        matter_attorney=matter,
        known_attorneys=known,
        copies_dir=copies_dir(spec),
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


__all__ = ["KIND", "REFERENCE_NAME", "open_store", "pick_reference", "select", "spec_dir"]
