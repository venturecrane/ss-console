"""`file`: the workbook into the library matter's standing folder, read back.

Unlike the chronology's upload stage, the folder here is a STANDING one (the
firm's "Litigation Status" folder holds every list, by hand or by the
Operator), so an existing folder of that name is the destination, not a
refusal. The filing is ``jobs/filing.file_into_standing_folder`` (wave 3, one
way to file into a firm's Smokeball); what it keeps are the lessons the upload
stage paid for:

* the file's name is this job's own (date + job suffix), so a name already in
  the folder that this run did not send is somebody else's: refused (held);
* a send is recorded as an intent BEFORE ``add_file`` and as sent after it,
  and a name with either row is never sent again (a lost response must not
  become a second copy on the firm's matter);
* the read-back is the only confirmation: the folder listed until the name
  appears at its byte count, then the file DOWNLOADED and its sha256 compared
  with the bytes built here (``proof.readback``). A short read-back fails
  (resumable), it is never reported delivered; different bytes fail as
  ``readback_mismatch``, which pages SMD (wrong content on the firm's matter,
  never a lag).

Casefile, deadlines and discovery12 file through ``file_workbook`` too, each
with its own ``record``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .. import filing
from ..proof.readback import READBACK_PAUSE_SECONDS, READBACK_TRIES
from .outcome import LitigationFailed, LitigationHold


def file_workbook(
    seat: Any,
    matter_id: str,
    folder_name: str,
    local: Path,
    data: Path,
    log: Callable[[str], None],
    *,
    tries: int = READBACK_TRIES,
    pause: float = READBACK_PAUSE_SECONDS,
    record: str = "delivery.json",
) -> dict[str, Any]:
    """``{folder_id, file_id, name, size, sha256}`` once read back and equal.
    ``record`` names this file's own send record: each workbook a job files
    (the list, then its one-sheet-per-case copy) keeps its own, so a resume
    never resends either. Raises ``LitigationHold`` or ``LitigationFailed``."""
    try:
        return filing.file_into_standing_folder(
            seat, matter_id, folder_name, local, data, log, tries=tries, pause=pause, record=record
        )
    except filing.FilingHeld as exc:
        raise LitigationHold(str(exc)) from None
    except filing.FilingFailed as exc:
        raise LitigationFailed(str(exc)) from None


__all__ = ["READBACK_PAUSE_SECONDS", "READBACK_TRIES", "file_workbook"]
