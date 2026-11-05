"""File the revision as NEW VERSIONS of the filed files, in place, by id.

The order and the guards are the 04:53 hand filing of 2026-11-04, as code:

1. the brief first, then the attorney notes, and the notes only once the
   brief's new version reads back as ours: the notes describe this revision,
   so a brief held or refused (the firm changed it, it would not read back)
   leaves the filed notes describing the brief that is still filed;
2. COMPARE AND STOP: before each upload the file's current bytes must still be
   the bytes the revision read (``base_sha256``), and its version id is read
   just before the upload and handed to it (``expected_version``): a file the
   firm changed while the revision ran is never written over (held: a person
   decides);
3. every upload's intent is written before it is sent, so a resume after a
   crash never versions a file twice: a file already carrying our bytes is
   done, and one still at the version read before is sent again;
4. the read-back: the file downloaded by id, its sha256 ours, its version id
   moved. Only then is the next file sent, and only all of them is delivered.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from ...proof.readback import remote_sha
from ..outcome import DraftingFailed, DraftingHold

ORDER = ("draft", "attorney_notes")
#: The proven method's read-back: 16 polls, 15 s apart (both landed in one wait).
TRIES = 16
PAUSE_SECONDS = 15.0
SHORT = "stage_unfinished: file: a new version did not read back as ours after its retries"


def _meta(seat: Any, matter_id: str, file_id: str) -> dict[str, Any]:
    m = seat.file_meta(matter_id, file_id)
    return m if isinstance(m, dict) else {}


def _same_file(m: dict[str, Any], p: dict[str, Any], folder_id: str) -> bool:
    """Still this file: not deleted, same folder, same name."""
    return (
        m.get("isDeleted") is False
        and str((m.get("folder") or {}).get("id") or "") == folder_id
        and f"{m.get('name') or ''}{m.get('fileExtension') or ''}" == p["name"]
    )


def _send(seat: Any, matter_id: str, p: dict[str, Any], folder_id: str, vid: str) -> None:
    blob = Path(p["local_path"]).read_bytes()
    try:
        seat.add_file_version(
            matter_id, str(p["file_id"]), blob, expected_version=vid, expected_folder=folder_id, expected_name=p["name"]
        )
    except Exception as exc:  # noqa: BLE001 - which of the two it is decides hold or failure
        if "nothing sent" in str(exc):
            raise DraftingHold(
                f"filing_refused: the filed {p['role']} changed just before its new version was sent; nothing was "
                "written over"
            ) from None
        raise DraftingFailed(
            f"stage_unfinished: file: the new version of the {p['role']} was not accepted ({type(exc).__name__})"
        ) from None


def file_versions(
    seat: Any,
    matter_id: str,
    plan: list[dict[str, Any]],
    data: Path,
    log: Callable[[str], None],
    *,
    folder_id: str,
    tries: int = TRIES,
    pause: float = PAUSE_SECONDS,
) -> list[dict[str, Any]]:
    """``plan``: per role {role, file_id, name (as filed, with extension),
    base_sha256, base_version, local_path, sha256, bytes}. Returns the proof
    files [{name, file_id, size, sha256, version_id}]."""
    state_p = data / "versions.json"
    state: dict[str, Any] = json.loads(state_p.read_text(encoding="utf-8")) if state_p.is_file() else {}

    def save() -> None:
        tmp = data / ".versions.json.tmp"
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(state_p)

    scratch = data / "readback"
    proven: list[dict[str, Any]] = []
    for p in sorted(plan, key=lambda x: ORDER.index(x["role"]) if x["role"] in ORDER else len(ORDER)):
        rec = state.setdefault(p["role"], {})
        fid = str(p["file_id"])
        meta = _meta(seat, matter_id, fid)
        vid = str(meta.get("versionId") or "")
        cur = remote_sha(seat, matter_id, fid, scratch)
        if cur is None or not vid:  # nothing minted or no version read: a lag, never "changed"
            raise DraftingFailed(SHORT)
        if cur != p["sha256"]:
            if rec.get("sent") and vid != rec.get("version_before"):
                raise DraftingHold(
                    f"filing_refused: the filed {p['role']} moved to a version that is not this revision; "
                    "nothing more was written"
                )
            if not rec.get("sent") and (vid != p["base_version"] or cur != p["base_sha256"]):
                raise DraftingHold(
                    f"filing_refused: the filed {p['role']} changed after the revision read it; nothing was "
                    "written over"
                )
            if not _same_file(meta, p, folder_id):
                raise DraftingHold(
                    f"filing_refused: the filed {p['role']} was moved, renamed or deleted; nothing was written"
                )
            rec.update(version_before=vid, sent=True, at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            save()  # the intent, before the send: a resume never sends twice
            _send(seat, matter_id, p, folder_id, vid)
            log(f"  sent a new version of the {p['role']}")
        before = rec.get("version_before")
        for attempt in range(tries):
            meta = _meta(seat, matter_id, fid)
            moved = not before or str(meta.get("versionId") or "") != before
            if moved and str(meta.get("sizeBytes") or "") == str(p["bytes"]):
                break
            if attempt + 1 < tries:
                time.sleep(pause)
        else:
            raise DraftingFailed(SHORT)
        if remote_sha(seat, matter_id, fid, scratch) != p["sha256"] or not _same_file(meta, p, folder_id):
            raise DraftingFailed(f"readback_mismatch: {p['role']}: the new version's bytes by id are not ours")
        version_after = str(meta.get("versionId") or "")
        rec.update(proven=True, version_after=version_after)
        save()
        proven.append(
            {
                "name": p["name"],
                "file_id": fid,
                "size": int(p["bytes"]),
                "sha256": p["sha256"],
                "version_id": version_after,
            }
        )
        log(f"  read back the {p['role']} by id: sha256 ours, version moved")
    return proven
