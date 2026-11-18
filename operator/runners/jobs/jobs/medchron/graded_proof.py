"""Proof 15: nothing ungraded is in the filed chronology. $0, no model.

WHY (2026-11-17). The audit grades CLAIMS (``audit/claims``: the paragraph a
citation closes), and every step that writes after the audit (a finisher
edit, a settle, a join, a note) either grades what it writes or must write
nothing a reader takes for a statement about the record. Nothing proved the
whole. Historically a repair that wrote after the audit invented a name and a
date that reached the firm. This proof reads the filed body and accounts for
every paragraph of it:

* SUPPORTED: each claim in it has a SUPPORTED latest audit row by its key
  (the auditor's, a code attestation such as ``mode: grid``, or SMD's);
* DELIVERED: it sits in an entry the delivered chronology carries byte for
  byte (an update never edits it, ``update/proofs`` 1);
* LABEL: it is ungraded by design, and the kinds are listed (``LABELS``):
  an entry's date and provider lines, a subsection heading, an
  ``[NTD: ...]`` note (the illegible-records house note, the pre-incident
  note), a fold's "Additional findings" heading, the prior history headings,
  and a cited fragment too short to be a claim (``claims.graded``).

Anything else is a finding: a claim with no SUPPORTED row
(``unsupported_claim``) or text no citation closes (``ungraded_text``).

RECORD-ONLY: it never fails a run (``update/proofs_final.ENFORCE``). It
writes the filed document's sha256 beside its findings, and the upload
stages record a filing whose chronology bytes differ from the ones proven
(``sha_changed_after_proof``). ``recorded`` is the one function the run
record reads them through.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audit import claims as CL

#: The kinds of ungraded text this proof accepts, each by name.
LABELS = (
    "entry date line",
    "entry provider line",
    "subsection heading",
    "[NTD: ...] note",
    "additional findings heading",
    "prior history heading",
    "fragment too short to grade",
)
#: The upload stages' record when the filed chronology is not the proven one.
SHA_CHANGED = "sha_changed_after_proof"
PROOFS = "proofs.json"
_ADDED_HISTORY = re.compile(
    r"^Additional prior history from records filed (?:through \d{2}-\d{2}-\d{2}|since the prior chronology)$"
)
_FOLD = re.compile(r"^Additional findings(?: from records received \d{2}/\d{2}/\d{4})?$")
_PMH = re.compile(r"^\**Prior Medical History\**$")
_ALNUM = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class Segment:
    """A run of the body a citation closes: (start, end) of its text, the
    citation's match, and the text as the audit keys it."""

    start: int
    end: int
    cite: re.Match[str]
    text: str

    @property
    def key(self) -> str | None:
        if not CL.graded(self.text):
            return None
        return CL.claim_key(int(self.cite.group(1)), self.cite.group(2), self.text)


def segments(body: str) -> list[Segment]:
    """Every cited run of `body`, as the audit walks it (``claims._walk``):
    from the last blank line before the citation (or the previous citation)
    to the citation. Its graded runs carry the audit's own claim keys."""
    out: list[Segment] = []
    cursor = 0
    for m in CL.CITE.finditer(body):
        part = body[cursor : m.start()]
        cut = part.rfind("\n\n")
        last = part[cut + 2 :] if cut >= 0 else part
        start = cursor + (cut + 2 if cut >= 0 else 0) + (len(last) - len(last.lstrip()))
        out.append(Segment(start, m.start(), m, last.strip()))
        cursor = m.end()
    return out


@dataclass
class Result:
    findings: Counter[str] = field(default_factory=Counter)
    #: paragraphs holding each kind of accounted text (a paragraph may hold several)
    accounted: Counter[str] = field(default_factory=Counter)
    modes: Counter[str] = field(default_factory=Counter)
    paragraphs: int = 0


def _label_lines(body: str, headings: set[str]) -> list[tuple[int, int, str]]:
    """(start, end, kind) of every line that is a label by itself."""
    from .update import body as B

    out: list[tuple[int, int, str]] = []
    pos = 0
    prev_date = False
    for ln in body.split("\n"):
        s = ln.strip()
        kind = ""
        if B.HEAD.match(s):
            kind = LABELS[0]
        elif prev_date and s:
            kind = LABELS[1]
        elif s in headings:
            kind = LABELS[2]
        elif _FOLD.match(s):
            kind = LABELS[4]
        elif _PMH.match(s) or _ADDED_HISTORY.match(s):
            kind = LABELS[5]
        if kind:
            out.append((pos, pos + len(ln), kind))
        prev_date = kind == LABELS[0]
        pos += len(ln) + 1
    return out


def check(
    body: str,
    supported: Callable[[dict[str, Any]], tuple[bool, str]],
    *,
    headings: Iterable[str] = (),
    delivered: Iterable[str] = (),
) -> Result:
    """Every paragraph of `body` accounted for. `supported(claim)` -> (SUPPORTED,
    the row's mode) for a claim dict (``claims.extract_claims`` shape).
    `delivered`: texts the delivered chronology carries byte for byte (its
    entries and preamble paragraphs); text inside one is DELIVERED."""
    heads = set(headings)
    covered = bytearray(len(body))
    kind_at: dict[int, str] = {}

    def cover(a: int, b: int, kind: str) -> None:
        for i in range(max(a, 0), min(b, len(body))):
            if not covered[i]:
                covered[i] = 1
                kind_at[i] = kind

    for text in sorted({t for t in delivered if t}, key=len, reverse=True):
        at = body.find(text)
        while at >= 0:
            cover(at, at + len(text), "delivered")
            at = body.find(text, at + len(text))
    was_delivered = bytes(covered)
    for m in CL.NTD.finditer(body):
        cover(m.start(), m.end(), LABELS[3])
    r = Result()
    for seg in segments(body):
        cover(seg.cite.start(), seg.cite.end(), "citation")
        if seg.text and was_delivered[seg.start : seg.end].count(0) == 0:
            continue  # wholly delivered
        k = seg.key
        if k is None:
            cover(seg.start, seg.end, LABELS[6])
            continue
        spec = seg.cite.group(2)
        claim = {"key": k, "exhibit": int(seg.cite.group(1)), "page_spec": (spec or "").strip(), "spec": spec}
        ok, mode = supported(claim | {"claim": seg.text})
        if ok:
            r.modes[mode or "model"] += 1
            cover(seg.start, seg.end, "supported")
        else:
            r.findings["unsupported_claim"] += 1
            cover(seg.start, seg.end, "unsupported")
    for a, b, kind in _label_lines(body, heads):
        cover(a, b, kind)
    pos = 0
    for para in body.split("\n\n"):
        end = pos + len(para)
        if _ALNUM.search(para):
            r.paragraphs += 1
            loose = [i for i in range(pos, end) if not covered[i] and _ALNUM.match(body[i])]
            if loose:
                r.findings["ungraded_text"] += 1
            else:
                r.accounted.update({kind_at[i] for i in range(pos, end) if i in kind_at and _ALNUM.match(body[i])})
        pos = end + 2
    return r


def latest_supported(rows: list[dict[str, Any]]) -> Callable[[dict[str, Any]], tuple[bool, str]]:
    """supported(claim) by the latest real audit row of its key, as every
    reader of the audit results reads it."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") == "real" and row.get("key"):
            latest[str(row["key"])] = row

    def get(c: dict[str, Any]) -> tuple[bool, str]:
        row = latest.get(str(c.get("key")))
        return (bool(row) and (row or {}).get("verdict") == "SUPPORTED", str((row or {}).get("mode") or "model"))

    return get


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def codes(res: Result) -> dict[str, int]:
    """The proof's findings as {code: count}, every code present."""
    return {f"P15_{k}": int(res.findings.get(k, 0)) for k in ("unsupported_claim", "ungraded_text")}


def _read(path: Path) -> dict[str, Any]:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def write(path: Path, recorded: dict[str, int], filed: dict[str, Any] | None) -> None:
    """Merge the record-only findings and the proven file's sha into `path`."""
    doc = _read(path)
    doc["recorded"] = {**(doc.get("recorded") or {}), **recorded}
    if filed is not None:
        doc["filed"] = filed
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def proofs_paths(slug_dir: Path, unit: str) -> list[Path]:
    """Where a run's proofs are recorded: an update's, then a full run's."""
    rd = slug_dir / "runs" / unit
    return [rd / "update" / PROOFS, rd / PROOFS]


def record_upload(path: Path, manifest: list[dict[str, Any]], log: Callable[[str], None]) -> str | None:
    """At upload: the code recorded when the manifest's bytes for the proven
    chronology are not the ones proven, else None. Never refuses, never raises."""
    doc = _read(path)
    filed = doc.get("filed") if isinstance(doc.get("filed"), dict) else None
    if not filed or not filed.get("name") or not filed.get("sha256"):
        return None  # no final proof on record: nothing to compare, and nothing said
    row = next((m for m in manifest if isinstance(m, dict) and m.get("name") == filed["name"]), None)
    if row is None or row.get("sha256") == filed["sha256"]:
        return None
    up = dict(doc.get("recorded_at_upload") or {})
    up[SHA_CHANGED] = 1  # a fact of this filing, not a count of attempts (a resume records it once)
    doc["recorded_at_upload"] = up
    try:
        path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    except OSError:
        pass
    log(f"final proof: {SHA_CHANGED}: the chronology filed is not the one the final proof read (recorded, not refused)")
    return SHA_CHANGED


def recorded(slug_dir: Path, unit: str) -> dict[str, int]:
    """{check code: count} of every record-only finding the run wrote: the
    one door the run record's audit block reads them through."""
    out: Counter[str] = Counter()
    for p in proofs_paths(slug_dir, unit):
        doc = _read(p)
        for k in ("recorded", "recorded_at_upload"):
            for code, n in (doc.get(k) or {}).items():
                if isinstance(n, int) and not isinstance(n, bool):
                    out[str(code)] += n
    return dict(out)


__all__ = [
    "LABELS",
    "PROOFS",
    "SHA_CHANGED",
    "Result",
    "check",
    "codes",
    "file_sha",
    "latest_supported",
    "proofs_paths",
    "record_upload",
    "recorded",
    "segments",
    "write",
]
