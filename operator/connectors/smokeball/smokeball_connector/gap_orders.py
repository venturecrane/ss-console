"""Records orders built from a matter's filed gap audit ("order the missing records").

The demand job files a Records and Billing Gap Audit on the matter: numbered
item tables, one row per thing the file lacks, each with its provider, what is
missing, where the file points to it, and a suggested request type. When the
requester answers the package asking for those records to be ordered, this module
turns that audit into records orders, with no model in the loop:

1. **Read the audit as filed.** The newest ``Gap Audit - ...docx`` on the
   matter, from Smokeball, so what is ordered is what she was sent.
2. **Keep the orderable rows.** Records, itemized bills, imaging and prior
   look-backs addressed to a medical provider. Not orderable, and said so:
   a request to ask, confirm, identify or clarify; a Medicals-tab update; a
   provider the audit itself does not name; and anything addressed to a payer,
   a lienholder, a carrier, a government program or a law firm.
3. **Group by provider**, carrying the record types and the dates the rows
   state. A provider's range starts at the earliest date its rows name (a
   look-back of N years reaches N years before that) and runs to today.
4. **Resolve each provider** against the matter's Medicals tab (the provider's
   own contact, for its ZIP and street) and then the vendor's directory. Each
   ends matched (one location, or one location whose street is the Medicals
   contact's), ambiguous (a question listing the candidates) or no-match (a
   question). Nothing is guessed.
5. **Build the orders** through ``records_orders.prepare``, unchanged: the
   matched facilities, ``MAX_LOCATIONS`` to an order.

Orders nothing. Each ready order goes to ``place_records_order`` as the usual
two-turn ``[act ...]`` line.
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Any, Callable

from . import gap_locate
from .records_orders import MAX_LOCATIONS, MAX_YEARS, prepare

AUDIT_PREFIX = "Gap Audit"
ITEM_HEADER = ("item", "provider", "what's missing")

#: A request that is not an order: someone must ask, confirm or decide first.
#: Anywhere in the request ("Identify PT provider; records..." is a question first).
_NOT_AN_ORDER = re.compile(
    r"\b(ask|confirm|identify|clarify|verify|written confirmation|update (the )?medicals|medicals tab|attorney)\b"
    r"|^\s*none\b",
    re.I,
)
#: Addressed to someone who is not a medical custodian, in the provider OR the
#: request ("Med pay ledger" is a payer's paper however it is worded). MediCal
#: is matched case-sensitively or with a separator: "medical" is a provider word.
_NOT_A_PROVIDER = re.compile(
    r"medicare|medicaid|\bmedi[- ]cal\b|(?-i:\bMediCal\b|\bMEDICAL\s*\(DHCS\))|\bdhcs\b|\bcms\b|msprc|noridian|"
    r"health ?plan|healthplan|health ?net\b|insurance|insurer|casualty|indemnity|underwriter|"
    r"workers'? ?comp|work ?comp|\bwcab\b|carrier|adjuster|claims? (administrat|adjust)|third[- ]party administrat|"
    r"blue shield|blue cross|molina|med[- ]?pay|subrogation|recovery services|optum|anthem|aetna|cigna|"
    r"unitedhealth|humana|tricare|geico|allstate|state farm|progressive|farmers|mercury insurance|\bloya\b|"
    r"\blegal\b|\blaw\b|\bllp\b|attorney|\bcounsel\b|lien ?holder|funding",
    re.I,
)
#: Counseling and psychotherapy are excluded ON PURPOSE: their notes need an
#: authorization of their own (HIPAA's psychotherapy-notes rule), not the
#: standard records release this order sends, so the firm requests them itself.
_COUNSELING = re.compile(
    r"counsel(ing|ling|or)|psychotherap|psychiatr|behavioral health|mental health|therapist\b", re.I
)
#: The audit names no provider.
_UNNAMED = re.compile(
    r"not named|unnamed|unknown|unidentified|^none\b|provider not|\(held out\)|^prior\b|providers\b|surgeons\b", re.I
)
#: A request that is about a lien or a payer's paper, not records or bills.
_PAYER_ASK = re.compile(r"\b(lien|eob|explanation of benefits|conditional payment|payoff|policy limits)\b", re.I)
_ORDERABLE = re.compile(
    r"record|bill|ledger|itemiz|image|film|\bcd\b|report|notes?|chart|printout|results|look-?back", re.I
)

_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
_YEARS = re.compile(r"\b(\d{1,2})[- ]year", re.I)


# ---- 1. the audit as filed ---------------------------------------------------------------
def _cells(tr: Any) -> list[str]:
    return [
        " ".join("".join(t.text or "" for t in tc.iter() if t.tag.endswith("}t")).split()) for tc in tr.findall("{*}tc")
    ]


def audit_rows(blob: bytes) -> list[dict[str, str]]:
    """Every numbered item row of the audit's item tables, with the heading of
    the section it sits under. The possible list and the vendor and wall
    tables carry no Item column and are not read."""
    from docx import Document

    body = Document(io.BytesIO(blob)).element.body
    out, section = [], ""
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = " ".join("".join(t.text or "" for t in child.iter() if t.tag.endswith("}t")).split())
            if text and len(text) <= 120:
                section = text
            continue
        if tag != "tbl":
            continue
        rows = child.findall(".//{*}tr")
        if not rows:
            continue
        head = [h.casefold() for h in _cells(rows[0])]
        if tuple(head[:3]) != ITEM_HEADER:
            continue
        for tr in rows[1:]:
            c = _cells(tr)
            if len(c) >= 7 and c[0].isdigit():
                out.append(
                    {
                        "item": c[0],
                        "section": section,
                        "provider": c[1],
                        "missing": c[2],
                        "where": c[3],
                        "basis": c[4],
                        "request": c[5],
                        "priority": c[6],
                    }
                )
    return out


def latest_audit(client: Any, matter_id: str) -> tuple[dict[str, Any] | None, bytes | None]:
    from .library import list_matter_files

    files = [
        f
        for f in list_matter_files(client, matter_id)
        if str(f.get("name") or "").startswith(AUDIT_PREFIX)
        and str(f.get("fileExtension") or ".docx").lower().lstrip(".") == "docx"
    ]
    if not files:
        return None, None
    files.sort(key=lambda f: str(f.get("dateCreated") or ""), reverse=True)
    _info, blob = client.download_file(matter_id, files[0]["id"])
    return files[0], blob


# ---- 2. orderable rows ----------------------------------------------------------------------
def classify(row: dict[str, str]) -> str | None:
    """None when the row is orderable, else why it is not."""
    if row["section"].lower().startswith("d.") or "timeline" in row["section"].lower():
        return "a treatment-timeline fact, not a request"
    if _UNNAMED.search(row["provider"]):
        return "the audit does not name the provider"
    if _NOT_A_PROVIDER.search(row["provider"]) or _NOT_A_PROVIDER.search(row["request"]):
        return "addressed to a payer, carrier, program or law firm, not a medical custodian"
    if _COUNSELING.search(row["provider"]):
        return "counseling or psychotherapy records need their own authorization; the firm requests them itself"
    if _NOT_AN_ORDER.search(row["request"]):
        return "the audit asks for a question or a confirmation first"
    if _PAYER_ASK.search(row["request"]) and not re.search(r"record|itemiz|image|film", row["request"], re.I):
        return "a lien, payer or balance request, not records or bills"
    if not _ORDERABLE.search(row["request"] + " " + row["missing"]):
        return "no record, bill or image is asked for"
    return None


# ---- 3. grouped by provider ---------------------------------------------------------------
def facility_name(provider: str) -> str:
    """The custodian the row addresses, without the audit's parenthetical
    (the treating clinician, an "ordered by", a date)."""
    name = re.sub(r"\s*\([^)]*\)?", "", provider).split(" / ")[0].strip(" ,;-")
    return re.sub(r"^(dr\.?|doctor)\s+", "", name, flags=re.I).strip() or provider.strip()


_SUFFIX = re.compile(r"\b(md|m d|do|dpm|dpt|pa|np|pc|inc|llc|llp|ltd|corp|a medical corporation)\b")


def _key(name: str) -> str:
    """Grouping key: "Pat Q. Example, MD" and "Pat Q. Example MD PC" are one custodian."""
    return " ".join(_SUFFIX.sub(" ", re.sub(r"[^a-z0-9]+", " ", name.casefold())).split())


def types_for(text: str) -> list[str]:
    t = text.casefold()
    out = []
    if re.search(r"record|notes?|chart|report|printout|results|look-?back|visit", t):
        out.append(
            "Radiology Record"
            if re.search(r"radiology|mri|ct\b|x-?ray|xr\b|imaging", t) and "report" in t
            else "Medical"
        )
    if re.search(r"bill|ledger|itemiz|cpt|charges|balance", t):
        out.append("Billing")
    if re.search(r"image|film|\bcd\b", t):
        out.append("Radiology Image")
    return list(dict.fromkeys(out)) or ["Medical", "Billing"]


_BIRTH = re.compile(r"\b(dob|d\.o\.b|birth|born)\b", re.I)


def _dates(text: str, today: date) -> list[date]:
    """Service dates the text names: never a date of birth (one written within
    a few words of DOB/birth/born), never a future date, never older than
    MAX_YEARS."""
    found, prev = [], 0
    for match in _DATE.finditer(text):
        # the words just before THIS date, never past the previous date
        lead, prev = text[max(prev, match.start() - 16) : match.start()], match.end()
        if _BIRTH.search(lead):
            continue
        m, d, y = match.groups()
        year = int(y) + (2000 if len(y) == 2 else 0)
        try:
            when = date(year, int(m), int(d))
        except ValueError:
            continue
        if when <= today and year >= today.year - MAX_YEARS:
            found.append(when)
    return found


def _back(when: date, years: int) -> date:
    try:
        return when.replace(year=when.year - years)
    except ValueError:
        return when.replace(year=when.year - years, day=28)


_RANK = {"blocks": 0, "strengthens": 1, "housekeeping": 2}


def merge_same_custodian(facilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two audit providers that resolve to one directory location are one
    facility on the order: the union of their record types, the wider range,
    and both names kept so the reply can say they were merged."""
    out: dict[str, dict[str, Any]] = {}
    for f in facilities:
        have = out.get(f["custodian_id"])
        if have is None:
            out[f["custodian_id"]] = {**f, "providers": list(f.get("providers") or [])}
            continue
        have["providers"] = have["providers"] + list(f.get("providers") or [])
        have["rank"] = min(have.get("rank", 3), f.get("rank", 3))
        have["record_types"] = list(dict.fromkeys(have["record_types"] + f["record_types"]))
        have["service_start"] = min(have["service_start"], f["service_start"])
        have["service_end"] = max(have["service_end"], f["service_end"])
    return list(out.values())


def first_date(rows: list[dict[str, str]], today: date) -> date | None:
    """The earliest date any ORDERABLE, non-look-back row names: the start of
    the treatment the file documents. Pass only orderable rows: a payer's or a
    timeline row's dates are not treatment (a look-back row reaches before it)."""
    found = [
        d
        for r in rows
        if not re.search(r"prior|look-?back", " ".join((r["missing"], r["request"])), re.I)
        for d in _dates(" ".join((r["missing"], r["where"])), today)
    ]
    return min(found) if found else None


def group(rows: list[dict[str, str]], today: date, file_start: date | None = None) -> list[dict[str, Any]]:
    """One entry per provider: its facility name, items, record types and range.
    A provider whose rows name no date orders from ``file_start``."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = facility_name(r["provider"])
        g = groups.setdefault(
            _key(name), {"name": name, "items": [], "types": [], "dates": [], "lookback": 0, "rows": []}
        )
        g["items"].append(int(r["item"]))
        g["rows"].append(r)
        text = " ".join((r["missing"], r["request"]))
        g["types"] = list(dict.fromkeys(g["types"] + types_for(text)))
        g["dates"] += _dates(" ".join((r["missing"], r["where"], r["request"])), today)
        if re.search(r"prior|look-?back", text, re.I):
            m = _YEARS.search(text)
            g["lookback"] = max(g["lookback"], min(int(m.group(1)), MAX_YEARS) if m else 0)
    out = []
    for g in groups.values():
        dates = sorted(g.pop("dates"))
        years = g.pop("lookback")
        if years and (file_start or dates):
            # a look-back reaches N years before the treatment the file documents,
            # the firm's brief's own measure, not N years before an old visit
            anchor = file_start or dates[0]
            start = min(_back(anchor, years), *(dates or [anchor]))
            g["service_start"], g["basis"] = start.isoformat(), f"{years}-year look-back from {anchor.isoformat()}"
        elif dates:
            g["service_start"], g["basis"] = dates[0].isoformat(), "the earliest date its rows name"
        elif file_start is not None:
            g["service_start"] = file_start.isoformat()
            g["basis"] = "its rows name no date; from the first treatment date the audit names"
        else:
            g["service_start"], g["basis"] = None, "its rows name no date"
        floor = _back(today, MAX_YEARS).isoformat()
        if g["service_start"] is not None and g["service_start"] < floor:
            g["service_start"], g["basis"] = floor, g["basis"] + f"; held to {MAX_YEARS} years back"
        g["service_end"] = today.isoformat()
        out.append(g)
    return out


# ---- 4. resolved against the Medicals tab, then the vendor's directory --------------------------
def medicals_contacts(client: Any, matter_id: str) -> list[dict[str, str]]:
    """Name, street and ZIP of each provider contact on the client's Medicals
    tab. Empty (never a guess) when the tab cannot be read or is not hers alone."""
    from .form_letter_facts import contact_name
    from .medicals_tools import _pi_items
    from .sr1_form import _address_parts

    try:
        tabs = _pi_items(client, matter_id)
        mine = [t for t in tabs if t.get("parentIndex") in (0, "0", None)] or (tabs if len(tabs) == 1 else [])
        if len(mine) != 1:
            return []
        resp = client.get(f"/matters/{matter_id}/layouts/{mine[0]['id']}/contacts")
        items = resp.get("value") if isinstance(resp, dict) else resp
    except Exception:  # noqa: BLE001 - an unreadable tab only means the directory search runs without its hints
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        contact = it.get("contact") if isinstance(it, dict) else None
        if not isinstance(contact, dict) or "Providers[" not in str(it.get("key")):
            continue
        cid = contact.get("id")
        try:
            full = client.get(f"/contacts/{cid}") if cid else contact
        except Exception:  # noqa: BLE001 - one unreadable contact leaves that provider without hints
            full = contact
        parts = _address_parts(
            {"company": (full or {}).get("company") or {}, "person": (full or {}).get("person") or {}}
        )
        name = contact_name(full or {}) or ""
        if name:
            out.append(
                {
                    "name": name,
                    "street": parts.get("street", ""),
                    "city": parts.get("city", ""),
                    "zip": parts.get("zip", "")[:5],
                }
            )
    return out


def client_home(client: Any, matter_id: str) -> tuple[str, ...]:
    """The client's own ZIP and city: on every record about her, so never
    evidence of where a provider is. Empty when the client cannot be read."""
    from .records_patient import read_matter_and_patient

    try:
        _matter, facts = read_matter_and_patient(client, matter_id)
    except Exception:  # noqa: BLE001 - no client read means nothing to exclude, not a refusal
        return ()
    return tuple(v for v in (str(facts.address.get("zip") or "")[:5], str(facts.address.get("city") or "")) if v)


def _hint(name: str, contacts: list[dict[str, str]]) -> dict[str, str] | None:
    """The Medicals-tab contact that is this provider: every distinctive word
    of the name in the contact's (generic words like "hospital" never count),
    and exactly one such contact."""
    hits = [c for c in contacts if gap_locate.names_match(name, c["name"])]
    return hits[0] if len(hits) == 1 else None


def _search_terms(name: str) -> list[str]:
    """The name, the name without department or time words ("prior", "labs",
    "ED physicians"), and its first three words."""
    name = re.sub(r"\s*#\s*\d+", "", name).strip()
    bare = " ".join(w for w in name.split() if w.casefold() not in gap_locate.QUALIFIERS)
    terms = [name, bare]
    if len(bare.split()) > 3:
        terms.append(" ".join(bare.split()[:3]))
    return [t for t in dict.fromkeys(terms) if t]


def one_question(questions: list[dict[str, Any]]) -> str | None:
    """Everything still unsettled as ONE short question, never a checklist."""
    if not questions:
        return None
    unknown = [q["facility"] for q in questions if not q.get("candidates")]
    several = [q for q in questions if q.get("candidates")]
    parts = []
    if unknown:
        names = ", ".join(unknown[:-1]) + (" and " if len(unknown) > 1 else "") + unknown[-1]
        verb = "is" if len(unknown) == 1 else "are"
        parts.append(
            f"{names} {verb} not in the vendor's directory at any address the file gives (send the address and "
            "it is ordered there)"
        )
    for q in several[:4]:
        opts = " or ".join(f"{c['name']}, {c['address']}" for c in q["candidates"][:3])
        parts.append(f"for {q['facility']}, {opts}?")
    if len(several) > 4:
        parts.append(f"and {len(several) - 4} more with several locations")
    return "One thing before these are ordered: " + "; ".join(parts)


# ---- 5. the orders ---------------------------------------------------------------------------------
NEXT_STEP = (
    "Pass the ready order to place_records_order unchanged (its one [act] line goes in the "
    "reply) and ask `question` as written, once. Say each `merged` line. Name `after_this_order` as what is "
    "ordered next, once this one is placed. On missing_client_facts, say what the client contact lacks and "
    "list would_order so she sees what will be ordered once it is fixed. Nothing has been ordered."
)


def split_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(orderable rows, the rest with why)."""
    kept, skipped = [], []
    for r in rows:
        why = classify(r)
        if why:
            skipped.append({"item": int(r["item"]), "provider": r["provider"], "why": why})
        else:
            kept.append(r)
    return kept, skipped


def _question(p: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    if p["service_start"] is None:
        return {"facility": p["name"], "items": p["items"], "reason": "the audit's rows name no date to order from"}
    return {
        "facility": p["name"],
        "items": p["items"],
        "reason": "several locations in the vendor's directory match"
        if res["outcome"] == "ambiguous"
        else "no location in the vendor's directory matches at an address the file gives",
        "candidates": res["candidates"],
    }


def _facility(p: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": min(_RANK.get(r["priority"].split()[0].casefold(), 3) for r in p["rows"]),
        "providers": [p["name"]],
        # the search that found it, plus the id: prepare re-reads the
        # directory and keeps exactly this location
        "name": res["term"],
        **({"zip": res["zip"]} if res["zip"] else {}),
        "custodian_id": res["location"]["custodian_id"],
        "record_types": p["types"],
        "service_start": p["service_start"],
        "service_end": p["service_end"],
    }


def resolve_providers(
    client: Any, yc: Any, matter_id: str, providers: list[dict[str, Any]], log: Callable[[str], None]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each provider located (records first) into a facility, or a question.
    Sets each provider's outcome, evidence flag and, when matched, location."""
    from .library import list_matter_files

    hints = medicals_contacts(client, matter_id)
    home = client_home(client, matter_id)
    files = list_matter_files(client, matter_id)
    facilities, questions = [], []
    for p in providers:
        context = " ".join(" ".join((r["provider"], r["missing"], r["request"])) for r in p["rows"])
        res = gap_locate.resolve(
            yc,
            p["name"],
            _search_terms(p["name"]),
            _hint(p["name"], hints),
            lambda p=p: gap_locate.evidence(client, matter_id, files, p["rows"], p["name"], exclude=home),
            context,
            log,
        )
        p["outcome"], p["evidence"] = res["outcome"], res.get("evidence", False)
        if p["service_start"] is None or res["outcome"] != "matched":
            questions.append(_question(p, res))
            continue
        p["location"] = res["location"]
        facilities.append(_facility(p, res))
    return facilities, questions


def one_order(
    facilities: list[dict[str, Any]], providers: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """(the order's facilities, the merged lines, the providers left for the
    next order). ONE order, so ONE [act] line and one yes per matter: the
    facilities whose rows block the demand first; any beyond the limit wait."""
    facilities = merge_same_custodian(facilities)
    name_of = {p["location"]["custodian_id"]: p["location"]["name"] for p in providers if p.get("location")}
    merged = [
        f"{' and '.join(f['providers'])} are one location at the vendor ({name_of[f['custodian_id']]}); ordered once"
        for f in facilities
        if len(f["providers"]) > 1
    ]
    facilities.sort(key=lambda f: f["rank"])
    later = [f["providers"][0] for f in facilities[MAX_LOCATIONS:]]
    kept = [{k: v for k, v in f.items() if k not in ("rank", "providers")} for f in facilities[:MAX_LOCATIONS]]
    return kept, merged, later


def _would_order(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "custodian": p["location"]["name"],
            "address": p["location"]["address"],
            "items": p["items"],
            "record_types": p["types"],
            "service_start": p["service_start"],
            "service_end": p["service_end"],
        }
        for p in providers
        if p.get("location")
    ]


def build(
    client: Any,
    yc: Any,
    matter_id: str,
    order_by_email: str,
    today: date,
    vendor_name: str,
    log: Callable[[str], None] = lambda m: None,
) -> dict[str, Any]:
    meta, blob = latest_audit(client, matter_id)
    if blob is None:
        return {"status": "refused", "reason": "No missing-records list is filed on this matter yet. Nothing was ordered."}
    kept, skipped = split_rows(audit_rows(blob))
    providers = group(kept, today, first_date(kept, today))
    facilities, questions = resolve_providers(client, yc, matter_id, providers, log)
    facilities, merged, later = one_order(facilities, providers)
    orders = []
    if facilities:
        request = {
            "matter_id": matter_id,
            "facilities": facilities,
            "order_by_email": order_by_email,
            "vendor_name": vendor_name,
        }
        orders.append(prepare(client, yc, request, today))
    shown = ("name", "items", "types", "service_start", "service_end", "basis", "outcome", "evidence")
    return {
        "status": "ready" if orders and all(o.get("status") == "ready" for o in orders) else "needs_choice",
        "gap_audit": (meta or {}).get("name"),
        "orderable_rows": len(kept),
        "rows_not_orderable": skipped,
        "providers": [{k: p.get(k) for k in shown} for p in providers],
        "counts": {o: sum(1 for p in providers if p["outcome"] == o) for o in ("matched", "ambiguous", "no_match")},
        "would_order": _would_order(providers),
        "merged": merged,
        "after_this_order": later,
        "orders": orders,
        "questions": questions,
        "question": one_question(questions),
        "next_step": NEXT_STEP,
    }
