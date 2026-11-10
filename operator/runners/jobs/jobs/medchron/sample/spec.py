"""Load a case spec and expand it into the page plan.

The spec is AUTHORED: providers, documents (each a list of pages), encounters
(each paragraph naming the page that proves it and a verbatim phrase from that
page), and treatment series (the serial visits a therapy course produces, too
many to author one by one). Expansion is deterministic: a template page draws
from a generator keyed by the matter seed and the document, so the same spec
always gives the same pages in the same order.

Expansion REFUSES a paragraph whose evidence phrase is not printed on the page
it cites. That is the property the whole set exists to have, so it is checked
where the set is made, not only in a test.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from . import answer_key, pages as clinical, pages_billing as billing, series as series_mod
from .model import Document, Encounter, Page, Para, Plan, Provider, norm, rng

TEMPLATES = {**clinical.TEMPLATES, **billing.TEMPLATES}


class SpecError(ValueError):
    pass


def load(path: Path) -> Plan:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return expand(raw)


def expand(raw: dict[str, Any]) -> Plan:
    matter, patient = dict(raw["matter"]), dict(raw["patient"])
    providers = {k: _provider(k, v) for k, v in (raw.get("providers") or {}).items()}
    series = {k: dict(v, id=k) for k, v in (raw.get("series") or {}).items()}
    labels: dict[str, tuple[str, int]] = {}
    docs: list[Document] = []
    encounters: list[Encounter] = []
    for spec in raw.get("documents") or []:
        doc = _document(spec, matter, patient, providers, series, encounters)
        docs.append(doc)
    for spec in raw.get("resends") or []:
        docs.append(_resend(spec, docs))
    for seq, doc in enumerate(docs, 1):
        doc.seq = seq
        _number(doc, matter, labels)
    plan = Plan(
        matter=matter,
        patient=patient,
        providers=providers,
        documents=docs,
        encounters=encounters,
        conflicts=[str(c) for c in raw.get("conflicts") or []],
        icd={str(k): str(v) for k, v in (raw.get("icd") or {}).items()},
        defects=dict(raw.get("defects") or {}),
        headings=[str(h) for h in raw["headings"]],
    )
    for enc in raw.get("encounters") or []:
        plan.encounters.append(_encounter(enc, labels))
    plan.encounters.sort(key=lambda e: (e.date, e.provider))
    _resolve_series_paras(plan, labels)
    _check_evidence(plan)
    return plan


def _provider(key: str, v: dict[str, Any]) -> Provider:
    return Provider(
        key=key,
        facility=str(v["facility"]),
        address=str(v.get("address", "")),
        phone=str(v.get("phone", "")),
        fax=str(v.get("fax", "")),
        match=str(v.get("match", "")),
        custodian=str(v.get("custodian", "Custodian of Records")),
        bill_name=str(v.get("bill_name") or v["facility"]),
        style=dict(v.get("style") or {}),
    )


def _document(
    spec: dict[str, Any],
    matter: dict[str, Any],
    patient: dict[str, Any],
    providers: dict[str, Provider],
    series: dict[str, dict[str, Any]],
    encounters: list[Encounter],
) -> Document:
    doc_id = str(spec["id"])
    cx = clinical.Ctx(doc_id, providers[str(spec["provider"])], patient, matter, rng(matter["seed"], doc_id))
    out: list[Page] = []
    for p in spec.get("pages") or []:
        out += _pages(dict(p), cx, series, encounters)
    return Document(doc_id, str(spec["name"]), str(spec["provider"]), out, fax=spec.get("fax"))


def _pages(
    p: dict[str, Any], cx: clinical.Ctx, series: dict[str, dict[str, Any]], encounters: list[Encounter]
) -> list[Page]:
    kind = str(p.get("kind", "note"))
    if kind == "note":
        return [_note(p, cx)]
    if kind == "series_notes":
        return series_mod.expand(series[str(p["series"])], p, cx, encounters)
    if kind == "series_claims":
        return billing.cms1500(dict(p, claims=series_mod.claims(series[str(p["series"])], p)), cx)
    if kind not in TEMPLATES:
        raise SpecError(f"{cx.doc_id}: unknown page kind {kind!r}")
    made = TEMPLATES[kind](p, cx)
    if p.get("id") and made:
        made[0].pid = str(p["id"])
    return made


def _note(p: dict[str, Any], cx: clinical.Ctx) -> Page:
    body = str(p.get("body", "")).rstrip("\n").split("\n")
    hand = [i for i, line in enumerate(body) if line.startswith("~")]
    body = [line[1:] if line.startswith("~") else line for line in body]
    return Page(
        cx.doc_id,
        "note",
        str(p.get("title", "")),
        body,
        pid=str(p.get("id", "")),
        date=str(p.get("date") or ""),
        scan=p.get("scan"),
        hand=hand,
        illegible=bool(p.get("illegible")),
        other_patient=str(p.get("other_patient", "")),
    )


def _resend(spec: dict[str, Any], docs: list[Document]) -> Document:
    """A re-sent production: a fax cover, then copies of pages of an earlier
    document. Every copied page is a scan of a page the set already holds."""
    src = next(d for d in docs if d.id == spec["of"])
    lo, hi = (int(x) for x in spec["pages"])
    copies = []
    for pg in src.pages[lo - 1 : hi]:
        c = copy.deepcopy(pg)
        c.doc_id, c.pid, c.scan, c.bill = str(spec["id"]), "", True, None
        copies.append(c)
    cover = copy.deepcopy(src.pages[0]) if src.pages and src.pages[0].kind == "fax_cover" else None
    head = []
    if cover is not None:
        cover.doc_id, cover.pid = str(spec["id"]), ""
        cover.lines = [
            line.replace("Medical records per your request", "RE-SENT at your request") for line in cover.lines
        ]
        head = [cover]
    return Document(
        str(spec["id"]),
        str(spec["name"]),
        src.provider,
        head + copies,
        fax=spec.get("fax") or src.fax,
        resend_of=src.id,
    )


def _number(doc: Document, matter: dict[str, Any], labels: dict[str, tuple[str, int]]) -> None:
    gen = rng(matter["seed"], doc.id, "scan")
    rate = float(matter.get("scan_rate", 0.7))
    total = len(doc.pages)
    for n, pg in enumerate(doc.pages, 1):
        pg.number = n
        pg.lines = [line.replace("{PAGES}", str(total)) for line in pg.lines]
        roll = gen.random()
        if pg.scan is None:
            pg.scan = bool(pg.hand or pg.illegible or roll < rate)
        if pg.bill is not None:
            _stamp(pg.bill, n)
        if pg.pid:
            if pg.pid in labels:
                raise SpecError(f"page id {pg.pid!r} is used twice")
            labels[pg.pid] = (doc.id, n)


def _stamp(obj: Any, n: int) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "page":
                obj[k] = n
            else:
                _stamp(v, n)
    elif isinstance(obj, list):
        for v in obj:
            _stamp(v, n)


def _encounter(e: dict[str, Any], labels: dict[str, tuple[str, int]]) -> Encounter:
    paras = []
    for heading, ref, evidence, text in e["paras"]:
        if str(ref) not in labels:
            raise SpecError(f"{e['date']}: paragraph cites unknown page id {ref!r}")
        doc, n = labels[str(ref)]
        paras.append(Para(str(heading), str(text).strip(), doc, n, str(evidence)))
    return Encounter(str(e["date"]), str(e["provider"]), str(e["signer"]), [str(c) for c in e.get("icd") or []], paras)


def _resolve_series_paras(plan: Plan, labels: dict[str, tuple[str, int]]) -> None:
    """Series encounters were built before numbering; their paragraphs carry
    a page label to resolve now."""
    for enc in plan.encounters:
        fixed = []
        for para in enc.paras:
            if para.page == 0:
                doc, n = labels[para.doc]
                para = Para(para.heading, para.text, doc, n, para.evidence)
            fixed.append(para)
        enc.paras = fixed


def _check_evidence(plan: Plan) -> None:
    for enc in plan.encounters:
        for para in enc.paras:
            doc = plan.doc(para.doc)
            text = norm(answer_key.transcription(plan, doc, doc.pages[para.page - 1]))
            if norm(para.evidence) not in text:
                raise SpecError(
                    f"{enc.date} {enc.signer}: evidence {para.evidence!r} is not printed on {doc.name} p. {para.page}"
                )
