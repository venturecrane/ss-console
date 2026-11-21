"""Which pages an update set aside as REPEATS a reader checks, and what it
decided. $0 here; the paid call is ``stages/update_repeat_check``.

WHY (2026-11-21). The update lane's repeat rules (a picture match its words
confirm, ``update/pageid_io``; one page's 8-word shingles, a run of one to
three pages holding a page whole and a dateless neighbour beside one,
``update/select``; two readings of one scan, ``update/twins``; a re-filed
extract by its words, ``update/subsets``) set pages aside as copies of pages
already covered. Three proven readers compared every set-aside page with the
page it was matched to on real delivered updates: of 387 recorded pairs 21
were DIFFERENT, of one matter's 342 pairs re-derived 214 were, and 75 pages
carried facts missing from the delivered update. Reading every page that was
not pixel-equal instead (PR #4171) was rejected: about 366 of the 387 really
were the same page, and reading them itemizes visits twice and charges the
firm's page allowance for pages already covered.

THE RULE. Every page set aside as a repeat (class ``already_delivered``,
``repeat`` or ``refiled_extract`` in ``runs/<unit>/pages.jsonl``) that is
not pixel-equal to its covering page is a PAIR: (the set-aside page, the
page that covers it). One reader, shown both pages as pictures, returns
SAME or DIFFERENT (``prompts/repeat-check-system.md``); code acts:

* SAME: the page stays set aside (uncharged, not composed).
* DIFFERENT, a reply that does not parse after one retry, a refusal, or any
  model failure: the page is READ (``forced``), exactly as if the rules had
  read it.
* A repeat with no recorded covering page is READ without a call (the
  fail-safe: nothing can be compared).
* A pixel-equal page, and every page set aside for another reason (blank,
  billing, a failed transcription, a withheld file), is never sent.

Each verdict is saved under ``update/repeat_check.json``, keyed by both
pages' file sha256 and page number, so a resumed job never pays twice.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import pages_record as PR

NAME = "repeat_check.json"
#: The page-record classes a repeat rule sets a page aside under (``stages/page_rows.CLASS``).
REPEAT_CLASSES = frozenset({"already_delivered", "repeat", "refiled_extract"})
SAME, DIFFERENT, FAILED, NO_COVER = "SAME", "DIFFERENT", "FAILED", "NO_COVER"
VERDICTS = (SAME, DIFFERENT)
#: The projection's figures per pair: two pictures at the audit render (1600
#: px on the long side, about 2,650 tokens each), the prompt and the labels;
#: the answer is one small JSON object.
PICTURE_TOKENS = 2_650
PROMPT_TOKENS = 700
OUT_TOKENS = 50


@dataclass(frozen=True)
class Pair:
    doc: str  # the set-aside page's document id
    sha: str  # its file's sha256
    page: int
    cover: str  # "exhibit:N" or a new document's id
    cover_sha: str
    cover_page: int
    cover_last: int = 0  # the run's last page, when the cover is a run of pages

    @property
    def key(self) -> str:
        last = f"-{self.cover_last}" if self.cover_last and self.cover_last != self.cover_page else ""
        return f"{self.sha}:{self.page}|{self.cover_sha}:{self.cover_page}{last}"

    @property
    def cover_pages(self) -> list[int]:
        end = self.cover_last if self.cover_last and self.cover_last > self.cover_page else self.cover_page
        return list(range(self.cover_page, end + 1))


@dataclass(frozen=True)
class Uncovered:
    """A repeat the rules recorded no covering page for: read, never sent."""

    doc: str
    sha: str
    page: int


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def pairs(
    rows: list[dict[str, Any]], exhibit_sha: dict[int, str], doc_sha: dict[str, str]
) -> tuple[list[Pair], list[Uncovered]]:
    """The pairs to check, and the repeats with no covering page, from the
    page record's rows. `exhibit_sha`: {delivered exhibit: its file's sha256};
    `doc_sha`: {new document id: its file's sha256}. A row that is not a
    repeat, or whose cover is pixel-equal, is neither."""
    out: list[Pair] = []
    lost: list[Uncovered] = []
    for r in rows:
        if r.get("status") != PR.EXCLUDED or r.get("class") not in REPEAT_CLASSES:
            continue
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        cov = ev.get("cover") if isinstance(ev.get("cover"), dict) else {}
        if cov.get("exact") is True:
            continue  # pixel-equal: the same page, never checked
        doc, page, sha = str(r.get("doc") or ""), _int(r.get("page")), str(r.get("sha256") or "")
        if not doc or page is None or page < 1 or not sha:
            continue
        ex, other, cp = _int(cov.get("exhibit")), cov.get("doc"), _int(cov.get("page"))
        cover = f"exhibit:{ex}" if ex is not None else str(other or "")
        csha = exhibit_sha.get(ex, "") if ex is not None else doc_sha.get(str(other or ""), "")
        if not cover or cp is None or cp < 1 or not csha:
            lost.append(Uncovered(doc, sha, page))
            continue
        out.append(Pair(doc, sha, page, cover, csha, cp, _int(cov.get("last")) or 0))
    return out, lost


def parse(reply: str) -> str | None:
    """SAME or DIFFERENT from the reader's one JSON object, else None (the
    entry verify's parse: the outermost braces, read as JSON)."""
    a, b = (reply or "").find("{"), (reply or "").rfind("}")
    try:
        obj = json.loads(reply[a : b + 1]) if 0 <= a < b else None
    except json.JSONDecodeError:
        return None
    v = obj.get("verdict") if isinstance(obj, dict) else None
    v = v.strip().upper() if isinstance(v, str) else None
    return v if v in VERDICTS else None


class Store:
    """``update/repeat_check.json``: {"verdicts": {pair key: {"verdict",
    "doc", "page", "why", "transient"}}, "forced": [[sha256, page], ...],
    "counts": {...}}. Written whole after every verdict, so a crash keeps
    every answer paid for. Parsed, never cast: a malformed file reads empty."""

    def __init__(self, ud: Path) -> None:
        self.path = ud / NAME
        self._lock = threading.Lock()
        raw = _read(self.path)
        v = raw.get("verdicts")
        self.verdicts: dict[str, dict[str, Any]] = (
            {str(k): x for k, x in v.items() if isinstance(x, dict)} if isinstance(v, dict) else {}
        )
        self.forced: set[tuple[str, int]] = _forced(raw)
        self.counts: dict[str, int] = {}

    def cached(self, key: str) -> str | None:
        """The verdict a resume reuses: any but a transient failure (asked again)."""
        got = self.verdicts.get(key) or {}
        if got.get("transient"):
            return None
        v = got.get("verdict")
        return v if v in (SAME, DIFFERENT, FAILED) else None

    def put(self, p: Pair, verdict: str, why: str = "", transient: bool = False) -> None:
        with self._lock:
            row: dict[str, Any] = {"verdict": verdict, "doc": p.doc, "page": p.page, "cover": p.cover}
            row["cover_page"] = p.cover_page
            if why:
                row["why"] = why[:200]
            if transient:
                row["transient"] = True
            self.verdicts[p.key] = row
            self.save()

    def save(self) -> None:
        body = {
            "verdicts": self.verdicts,
            "forced": sorted([s, n] for s, n in self.forced),
            "counts": self.counts,
        }
        tmp = self.path.with_suffix(".json.tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _read(path: Path) -> dict[str, Any]:
    try:
        got = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _forced(raw: dict[str, Any]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for x in raw.get("forced") or []:
        if isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) and _int(x[1]) is not None:
            out.add((x[0], int(x[1])))
    return out


def forced(ud: Path) -> set[tuple[str, int]]:
    """(file sha256, page) of every page the check READS although a repeat
    rule set it aside: ``update_select`` reads each one as if no rule had
    matched it. Empty when the check has not run."""
    return _forced(_read(ud / NAME))


def forced_by_document(ud: Path, docs: list[dict[str, Any]]) -> dict[str, set[int]]:
    """``forced`` as {document id: pages}, for the pulled rows `docs` (each
    with its id and sha256); a document with none is left out."""
    by_sha: dict[str, set[int]] = {}
    for s, n in forced(ud):
        by_sha.setdefault(s, set()).add(n)
    return {str(r["id"]): by_sha[str(r["sha256"])] for r in docs if str(r.get("sha256") or "") in by_sha}


def shas(slug_dir: Path, unit: str) -> tuple[dict[int, str], dict[str, str]]:
    """({delivered exhibit the delivery read: its file's sha256}, {pulled
    document id: its file's sha256}): what a pair is keyed by. Only the
    exhibits the delivered Exhibit List names cover a page (``update_select``)."""
    from ...stages.base import read_json, read_jsonl
    from .body import delivered_numbers

    ud = slug_dir / "runs" / unit / "update"
    exhibits = [e for e in (read_json(ud / "update_plan.json", {}) or {}).get("exhibits") or [] if isinstance(e, dict)]
    base = ud / "base.md"
    listed = delivered_numbers(base.read_text(encoding="utf-8")) if base.is_file() else None
    ex: dict[int, str] = {}
    for e in exhibits:
        n = int(e["exhibit"]) if str(e.get("exhibit", "")).isdigit() else None
        if n is not None and e.get("sha256") and (listed is None or n in listed):
            ex[n] = str(e["sha256"])
    raw = read_jsonl(slug_dir / "raw_manifest.jsonl")
    return ex, {str(r.get("id")): str(r.get("sha256") or "") for r in raw if r.get("ok") and r.get("sha256")}


def current(slug_dir: Path, unit: str) -> tuple[list[Pair], list[Uncovered]]:
    """The pairs and the uncovered repeats in the page record as it stands."""
    ex, docs = shas(slug_dir, unit)
    return pairs(PR.read(slug_dir / "runs" / unit), ex, docs)


def open_count(slug_dir: Path, unit: str) -> int:
    """How many repeats the check has still to settle (a pair with no saved
    verdict, or a repeat with no covering page): ``update_select`` never ends
    an update as already current while any is open."""
    st = Store(slug_dir / "runs" / unit / "update")
    got, lost = current(slug_dir, unit)
    return sum(1 for p in got if st.cached(p.key) is None) + len(lost)


def counts(ud: Path) -> dict[str, int]:
    """The check's counts for the run record (``core_counts``)."""
    c = _read(ud / NAME).get("counts")
    return {str(k): v for k, v in c.items() if isinstance(v, int) and v >= 0} if isinstance(c, dict) else {}


def pair_tokens(p: Pair) -> int:
    """The input tokens of one pair's call: its pictures and the prompt."""
    return PROMPT_TOKENS + PICTURE_TOKENS * (1 + len(p.cover_pages))
