"""Update mode: a matter already on the list is kept current from the
documents that ARRIVED, never by re-reading the case.

The design (2026-10-09). A first read of a matter is the full three-pass read
and stays so. After that, the list's value comes from what each new paper
changes, and the cost should follow the papers, not the size of the case:

* **What starts an update.** A new or changed court paper, process server's
  record or discovery paper, by name; or a new email whose TEXT carries court,
  discovery, service or settlement words (``screen``, code, no model). A new
  medical bill or an ordinary email starts nothing.
* **What the read sees.** The matter's current values and the arrived
  documents only. The read may still open other files through its tools when
  an arrived paper points at one; it is not handed them.
* **What code checks, free.** ``guard``: a changed value must cite an arrived
  document, and a changed date must appear in that document's text. A value
  the read dropped, or changed without that citation, is put back as it was.
  A new document cannot un-serve a defendant.
* **Where a second read goes.** Only to the values the update changed
  (``changed_paths``), with only the arrived documents: the independent check
  is spent where a mistake would reach an attorney, nowhere else.
* **The audit pass** runs only for settlement-scan hits among the arrived
  documents on an open matter.
"""

from __future__ import annotations

import copy
from typing import Any

from . import gates, manifest, parity, vocab
from .tools import dump

MODE = "update"
#: The read's instruction, appended to the read pass's system prompt.
TAIL = (
    "\n\nTHIS IS AN UPDATE. The list already holds the CURRENT VALUES below, each checked earlier against the file. "
    "New documents have arrived; they are the files listed. Report the field groups as they stand AFTER these "
    "documents: copy every current value exactly (same date, same doc) unless an arrived document changes it, and "
    "cite the arrived document for any value you change or add. Do not re-derive values the arrived documents do not "
    "touch, and do not drop a defendant or a discovery set. Then call record_result."
)

_EMAIL_GROUPS = {
    "court": (vocab.GROUP_CASE, vocab.GROUP_DEFENDANTS),
    "server": (vocab.GROUP_DEFENDANTS,),
    "discovery": (vocab.GROUP_DISCOVERY,),
    "settlement": (vocab.GROUP_CASE,),
}


def plan_changed(trigger: list[str], moved_emails: list[str], classes: dict[str, str]) -> dict[str, Any]:
    """The plan of a matter already on the list that has arrivals. Unnamed
    new emails ride along unscreened; ``screen`` keeps only those whose text
    carries litigation words."""
    named = [f for f in trigger if classes.get(f) != "newest_email"]
    unscreened = [f for f in moved_emails if f not in named]
    return {
        "reason": "changed",
        "mode": MODE,
        "read_groups": manifest.groups_for(named, classes),
        "audit": "changed",
        "trigger_files": named,
        "unscreened": unscreened,
        "candidates": named + unscreened,
    }


def email_hits(text: str, firm: Any) -> list[str]:
    """Which litigation word families an email's text carries."""
    out = []
    if any(p.search(text) for p in firm.court_rx):
        out.append("court")
    low = text.lower()
    if any(s in low for s in firm.process_servers):
        out.append("server")
    if any(p.search(text) for p in firm.discovery_rx):
        out.append("discovery")
    if any(p.search(text) for p in firm.settlement_rx):
        out.append("settlement")
    return out


def screen_plan(p: dict[str, Any], texts: dict[str, str], firm: Any) -> dict[str, Any]:
    """One update plan after its unscreened emails are read by code: the kept
    ones join the triggers with the groups their words reach; the rest drop."""
    if p.get("mode") != MODE:
        return p
    kept, groups = [], set(p["read_groups"])
    for fid in p.get("unscreened") or []:
        hits = email_hits(texts.get(fid, ""), firm)
        if hits:
            kept.append(fid)
            groups |= {g for h in hits for g in _EMAIL_GROUPS[h]}
    trigger = [*p["trigger_files"], *kept]
    return {
        **p,
        "trigger_files": trigger,
        "candidates": trigger,
        "unscreened": [],
        "screened_out": [f for f in p.get("unscreened") or [] if f not in kept],
        "read_groups": [g for g in vocab.GROUPS if g in groups] if trigger else [],
        "audit": "changed" if trigger else "none",
    }


def screen(r: Any, texts_of: Any) -> None:
    plan = r._plan()
    out = {mid: screen_plan(p, texts_of(mid), r.firm) for mid, p in plan.items()}
    dump(r.data / "plan.json", out)


# ---- the guard: code decides which of the read's changes stand ---------------------------
def _carried(obj: Any, trigger: set[str], texts: dict[str, str], date_key: str = "date") -> bool:
    """A changed value stands only when it cites an arrived document and, if
    it is a date, that document's text carries the date."""
    fid = parity._src_id(obj)
    if not fid or fid not in trigger:
        return False
    date = obj.get(date_key) if isinstance(obj, dict) else None
    return not date or gates.text_carries(texts.get(fid, ""), str(date))


def _same(a: Any, b: Any, *keys: str) -> bool:
    return parity._key(a, *keys) == parity._key(b, *keys)


def _guard_case(prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict) -> None:
    for k, keys in (("complaint_filed", ("date",)), ("next_court_date", ("date",)), ("case_status", ("value",))):
        if k not in out or _same(prior.get(k), out.get(k), *keys):
            continue
        if k == "case_status":
            ok = parity._src_id(out[k]) in trigger
        else:
            ok = _carried(out[k], trigger, texts)
        _record(log, k, ok, prior, out)
    for k in ("case_name", "court", "case_number", "firm_role"):
        if prior.get(k) not in (None, "") and out.get(k) != prior.get(k):
            out[k] = prior[k]  # identity fields are not an update's to change
            log["reverted"].append(k)


def _record(log: dict, path: str, ok: bool, prior: dict[str, Any], out: dict[str, Any]) -> None:
    if ok:
        log["changed"].append(path)
    else:
        out[path] = copy.deepcopy(prior.get(path))
        log["reverted"].append(path)


def _guard_defendants(
    prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict
) -> None:
    before = {parity.party_key(str(d.get("name") or "")): d for d in prior.get("defendants") or []}
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in out.get("defendants") or []:
        key = parity.party_key(str(d.get("name") or ""))
        old = before.get(key)
        if old is None:
            sourced = any(_carried(d.get(f), trigger, texts) for f in ("served", "answered"))
            if sourced:
                kept.append(d)
                seen.add(key)
                log["changed"].append(f"defendants[{d.get('name')}]")
            else:
                log["reverted"].append(f"defendants[{d.get('name')}]")
            continue
        seen.add(key)
        d = copy.deepcopy(d)
        for f in ("served", "answered"):
            if not _same(old.get(f), d.get(f), "date"):
                if _carried(d.get(f), trigger, texts) and d.get(f, {}).get("date"):
                    log["changed"].append(f"defendants[{d.get('name')}].{f}")
                else:
                    d[f] = copy.deepcopy(old.get(f))  # never un-serve, never an uncited date
                    log["reverted"].append(f"defendants[{d.get('name')}].{f}")
        if d.get("status") != old.get("status"):
            log["changed"].append(f"defendants[{d.get('name')}].status")  # always goes to the second read
        kept.append(d)
    for key, d in before.items():
        if key not in seen:
            kept.append(copy.deepcopy(d))
            log["reverted"].append(f"defendants[{d.get('name')}] (dropped by the read)")
    out["defendants"] = kept


def _row_key(kind: str, r: dict[str, Any]) -> str:
    return f"{kind}[{r.get('set')}|{r.get('served_on') or r.get('served_by')}]"


def _guard_discovery(
    prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict
) -> None:
    for kind in ("discovery_propounded", "discovery_served_on_client"):
        before = {_row_key(kind, r): r for r in prior.get(kind) or []}
        kept, seen = [], set()
        for r in out.get(kind) or []:
            key = _row_key(kind, r)
            old = before.get(key)
            if old is None:
                if _carried(r, trigger, texts) or (not r.get("date") and parity._src_id(r) in trigger):
                    kept.append(r)
                    seen.add(key)
                    log["changed"].append(key)
                else:
                    log["reverted"].append(key)
                continue
            seen.add(key)
            r = copy.deepcopy(r)
            if not _same(old, r, "date"):
                if _carried(r, trigger, texts):
                    log["changed"].append(key + ".date")
                else:
                    r["date"], r["source"] = old.get("date"), copy.deepcopy(old.get("source"))
                    log["reverted"].append(key + ".date")
            if kind == "discovery_served_on_client":
                rs, ors = r.get("responses_served") or {}, old.get("responses_served") or {}
                if not _same(ors, rs, "value", "date"):
                    if _carried(rs, trigger, texts) or (not rs.get("date") and parity._src_id(rs) in trigger):
                        log["changed"].append(key + ".responses")
                    else:
                        r["responses_served"] = copy.deepcopy(ors)
                        log["reverted"].append(key + ".responses")
            kept.append(r)
        for key, r in before.items():
            if key not in seen:
                kept.append(copy.deepcopy(r))
                log["reverted"].append(key + " (dropped by the read)")
        out[kind] = kept


def guard(
    prior: dict[str, Any], fresh: dict[str, Any], groups: list[str], trigger: list[str], texts: dict[str, str]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """``fresh`` (the update read merged over ``prior``) with every change
    that is not carried by an arrived document put back. Returns the result
    and ``{"changed": [...], "reverted": [...]}``."""
    out = copy.deepcopy(fresh)
    trig = set(trigger)
    log: dict[str, list[str]] = {"changed": [], "reverted": []}
    if vocab.GROUP_CASE in groups:
        _guard_case(prior, out, trig, texts, log)
    if vocab.GROUP_DEFENDANTS in groups:
        _guard_defendants(prior, out, trig, texts, log)
    if vocab.GROUP_DISCOVERY in groups:
        _guard_discovery(prior, out, trig, texts, log)
    return out, log


def check_scope(changed: list[str]) -> str:
    """The second read's scope line: only the values the update changed."""
    return "\nCHECK ONLY these values, changed by the arrived documents (the files listed): " + "; ".join(changed)
