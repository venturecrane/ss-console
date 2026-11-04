"""A prior value the gates would refuse is READ AGAIN, never handed to them as-is.

WHY (2026-10-13, A&P job 01M4VKNWGS33KFTWVNQZMMZ4V2). The live Irma list held
at the gates with 32 "date is the Smokeball save date", 11 "date without
source", case statuses not normalized or without source, and a past court
date, while the proof run of the same scope passed. The gates run on every
matter in scope, and a matter this job does not read reaches them as its
STORED prior. The first prior is the hand-run seed: most of its dates equal
the file-list date and some carry no source at all. "Save date" is refused
whenever the job holds no text for the value's source file, which is every
seed value whose file this job did not fetch. The proof run started from an
empty state, read everything fresh, and passed.

The rule: the gates see only values this job read or values whose source text
this job holds. At plan time (``gaps``), every prior value is checked against
the gate rules that do not need text:

* **hard**: a rule no document text can satisfy (no source, a source not on
  the matter, a status outside the vocabulary or without a source, a past
  court date on an active case, a future date, a malformed date, a defendant
  row the rules refuse). Its whole field group is read again.
* **soft**: a date equal to its source file's saved date. Its source file is
  fetched; after extraction (``settle``, in the screen stage) a group whose
  every such date appears in its source's text is NOT read (the gate will see
  the text and pass); any other is read again.

A changed matter (an update) with a group to read fresh is read in full for
those groups plus its update groups: an update read copies current values, so
a value that must be re-derived cannot ride an update.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from . import gates, vocab

#: Matter-level gate rules and the field group whose read can satisfy them.
_GROUP_RULES = {
    "case status not normalized": vocab.GROUP_CASE,
    "case status without source": vocab.GROUP_CASE,
    "filed case with no complaint date": vocab.GROUP_CASE,
    "next court date is in the past (active case)": vocab.GROUP_CASE,
    "no defendants": vocab.GROUP_DEFENDANTS,
    "defendant status not normalized": vocab.GROUP_DEFENDANTS,
    "answered but no answer date": vocab.GROUP_DEFENDANTS,
    "answer date but status says no answer": vocab.GROUP_DEFENDANTS,
    "served date but status says not served": vocab.GROUP_DEFENDANTS,
}
#: Value-level gate rules: the item's second element is the value's path.
_PATH_RULES = ("future date", "date not YYYY-MM-DD", "date without source")


def ordered(groups: Any) -> list[str]:
    want = set(groups)
    return [g for g in vocab.GROUPS if g in want]


def _values(m: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(group, gate path, value) for every value the gates check a source of."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    case = {k: m.get(k) for k in ("complaint_filed", "next_court_date")}
    out += [(vocab.GROUP_CASE, p, v) for p, v in gates._dated(case)]
    if isinstance(m.get("case_status"), dict):
        out.append((vocab.GROUP_CASE, "case_status", m["case_status"]))
    defs = gates._dated({"defendants": m.get("defendants")})[2:]
    out += [(vocab.GROUP_DEFENDANTS, p, v) for p, v in defs]
    disc = {k: m.get(k) for k in ("discovery_propounded", "discovery_served_on_client")}
    out += [(vocab.GROUP_DISCOVERY, p, v) for p, v in gates._dated(disc)[2:]]
    return out


def gaps(prior: dict[str, Any], files: list[dict[str, Any]], today: dt.date) -> dict[str, Any]:
    """``{"hard": [groups], "soft": {group: [[file_id, date], ...]}}`` for a
    stored prior against this job's file list."""
    on_matter = {str(f["id"]) for f in files if not f.get("deleted")}
    saved = {str(f["id"]): {str(f.get(k) or "")[:10] for k in ("created", "modified")} for f in files}
    vals = _values(prior)
    group_of = {p: g for g, p, _v in vals}
    hard: set[str] = set()
    probe = {**prior, "number": "-", "matter_id": "-", "responsible": "-"}
    for rule, items in gates.rule_matters([probe], today).items():
        if not items:
            continue
        if rule in _GROUP_RULES:
            hard.add(_GROUP_RULES[rule])
        elif rule in _PATH_RULES:
            hard |= {group_of.get(str(it[1]), vocab.GROUP_CASE) for it in items}
    soft: dict[str, list[list[str]]] = {}
    for g, _path, v in vals:
        src = v.get("source")
        if not isinstance(src, dict) or not src:
            continue
        fid = str(src.get("file_id") or "")
        if fid not in on_matter:
            hard.add(g)
            continue
        d = v.get("date")
        if isinstance(d, str) and gates.DATE.match(d) and d in saved.get(fid, set()):
            soft.setdefault(g, []).append([fid, d])
    return {"hard": ordered(hard), "soft": {g: soft[g] for g in ordered(soft)}}


def attach(p: dict[str, Any], fresh: list[str], soft: dict[str, list[list[str]]]) -> dict[str, Any]:
    """The plan with the seed check's findings: ``seed_fresh`` (read in full),
    ``seed_soft`` (read unless the fetched text carries every date), and
    ``seed_files`` (fetched so the text can be checked)."""
    files = list(dict.fromkeys(fid for pairs in soft.values() for fid, _d in pairs))
    return {**p, "seed_fresh": ordered(fresh), "seed_soft": soft, "seed_files": files}


def unresolved(p: dict[str, Any], text_of: Callable[[str], "str | None"]) -> list[str]:
    """The soft groups whose dates the fetched texts do not all carry."""
    out = []
    for g, pairs in (p.get("seed_soft") or {}).items():
        if any(not gates.text_carries(text_of(fid) or "", d) for fid, d in pairs):
            out.append(g)
    return ordered(out)


def to_full(p: dict[str, Any], base: list[str], extra: list[str]) -> dict[str, Any]:
    """An update plan turned into a full read of ``base`` + ``extra``, handed
    the matter's whole candidate list plus the arrived and seed files."""
    trig = list(p.get("trigger_files") or []) + list(p.get("unscreened") or [])
    cands = list(dict.fromkeys(trig + list(p.get("full_candidates") or []) + list(p.get("seed_files") or [])))
    out = {k: v for k, v in p.items() if k != "mode"}
    groups = ordered([*base, *extra])
    return {
        **out,
        "reason": "changed_and_seed_gaps",
        "read_groups": groups,
        "audit": "changed" if groups else "none",
        "candidates": cands,
        "unscreened": [],
        "seed_base": ordered(base),
    }


def settle(p: dict[str, Any], text_of: Callable[[str], "str | None"]) -> dict[str, Any]:
    """One plan after fetch and extract: a soft group the texts settle is not
    read; every other seed group is. Idempotent (a resumed screen re-runs it)."""
    from .update import MODE  # update builds on manifest, which builds on this module

    if "seed_fresh" not in p:
        return p
    left = unresolved(p, text_of)
    fresh = list(p.get("seed_fresh") or [])
    if p.get("mode") == MODE:
        if not (fresh or left):
            return {**p, "seed_unresolved": []}
        base = list(p["read_groups"])  # the screened update groups ([] when nothing was kept)
        return {**to_full(p, base, [*fresh, *left]), "seed_unresolved": left}
    groups = ordered([*(p.get("seed_base") or []), *fresh, *left])
    audit = p["audit"] if p["audit"] == "all" else ("changed" if groups else "none")
    return {**p, "read_groups": groups, "audit": audit, "seed_unresolved": left}


def settle_all(r: Any, text_of: Callable[[str, str], "str | None"]) -> None:
    from .tools import dump

    plan = r._plan()
    out = {mid: settle(p, lambda fid, mid=mid: text_of(mid, fid)) for mid, p in plan.items()}
    dump(r.data / "plan.json", out)
