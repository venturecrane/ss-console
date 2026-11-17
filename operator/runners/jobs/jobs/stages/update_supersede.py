"""The delivered companion files an update supersedes, and when each may go. $0.

WHY (2026-11-17). An update retired the delivered chronology by id and left
the delivered Records Review beside the new one, which then filed as "(2)"
next to a stale review; a refreshed billing worksheet would do the same. An
update that files a new Records Review, Chronology Review Notes or Medical
Billing Worksheet now retires the delivered one of that kind, the same proven
way the chronology goes (by id, after every new file is proven, read back
until the id reads gone), and only while the delivered copy is still the file
SMD filed: its bytes must hash to what the delivered job's ledger row recorded
(``job.delivered_files``, stamped by the broker). A copy the firm edited
since, or one no delivery record names, is left in the folder, and the
reply says so.

``update_base`` calls ``find`` and records the result in the plan
(``superseded``); ``update_upload`` calls ``retirable`` after the filing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..medchron.update import review_list as RL

WORKSHEET = "worksheet"
RECORDS_REVIEW = "records_review"
REVIEW_NOTES = "review_notes"


def _pattern(template: str, client: str) -> re.Pattern:
    """A delivered name of `template` for `client`, any date stamp, and any
    "(n)" a same-day clash added."""
    stem, _, ext = template.rpartition(".")
    rx = (
        re.escape(stem)
        .replace(re.escape("{CLIENT}"), re.escape(client))
        .replace(re.escape("{MM-DD-YY}"), r"\d{2}-\d{2}-\d{2}")
    )
    return re.compile(rf"^{rx}(?: \(\d+\))?\.{re.escape(ext)}$")


def templates(cfg: Any) -> dict[str, str]:
    from .review import NOTES_TEMPLATE  # lazy: the review stage imports the update's stages

    glob = str(cfg.get("delivery", "worksheet_glob") or "* - Medical Billing Worksheet *.docx")
    return {
        WORKSHEET: glob.replace("*", "{CLIENT}", 1).replace("*", "{MM-DD-YY}", 1),
        RECORDS_REVIEW: RL.TEMPLATE,
        REVIEW_NOTES: NOTES_TEMPLATE,
    }


def kind_of(name: str, cfg: Any, client: str) -> str | None:
    return next((k for k, t in templates(cfg).items() if _pattern(t, client).match(name)), None)


def find(listing: list[dict[str, Any]], folder_id: str, cfg: Any, client: str) -> list[dict[str, Any]]:
    """Every file in the chronology folder named as one of SMD's companion
    deliverables for `client`: ``{kind, id, name, size}``."""
    from ..medchron.update.locate import vendor_name

    out = []
    for d in listing:
        if d.get("deleted") or str(d.get("folderId") or "") != folder_id:
            continue
        name = vendor_name(d)
        k = kind_of(name, cfg, client)
        if k:
            out.append({"kind": k, "id": str(d["id"]), "name": name, "size": d.get("size")})
    return out


def stamp(rows: list[dict[str, Any]], pulled: Path, delivered: dict[str, str]) -> list[dict[str, Any]]:
    """Each row with its bytes' sha256 (pulled under `pulled/<name>`) and
    whether it is still the file SMD delivered (``unchanged``)."""
    out = []
    for r in rows:
        p = pulled / r["name"]
        sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
        was = delivered.get(r["id"], "")
        out.append({**r, "sha256": sha, "unchanged": bool(sha) and sha == was, "recorded": bool(was)})
    return out


def retirable(plan: dict[str, Any], manifest: list[dict[str, Any]], cfg: Any, client: str) -> tuple[list, list]:
    """(delivered companions to retire, ones to leave): a kind this update
    files a new copy of is superseded; an unchanged copy is retired, a changed
    or unrecorded one is left beside the new one. A kind not filed again is
    neither (it still describes the delivered package)."""
    filed = {kind_of(str(m.get("name") or ""), cfg, client) for m in manifest}
    sent = {str(m.get("name") or "") for m in manifest}
    go, stay = [], []
    for r in plan.get("superseded") or []:
        if r.get("kind") not in filed or r.get("name") in sent:
            continue
        (go if r.get("unchanged") else stay).append(r)
    return go, stay


__all__ = ["RECORDS_REVIEW", "REVIEW_NOTES", "WORKSHEET", "find", "kind_of", "retirable", "stamp", "templates"]
