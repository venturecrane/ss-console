"""Offline-run fixes 18, 24, 25 and 27 of the update lane: each test fails on
the code before the fix (f534d279) and passes after. Every value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
from medchron.audit import claims as CL
from medchron.update import assemble as A, body as B, collect as C, providers as P, proofs as PR


def _ent(provider: str, text: str, d: str = "02/10/2026", cite: str = "(Exhibit 2 - p. 1)") -> str:
    return f"{d}\n{provider} | Medical Diagnoses\n\nMedical Diagnoses\n{text} {cite}"


def _assemble(base: str, audit: str, nex: list[dict], **kw) -> tuple[str, dict]:
    return A.assemble(
        base,
        audit,
        describe=lambda c, o, t: (c, None, "x"),
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
        **kw,
    )


# ---- fix 18: the delivered spelling lives in entry text and titles ------------------------
_SEEN = "Marcus Angeli, M.D. examined the neck and recorded pain rated 6 of 10 at the visit."
_BASE_POST = _ent("Example Clinic", _SEEN, "01/20/2026", "(Exhibit 1 - p. 2)")
_NEW = _ent(
    "Marcus Angell, M.D.",
    "Marcus Angell, M.D. reviewed cervical radiographs; Dr. Angell recommended six weeks of therapy.",
)


def test_a_name_spelled_only_in_delivered_text_or_titles_is_audited_and_respelled_everywhere() -> None:
    base = K.base_md(entries=(K.ENTRY_PRE, _BASE_POST))
    _, base_ents = B.entries_of(base)
    # The delivered provider lines say only "Example Clinic": the name is in the text.
    _, log = P.snap([_NEW], base_ents)
    assert [r["likely_same_person"] for r in log] == [["Marcus Angeli, M.D."]]
    # A title alone carries the spelling, too.
    _, by_title = P.snap([_NEW], [], ["Marcus Angeli, M.D. - 01-02-2026 (Medical Records)"])
    assert [r["likely_same_person"] for r in by_title] == [["Marcus Angeli, M.D."]]

    lines, composed = P.claims([_NEW], log)
    assert lines == ["Provider named on the record: Marcus Angell, M.D.. (Exhibit 2 - p. 1)"]
    audited = [lines[0].replace("Angell,", "Angeli,")]  # the page image shows the delivered spelling
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1}]
    audit_doc = C.audit_document(base, [_NEW], nex, icd_claims=audited)
    md, rep = _assemble(base, audit_doc, nex, provider_names=composed)
    assert "Angell" not in md  # provider line, text, "Dr." short form and the exhibit title
    assert "Dr. Angeli recommended" in md
    assert rep["new_exhibits_titled"]["2"].startswith("Marcus Angeli, M.D. - ")

    # The respelled sentence keeps the verdict the audit gave it (proof 4).
    keep = {1, 2}
    audit_claims = CL.extract_claims(CL.body_of(audit_doc), keep)
    base_keys = {c["key"] for c in CL.extract_claims(CL.body_of(base), keep)}
    new = [c for c in CL.extract_claims(CL.body_of(md), keep) if c["key"] not in base_keys]
    x = SimpleNamespace(respelled=rep["provider_spelling_from_audit"], audit_claims=audit_claims)
    audit_keys = {c["key"] for c in audit_claims}
    assert new and all(PR._as_audited(c, x) in audit_keys for c in new)


def test_two_clinicians_the_page_shows_distinct_stay_distinct() -> None:
    base = K.base_md(
        entries=(
            K.ENTRY_PRE,
            _ent("Example Clinic", "Daniel Peterson, M.D. examined the neck.", "01/20/2026", "(Exhibit 1 - p. 2)"),
        )
    )
    _, base_ents = B.entries_of(base)
    new = _ent("Daniel Pederson, M.D.", "Daniel Pederson, M.D. reviewed the radiographs at the visit.")
    _, log = P.snap([new], base_ents)
    lines, composed = P.claims([new], log)
    assert lines  # audited, because the name is near a delivered one
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1}]
    # The audit confirms the record's own spelling: nothing is respelled.
    md, rep = _assemble(base, C.audit_document(base, [new], nex, icd_claims=lines), nex, provider_names=composed)
    assert "Daniel Pederson, M.D. reviewed" in md and "Daniel Peterson, M.D. examined" in md
    assert rep["provider_spelling_from_audit"] == {}


def test_a_given_name_misread_inside_a_facility_lane_is_respelled_in_lane_text_and_title() -> None:
    """The shape the offline run shipped: the clinician written surname first
    inside a facility's provider line, the delivered spelling only in the
    delivered entries' text."""
    seen = "An appointment was made with Okafor, Lucienne Marisol, DO for a recheck."
    base = K.base_md(entries=(K.ENTRY_PRE, _ent("Example Clinic", seen, "01/20/2026", "(Exhibit 1 - p. 2)")))
    _, base_ents = B.entries_of(base)
    lane = "Example Internal Medicine, Springfield (Okafor, Luciene Marisol, DO)"
    new = _ent(lane, "A result note by Luciene Marisol Okafor, DO acknowledged the cervical radiographs.")
    _, log = P.snap([new], base_ents)
    lines, composed = P.claims([new], log)
    assert len(lines) == 1 and "Luciene" in lines[0] and lines[0].endswith(", DO. (Exhibit 2 - p. 1)")
    audited = [lines[0].replace("Luciene", "Lucienne")]
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1}]
    md, rep = _assemble(base, C.audit_document(base, [new], nex, icd_claims=audited), nex, provider_names=composed)
    assert "Luciene " not in md
    assert "A result note by Lucienne Marisol Okafor, DO acknowledged" in md
    title = rep["new_exhibits_titled"]["2"]
    assert title.startswith("Example Internal Medicine, Springfield (Okafor, Lucienne Marisol, DO)")


# ---- fix 24: exhibit ranges and visits come from dates of service only --------------------
def test_a_records_print_date_is_kept_but_never_a_visit_or_an_exhibit_date() -> None:
    visit = _ent("Example Imaging", "Cervical radiographs showed straightening of the lordosis.")
    printed = (
        "03/12/2026 (record print date)\nExample Records Release | All Other Information\n\n"
        "All Other Information\nThe immunization roster printed 03/12/2026 lists a tetanus booster. (Exhibit 2 - p. 3)"
    )
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 3}]
    base = K.base_md()
    md, rep = _assemble(base, C.audit_document(base, [visit, printed], nex), nex)
    assert rep["new_exhibits_titled"]["2"] == "Example Imaging - 02-10-2026 (Medical Records)"
    timeline = B.table_rows(B.sections(md)["## Treatment Timeline"])
    assert "| Example Imaging | 02/10/2026 | 1 | Exhibit 2 - p. 1 |" in timeline
    assert not [r for r in timeline if "03/12/2026" in r or "Records Release" in r]
    assert printed in md  # kept, never dropped
    # An exhibit of records-dated entries only carries no dates at all.
    _, rep2 = _assemble(base, C.audit_document(base, [printed], nex), nex)
    assert rep2["new_exhibits_titled"]["2"] == "Example Records Release (Medical Records)"


def test_a_date_only_a_print_line_states_is_marked_a_records_date_from_the_page() -> None:
    from medchron.update import records_dates as RD

    stamped = _ent("Example Records Release", "The roster lists a tetanus booster given in 2019.", "03/12/2026")
    seen = _ent("Example Imaging", "Cervical radiographs showed straightening.", "03/12/2026", "(Exhibit 2 - p. 2)")
    pages = {
        2: {
            1: "Example Health Records Release\nPrinted 3/12/26 09:14 by ROI\nTetanus booster 05/01/2019",
            2: "Visit date 03/12/2026\nCervical radiographs. Faxed 03/12/2026",
        }
    }
    out, log = RD.mark([stamped, seen], pages)
    assert out[0].startswith("03/12/2026 (records date, not a date of service)\nExample Records Release")
    assert not B.is_service(out[0]) and out[1] == seen and len(log) == 1
    # A date the cited pages never state is left as composed.
    assert RD.mark([stamped], {2: {1: "No dates here"}})[0] == [stamped]


# ---- fix 25: Records Reviewed counts every document filed and names every copy ------------
def test_records_reviewed_counts_every_filed_document_and_names_each_copy(tmp_path: Path) -> None:
    from medchron.stages.update_finish import update_note

    ud = tmp_path / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)

    def row(i: str, **kw) -> dict:
        return {"id": i, "name": f"{i} records", "ext": ".pdf", "ok": True, "sha256": f"sha-{i}", **kw}

    raw = [
        row("k1"),
        row("u1"),
        row("dupA", duplicate_of="k1", sha256="sha-k1"),
        row("dupB", sha256="sha-delivered-1"),
        row("sub1"),
        row("photo", ext=".heic"),
        row("lost"),
        row("gone", ok=False),
    ]
    (tmp_path / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in raw))
    keep = [{"id": "k1", "exhibit": 2, "pages": 3, "read": [1, 2, 3], "excluded": {}, "source_name": "k1 records.pdf"}]
    unc = [{"id": "u1", "exhibit": 4, "pages": 1, "read": [1], "excluded": {}, "source_name": "u1 records.pdf"}]
    sel = {
        "skipped": {"byte-identical to a delivered exhibit": ["dupB"], "not a PDF or an image": ["photo"]},
        "subsets": [
            {
                "id": "sub1",
                "source_name": "sub1 records.pdf",
                "pages": 2,
                "matched": 2,
                "subset_of": {"id": "k1", "exhibit": 3, "title": "Exhibit 3 - k1 records (Medical Records)"},
            }
        ],
    }
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep))
    (ud / "new_exhibits.uncited.json").write_text(json.dumps(unc))
    (ud / "selection.json").write_text(json.dumps(sel))
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": [{"exhibit": 1, "sha256": "sha-delivered-1"}]}))
    text = update_note(SimpleNamespace(), ud)({2: "Example Imaging - 02-10-2026 (Medical Records)"})  # type: ignore[arg-type]
    assert "7 medical record document(s) filed on the matter after the earlier delivery were reviewed" in text
    assert "dupA records.pdf (duplicate copy of Exhibit 2)" in text
    assert "dupB records.pdf (duplicate copy of Exhibit 1)" in text
    assert "sub1 records.pdf (re-filed pages of Exhibit 2)" in text  # the subset's exhibit as finally numbered
    assert "photo records.heic" in text and "lost records.pdf" in text
    assert "gone records" not in text


def test_records_reviewed_says_after_the_incident_only_when_only_later_visits_were_added(tmp_path: Path) -> None:
    from medchron.stages.update_finish import update_note

    ud = tmp_path / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1, "read": [1], "excluded": {}}]
    post = _ent("Example Imaging", "Cervical radiographs showed straightening.")  # 02/10/2026, after the incident
    (ud.parent / "final-chronology.md").write_text(C.audit_document(K.base_md(), [post], nex))
    (ud / "new_exhibits.final.json").write_text(json.dumps(nex))
    sr = SimpleNamespace(job=SimpleNamespace(incident_date=K.INCIDENT))
    text = update_note(sr, ud)({2: "Example Imaging - 02-10-2026 (Medical Records)"})  # type: ignore[arg-type]
    assert "were added, after the incident, and they are cited" in text and "before" not in text
    pre = _ent("Example Imaging", "Cervical radiographs before the incident.", "11/03/2025")
    (ud.parent / "final-chronology.md").write_text(C.audit_document(K.base_md(), [pre, post], nex))
    text = update_note(sr, ud)({2: "Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records)"})  # type: ignore[arg-type]
    assert "were added, before and after the incident, and" in text


# ---- fix 27: delivered timeline lanes stay as delivered; credentials are kept ---------------
def test_the_grouping_keeps_nurse_and_trailing_credentials() -> None:
    from medchron.stages.group import Canon

    c = Canon(SimpleNamespace(get=lambda *a, **k: None))
    assert c("Jane A. Roe, M.D., Ph.D.") == "Jane A. Roe, M.D., Ph.D."  # never "Ph.D." alone
    assert c("Jane Roe, MSN, RN") == "Jane Roe, MSN, RN"
    kept = "Example Home Training (PD), RD and Aaron T. Young, MD, Example Unit (PD)"
    assert c(kept) == kept  # a credential after a kept segment stays
    assert c("Example Clinic, Jane Roe, MD") == "Example Clinic"  # a clinician variant still collapses


def test_a_delivered_timeline_row_is_never_relabelled_or_absorbed() -> None:
    eye = _ent(
        "Example Health, Example Eye Institute, Yin Lee, MD",
        "Dilated exam was normal.",
        "12/05/2025",
        "(Exhibit 1 - p. 1)",
    )
    nurse = _ent(
        "Example On-Call, Mary Poe, OCN, RN, EXTCARE, ATLAS",
        "Phone triage for dizziness.",
        "12/10/2025",
        "(Exhibit 1 - p. 2)",
    )
    hosp = _ent("Example Health Medical Center", "Labs were drawn.", "12/07/2025", "(Exhibit 1 - p. 3)")
    timeline = (
        "| Medical Provider | Treatment Period | Visits | Reference |\n"
        "| Example Health, Example Eye Institute | 12/05/2025 | 1 | Exhibit 1 - p. 1 |\n"
        "| Example Medical Center | 12/07/2025 | 1 | Exhibit 1 - p. 3 |\n"
        "| Example On-Call, Mary Poe, RN, EXTCARE | 12/10/2025 | 1 | Exhibit 1 - p. 2 |\n"
    )
    base = K.base_md(entries=(eye, hosp, nurse))
    head, rest = base.split("## Treatment Timeline\n", 1)
    base = (
        head
        + "## Treatment Timeline\n\n"
        + timeline
        + "\n## Diagnostic Highlights"
        + rest.split("## Diagnostic Highlights", 1)[1]
    )

    def canon(s: str) -> str:  # a firm alias that now brands every "Example Health" record one system
        return "Example Medical Center" if "Example Health" in s else s.split(",")[0]

    new = _ent("Example Health, Example Eye Institute, Yin Lee, MD", "Follow-up exam was normal.", "02/10/2026")
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1}]
    md, rep = A.assemble(
        base,
        C.audit_document(base, [new], nex),
        describe=lambda c, o, t: (c, None, "x"),
        canon=canon,
        headings=K.HEADINGS,
        new_exhibits=nex,
    )
    rows = B.table_rows(B.sections(md)["## Treatment Timeline"])
    assert "| Example Health, Example Eye Institute | 12/05/2025 - 02/10/2026 | 2 | Exhibit 1 - p. 1 |" in rows
    assert "| Example Medical Center | 12/07/2025 | 1 | Exhibit 1 - p. 3 |" in rows
    assert "| Example On-Call, Mary Poe, RN, EXTCARE | 12/10/2025 | 1 | Exhibit 1 - p. 2 |" in rows
    assert len(rows) == 4 and rep["delivered_table_vs_its_own_entries"] == []
