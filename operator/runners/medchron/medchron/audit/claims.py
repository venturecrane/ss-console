"""What the audit audits: the claims extracted from the chronology body, their
keys, and the results ledger they are recorded in.

A claim is the paragraph ending in a citation. Its key hashes (exhibit,
page_spec, text), so a repaired claim is a new key and its old verdict no
longer applies; the coverage gate is built on exactly that. An [NTD: ...]
block is an annotation to the drafter about our own handling, not an
assertion about the record, and is never audited. Out-of-range pages are a
FINDING, never a clamp: a citation the firm cannot follow is the defect an
audit exists to find.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

CITE = re.compile(r"\(Exhibit (\d+)\s*(?:-\s*p\.\s*([0-9,\s\-]+?))?\s*(?:,\s*machine transcription)?\)")
BODY_START, BODY_END = "## Medical Chronology", "## Exhibit List"
_lock = threading.Lock()


def body_of(doc_text: str) -> str:
    return doc_text.split(BODY_START, 1)[1].split(BODY_END, 1)[0]


def doc_sha_of(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def claim_key(exhibit: int, page_spec: str | None, claim: str) -> str:
    return hashlib.sha256(f"{exhibit}|{page_spec}|{claim}".encode()).hexdigest()[:16]


def parse_pages(spec: str | None) -> list[int]:
    """No clamping; range validation happens later. A nonsense span (b < a,
    or wider than 12) yields its two ends, which is itself a finding."""
    if not spec:
        return []
    got: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            got.extend([a, b] if (b < a or b - a > 12) else range(a, b + 1))
        elif part.isdigit():
            got.append(int(part))
    return list(dict.fromkeys(got))


def extract_claims(body: str, keep: set[int]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    cursor = 0
    for m in CITE.finditer(body):
        seg = body[cursor : m.start()].split("\n\n")[-1].strip()
        cursor = m.end()
        n = int(m.group(1))
        if n not in keep or len(seg) < 30 or seg.lstrip().startswith("[NTD:"):
            continue
        spec = m.group(2)
        claims.append(
            {
                "exhibit": n,
                "page_spec": (spec or "").strip(),
                "cite": m.group(0),
                "claim": seg,
                "key": claim_key(n, spec, seg),
            }
        )
    return claims


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def append_row(path: Path, rec: dict[str, Any]) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def done_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {r["key"] for r in rows if "key" in r}


def lineage_orphans(rows: list[dict[str, Any]], current_keys: set[str], sha: str) -> list[str]:
    """Keys of prior real rows for THIS body that are not current keys: the
    double-sweep signature (the hashing or the extraction changed under an
    unchanged document, and resuming would re-bill every claim)."""
    return sorted(
        {
            r["key"]
            for r in rows
            if r.get("kind") == "real" and r.get("doc_sha") == sha and r.get("key") not in current_keys
        }
    )


def latest_real(rows: list[dict[str, Any]], live_keys: set[str]) -> dict[str, dict[str, Any]]:
    """The last real row per live key."""
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "real" and r.get("key") in live_keys:
            latest[r["key"]] = r
    return latest
