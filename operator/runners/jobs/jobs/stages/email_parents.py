"""A named email attachment is read through the email it came from.

WHY (2026-10-25). A delivered chronology's coverage listed 37 attachments
folded out of emails under the runner's own ids (``msgatt-<hash>``, minted by
``msg.run_fold``). The Operator named them in an update, the update's pull
looked for documents with those ids on the matter, found none, and held:
"a named document id is not on the matter". The attachment IS on the matter,
inside an email, and the way to read it again is to open that email.

The rule. For each named ``msgatt-`` id, the pull finds the emails it came
from in a DELIVERED job on the same matter still on the volume (the jobs the
update reads from, ``vision_seed``): the row's ``container_id`` when the run
recorded it, otherwise every email in that job's pull whose file name is the
row's ``container`` (two emails can share a name; opening both costs little,
and the update skips every page already delivered). The emails are added to
the pull; the email stage opens them (``msg.named_ids``) and the fold puts the
attachment back under the same id (its hash). An id no delivered job explains
stays unresolved, and the pull holds naming it, as before.

``covered`` now lists the email's id instead of the attachment's for every run
that records ``container_id``, so this is the path for older deliveries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import StageRun, read_json, read_jsonl
from .vision_seed import delivered_same_matter

RECORD = "named_email_parents.json"
PREFIX = "msgatt-"


def _parents_in(sd: Path, wanted: set[str]) -> dict[str, list[str]]:
    emails: dict[str, list[str]] = {}
    for r in read_jsonl(sd / "msg_manifest.jsonl"):
        if r.get("id"):
            emails.setdefault(f"{r.get('name') or ''}{r.get('ext') or ''}", []).append(str(r["id"]))
    out: dict[str, list[str]] = {}
    for r in read_jsonl(sd / "raw_manifest.jsonl"):
        rid = str(r.get("id") or "")
        if rid not in wanted:
            continue
        ids = [str(r["container_id"])] if r.get("container_id") else emails.get(str(r.get("container") or ""), [])
        if ids:
            out[rid] = sorted(set(ids))
    return out


def resolve(sr: StageRun, named: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """({attachment id: [email ids]}, [attachment ids no delivered job explains])."""
    wanted = {i for i in named if i.startswith(PREFIX)}
    found: dict[str, list[str]] = {}
    if wanted:
        for sd in delivered_same_matter(sr.job, sr.slug_dir.name):
            for k, v in _parents_in(sd, wanted - set(found)).items():
                found[k] = v
            if set(found) == wanted:
                break
    return found, sorted(wanted - set(found))


def write(slug_dir: Path, found: dict[str, list[str]]) -> None:
    (slug_dir / RECORD).write_text(json.dumps(found, indent=1, sort_keys=True), encoding="utf-8")


def emails(slug_dir: Path) -> set[str]:
    """The email ids the pull added for named attachments (none when it added none)."""
    raw: Any = read_json(slug_dir / RECORD, {}) or {}
    return {str(e) for ids in raw.values() if isinstance(ids, list) for e in ids} if isinstance(raw, dict) else set()
