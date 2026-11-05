"""One drafting job as the runner reads it: the broker's envelope plus what the
lane stamps before every run (the seat's slug and the month's drafting spend,
read fresh, never taken from the envelope).

The envelope (the broker's ``drafting_job_submit`` writes exactly this)::

    {"kind": "drafting", "job_id": <ULID>, "matter_id": str, "matter_number": str,
     "document_class": one of CLASSES, "requester": email, "message_ref": str,
     "request_text": str, "file_to_matter_id": str|null, "file_to_matter_number": str|null,
     "attachments"?: [{"filename": .docx, "sha256", "b64"}],   (the request email's own Word documents)
     "revise"?: {"prior_job_id": <ULID>, "folder_id": str,
                 "files": [{"role", "file_id", "name", "size", "sha256"}]}}
                 (revise mode: the broker named the filed files from its delivery proof)

Re-validated here rather than imported from the broker, whose package is not on
the runner's venv: the runner refuses an envelope it cannot read the same way
the broker refused to queue one. Not demand's ``job.py``, which refuses any
kind but ``demand`` and carries a different envelope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .firm import CLASSES

JOB_FILE = "job.json"
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_NUMBER = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")


class DraftingJobError(ValueError):
    pass


@dataclass(frozen=True)
class DraftingJob:
    job_dir: Path
    job_id: str
    slug: str
    matter_id: str
    matter_number: str
    document_class: str
    requester: str
    message_ref: str
    request_text: str
    file_to_id: str
    file_to_number: str
    month_cents_used: int
    #: Drafting jobs left this cycle with this one excluded; None on a laptop run.
    allowance_remaining: int | None = None
    #: The request email's own Word attachments, (filename, bytes), as the
    #: broker carried them (sha256-checked here again): an attorney's own sets
    #: to place in the caption (place.py).
    attachments: tuple[tuple[str, bytes], ...] = ()
    #: Ledger-path briefs SMD has released for this firm, counted by the lane
    #: from its release record (``drafting_lane.RELEASES``); None when the lane
    #: did not stamp it, which counts as none released.
    ledger_releases: int | None = None
    #: Revise mode (``drafting/revise``): the prior delivered job and its filed
    #: files by id, as the BROKER resolved them from its delivery proof. None
    #: for a fresh draft.
    revise: dict[str, Any] | None = None

    @property
    def data(self) -> Path:
        return self.job_dir / "data"


def _str(data: dict[str, Any], key: str) -> str:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        raise DraftingJobError(f"{key} is required")
    return v.strip()


def parse(data: Any, job_dir: Path) -> DraftingJob:
    if not isinstance(data, dict) or data.get("kind") != "drafting":
        raise DraftingJobError("not a drafting job (kind must be 'drafting')")
    job_id, mid, number = _str(data, "job_id"), _str(data, "matter_id"), _str(data, "matter_number")
    if not _ULID.match(job_id):
        raise DraftingJobError("job_id must be a ULID")
    if not _UUID.match(mid) or not _NUMBER.match(number):
        raise DraftingJobError("matter_id and matter_number must name the matter")
    cls = data.get("document_class")
    if cls not in CLASSES:
        raise DraftingJobError(f"document_class must be one of {list(CLASSES)}")
    requester = _str(data, "requester")
    if not _EMAIL.match(requester):
        raise DraftingJobError("requester must be an email address")
    fid, fnum = data.get("file_to_matter_id"), data.get("file_to_matter_number")
    if (fid is None) != (fnum is None):
        raise DraftingJobError("file_to_matter_id and file_to_matter_number come together or not at all")
    if fid is not None:
        if not (isinstance(fid, str) and _UUID.match(fid) and isinstance(fnum, str) and _NUMBER.match(fnum)):
            raise DraftingJobError("file_to_matter_id and file_to_matter_number must name a matter")
    else:
        fid, fnum = mid, number
    cents = data.get("month_cents_used")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise DraftingJobError(
            "month_cents_used must be stamped (a non-negative int) before a run; unmetered is refused"
        )
    remaining = data.get("allowance_remaining")
    return DraftingJob(
        job_dir=job_dir,
        job_id=job_id,
        slug=_str(data, "slug"),
        matter_id=mid,
        matter_number=number,
        document_class=str(cls),
        requester=requester,
        message_ref=_str(data, "message_ref"),
        request_text=_str(data, "request_text"),
        file_to_id=str(fid),
        file_to_number=str(fnum),
        month_cents_used=cents,
        allowance_remaining=remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else None,
        attachments=_attachments(data.get("attachments")),
        ledger_releases=_count(data.get("ledger_releases")),
        revise=_revise(data.get("revise")),
    )


_ROLES = ("draft", "attorney_notes")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_FILE_ID = re.compile(r"^[0-9A-Za-z-]{8,64}$")


def _revise_file_ok(f: Any) -> bool:
    return (
        isinstance(f, dict)
        and set(f) == {"role", "file_id", "name", "size", "sha256"}
        and f["role"] in _ROLES
        and isinstance(f["file_id"], str)
        and bool(_FILE_ID.match(f["file_id"]))
        and isinstance(f["name"], str)
        and 0 < len(f["name"]) <= 240
        and isinstance(f["size"], int)
        and not isinstance(f["size"], bool)
        and f["size"] > 0
        and isinstance(f["sha256"], str)
        and bool(_SHA.match(f["sha256"]))
    )


def _revise(raw: Any) -> dict[str, Any] | None:
    """The revise block, re-checked: a ULID prior job, a folder id, and one
    filed file per role (the draft, and its attorney notes when filed), each
    by id, size and sha256."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"prior_job_id", "folder_id", "files"}:
        raise DraftingJobError("revise carries exactly prior_job_id, folder_id and files")
    if not (isinstance(raw["prior_job_id"], str) and _ULID.match(raw["prior_job_id"])):
        raise DraftingJobError("revise.prior_job_id must be the prior job's ULID")
    if not (isinstance(raw["folder_id"], str) and _FILE_ID.match(raw["folder_id"])):
        raise DraftingJobError("revise.folder_id must be the delivery folder's id")
    files = raw["files"]
    if not isinstance(files, list) or not files:
        raise DraftingJobError("revise.files names the filed files")
    roles = [f.get("role") for f in files if isinstance(f, dict)]
    if not all(_revise_file_ok(f) for f in files) or len(set(roles)) != len(files):
        raise DraftingJobError("each revise file is one role's {role, file_id, name, size, sha256}")
    if "draft" not in roles:
        raise DraftingJobError("revise.files must name the filed draft")
    return {"prior_job_id": raw["prior_job_id"], "folder_id": raw["folder_id"], "files": [dict(f) for f in files]}


def _count(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


MAX_ATTACHMENTS = 6
_SAFE_NAME = re.compile(r"^[^/\\\x00]{1,200}\.docx$", re.I)


def _attachments(raw: Any) -> tuple[tuple[str, bytes], ...]:
    """The envelope's ``attachments`` ([{filename, sha256, b64}]), decoded and
    re-checked: each a .docx whose bytes match their sha256. Absent is none."""
    import base64
    import binascii
    import hashlib

    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
        raise DraftingJobError(f"attachments must be a list of at most {MAX_ATTACHMENTS}")
    out = []
    for a in raw:
        if not isinstance(a, dict) or set(a) != {"filename", "sha256", "b64"}:
            raise DraftingJobError("each attachment carries exactly filename, sha256 and b64")
        name = a["filename"]
        if not (isinstance(name, str) and _SAFE_NAME.match(name)):
            raise DraftingJobError("an attachment's filename must be a plain .docx name")
        try:
            blob = base64.b64decode(str(a["b64"]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DraftingJobError(f"an attachment is not base64: {exc}") from None
        if hashlib.sha256(blob).hexdigest() != a["sha256"]:
            raise DraftingJobError("an attachment's bytes do not match its sha256")
        out.append((name, blob))
    return tuple(out)


def load(job_dir: Path) -> DraftingJob:
    path = Path(job_dir) / JOB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DraftingJobError(f"{path}: {exc}") from exc
    return parse(data, Path(job_dir))
