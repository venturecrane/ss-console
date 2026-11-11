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
library is asked only whether a newer version exists. A newer one is the firm's
own save and goes through ``jobs/library_edit`` (design piece 4); a prototypes
file is refused there (``contract_not_checkable``), so the demand is written in
the accepted copy and SMD is paged, until a later piece can judge one.

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
    known_attorneys,
    matter_attorney,
    write_record,
)
from operator_connector_sdk.library import readers, roster as roster_mod
from operator_connector_sdk.library.resolver import replace_status

from .. import library_edit
from ..attorneys.seat import roster_for
from . import house_crabtree, terms
from .outcome import DemandFailed, DemandHold
from .signers import signer_for, who_signs

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


def _attorneys(run: Any, firm: Any, who: str | None) -> tuple[Attorney | None, list[Attorney]]:
    """The matter's attorney and the firm's, the roster's when one is installed.
    ``select`` has already held a demand whose attorney the roster does not list."""
    known = [Attorney(attorney_key(name), str(row["signer"])) for name, row in firm.data["attorneys"].items()]
    signer = firm.attorney.get("signer")
    roster = roster_for(run)
    found = matter_attorney(who, str(signer or who), roster)
    if found.hold:
        raise _stop(run, Resolution(REFUSED, KIND, found.reason))
    return found.attorney, known_attorneys(roster, known)


def _reader(run: Any) -> dict[str, Any]:
    """Who this demand is written for and the profile in effect (``readers.record``):
    the variant's reader once a variant is selected, else the kind's."""
    variant = run.firm.data.get("_variant")
    return readers.record(readers.DEMAND_JOB, KIND, str(variant) if variant else None)


def _stop(run: Any, res: Resolution) -> DemandFailed:
    write_record(run.data, build_record(DEMAND_JOB, res, BaseUsed(NOT_STARTED), reader=_reader(run)))
    return DemandFailed(
        f"the firm's document library cannot give this demand's file ({res.status}: {res.reason}); "
        "it was not written on another file. Nothing was spent."
    )


def _who(run: Any) -> str | None:
    """The attorney the demand is for: ``select``'s choice once made, else the
    matter's responsible attorney."""
    return run.firm.data.get("_who") or run._json("facts.json").get("responsible_attorney")


def select(run: Any, variant: str) -> tuple[Any, Path | None]:
    """The firm selected for the signing attorney and ``variant``, and the
    reference the demand is written in. The attorney is the one the request
    names, else the matter record's responsible attorney, chosen in code
    (``signers.who_signs``); the demand figure is the request's
    (``terms.demand_figure``), read only for a layout that writes it (the
    crabtree layout). An unmapped attorney, a variant with no format for them,
    or a request naming two figures holds the job (``DemandHold``) before
    anything is spent."""
    firm = run.firm
    request = str(getattr(run.job, "request_text", "") or "")
    roster = roster_for(run)
    who, named = who_signs(firm, run._json("facts.json").get("responsible_attorney"), request, roster)
    signs = signer_for(firm, who, roster)
    if roster.status == roster_mod.OK and not roster.is_attorney(who):
        raise DemandHold(
            f"the {'requested' if named else 'responsible'} attorney ({who or 'none on the matter'}) is not an attorney on the firm's roster; "
            "a demand is never signed for someone who is not. Nothing was spent."
        )
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
            f"no signature is authored for the {'requested' if named else 'responsible'} attorney ({who or 'none on the matter'}); "
            "a demand is never signed for an attorney who has not been mapped. Nothing was spent."
        )
    figure = None
    if firm.variants_for(signs)[variant].get("layout") == "crabtree":
        try:
            figure = terms.demand_figure(request)
        except terms.FigureUnclear as exc:
            raise DemandHold(f"{exc}; which one is the demand is the attorney's call. Nothing was spent.") from None
    run.firm = firm.select(variant, signs, figure)
    run.firm.data["_who"] = who
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
    matter, known = _attorneys(run, firm, _who(run))
    store = LazyStore(lambda: open_store(run.seat))
    spec = spec_dir()
    asked = resolve(
        store,
        read_installed(spec),
        slot_name(KIND, firm.variant_name),
        matter_attorney=matter,
        known_attorneys=known,
        copies_dir=copies_dir(spec),
    )
    considered = library_edit.consider(asked, store, KIND)
    library_edit.leave(run.data, asked, considered)
    res = considered.resolution
    if res.status == NONE_ACCEPTED:
        write_record(run.data, build_record(DEMAND_JOB, res, pinned_used, reader=_reader(run)))
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
    write_record(
        run.data,
        build_record(DEMAND_JOB, res, used, overridden=pinned_used, firm_edit=considered.note, reader=_reader(run)),
    )
    return dest


def decided(firm: Any) -> list[str]:
    """What code decided from the request before drafting (``select``), as
    matter-field lines for the drafter: who signs, and, in the crabtree layout,
    the demand's amount, which the letter carries as a token the renderer fills."""
    if "_variant" not in firm.data:
        return []
    out = []
    who = firm.data.get("_who")
    if who:
        out.append(f"the signing attorney (decided by code): {who}")
    if firm.variant.get("layout") == "crabtree":
        fig = firm.figure
        kind = (
            "a policy-limits demand"
            if fig is None
            else f"a sum-certain demand of {house_crabtree.figure_text(fig)}, named in the request"
        )
        out.append(
            f"the demand (decided by code): {kind}; write {house_crabtree.FIGURE_TOKEN} wherever the letter "
            "states the amount demanded, and never write the amount yourself"
        )
    return out


__all__ = ["KIND", "REFERENCE_NAME", "decided", "open_store", "pick_reference", "select", "spec_dir"]
