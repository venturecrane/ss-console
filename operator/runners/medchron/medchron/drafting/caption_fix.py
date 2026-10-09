"""Correct the matter record where the court's own paper settles it.

Only a discrepancy ``caption.compare`` tagged with a ``fix`` (the narrow rules
in ``caption_rules``, on a value verified against the page's text layer) is
ever written. Each write is proven by reading the WHOLE record back against
its pre-image; anything that moved besides the intended field is put back, and
the attempt is reported as a difference, never as a correction.

Two write paths, both run live on real records on 2026-10-08:

* A contact's name: ``GET /contacts/{id}``, change only the misspelled word(s)
  inside the name fields of the full ``person`` (or ``company``) object, in the
  record's own capitalization, and ``PUT`` the full object back. Smokeball
  answers 202 and applies it later, so the read-back polls.
* The case number: ``PATCH`` the layout item that carries the CaseNumber key.
  INCIDENT (2026-10-08): a PATCH that carried ONLY CaseNumber CLEARED that
  item's StatuteOfLimitationDate; a set date is dropped when it is not re-sent.
  So every date key with a value is re-sent with its current value, and the
  read-back compares every key of the item.

Every attempt is journaled to ``caption-writes.jsonl`` in the job's data
directory before and after the write, so a write is never invisible: the
runner's process is exempt from the seat's per-write audit row (the daemon
records the job instead), and this journal plus the verdict's
``caption_corrections`` are the job's own record of what it changed.
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import caption_rules as rules
from .caption import CASE_KEY, case_item

WAITS = (1.0, 2.0, 4.0, 8.0, 15.0)
SLEEP: Callable[[float], None] = time.sleep
#: Keys a contact's read-back may change on its own.
IGNORED = frozenset({"href", "versionId", "lastUpdated"})
NAME_FIELDS = {"person": ("firstName", "middleName", "lastName"), "company": ("name",)}
APPLIED, RESTORED, INCOMPLETE, NOT_APPLIED = "applied", "restored", "restore_incomplete", "not_applied"


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in IGNORED}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _poll(read: Callable[[], Any], until: Callable[[Any], bool]) -> Any:
    got: Any = None
    for wait in WAITS:
        SLEEP(wait)
        got = read()
        if until(got):
            break
    return got


def _restyle(original: str, new: str) -> str:
    """``new`` in ``original``'s capitalization: upper stays upper, lower stays
    lower, anything else is title case."""
    if original.isupper():
        return new.upper()
    if original.islower():
        return new.lower()
    return new[:1].upper() + new[1:].lower()


def renamed(body: dict[str, Any], fields: tuple[str, ...], pairs: list[tuple[str, str]]) -> dict[str, Any] | None:
    """``body`` with each misspelled word replaced inside ``fields``; None
    unless every pair is found exactly once (nothing else is touched)."""
    want = dict(pairs)
    new, hits = copy.deepcopy(body), {w: 0 for w in want}

    def sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        low = tok.lower()
        if low in want:
            hits[low] += 1
            return _restyle(tok, want[low])
        return tok

    for f in fields:
        if isinstance(new.get(f), str):
            new[f] = re.sub(r"[A-Za-z0-9]+", sub, new[f])
    return new if all(n == 1 for n in hits.values()) else None


def fix_contact(client: Any, contact_id: str, court_name: str, record_name: str) -> dict[str, Any]:
    from smokeball_connector import form_letter_facts as flf

    pairs = rules.misspelled_words(court_name, record_name)
    if not pairs:
        return {"status": NOT_APPLIED, "why": "not a misspelling"}
    path = f"/contacts/{contact_id}"
    pre = client.get(path)
    key = next((k for k in NAME_FIELDS if isinstance((pre or {}).get(k), dict)), None)
    if key is None:
        return {"status": NOT_APPLIED, "why": "the contact carries no person or company record"}
    body = renamed(pre[key], NAME_FIELDS[key], pairs)
    intended = {**pre, key: body}
    if body is None or rules.tokens(flf.contact_name(intended) or "") != rules.tokens(court_name):
        return {"status": NOT_APPLIED, "why": "the contact's name fields do not carry the misspelling once"}
    try:
        client.request("PUT", path, json={key: body})
        post = _poll(lambda: client.get(path), lambda got: _strip(got) == _strip(intended))
        if _strip(post) == _strip(intended):
            return {"status": APPLIED}
        why = (
            "the read-back changed more than the name"
            if _strip(post) != _strip(pre)
            else "the change did not read back"
        )
    except Exception as exc:  # noqa: BLE001 - the write may have landed; put the pre-image back either way
        why = f"the write did not finish ({type(exc).__name__})"
    return _restore_contact(client, path, key, pre, why)


def _restore_contact(client: Any, path: str, key: str, pre: dict[str, Any], why: str) -> dict[str, Any]:
    try:
        client.request("PUT", path, json={key: pre[key]})
        back = _poll(lambda: client.get(path), lambda got: _strip(got) == _strip(pre))
        ok = _strip(back) == _strip(pre)
    except Exception:  # noqa: BLE001 - a restore that cannot be confirmed is reported as incomplete, loudly
        ok = False
    return {"status": RESTORED if ok else INCOMPLETE, "why": why}


def _same(a: Any, b: Any) -> bool:
    return ("" if a is None else str(a)) == ("" if b is None else str(b))


def _moved(pre: dict[str, Any], post: dict[str, Any]) -> set[str]:
    return {k for k in set(pre) | set(post) if not _same(pre.get(k), post.get(k))}


def resent_dates(values: dict[str, Any]) -> dict[str, Any]:
    """Every date key that holds a value, as it is now: a PATCH that leaves a
    set date out clears it (the 2026-10-08 incident)."""
    return {k: v for k, v in values.items() if "Date" in k and v not in (None, "")}


def _patch(client: Any, path: str, values: dict[str, Any]) -> None:
    client.request("PATCH", path, json={"values": [{"key": k, "value": v} for k, v in values.items()]})


def fix_case_number(client: Any, matter_id: str, new: str) -> dict[str, Any]:
    from smokeball_connector.medicals_layout import layout_values

    found = case_item(client, matter_id)
    if found is None:
        return {"status": NOT_APPLIED, "why": "no layout item carries the case number"}
    item_id, pre = found
    path = f"/matters/{matter_id}/layouts/{item_id}"

    def read() -> dict[str, Any]:
        return layout_values(client.get(path))

    try:
        _patch(client, path, {CASE_KEY: new, **resent_dates(pre)})
        post = _poll(read, lambda got: _same(got.get(CASE_KEY), new))
        moved = _moved(pre, post) - {CASE_KEY}
        if _same(post.get(CASE_KEY), new) and not moved:
            return {"status": APPLIED, "item": item_id}
        why = f"the write also moved {', '.join(sorted(moved))}" if moved else "the change did not read back"
        keys = moved | {CASE_KEY}
    except Exception as exc:  # noqa: BLE001 - the write may have landed; put every key back either way
        why, keys = f"the write did not finish ({type(exc).__name__})", set(pre) | {CASE_KEY}
    try:
        _patch(client, path, {**{k: pre.get(k) or "" for k in keys}, **resent_dates(pre)})
        back = _poll(read, lambda got: not _moved(pre, got))
        left = sorted(_moved(pre, back))
    except Exception:  # noqa: BLE001 - a restore that cannot be confirmed is reported as incomplete, loudly
        left = sorted(keys)
    if left:
        return {"status": INCOMPLETE, "item": item_id, "why": why, "still_differs": left}
    return {"status": RESTORED, "item": item_id, "why": why}


def _journal(data: Path, row: dict[str, Any]) -> None:
    with (data / "caption-writes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}) + "\n")


def _attempt(client: Any, matter_id: str, d: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    try:
        if fix["kind"] == "case_number":
            return fix_case_number(client, matter_id, d["document_value"])
        return fix_contact(client, fix["contact_id"], d["document_value"], d["record_value"])
    except Exception as exc:  # noqa: BLE001 - a correction never fails the document; the attempt is reported
        return {"status": NOT_APPLIED, "why": f"{type(exc).__name__}: {str(exc)[:200]}"}


def apply(
    client: Any, matter_id: str, diffs: list[dict[str, Any]], data: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """(corrections applied, discrepancies still to report, restore_incomplete
    alarms for SMD). A discrepancy without a ``fix``, or with no connector
    client to write through, is reported as it is."""
    corrections: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    alarms: list[str] = []
    for d in diffs:
        fix = d.pop("fix", None)
        if not fix or client is None:
            left.append(d)
            continue
        target = {"field": d["field"], "from": d["record_value"], "to": d["document_value"], **fix}
        _journal(data, {"event": "attempt", **target})
        got = _attempt(client, matter_id, d, fix)
        _journal(data, {"event": "result", **target, **got})
        if got["status"] == APPLIED:
            corrections.append(
                {
                    "field": d["field"],
                    "from": d["record_value"],
                    "to": d["document_value"],
                    "source_document": d["source"],
                }
            )
            continue
        if got["status"] == INCOMPLETE:
            still = ", ".join(got.get("still_differs") or ["the contact"])
            alarms.append(f"{d['field']}: {got.get('why')}; still differs from before the write: {still}")
            d["why"] = f"{d['why']}; a correction was attempted and could not be fully put back"
        elif got["status"] == RESTORED:
            d["why"] = f"{d['why']}; a correction was attempted and put back ({got.get('why')})"
        left.append(d)
    return corrections, left, alarms
