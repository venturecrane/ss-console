"""`upload`: the deliverable set onto the matter, into its own dated folder.
$0. The last stage; nothing is written to the firm's system before it and
nothing after.

The filing itself is ``jobs/filing.file_into_new_folder`` (wave 3: one way to
file into a firm's Smokeball, shared with demand, drafting and the update).
Its docstring carries the lessons this stage paid for: the folder id recorded
before the first file goes up (ss#2614), a same-name folder this run did not
create left untouched and the delivery numbered past it (stop audit row 19),
every send recorded as an intent before it starts and as sent after it, so a
lagging vendor list can never cause a second copy (2026-09-24, review
2026-10-06 N12), the vendor's split name and extension composed back
(ss#2914), a record that predates the `sent` flag held (ss#2901), and every
file downloaded back by id and hashed before the stage says delivered
(2026-10-13). A send that raises has its one name read back by hash; proven, it
counts as sent, and not proven, the stage holds for a person (wave 3).
"""

from __future__ import annotations

from .. import filing
from ..filing import EXIT_PRE_FLAG, EXIT_REFUSED, EXIT_SEND_UNKNOWN, EXIT_SHORT, FOLDER_SUFFIX_LIMIT
from ..proof.readback import READBACK_PAUSE_SECONDS, READBACK_TRIES
from .base import StageRun

#: The stage's exits (dag.py reads EXITS). Only EXIT_SHORT is a wait the daemon
#: re-runs (requeue.UPLOAD_SHORT keys on its reason). Before 2026-10-24 a send
#: whose outcome is unknown and a record that predates the `sent` flag also
#: exited 2, so both were re-run four times, 900 s apart, before parking: a
#: re-run can resolve neither, because the runner never resends either name.
EXITS = {
    EXIT_REFUSED: (
        "failed",
        "upload: refused (the local bytes changed since the manifest, every numbered folder name is "
        "already taken, no folder id, or the read-back found other bytes than ours)",
    ),
    EXIT_SHORT: (
        "held",
        "upload: the folder read back short after the retries; the files may still be materializing, nothing was "
        "deleted",
    ),
    EXIT_SEND_UNKNOWN: ("held", "upload: a send failed and its outcome is unknown; a person reads the folder"),
    EXIT_PRE_FLAG: ("held", "upload: delivery.json predates the sent flag; a person reads the folder"),
}


def run(sr: StageRun, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES) -> int:
    return filing.file_into_new_folder(
        lambda: sr.seat, sr.job.matter_id, sr.slug_dir, sr.unit.unit, sr.log, pause=pause, tries=tries
    )


__all__ = [
    "EXITS",
    "EXIT_PRE_FLAG",
    "EXIT_REFUSED",
    "EXIT_SEND_UNKNOWN",
    "EXIT_SHORT",
    "FOLDER_SUFFIX_LIMIT",
    "READBACK_PAUSE_SECONDS",
    "READBACK_TRIES",
    "run",
]
