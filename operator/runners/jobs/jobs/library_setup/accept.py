"""The administrator chose a document: make the library file from it and file it.

The broker queues this job only for a choice it checked in code (the verified
sender is the Named Administrator who asked, the reply names exactly one of the
listed documents, the list is the newest; ``workspace_broker/library_setup_
choose.py``). This job, the jobs uid's child, then:

1. reads the seat's switch again (unreadable holds; off does nothing);
2. downloads the chosen document and requires the bytes the hunt listed (a file
   saved since is not what was chosen: the firm is told, nothing is made);
3. makes the library file (``make.make``): adopt and clean through the kind's
   plan, custom XML dropped, headers and footers judged against the firm's own
   identity, marked places proven writable;
4. reads the slot's accepted file from the seat's ledger (what the acceptance
   says it replaces);
5. files the made file in the firm's Document Library (``file.file_library``,
   through a client that can make exactly one ``add_file`` or
   ``add_file_version`` write and no other);
6. leaves the made file in the job's data folder for the lane, which hands it to
   the broker for the root writer (``establish_intake/library_accept.py``).

THE VERDICT, by outcome: ``filed`` (the lane spools the acceptance),
``delivered`` with ``setup_outcome`` ``changed``, ``unusable`` (it kept words
of its case) or ``not_a_form`` (its layout gives no places to write into; the
firm is told, nothing was filed), ``held`` (SMD's: the firm is told nothing), or
``observed`` (the switch is off: nothing done). Counts, codes and ids only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ..firmbase import Source
from . import file as file_mod, make as make_mod


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is an object, else an empty one."""
    return value if isinstance(value, dict) else {}


MADE_NAME = "library-file.docx"
CHANGED = "changed"
UNUSABLE = "unusable"
#: The plan found no places to write into: the layout, not the words (``make.STRUCTURAL``).
NOT_A_FORM = "not_a_form"


def _switch() -> Any:
    from smokeball_connector import logic_in_code

    return logic_in_code.load_switch(logic_in_code.LIBRARY_SETUP)


def _client() -> Any:
    from smokeball_connector.client import build_client_from_env

    return build_client_from_env()


def identity(inputs_dir: str | None) -> list[str]:
    """The allow list: ``firm_identity`` and the authored attorney blocks."""
    from smokeball_connector.letterhead import load_firm_identity

    from .job import _inputs

    inputs = _inputs(inputs_dir)
    blocks = [a.get("block") or [] for a in (inputs.attorneys if inputs is not None else [])]
    return make_mod.identity_pieces(load_firm_identity(), blocks)


def replaces(kind: str, attorney: str | None, spec_dir: str | Path | None) -> dict[str, str] | None | str:
    """The slot's accepted file ({file_id, version, sha256}), None, or "" when
    the seat's ledger cannot be read."""
    from operator_connector_sdk.library import read_installed

    from ..drafting.firm_rules import _spec_dir

    read = read_installed(spec_dir or _spec_dir())
    if read.ledger is None:  # unusable: "could not look" is never "nothing accepted"
        return ""
    entry = read.ledger.entry_for(kind, attorney)
    return {"file_id": entry.file_id, "version": entry.version, "sha256": entry.sha256} if entry else None


def _held(code: str, **more: Any) -> dict[str, Any]:
    return {"outcome": "held", "live": True, "hold": code, "reason": f"{code}: the library setup job stopped", **more}


def _told(outcome: str, **more: Any) -> dict[str, Any]:
    return {"outcome": "delivered", "live": True, "setup_outcome": outcome, **more}


def _made(job: dict[str, Any], blob: bytes, choice: dict[str, Any], inputs_dir: str | None, pieces: Any) -> Any:
    kind = str(job["setup_kind"])
    source = Source(str(choice["matter_id"]), str(choice["file_id"]), "", hashlib.sha256(blob).hexdigest())
    return make_mod.make(kind, blob, source, identity(inputs_dir) if pieces is None else pieces)


def run(
    job: dict[str, Any],
    job_dir: Path,
    *,
    client: Any = None,
    inputs_dir: str | None = None,
    switch: Callable[[], Any] = _switch,
    pieces: list[str] | None = None,
    spec_dir: str | Path | None = None,
    pause: Any = None,
) -> dict[str, Any]:
    """The verdict for one chosen document (the module docstring)."""
    sw = switch()
    if sw.error is not None:
        return _held("switch_unreadable")
    if not (bool(job.get("live")) and sw.enforcing):
        return {"outcome": "observed", "live": False, "setup_outcome": "nothing_done"}
    choice = _obj(job.get("choice"))
    if not all(choice.get(k) for k in ("file_id", "matter_id", "sha256")):
        return _held("bad_job")
    limited = file_mod.WriteLimited(client if client is not None else _client())
    try:
        blob = limited.download_file(str(choice["matter_id"]), str(choice["file_id"]))[1]
    except Exception:  # noqa: BLE001 - a download that fails is SMD's to look at, never "unusable"
        return _held("could_not_read")
    if hashlib.sha256(blob).hexdigest() != choice["sha256"]:
        return _told(CHANGED)
    try:
        made = _made(job, blob, choice, inputs_dir, pieces)
    except make_mod.Unusable as exc:
        if exc.code == "header_picture_lost":  # our plan's fault, never the firm's: SMD looks
            return _held(exc.code)
        return _told(NOT_A_FORM if exc.code in make_mod.STRUCTURAL else UNUSABLE, unusable=exc.code)
    kind, attorney = str(job["setup_kind"]), job.get("attorney_key") or None
    slot = replaces(kind, attorney, spec_dir)
    if slot == "":
        return _held("ledger_unreadable")
    try:
        filed = file_mod.file_library(limited, kind, job.get("attorney_name") or None, made.data, pause)
    except Exception as exc:  # noqa: BLE001 - any filing fault holds for SMD; never retried blind
        return _held("filing_unproven", why=type(exc).__name__)
    (Path(job_dir) / "data").mkdir(parents=True, exist_ok=True)
    (Path(job_dir) / "data" / MADE_NAME).write_bytes(made.data)
    return {
        "outcome": "filed",
        "live": True,
        "filed": {
            "file_id": filed.file_id,
            "version": filed.version,
            "sha256": filed.sha256,
            "new_file": filed.new_file,
        },
        "fixed_strings": list(made.fixed_strings),
        "residue": made.residue,
        "replaces": slot,
    }


__all__ = ["CHANGED", "MADE_NAME", "NOT_A_FORM", "UNUSABLE", "identity", "replaces", "run"]
