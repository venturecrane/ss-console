#!/usr/bin/env python3
"""rule-store-parity: is every firm rule SMD drafts by also in the seat's rule store?

WHY (Captain's go, 2026-11-23). A firm's rules lived in three places: the
hand-drafting prompts in the private engagements repo (each rule quoted in the
attorney's own words, with the date he stated it and the kind of response it
covers), the seat's rule store (committed ``pending_rules`` rows and the
installed ``classes/work_product/wording.json``), and the work the seat owes SMD
(``smd_owed`` rows of a ``rule_*`` kind). A rule that lives only in a prompt is
applied by SMD's hand and by no job; a committed rule with no document kind is
never installed; an owed rule nobody records is a promise to the attorney
nobody keeps. This check names each of those, and exits nonzero while any
stands.

TWO HALVES, ONE FILE (so the hashing is the same code on both sides):

  On the seat, as the hermes user, read-only (piped by
  ``operator/ops-machine/laptop/ops-rule-parity``)::

      python3 - --seat-snapshot          # prints one SNAPSHOT line of JSON

  Where the engagements checkout is::

      rule-store-parity.py --snapshot snap.json --prompts <prompts dir or file>...

NO RULE TEXT LEAVES EITHER SIDE. The seat half sends, per rule, its id, its
document kinds, its times and the hashes of its five-word runs. The report
prints rule numbers, dates, kinds, file names, ids and digests, never words.
A prompt rule counts as in the store when at least ``--min-cover`` of its
five-word runs appear in some stored rule (an attorney's email often states
several rules in one paragraph, so a paragraph only one of whose rules was
recorded is reported, with how much of it was found).

Exit: 0 no gap; 1 a gap, each one listed; 3 a read failed (said in words, never
reported as "no gap"). Standard library only: the seat image's python3 runs it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

#: The seat's own layout file (generated from operator/contracts/seat-layout.yaml).
LAYOUT_ENV = "SMD_SEAT_LAYOUT"
LAYOUT_DEFAULT = "/app/seat-layout.generated.json"
WORDING_REL = "classes/work_product/wording.json"
RUN = 5
MIN_COVER = 0.5
SNAPSHOT_TAG = "SNAPSHOT "

#: The document kinds a rule may cover (``shared/wording_rules_store.KINDS``).
KINDS = (
    "mediation_brief",
    "discovery_set",
    "discovery_response",
    "memo",
    "depo_outline",
    "demand_letter",
    "letter",
)
#: A prompt rule's "Covers:" words to a kind, first match wins.
COVERS = (
    ("propounded", "discovery_set"),
    ("response", "discovery_response"),
    ("mediation brief", "mediation_brief"),
    ("deposition", "depo_outline"),
    ("demand", "demand_letter"),
    ("memo", "memo"),
    ("letter", "letter"),
)
HEADER = re.compile(r"^RULE (\d+)\. Stated (\d{4}-\d{2}-\d{2})\b.*?Covers: (.*?)\s*His words:\s*$")
PROMPT_PREFIX = re.compile(r"^(?:evidence-compose|evidence-extract|compose|audit|repair|digest)-")


class ReadFailed(Exception):
    """A source could not be read; never reported as "no gap"."""


# ---------------------------------------------------------------------------
# the hashing both halves share
# ---------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def runs(text: str) -> set[str]:
    """The hashes of every five-word run in ``text`` (the whole text when shorter)."""
    w = _words(text)
    if len(w) <= RUN:
        return {_h(" ".join(w))} if w else set()
    return {_h(" ".join(w[i : i + RUN])) for i in range(len(w) - RUN + 1)}


# ---------------------------------------------------------------------------
# the seat half
# ---------------------------------------------------------------------------


def _layout(path: str) -> tuple[str, str]:
    try:
        paths = json.loads(Path(path).read_text("utf-8"))["paths"]
        return paths["audit_volume_db"]["path"], paths["specs"]["path"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReadFailed(f"the seat layout {path} cannot be read ({type(exc).__name__})") from exc


def _rows(con: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise ReadFailed(f"the audit database cannot be read ({exc})") from exc


def _kinds(subject_json: Any) -> list[str]:
    try:
        subject = json.loads(subject_json or "{}")
    except ValueError:
        return []
    kinds = subject.get("doc_kinds") if isinstance(subject, dict) else None
    return [k for k in kinds if isinstance(k, str)] if isinstance(kinds, list) else []


def _read_db(db: str) -> tuple[list[dict], list[dict]]:
    if not os.path.exists(db):
        raise ReadFailed(f"the audit database {db} is not there")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        pending = [
            {
                "id": r["proposal_id"],
                "doc_kinds": _kinds(r["subject_json"]),
                "committed_at": r["consumed_at"],
                "installed_at": r["installed_at"],
                "runs": sorted(runs(r["text"])),
            }
            for r in _rows(
                con,
                "SELECT proposal_id, subject_json, text, consumed_at, installed_at FROM pending_rules "
                "WHERE kind = 'rule' AND consumed_at IS NOT NULL AND declined_at IS NULL",
            )
        ]
        owed = [
            dict(r)
            for r in _rows(
                con,
                "SELECT id, created_at, kind FROM smd_owed WHERE closed_at IS NULL AND kind LIKE 'rule%' "
                "ORDER BY created_at",
            )
        ]
    finally:
        con.close()
    return pending, owed


def _read_wording(specs: str) -> list[dict]:
    path = Path(specs) / WORDING_REL
    try:
        doc = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ReadFailed(f"the installed wording store cannot be read ({type(exc).__name__})") from exc
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        raise ReadFailed("the installed wording store has no rule list")
    return [
        {
            "id": str(r.get("rule_id") or ""),
            "doc_kinds": [k for k in r.get("doc_kinds") or [] if isinstance(k, str)],
            "runs": sorted(runs(r.get("text") or "")),
        }
        for r in rules
        if isinstance(r, dict)
    ]


def seat_snapshot(layout_path: str) -> dict[str, Any]:
    db, specs = _layout(layout_path)
    pending, owed = _read_db(db)
    return {"schema": 1, "pending": pending, "installed": _read_wording(specs), "owed": owed}


# ---------------------------------------------------------------------------
# the prompt half
# ---------------------------------------------------------------------------


def kind_of(covers: str, file_kind: str) -> str:
    text = covers.casefold()
    for word, kind in COVERS:
        if word in text:
            return kind
    return file_kind


def _file_kind(path: Path) -> str:
    stem = PROMPT_PREFIX.sub("", path.stem).replace("-", "_")
    return stem if stem in KINDS else "unknown"


def parse_prompt(text: str, path: Path) -> list[dict[str, Any]]:
    """Each ``RULE n. Stated <date> ... Covers: <kind>. His words:`` block, with
    the indented lines under it."""
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            current = {
                "number": int(m.group(1)),
                "date": m.group(2),
                "kind": kind_of(m.group(3), _file_kind(path)),
                "words": [],
                "files": {path.name},
            }
            out.append(current)
        elif current is not None and line[:1] in (" ", "\t") and line.strip():
            current["words"].append(line.strip())
        else:
            current = None
    return out


def read_prompts(paths: list[str]) -> list[dict[str, Any]]:
    """Every rule entry in the prompt files, one per distinct wording."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(p.rglob("*.md"))
        elif p.is_file():
            files.append(p)
        else:
            raise ReadFailed(f"the prompt path {raw} is not there")
    by_digest: dict[str, dict[str, Any]] = {}
    for f in files:
        try:
            text = f.read_text("utf-8")
        except (OSError, ValueError) as exc:
            raise ReadFailed(f"the prompt {f.name} cannot be read ({type(exc).__name__})") from exc
        for entry in parse_prompt(text, f):
            words = " ".join(entry.pop("words"))
            digest = _h(" ".join(_words(words)))
            entry.update(digest=digest, runs=runs(words))
            if digest in by_digest:
                by_digest[digest]["files"] |= entry["files"]
            elif entry["runs"]:
                by_digest[digest] = entry
    return sorted(by_digest.values(), key=lambda e: (e["date"], e["number"]))


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------


def _store(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Installed rules, then committed rows not yet installed (a row and its
    installed rule share an id prefix)."""
    installed = [dict(r, runs=set(r.get("runs") or []), source="installed") for r in snap.get("installed") or []]
    ids = [str(r["id"]) for r in installed if r.get("id")]
    pending = [
        dict(r, runs=set(r.get("runs") or []), source="committed")
        for r in snap.get("pending") or []
        if not any(str(r.get("id") or "").startswith(i) for i in ids)
    ]
    return installed + pending


def compare(entries: list[dict[str, Any]], snap: dict[str, Any], min_cover: float = MIN_COVER) -> dict[str, Any]:
    store = _store(snap)
    union: set[str] = set().union(*(r["runs"] for r in store)) if store else set()
    missing, mismatch = [], []
    for e in entries:
        cover = len(e["runs"] & union) / len(e["runs"])
        if cover < min_cover:
            missing.append(dict(e, cover=cover))
            continue
        matched = [
            r
            for r in store
            if r["runs"] and r.get("doc_kinds") and len(r["runs"] & e["runs"]) / len(r["runs"]) >= min_cover
        ]
        if matched and e["kind"] in KINDS and not any(e["kind"] in r["doc_kinds"] for r in matched):
            mismatch.append(dict(e, cover=cover, store_ids=[r["id"] for r in matched]))
    no_kind = [r for r in store if not r.get("doc_kinds")]
    owed = list(snap.get("owed") or [])
    return {
        "entries": entries,
        "store": store,
        "missing": missing,
        "mismatch": mismatch,
        "no_kind": no_kind,
        "owed": owed,
    }


def _age(created: Any, now: dt.datetime) -> str:
    try:
        at = dt.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return "age unknown"
    at = at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)
    hours = int((now - at).total_seconds() // 3600)
    return f"{hours // 24}d {hours % 24}h"


def render(result: dict[str, Any], seat: str, now: dt.datetime) -> list[str]:
    store = result["store"]
    out = [
        f"RULE STORE PARITY {seat}",
        f"prompts: {len(result['entries'])} distinct rules; store: "
        f"{sum(r['source'] == 'installed' for r in store)} installed, "
        f"{sum(r['source'] == 'committed' for r in store)} committed and not installed",
        f"IN PROMPTS, NOT IN STORE: {len(result['missing'])}",
    ]
    for e in result["missing"]:
        out.append(
            f"  RULE {e['number']} stated {e['date']} kind {e['kind']} digest {e['digest']} "
            f"found {e['cover']:.0%} ({', '.join(sorted(e['files']))})"
        )
    out.append(f"IN STORE UNDER ANOTHER KIND: {len(result['mismatch'])}")
    for e in result["mismatch"]:
        out.append(f"  RULE {e['number']} stated {e['date']} kind {e['kind']} store {', '.join(e['store_ids'])}")
    out.append(f"STORE ROWS WITH NO DOCUMENT KIND: {len(result['no_kind'])}")
    out += [f"  {r['id']} ({r['source']})" for r in result["no_kind"]]
    out.append(f"OPEN OWED RULES: {len(result['owed'])}")
    out += [f"  {r.get('id')} kind {r.get('kind')} age {_age(r.get('created_at'), now)}" for r in result["owed"]]
    gaps = sum(len(result[k]) for k in ("missing", "mismatch", "no_kind", "owed"))
    out.append(f"RESULT: {gaps} GAPS" if gaps else "RESULT: CLEAN")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--seat-snapshot", action="store_true", help="run on the seat: print the store's hashes")
    ap.add_argument("--snapshot", help="the seat half's JSON (a file, or - for stdin)")
    ap.add_argument("--prompts", nargs="+", default=[], help="prompt files or directories")
    ap.add_argument("--seat", default="(seat)", help="the seat's name for the report")
    ap.add_argument("--min-cover", type=float, default=MIN_COVER)
    args = ap.parse_args(argv)
    try:
        if args.seat_snapshot:
            snap = seat_snapshot(os.environ.get(LAYOUT_ENV) or LAYOUT_DEFAULT)
            print(SNAPSHOT_TAG + json.dumps(snap, sort_keys=True))
            return 0
        if not args.snapshot or not args.prompts:
            ap.error("--snapshot and --prompts are required (or --seat-snapshot)")
        raw = sys.stdin.read() if args.snapshot == "-" else Path(args.snapshot).read_text("utf-8")
        snap = json.loads(raw.strip().removeprefix(SNAPSHOT_TAG))
        if not isinstance(snap, dict) or snap.get("schema") != 1:
            raise ReadFailed("the snapshot is not the schema this code reads")
        entries = read_prompts(args.prompts)
        if not entries:
            raise ReadFailed("the prompts hold no RULE entries; check the path")
    except (ReadFailed, OSError, ValueError) as exc:
        print(f"READ FAILED: {exc}")
        print("This is NOT a clean result. Nothing about the rule store is known from this run.")
        return 3
    result = compare(entries, snap, args.min_cover)
    print("\n".join(render(result, args.seat, dt.datetime.now(dt.timezone.utc))))
    return 1 if any(result[k] for k in ("missing", "mismatch", "no_kind", "owed")) else 0


if __name__ == "__main__":
    sys.exit(main())
