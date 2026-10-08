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


def _walk(body: str, keep: set[int]) -> list[tuple[dict[str, Any], str]]:
    """Each claim with its EXACT span in the body (text through citation).

    The span is what a repair edits. Rebuilding it as `claim + " " + cite`
    failed whenever the citation sat on the next line or behind two spaces:
    the claim was logged `SKIP: claim not located` every round, never repaired
    and never dropped, and held the package at the gate (live 2026-10-07).
    """
    out: list[tuple[dict[str, Any], str]] = []
    cursor = 0
    for m in CITE.finditer(body):
        part = body[cursor : m.start()]
        cut = part.rfind("\n\n")
        last = part[cut + 2 :] if cut >= 0 else part
        start = cursor + (cut + 2 if cut >= 0 else 0) + (len(last) - len(last.lstrip()))
        seg = last.strip()
        cursor = m.end()
        n = int(m.group(1))
        if n not in keep or len(seg) < 30 or seg.lstrip().startswith("[NTD:"):
            continue
        spec = m.group(2)
        claim = {
            "exhibit": n,
            "page_spec": (spec or "").strip(),
            "cite": m.group(0),
            "claim": seg,
            "key": claim_key(n, spec, seg),
        }
        out.append((claim, body[start : m.end()]))
    return out


def extract_claims(body: str, keep: set[int]) -> list[dict[str, Any]]:
    return [c for c, _ in _walk(body, keep)]


def claim_spans(body: str, keep: set[int]) -> dict[str, str]:
    """key -> the claim's exact text-through-citation span in `body`."""
    return {c["key"]: span for c, span in _walk(body, keep)}


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


REAUDIT = "reaudit"


def pending_reaudit(rows: list[dict[str, Any]]) -> set[str]:
    """Keys whose LAST verdict-bearing row is a re-audit marker: the verdict on
    file is stale and the next round grades the claim again.

    Repair writes the marker when the judgment tier, shown the auditor's
    findings, returns the claim unchanged. Before, that no-op counted as a
    repair, the key (which hashes the text) did not move, the next round
    resumed the cached PARTIAL, and at the cap the claim was dropped: three
    record-true facts left a delivered package that way (2026-10-07), each
    graded PARTIAL on a printed page label, an omission, or a point the
    auditor itself marked supported."""
    last: dict[str, str] = {}
    for r in rows:
        if r.get("kind") in ("real", REAUDIT) and "key" in r:
            last[r["key"]] = r["kind"]
    return {k for k, kind in last.items() if kind == REAUDIT}


def done_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {r["key"] for r in rows if "key" in r} - pending_reaudit(rows)


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
