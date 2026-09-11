"""`icd_tables`: vendor the CMS ICD code tables once per LAPTOP install into
`<install_root>/controls/icd/`, with a VERSION.json of source URLs and sha256s.
$0 (a download). The driver skips this stage when VERSION.json exists.

On a seat this stage never fetches: install_root is the entrypoint's
root-owned, read-only controls tree, pre-seeded from the firm's vault, and
the tables arrive there because provision-customer.sh runs `vendor()` below
on the console (`operator/bin/lib/medchron-vendor-icd.sh`) and stages the
result under `medchron-controls/icd/`. A seat whose tree lacks VERSION.json
fails this stage loudly (PermissionError on the fetch), which boot smoke
catches first (`medchron-icd-tables-present`).

The zip names carry the fiscal year; a new year means editing the two URLs
here and rerunning. The fetch is injectable so the unzip and version record
are testable without the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Callable

from ..icd_tables import ICD9_FILE, ICD10_FILE, VERSION_FILE, icd_dir
from .base import StageRun

ICD10_URL = "https://www.cms.gov/files/zip/april-1-2026-code-descriptions-tabular-order.zip"
ICD10_LABEL = "ICD-10-CM FY2026, April 1 2026 update"
ICD10_MEMBER = "icd10cm_order_2026.txt"
ICD9_URL = (
    "https://www.cms.gov/medicare/coding/icd9providerdiagnosticcodes/downloads/icd-9-cm-v32-master-descriptions.zip"
)
ICD9_LABEL = "ICD-9-CM v32 (FY2015, final release)"
ICD9_MEMBER = "CMS32_DESC_LONG_DX.txt"
Fetch = Callable[[str], bytes]


def _http_fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(
        url, timeout=120, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (smd-medchron icd fetch)"}
    )
    r.raise_for_status()
    return r.content


def _member(blob: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if n == member or n.endswith("/" + member)]
        if not names:
            raise FileNotFoundError(f"{member} not in zip; contents: {z.namelist()[:10]}")
        return z.read(names[0])


def vendor(dest: Path, fetch: Fetch = _http_fetch) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    z10, z9 = fetch(ICD10_URL), fetch(ICD9_URL)
    (dest / ICD10_FILE).write_bytes(_member(z10, ICD10_MEMBER))
    (dest / ICD9_FILE).write_bytes(_member(z9, ICD9_MEMBER))
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731 - a one-line digest alias used twice on the next lines; a def adds only a name
    version = {
        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "icd10cm": {
            "label": ICD10_LABEL,
            "url": ICD10_URL,
            "member": ICD10_MEMBER,
            "file": ICD10_FILE,
            "sha256": sha((dest / ICD10_FILE).read_bytes()),
            "zip_sha256": sha(z10),
        },
        "icd9cm": {
            "label": ICD9_LABEL,
            "url": ICD9_URL,
            "member": ICD9_MEMBER,
            "file": ICD9_FILE,
            "sha256": sha((dest / ICD9_FILE).read_bytes()),
            "zip_sha256": sha(z9),
        },
    }
    (dest / VERSION_FILE).write_text(json.dumps(version, indent=1), encoding="utf-8")
    return version


def run(sr: StageRun) -> int:
    dest = icd_dir(sr.job.install_root)
    if (dest / VERSION_FILE).is_file():
        sr.log(f"ICD tables present in {dest}")
        return 0
    try:
        v = vendor(dest)
    except Exception as exc:  # noqa: BLE001 - a failed fetch is a failed stage with its reason
        sr.log(f"ICD fetch failed: {str(exc)[:200]}")
        return 1
    sr.log(f"vendored {v['icd10cm']['label']} and {v['icd9cm']['label']} into {dest}")
    return 0
