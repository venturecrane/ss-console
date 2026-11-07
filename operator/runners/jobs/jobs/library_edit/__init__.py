"""A firm's own edit of its accepted library file, used from the next draft on
with nobody at SMD doing anything (design 2026-11-06, "What a firm
experiences" paragraph 5, "How it is built" paragraph 4).

WHAT HAPPENS. A drafting or demand job asks the resolver which file it is made
in. When the resolver answers with the accepted file AND reports that the
library holds a newer saved version of it (``Resolution.newer``), that version
is the firm's own save. :func:`consider`:

1. fetches it, read only (``LibraryStore.fetch_version``; nothing is filed);
2. cleans it and checks it against the slot's accepted copy, the seat-kept one
   by fingerprint (``check.py``: every step a code);
3. on a pass, makes THIS draft in the cleaned bytes with no one's yes: the
   resolution the job uses is the firm's version, ``accepted_by``
   ``firm_made``, with the case system's version id and the cleaned bytes'
   sha256 (two values: the version the firm saved and the bytes we kept). The
   job leaves the acceptance in its data folder (:data:`EDIT_JSON`,
   :data:`EDIT_DOCX`) and the root daemon hands it to the root writer after
   the child (``handoff.py``), through the same spool and intake scope 3b built
   (``library_accept``); the record says ``accepted_pending_install`` because a
   job cannot write the root store and the intake answers after the draft;
4. on a refusal, makes the draft in the accepted copy, exactly as before this
   piece, and leaves the refusal's code for the daemon to page SMD with. The
   firm is told nothing here (``library_setup_wording.EDIT_NOT_USED``, behind
   ``logic_in_code.library_edit_notice``, off).

The record beside the document (``library-resolution.json``) carries the
``firm_edit`` block: the version considered, the one it replaces, used or the
refusal's code, the sha256 kept, when.

DARK. It acts only on a slot that HAS an accepted file, and no seat has one:
a file is accepted only through "set up our ...", behind
``logic_in_code.library_setup``, observe everywhere. With nothing accepted the
resolver answers ``none_accepted`` and this module is never reached.

No model call.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import RESOLVED, LibraryUnavailable, Resolution, UsedFile
from operator_connector_sdk.library.caller import EDIT_REFUSED, EDIT_USED, FIRM_MADE
from operator_connector_sdk.library.resolver import ATTORNEY_FILE

from . import check

#: What the job leaves for the root daemon in its data folder.
EDIT_JSON = "library-edit.json"
EDIT_DOCX = "library-edit.docx"
#: The record's ``install`` before the intake has answered (3b's two-value answer).
PENDING = "accepted_pending_install"


@dataclass(frozen=True)
class Considered:
    """What the document is made in after the firm's save was considered, and
    the record's ``firm_edit`` block (None when there was no newer version)."""

    resolution: Resolution
    note: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(status: str, code: str, res: Resolution, sha: str | None, at: str, install: str) -> dict[str, Any]:
    newer = res.newer
    return {
        "status": status,
        "code": code,
        "version": newer.newest_version if newer else "",
        "replaces_version": newer.accepted_version if newer else "",
        "sha256": sha,
        "accepted_at": at,
        "install": install,
    }


def _refused(res: Resolution, code: str) -> Considered:
    return Considered(res, _note(EDIT_REFUSED, code, res, None, "", ""))


def _fetch(store: Any, res: Resolution) -> bytes | None:
    f, newer = res.file, res.newer
    try:
        return store.fetch_version(f.file_id, newer.newest_version) if f and newer else None
    except LibraryUnavailable:
        return None


def consider(res: Resolution, store: Any, cls: str) -> Considered:
    """The resolution a job's document is made in, a firm's newer save considered.
    ``cls`` is the kind the firm-base plan loads a marked file as. Never raises
    for the firm's file: every way it cannot be used is a code."""
    if res.status != RESOLVED or res.newer is None or res.file is None or res.data is None:
        return Considered(res)
    blob = _fetch(store, res)
    if blob is None:
        return _refused(res, "could_not_fetch")
    try:
        cleaned = check.clean(blob)
    except Exception:  # noqa: BLE001 - a save the cleaner cannot open is a code, never a crash
        return _refused(res, "not_a_word_file")
    codes = check.problems(cls, res.file.contract, res.data, cleaned)
    if codes:
        return _refused(res, ",".join(codes))
    at = _now()
    sha = hashlib.sha256(cleaned).hexdigest()
    old = res.file
    used = UsedFile(
        old.file_id, res.newer.newest_version, sha, old.contract, old.scope, FIRM_MADE, at, old.chart_source
    )
    made = Resolution(RESOLVED, res.kind, signer=res.signer, file=used, newer=None, data=cleaned)
    return Considered(made, _note(EDIT_USED, "", res, sha, at, PENDING))


def acceptance(before: Resolution, after: Considered) -> dict[str, Any] | None:
    """The acceptance the root writer is handed for a used edit, in
    ``library_accept.SUBMISSION_FIELDS``' shape; None for anything else."""
    note, f, old = after.note, after.resolution.file, before.file
    if note is None or note["status"] != EDIT_USED or f is None or old is None or after.resolution.data is None:
        return None
    attorney = before.signer.key if old.scope == ATTORNEY_FILE and before.signer else None
    return {
        "kind": before.kind,
        "attorney": attorney,
        "file_id": f.file_id,
        "version": f.version,
        "contract": f.contract,
        "accepted_by": FIRM_MADE,
        "accepted_at": f.accepted_at,
        "sha256": f.sha256,
        "fixed_strings": sorted(check.fixed_of(before.data or b"")),
        "replaces": {"file_id": old.file_id, "version": old.version, "sha256": old.sha256},
        "chart_source": old.chart_source,  # a firm's look edit keeps where its memo's tables' look came from
    }


def _write(path: Path, data: bytes) -> None:
    part = path.with_name(f".{path.name}.tmp")
    part.write_bytes(data)
    os.replace(part, path)


def leave(data_dir: Path, before: Resolution, after: Considered) -> None:
    """What the root daemon reads after the child: the acceptance and its bytes,
    or the refusal's code. Nothing when there was no newer version."""
    if after.note is None:
        return
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    acc = acceptance(before, after)
    doc: dict[str, Any] = {"note": after.note, "acceptance": acc}
    if acc is not None and after.resolution.data is not None:
        _write(folder / EDIT_DOCX, after.resolution.data)
    _write(folder / EDIT_JSON, (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))


__all__ = ["EDIT_DOCX", "EDIT_JSON", "PENDING", "Considered", "acceptance", "consider", "leave"]
