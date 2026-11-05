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
* **What the read returns.** Only what an arrived document changes or adds
  (``TAIL``), each with its document; ``overlay`` lays that over the current
  values. It does not retype the list.
* **What code checks, free.** ``guard``: a changed value must cite an arrived
  document, and a changed date must appear in that document's text. A value
  the read dropped, or changed without that citation, is put back as it was.
  A new document cannot un-serve a defendant. Statuses come last, from what
  stood (``defstatus.after_update``).
* **Where a second read goes.** Only to the values the update changed
  (``changed_paths``), with only the arrived documents: the independent check
  is spent where a mistake would reach an attorney, nowhere else.
* **The audit pass** runs only for settlement-scan hits among the arrived
  documents on an open matter.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Any

from . import answers, defstatus, gates, identity, manifest, parity, vocab
from .tools import dump

MODE = "update"
#: The read's instruction, appended to the read pass's system prompt.
TAIL = (
    "\n\nTHIS IS AN UPDATE. The list already holds the CURRENT VALUES below, each checked earlier against the file. "
    "New documents have arrived; they are the files listed. Report ONLY what an arrived document changes or adds, "
    "each with that document: code keeps every value you leave out exactly as it is. List a defendant only for an "
    "event an arrived document gives it, by the name the current values use, or to add one. List a discovery set only "
    "if an arrived document changes or adds it. If an arrived document changes the court dates, court_dates is the "
    "whole list as it now stands. Then call record_result."
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


#: Litigation words an email's TEXT carries when it can move a list value.
#: The firm's own patterns are written for file NAMES ("Notice of CMC.pdf")
#: and miss how people write in mail ("hearing set for 10/21"); these are the
#: product's, in addition to the firm's (2026-10-09 replay: a CMC email was
#: screened out on the firm's patterns alone).
_COURT_WORDS = re.compile(
    r"(?i)\b(hearing|case management|cmc|trial (?:date|setting|confirmation)|court date|continu(?:ed|ance)|"
    r"dep(?:artmen)?t\.? ?\d|minute order|tentative ruling|order to show cause|osc|motion|default|"
    r"dismiss(?:al|ed)?|stipulat\w*|complaint|summons|answer(?:ed)?|cross-complaint|mediation|arbitration)\b"
)
_SERVICE_WORDS = re.compile(
    r"(?i)\b(served|personal service|sub(?:stituted?)? service|proof of service|process server|service of process)\b"
)
_DISCOVERY_WORDS = re.compile(
    r"(?i)\b(interrogator\w*|requests? for (?:production|admission)|deposition|subpoena|discovery|"
    r"responses? (?:are )?due|meet and confer)\b"
)


_QUOTE_START = re.compile(
    r"(?im)^\s*(?:from:\s.*\n\s*(?:sent|date):|-{2,}\s*original message\s*-{2,}|on .{4,120} wrote:\s*$|_{20,})"
)
_ATTACHMENT = "\n\n[ATTACHMENT] "


def fresh_text(text: str) -> str:
    """What an email itself says: its subject, its body down to the quoted
    history, and every attachment. A reply that only says "Will do." above a
    quoted court thread carries no court words of its own (2026-10-09 replay:
    with the whole text screened, every email passed)."""
    head, _, rest = text.partition("\n\n")
    if not head.lower().startswith(("from:", "to:", "date:", "subject:")):
        head, rest = "", text  # no header block: all of it is the message
    subject = next((ln for ln in head.splitlines() if ln.lower().startswith("subject:")), "")
    body, *atts = rest.split(_ATTACHMENT)
    m = _QUOTE_START.search(body)
    body = body[: m.start()] if m else body
    return "\n".join([subject, body, *(_ATTACHMENT.strip() + " " + a for a in atts)])


def email_hits(text: str, firm: Any) -> list[str]:
    """Which litigation word families an email's own text carries."""
    text = fresh_text(text)
    out = []
    if any(p.search(text) for p in firm.court_rx) or _COURT_WORDS.search(text):
        out.append("court")
    low = text.lower()
    if any(s in low for s in firm.process_servers) or _SERVICE_WORDS.search(text):
        out.append("server")
    if any(p.search(text) for p in firm.discovery_rx) or _DISCOVERY_WORDS.search(text):
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
        text = texts.get(fid)
        if not (text or "").strip():
            # Could not read it is not "nothing in it": an email whose text
            # was not extracted is read, never screened out (2026-10-09
            # replay: a "hearing set for 10/21" email was dropped unread).
            kept.append(fid)
            groups |= set(vocab.GROUPS)
            continue
        hits = email_hits(text or "", firm)
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


# ---- the overlay: the read's changes laid over the current values -------------------------
def _overlay_defendant(ctx: Any, old: dict[str, Any], raw: dict[str, Any], today: dt.date) -> dict[str, Any]:
    """``old`` with each value ``raw`` names replaced. A date the read did not
    give changes nothing here, and a sent-out list keeps the most recent date
    of what is on the list and what arrived (``answers.latest_sent``)."""
    new = answers.defendant(ctx, raw, today)
    out = copy.deepcopy(old)
    for f in (*vocab.DEFENDANT_EVENTS, "out_for_service", "role"):
        if f not in raw:
            continue
        if f == "sent_for_service":
            out[f] = _later_sent(old.get(f), new.get(f))
        elif f in new:
            out[f] = new[f]
    out["flags"] = list(dict.fromkeys([*(old.get("flags") or []), *new["flags"]]))
    return out


def _later_sent(old: Any, new: Any) -> Any:
    new_date, old_date = (new or {}).get("date"), (old or {}).get("date")
    if not new_date or (old_date and str(old_date) >= str(new_date)):
        return copy.deepcopy(old) if old is not None else new
    return new


def _overlay_defendants(ctx: Any, prior: dict[str, Any], raw: Any, today: dt.date) -> list[dict[str, Any]]:
    out = [copy.deepcopy(d) for d in prior.get("defendants") or []]
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        i = next((i for i, d in enumerate(out) if identity.same_person(str(d.get("name") or ""), name)), None)
        if i is None:
            out.append(answers.defendant(ctx, row, today))
        else:
            out[i] = _overlay_defendant(ctx, out[i], row, today)
    return out


def _overlay_rows(ctx: Any, prior: dict[str, Any], raw: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    rows = identity.split_rows(prior.get(kind))
    index = {identity.row_key(r): i for i, r in enumerate(rows)}
    for r in answers.discovery(ctx, raw.get(kind), kind == "discovery_served_on_client"):
        key = identity.row_key(r)
        if key in index:
            rows[index[key]] = r
        else:
            index[key] = len(rows)
            rows.append(r)
    return rows


def _overlay_case(ctx: Any, prior: dict[str, Any], raw: dict[str, Any], today: dt.date) -> dict[str, Any]:
    out = {k: copy.deepcopy(prior.get(k)) for k in vocab.GROUP_FIELDS[vocab.GROUP_CASE]}
    if "court_dates_read" in prior:
        out["court_dates_read"] = copy.deepcopy(prior["court_dates_read"])
    for k in ("case_name", "court", "case_number"):
        if k in raw:
            out[k] = raw[k]
    if "firm_role" in raw:
        out["firm_role"] = answers.firm_role(raw["firm_role"])
    if "case_status" in raw:
        out["case_status"] = answers.case_status(ctx, raw["case_status"])
    if "complaint_filed" in raw:
        out["complaint_filed"] = answers.val(ctx, raw["complaint_filed"], ("date",))
    if "court_dates" in raw:
        out["court_dates_read"] = answers.court_rows(ctx, raw["court_dates"])
        out["next_court_date"] = answers.next_court(out["court_dates_read"], today)
    return out


def overlay(ctx: Any, prior: dict[str, Any], raw: dict[str, Any], groups: list[str], today: dt.date) -> dict[str, Any]:
    """An update read's answer (changes only) as the groups' whole values, each
    one it left out as the last list had it. ``guard`` then decides which of the
    changes stand. A status it may have typed is not read."""
    out: dict[str, Any] = {}
    if vocab.GROUP_CASE in groups:
        out.update(_overlay_case(ctx, prior, raw, today))
    if vocab.GROUP_DEFENDANTS in groups:
        out["defendants"] = _overlay_defendants(ctx, prior, raw.get("defendants"), today)
    if vocab.GROUP_DISCOVERY in groups:
        for kind in ("discovery_propounded", "discovery_served_on_client"):
            out[kind] = _overlay_rows(ctx, prior, raw, kind)
    # A flag is a finding of this read, and what the list held stays beside it.
    new_flags = [str(x) for x in raw.get("matter_flags") or []]
    out["matter_flags"] = list(dict.fromkeys([*(prior.get("matter_flags") or []), *new_flags]))
    if raw.get("notes"):
        out["notes"] = str(raw["notes"])
    return out


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


def _guard_case(
    prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict
) -> None:
    for k, keys in (("complaint_filed", ("date",)), ("next_court_date", ("date",)), ("case_status", ("value",))):
        if k not in out or _same(prior.get(k), out.get(k), *keys):
            continue
        if k == "case_status":
            ok = parity._src_id(out[k]) in trigger
        else:
            ok = _carried(out[k], trigger, texts)
        _record(log, k, ok, prior, out)
    if "next_court_date" in log["reverted"]:  # the list it was picked from goes back with it
        if "court_dates_read" in prior:
            out["court_dates_read"] = copy.deepcopy(prior["court_dates_read"])
        else:
            out.pop("court_dates_read", None)
    for k in ("case_name", "court", "case_number", "firm_role"):
        if prior.get(k) == vocab.UNCLEAR and out.get(k) in vocab.FIRM_ROLES:
            continue  # the side the last list could not say, now read
        if prior.get(k) not in (None, "") and out.get(k) != prior.get(k):
            out[k] = prior[k]  # identity fields are not an update's to change
            log["reverted"].append(k)


def _record(log: dict, path: str, ok: bool, prior: dict[str, Any], out: dict[str, Any]) -> None:
    if ok:
        log["changed"].append(path)
    else:
        out[path] = copy.deepcopy(prior.get(path))
        log["reverted"].append(path)


#: A defendant's dated values an update may change (``identity.DATED_FIELDS``).
_DEF_DATES = tuple(f for f, _k in identity.DATED_FIELDS)


def _restore(d: dict[str, Any], old: dict[str, Any], f: str) -> None:
    """``d[f]`` put back as ``old`` had it; a value the last list did not carry
    is removed (a sent-out date stays "Not read yet" rather than "Not in file")."""
    if f not in old:
        d.pop(f, None)
    else:
        d[f] = copy.deepcopy(old.get(f))


def _guard_standing(d: dict[str, Any], old: dict[str, Any], trigger: set[str], log: dict) -> None:
    """A role or an out-for-service answer stands only on an arrived document:
    each moves the status, and neither carries a date for ``_carried`` to check."""
    for f in ("out_for_service", "role"):
        if _same(old.get(f), d.get(f), "value"):
            continue
        if parity._src_id(d.get(f)) in trigger:
            log["changed"].append(f"defendants[{d.get('name')}].{f}")
        else:
            _restore(d, old, f)
            log["reverted"].append(f"defendants[{d.get('name')}].{f}")


def _guard_defendants(
    prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict, today: dt.date
) -> None:
    before = {parity.party_key(str(d.get("name") or "")): d for d in prior.get("defendants") or []}
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in out.get("defendants") or []:
        key = parity.party_key(str(d.get("name") or ""))
        old = before.get(key)
        if old is None:
            sourced = any(_carried(d.get(f), trigger, texts) for f in _DEF_DATES)
            if sourced:
                d = copy.deepcopy(d)
                if not (
                    _carried(d.get("sent_for_service"), trigger, texts)
                    and (d.get("sent_for_service") or {}).get("date")
                ):
                    d.pop("sent_for_service", None)  # an update read did not read the whole file
                kept.append(d)
                seen.add(key)
                log["changed"].append(f"defendants[{d.get('name')}]")
            else:
                log["reverted"].append(f"defendants[{d.get('name')}]")
            continue
        seen.add(key)
        d = copy.deepcopy(d)
        for f in _DEF_DATES:
            if not _same(old.get(f), d.get(f), "date"):
                if _carried(d.get(f), trigger, texts) and (d.get(f) or {}).get("date"):
                    log["changed"].append(f"defendants[{d.get('name')}].{f}")
                else:
                    _restore(d, old, f)  # never un-serve, never an uncited date
                    log["reverted"].append(f"defendants[{d.get('name')}].{f}")
            elif f == "sent_for_service" and (f in old) != (f in d):
                # An update reads only the arrived papers: finding no request
                # there is not "Not in file", and leaving the key out does not
                # unread one; the list keeps what it carried.
                _restore(d, old, f)
        _guard_standing(d, old, trigger, log)
        defstatus.after_update(d, old, today)
        if d.get("status") != old.get("status"):
            log["changed"].append(f"defendants[{d.get('name')}].status")  # always goes to the second read
        kept.append(d)
    for key, d in before.items():
        if key not in seen:
            kept.append(copy.deepcopy(d))
            log["reverted"].append(f"defendants[{d.get('name')}] (dropped by the read)")
    out["defendants"] = kept


set_identity = identity.set_identity  # one set however a read words it (identity.py)


def _row_key(kind: str, r: dict[str, Any]) -> str:
    return f"{kind}[{identity.row_key(r)}]"


def _guard_discovery(
    prior: dict[str, Any], out: dict[str, Any], trigger: set[str], texts: dict[str, str], log: dict
) -> None:
    for kind in ("discovery_propounded", "discovery_served_on_client"):
        before = {_row_key(kind, r): r for r in identity.split_rows(prior.get(kind))}
        kept, seen = [], set()
        for r in identity.split_rows(out.get(kind)):
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
            # The row keeps the wording already on the list; a read's re-wording is not a change.
            for f in ("set", "served_on", "served_by"):
                if f in old:
                    r[f] = old[f]
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
    prior: dict[str, Any],
    fresh: dict[str, Any],
    groups: list[str],
    trigger: list[str],
    texts: dict[str, str],
    *,
    today: dt.date,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """``fresh`` (the update read merged over ``prior``) with every change
    that is not carried by an arrived document put back, and each defendant's
    status worked out from what stood. Returns the result and
    ``{"changed": [...], "reverted": [...]}``."""
    out = copy.deepcopy(fresh)
    trig = set(trigger)
    log: dict[str, list[str]] = {"changed": [], "reverted": []}
    if vocab.GROUP_CASE in groups:
        _guard_case(prior, out, trig, texts, log)
    if vocab.GROUP_DEFENDANTS in groups:
        _guard_defendants(prior, out, trig, texts, log, today)
    if vocab.GROUP_DISCOVERY in groups:
        _guard_discovery(prior, out, trig, texts, log)
    return out, log


def check_scope(changed: list[str]) -> str:
    """The second read's scope line: only the values the update changed."""
    return "\nCHECK ONLY these values, changed by the arrived documents (the files listed): " + "; ".join(changed)
