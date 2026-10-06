"""The police report request on the firm's own letters (2026-10-06).

WHAT THE FIRM DOES. A&P asks the agency that took the report: the Highway
Patrol by its "Police Rept Req. North Sac CHP" letter (crash time, NCIC
number and officer's ID number, as printed on the officer's crash card), a
city police department by its "Police Rept Req. Sac PD" letter (report
number), faxed, emailed, mailed or attached in the agency's records portal.
Smokeball holds no agency field the firm uses (ReportedTo on 6 of 518
matters), so the agency comes from the sender's words or the crash card.

THE AGENCY IS VERIFIED, NEVER TRUSTED. ``form_letters.police_agencies`` in
customer.yaml lists the agencies the firm's files name, each with fixed
``match`` words, its address, and ONE current channel, all authored from the
firm's own letters and the agencies' own emails. An entry is used only when
exactly one entry's match words are in the sender's words, or (with none) in
the cited crash card's transcription. Two is refused as ambiguous; none takes
``agency_given`` (the agency as the sender wrote it, or as the card prints it,
which is then checked line by line against the card); nothing at all is
``needs_agency`` and nothing is filed.

THE REPLY IS BUILT HERE (``reply_block``): the reply renderer joins adjacent
lines, so every line is its own paragraph, and its words carry no acronym a
reply check would refuse.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from typing import Any

from . import cited_facts
from . import form_letter_facts as facts
from .form_letter_facts import Fact
from .form_letters import MARKER, FormSpec, ResolvedTemplate, _fill_and_file, _refused, _resolve_form

KINDS = {
    "city": ("police_request_city", "Form - Police Req City.docx"),
    "chp": ("police_request_chp", "Form - Police Req CHP.docx"),
}
CARD_FIELDS = {
    "city": ("report_number",),
    "chp": ("crash_time", "ncic_number", "officer_id"),
}
_MAX = 200


@dataclasses.dataclass(frozen=True)
class Agency:
    key: str
    name: str
    short: str
    reply_name: str
    kind: str
    address: tuple[str, ...]
    channel: str  # fax | email | portal | mail
    route: str  # the fax number, email address or portal; "" for mail
    source: str


def load_agencies(path: str | None = None) -> list[Agency]:
    """``form_letters.police_agencies``: the authored directory."""
    block = facts._load_yaml(path).get(facts.CONFIG_BLOCK)
    rows = block.get("police_agencies") if isinstance(block, dict) else None
    out = []
    for key, row in (rows or {}).items() if isinstance(rows, dict) else []:
        if not isinstance(row, dict) or row.get("kind") not in KINDS:
            continue
        out.append(
            Agency(
                key=str(key),
                name=str(row.get("name") or key),
                short=str(row.get("short") or row.get("name") or key),
                reply_name=str(row.get("reply_name") or row.get("name") or key),
                kind=str(row["kind"]),
                address=tuple(str(x).strip() for x in row.get("address") or [] if str(x).strip()),
                channel=str(row.get("channel") or "mail"),
                route=str(row.get("route") or ""),
                source="the firm's agency list",
            )
        )
    return out


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("’", "'")).split())


def _matches(agencies: list[Agency], match: dict[str, list[str]], text: str) -> list[Agency]:
    hay = f" {_words(text)} "
    return [a for a in agencies if any(f" {_words(m)} " in hay for m in match.get(a.key, []))]


def _load_match(path: str | None = None) -> dict[str, list[str]]:
    block = facts._load_yaml(path).get(facts.CONFIG_BLOCK)
    rows = block.get("police_agencies") if isinstance(block, dict) else None
    return {str(k): [str(m) for m in (v.get("match") or [])] for k, v in (rows or {}).items() if isinstance(v, dict)}


def _card_text(client: Any, matter_id: str, cited: Any) -> str:
    ids = {str(v.get("file_id")) for v in (cited or {}).values() if isinstance(v, dict) and v.get("file_id")}
    if len(ids) != 1:
        return ""
    return cited_facts._document_text(client, matter_id, ids.pop())[0]


def _given(given: dict[str, Any], card: str) -> Agency | str:
    name = " ".join(str(given.get("name") or "").split())
    lines = [ln.strip() for ln in str(given.get("address") or "").split("\n") if ln.strip()]
    if not name or len(name) > _MAX or any(len(ln) > _MAX for ln in lines):
        return 'agency_given must be {"name": ..., "address": "line 1\\nline 2"} with a name'
    on_card = bool(card) and all(_words(x) in _words(card) for x in [name, *lines])
    kind = "chp" if re.search(r"(?i)\bchp\b|highway patrol", name) else "city"
    short = " ".join(re.sub(r"[^A-Za-z0-9 .,&'-]", " ", name).split())[:60] or "agency"
    return Agency(
        key="",
        name=name,
        short=short,
        reply_name=name,
        kind=kind,
        address=tuple(lines),
        channel="mail",
        route="",
        source="as printed on the crash card" if on_card else "as the sender wrote it",
    )


def resolve(
    agencies: list[Agency], match: dict[str, list[str]], words: str | None, card: str, given: dict[str, Any] | None
) -> Agency | dict[str, Any]:
    """The one agency, or a status dict that files nothing."""
    hits = _matches(agencies, match, words) if words and words.strip() else _matches(agencies, match, card)
    if len(hits) == 1:
        return hits[0]
    names = [a.reply_name for a in hits or agencies]
    if len(hits) > 1:
        return {"status": "agency_unclear", "candidates": names}
    if given:
        got = _given(given, card)
        return got if isinstance(got, Agency) else _refused(got)
    return {"status": "needs_agency", "candidates": names}


def where_it_goes(agency: Agency) -> str:
    if agency.channel == "fax" and agency.route:
        return f"Fax it to {agency.reply_name} at {agency.route}."
    if agency.channel == "email" and agency.route:
        return f"Email it to {agency.reply_name} at {agency.route}."
    if agency.channel == "portal":
        where = f": {agency.route}" if agency.route else ""
        return f"Requests to {agency.reply_name} go through the records portal{where}. Attach this letter there."
    if agency.key:
        return f"Mail it to {agency.reply_name}."
    return f"No delivery route is on file for {agency.reply_name}; the letter is addressed as {agency.source}."


def _gather_for(agency: Agency, spec_kind: str):
    def gather(client: Any, matter: str, spec: FormSpec, when: date, carried: set[str], cited: Any):
        record = client.get(f"/matters/{matter}")
        record = record if isinstance(record, dict) else {}
        layout = facts.matter_layout_values(client, matter)
        preparer = facts.preparer_facts(client, record, facts.load_preparer_title())
        confirmed = cited_facts.confirm(client, matter, cited, set(CARD_FIELDS[spec_kind]) & carried)
        missing = MARKER.format(f"{agency.reply_name}'s mailing address")
        lines = list(agency.address) or [missing]
        block = agency.name if agency.channel == "portal" else "\n".join([agency.name, *lines])
        delivery = f"VIA FAX: {agency.route}" if agency.channel == "fax" and agency.route else ""
        found = {
            "date": Fact(facts.long_date(when), "the letter date"),
            "delivery_line": Fact(delivery, agency.source),
            "agency_block": Fact(block, agency.source),
            "client_name": facts.client_name(client, record),
            "client_salutation": facts.client_salutation(client, record),
            "date_of_loss": facts.date_of_loss(layout),
            "preparer_email": preparer["email"],
            "signer_name": preparer["name"],
            "signer_title": preparer["title"],
            **confirmed.facts,
        }
        if agency.channel != "portal" and not agency.address:
            found["agency_mailing_address"] = Fact(None, "", f"{agency.reply_name}'s mailing address")
        return found, confirmed

    return gather


def reply_block(out: dict[str, Any], agency: Agency, number: str) -> str:
    lines = [f"Filed: {out.get('fileName')} on matter {number}."]
    if not agency.key:
        lines.append(f"{agency.name} is not on the firm's agency list; the letter uses the agency {agency.source}.")
    lines.append(where_it_goes(agency))
    shown = out.get("card_values_to_check") or {}
    if shown:
        lines.append("Check against the crash card: " + ", ".join(f"{k} {v}" for k, v in shown.items()) + ".")
    gaps = [g for g in out.get("unfilled") or []]
    if gaps:
        lines.append("Gaps the file did not hold: " + " ".join(gaps))
    if any("report number" in g for g in gaps):
        lines.append("The letter has a blank for the report number.")
    return "\n\n".join(lines)


def render_police(
    client: Any, matter: str, when: date, cited: Any, words: str | None, given: dict[str, Any] | None
) -> dict[str, Any]:
    try:
        card = _card_text(client, matter, cited)
    except Exception:  # noqa: BLE001 - an unreadable card names no agency and confirms nothing
        card = ""
    agency = resolve(load_agencies(), _load_match(), words, card, given)
    if not isinstance(agency, Agency):
        return {**agency, "fileId": None, "matterId": matter, "reply_block": _ask(agency)}
    document_class, template = KINDS[agency.kind]
    spec = FormSpec(
        label=f"police report request ({agency.kind})",
        document_class=document_class,
        default_template=template,
        file_name=f"Police Rept Req. {agency.short}.docx",
        side="Plaintiffs",
        client_side=True,
        fax_label="",
        email_label="",
        paragraph_fields=frozenset({"agency_block"}),
        signer_name_from="preparer",
        facts_kind="police",
    )
    resolved = _resolve_form(client, spec)
    if not isinstance(resolved, ResolvedTemplate):
        return _refused(f"the firm's {spec.label} form did not resolve ({resolved.reason}); nothing was filed")
    out = _fill_and_file(client, matter, spec, when, resolved, cited, gather=_gather_for(agency, agency.kind))
    if out.get("status") in ("filed", "filed_not_visible"):
        record = client.get(f"/matters/{matter}")
        number = str(record.get("number") or matter) if isinstance(record, dict) else matter
        out["agency"] = {"name": agency.name, "source": agency.source, "channel": agency.channel}
        out["where_it_goes"] = where_it_goes(agency)
        out["reply_block"] = reply_block(out, agency, number)
    return out


def _ask(status: dict[str, Any]) -> str:
    names = ", ".join(status.get("candidates") or [])
    if status.get("status") == "agency_unclear":
        return f"Needs a word from you: that could be {names}. Which agency took the report?"
    if status.get("status") == "needs_agency":
        return f"Needs a word from you: which agency took the report? The firm's list has {names}."
    return f"Not made: {status.get('reason')}"


__all__ = ["Agency", "load_agencies", "render_police", "resolve", "where_it_goes"]
