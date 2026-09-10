"""ICD descriptors from the vendored CMS tables, never from a model.

A model once supplied the descriptor for every code in the Diagnostic
Highlights table; it labelled a dermatitis code "low back pain" and an aortic
ectasia code "Nutcracker syndrome". CMS publishes both code sets as plain
text; the icd_tables stage downloads them once into `<install_root>/controls/icd/`
with a VERSION.json of sha256s, and this module reads them. A descriptor
either comes from the table or is blank.

Lookup order: ICD-10-CM tabular-order file (dots stripped; header rows loaded
too, so a category code a record cites resolves), then ICD-9-CM long
descriptions marked "(ICD-9-CM)". One refinement: an ICD-10-CM HEADER row
(not a valid billing code) yields to an exact ICD-9-CM code, because the two
systems' V and E ranges collide.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ICD10_FILE = "icd10cm_order.txt"
ICD9_FILE = "CMS32_DESC_LONG_DX.txt"
VERSION_FILE = "VERSION.json"


class TablesMissing(RuntimeError):
    pass


def icd_dir(install_root: Path) -> Path:
    """Install-level, beside the classifier's controls: `Job.install_root`."""
    return install_root / "controls" / "icd"


def strip_dots(code: str) -> str:
    return re.sub(r"[.\s]", "", (code or "").upper())


def _load_icd10(path: Path) -> dict[str, tuple[str, bool]]:
    """Fixed-width CMS order file: order(5) code(7) flag(1) short(60) long."""
    out: dict[str, tuple[str, bool]] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if len(line) < 78:
                continue
            code, flag, long = line[6:13].strip(), line[14:15].strip(), line[77:].rstrip("\n").strip()
            if code and long and code not in out:
                out[code] = (long, flag == "1")
    return out


def _load_icd9(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="cp1252", errors="replace") as fh:
        for line in fh:
            m = re.match(r"^\s*(\S+)\s+(.*\S)\s*$", line)
            if m and m.group(1) not in out:
                out[m.group(1).upper()] = m.group(2)
    return out


def load(install_root: Path) -> dict[str, Any]:
    d = icd_dir(install_root)
    p10, p9 = d / ICD10_FILE, d / ICD9_FILE
    if not (p10.is_file() and p9.is_file()):
        raise TablesMissing(f"ICD tables not found in {d}; the icd_tables stage fetches them once")
    version: dict[str, Any] = {}
    vp = d / VERSION_FILE
    if vp.is_file():
        try:
            version = json.loads(vp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt version file reads as no version; the ICD tables themselves are still loaded
            version = {}
    return {"icd10": _load_icd10(p10), "icd9": _load_icd9(p9), "version": version, "dir": str(d)}


def describe(code: str, tables: dict[str, Any]) -> str | None:
    k = strip_dots(code)
    if not k:
        return None
    ten = tables["icd10"].get(k)
    if ten and ten[1]:
        return ten[0]
    nine = tables["icd9"].get(k)
    if nine:
        return f"{nine} (ICD-9-CM)"
    return ten[0] if ten else None
