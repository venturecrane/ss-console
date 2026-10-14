"""Where the delivered chronology is on the matter. $0, a listing read only.

The delivered document is found by the FIRM'S OWN file name for it (the
config's `delivery.chronology_name_template`, "<Client> - Medical Chronology
MM-DD-YY.docx" today), so a chronology the laptop pipeline delivered is found
the same way as one the seat delivered: neither the ledger nor a folder name
is trusted for it. When the requester's skill named the document by id, that
id must be a live document of that name, or the update holds.

The chronology folder is the folder that document sits in. Its exhibits are
the files named "Exhibit N - <title>.pdf" there; every other file in it is the
firm's or an earlier package's, and the update never touches it.
"""

from __future__ import annotations

import re
from typing import Any

EXHIBIT_FILE = re.compile(r"^Exhibit (\d+) - .+\.pdf$", re.I)


class LocateHold(ValueError):
    """The delivered chronology cannot be placed; the update holds."""


def vendor_name(row: dict[str, Any]) -> str:
    """The full filename: the vendor gives the extension in its own field."""
    name, ext = str(row.get("name") or ""), str(row.get("ext") or "")
    return name if not ext or name.lower().endswith(ext.lower()) else name + ext


def name_pattern(template: str, client: str) -> re.Pattern[str]:
    """The template as a pattern: {CLIENT} the client's name, {MM-DD-YY} a
    date, and an optional " (k)" before the extension: a same-day update files
    its chronology VERSIONED rather than over a file of the same name
    (``stages/update_upload.versioned``), and the next update must find it."""
    stem, dot, ext = template.rpartition(".")
    if not dot or "{" in ext:
        stem, ext = template, ""

    def esc(t: str) -> str:
        r = re.escape(t).replace(re.escape("{CLIENT}"), re.escape(client))
        return r.replace(re.escape("{MM-DD-YY}"), r"(\d{2})-(\d{2})-(\d{2})")

    rx = esc(stem) + r"(?: \((\d+)\))?" + (re.escape("." + ext) if ext else "")
    return re.compile("^" + rx + "$", re.I)


def _stamp(m: re.Match[str]) -> str:
    """Date, then version: a versioned file of a date is the newer one."""
    mm, dd, yy = m.groups()[:3]
    return f"{yy}{mm}{dd}.{int(m.group(4) or 1):04d}"


def _pick(cands: list[tuple[dict[str, Any], re.Match[str]]], file_id: str | None) -> dict[str, Any]:
    if file_id:
        hit = [d for d, _ in cands if str(d.get("id")) == file_id]
        if not hit:
            raise LocateHold(
                f"the named chronology document {file_id} is not a live document named as this client's chronology"
            )
        return hit[0]
    if not cands:
        raise LocateHold(
            "no delivered chronology for this client is on the matter (none carries the firm's name for it)"
        )
    newest = max(_stamp(m) for _, m in cands)
    top = [d for d, m in cands if _stamp(m) == newest]
    if len(top) > 1:
        raise LocateHold(
            f"{len(top)} chronology documents for this client carry the newest date and version; "
            "name the one to update by id"
        )
    return top[0]


def locate(
    docs: list[dict[str, Any]], folders: list[dict[str, Any]], *, template: str, client: str, file_id: str | None
) -> dict[str, Any]:
    """The update plan's matter half: the delivered document, its folder, the
    exhibits in that folder, and the other files there (left alone)."""
    rx = name_pattern(template, client)
    live = [d for d in docs if not d.get("deleted")]
    cands = [(d, m) for d in live if (m := rx.match(vendor_name(d)))]
    doc = _pick(cands, file_id)
    folder_id = str(doc.get("folderId") or "")
    paths = {str(f.get("id")): str(f.get("path") or f.get("name") or "") for f in folders}
    if not folder_id or folder_id not in paths:
        raise LocateHold("the delivered chronology does not sit in a folder on the matter")
    in_folder = [d for d in live if str(d.get("folderId") or "") == folder_id]
    exhibits: dict[int, dict[str, Any]] = {}
    for d in in_folder:
        m = EXHIBIT_FILE.match(vendor_name(d))
        if not m:
            continue
        n = int(m.group(1))
        if n in exhibits:
            raise LocateHold(f"the chronology folder holds two files numbered Exhibit {n}")
        exhibits[n] = {"exhibit": n, "id": str(d["id"]), "name": vendor_name(d), "size": d.get("size")}
    others = sorted(
        vendor_name(d) for d in in_folder if str(d["id"]) != str(doc["id"]) and not EXHIBIT_FILE.match(vendor_name(d))
    )
    return {
        "chronology": {"id": str(doc["id"]), "name": vendor_name(doc), "size": doc.get("size")},
        "folder_id": folder_id,
        "folder_name": paths[folder_id].rsplit("/", 1)[-1],
        "folder_path": paths[folder_id],
        "exhibits": [exhibits[n] for n in sorted(exhibits)],
        "others": others,
    }
