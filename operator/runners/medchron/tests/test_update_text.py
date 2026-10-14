"""The update's text rules, on synthetic markdown: which pages are new, which
composed entries are kept, the one updated document, and the proofs (proof
10 above all: no value composed from a new record is dropped)."""

from __future__ import annotations

import pytest

import update_testkit as K
from medchron.update import assemble as A, body as B, collect as C, proofs as P, select as S
from medchron.update.values import lost, numbers

DELIVERED_PAGE = (
    "Example Clinic progress note. The patient reports neck pain after the collision and stiffness in the morning "
    "with reduced rotation to the left side. Plan is physical therapy twice weekly and a recheck in four weeks."
)
NEW_PAGE = (
    "Example Imaging report. Magnetic resonance imaging of the cervical spine shows a small disc protrusion at the "
    "fifth and sixth level without cord compression. Impression is mild degenerative change with protrusion."
)


# ---- select: what the delivery already read is read off its exhibits --------
def test_a_page_already_in_a_delivered_exhibit_is_not_read_again() -> None:
    seen = S.shingles(DELIVERED_PAGE)
    refiled = "Fax 2026-02-03 page 1 of 9\n" + DELIVERED_PAGE  # re-filed copy: a stamp, the same words
    pages = {1: refiled, 2: NEW_PAGE, 3: "", 4: "[page transcription failed]"}
    got = S.classify_new(pages, 4, seen)
    assert got[1] == (S.EXCLUDED, S.ALREADY)
    assert got[2][0] == S.READ
    assert got[3] == (S.EXCLUDED, S.BLANK)
    assert got[4][0] == S.UNREAD  # never skipped silently: the stage holds on it


def test_page_text_splits_native_and_transcribed_markers() -> None:
    text = "[p.1]\nalpha\n[p.2] (machine transcription)\nbeta\n"
    assert S.pages_of_text(text) == {1: "alpha", 2: "beta"}
    assert S.selected_text({1: "alpha", 2: "beta"}, [2]) == "[p.2]\nbeta"


# ---- collect: keep what the new documents add ---------------------------------
NEW_PRE = (
    "11/03/2025\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical radiographs showed straightening of the lordosis at 11:40. (Exhibit 2 - p. 1)"
)
NEW_POST = (
    "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level. (Exhibit 2 - p. 2)"
)
SAME_VISIT = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical strain after the collision, pain 6 of 10. (Exhibit 2 - p. 3)"
)
SAME_VISIT_NEW_VALUE = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cyclobenzaprine 7.5 mg was prescribed at the visit for spasm. (Exhibit 2 - p. 3)"
)


def test_collect_keeps_new_visits_before_and_after_the_incident_and_drops_a_repeat() -> None:
    _, base = B.entries_of(K.base_md())
    kept, rep = C.collect([NEW_POST, SAME_VISIT, NEW_PRE, SAME_VISIT_NEW_VALUE], base, K.INCIDENT, str)
    # A delivered visit restated with a new value keeps only what it adds,
    # as an "Additional findings" row (change 4).
    added = (
        "01/20/2026\nExample Clinic | Additional findings\n\nAdditional findings\n"
        "Cyclobenzaprine 7.5 mg was prescribed at the visit for spasm. (Exhibit 2 - p. 3)"
    )
    assert kept == [NEW_PRE, added, NEW_POST]  # date order
    assert rep["kept: before the incident"] == 1 and rep["kept: after the incident"] == 2
    assert rep["dropped: same visit as a delivered entry, no value or finding it lacks"] == 1


def test_collect_refuses_an_unresolved_citation() -> None:
    _, base = B.entries_of(K.base_md())
    with pytest.raises(C.CollectRefusal, match="FILE"):
        C.collect([NEW_POST.replace("(Exhibit 2 - p. 2)", "(FILE: lost.pdf, p. 2)")], base, K.INCIDENT, str)


def test_renumber_maps_assembled_numbers_to_the_update_exhibits_and_refuses_strangers() -> None:
    em = {"Exhibit 7 - Example Imaging (Medical Records).pdf": 1}
    assert C.renumber("x (Exhibit 1 - p. 4)", em) == "x (Exhibit 7 - p. 4)"
    with pytest.raises(C.CollectRefusal):
        C.renumber("x (Exhibit 1 - p. 4)", {"some-file.pdf": 1})


def test_compact_drops_an_uncited_new_exhibit_and_closes_the_gap() -> None:
    nex = [
        {"exhibit": 2, "title": "Exhibit 2 - A (Medical Records)", "pages": 1},
        {"exhibit": 3, "title": "Exhibit 3 - B (Medical Records)", "pages": 2},
    ]
    ents, keep, uncited, remap = C.compact([NEW_POST.replace("Exhibit 2", "Exhibit 3")], nex)
    assert remap == {3: 2} and [r["exhibit"] for r in keep] == [2] and keep[0]["composed_as"] == 3
    assert keep[0]["title"] == "Exhibit 2 - B (Medical Records)" and uncited[0]["composed_as"] == 2
    assert "(Exhibit 2 - p. 2)" in ents[0]


def test_the_audit_document_holds_only_the_new_entries() -> None:
    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 2}]
    doc = C.audit_document(K.base_md(), [NEW_PRE, NEW_POST], nex)
    _, ents = B.entries_of(doc)
    assert ents == [NEW_PRE, NEW_POST]
    assert K.ENTRY_POST not in doc
    assert "| 2 | Example Imaging (Medical Records) |" in doc and "| 1 | Example Clinic" in doc


# ---- assemble: ONE complete document ------------------------------------------
ICD_CLAIM = "Diagnosis code M50.20 recorded on 02/10/2026. (Exhibit 2 - p. 2)"


def _describe(code: str, on: str, text: str) -> tuple[str, str | None, str]:
    return code, ("Other cervical disc displacement" if code == "M50.20" else None), "table"


def _assemble(new_ents: list[str], pmh: str | None = None, claims: tuple[str, ...] = (ICD_CLAIM,)):
    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 2}]
    audit = C.audit_document(K.base_md(), new_ents, nex, pmh, icd_claims=list(claims))
    return A.assemble(
        K.base_md(),
        audit,
        describe=_describe,
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
        update_note=lambda retitled: f"Update: cited as {retitled}.",
    )


def test_the_updated_document_carries_every_delivered_entry_byte_identical_in_order() -> None:
    md, rep = _assemble([NEW_PRE, NEW_POST])
    _, ents = B.entries_of(md)
    assert ents == [NEW_PRE, K.ENTRY_PRE, K.ENTRY_POST, NEW_POST]
    assert rep["delivered_entries"] == 2 and rep["new_entries"] == 2 and rep["output_entries"] == 4
    # Tables recomputed from the whole entry set; a code first recorded by a
    # new entry gets its row from the vendored tables, never a model.
    assert "| Example Imaging | 11/03/2025 - 02/10/2026 | 2 | Exhibit 2 - p. 1 |" in md
    assert "| Example Clinic | 12/01/2025 - 01/20/2026 | 2 | Exhibit 1 - p. 1 |" in md
    assert "| M50.20 | Other cervical disc displacement | 02/10/2026 | Exhibit 2 - p. 2 |" in md
    # The new exhibit is titled the delivered way, after the delivered ones.
    assert "| 2 | Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records) |" in md
    assert rep["new_exhibits_titled"] == {"2": "Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records)"}
    # One factual Records Reviewed paragraph about the update.
    assert md.rstrip().endswith("Update: cited as {2: 'Example Imaging - 11-03-2025 - 02-10-2026 (Medical Records)'}.")
    assert "This chronology was prepared from 1 document on the matter." in md


ADDED = (
    "Additional prior history from records filed through 10-13-26\n"
    "Radiographs before the incident showed straightening of the lordosis. (Exhibit 2 - p. 1)"
)


def test_the_delivered_prior_medical_history_is_carried_and_the_new_history_is_added_under_it() -> None:
    """Change 6. Before: the delivered block was REPLACED by a model rewrite
    and proof 1 exempted it, so a delivered fact could vanish unseen. Now it
    is carried verbatim and the addition sits under it, dated."""
    md, rep = _assemble([NEW_PRE, NEW_POST], ADDED)
    assert rep["prior_history_added"] is True
    pre, _ = B.entries_of(md)
    assert pre.index(K.PMH) < pre.index(ADDED)
    r = P.run(_inputs(md, [NEW_PRE, NEW_POST]))
    assert r.fails == [], r.lines
    # Proof 1 now holds the delivered block like any delivered line.
    assert (
        "1"
        in P.run(
            _inputs(md.replace("blood pressure 120/80 before", "blood pressure before"), [NEW_PRE, NEW_POST])
        ).fails
    )


def test_assemble_refuses_a_citation_to_an_exhibit_neither_delivered_nor_new() -> None:
    with pytest.raises(ValueError, match="neither delivered nor new"):
        _assemble([NEW_POST.replace("Exhibit 2", "Exhibit 9")])


# ---- proofs --------------------------------------------------------------------
def _inputs(updated_md: str, composed: list[str], rows: list[dict] | None = None, **kw) -> P.Inputs:
    from medchron.audit import claims as CL

    keep = {1, 2}
    base = K.base_md()
    return P.Inputs(
        delivered_lines=[ln for ln in B.chronology_lines(base) if ln.strip()],
        updated_lines=[ln for ln in B.chronology_lines(updated_md) if ln.strip()],
        base_md=base,
        updated_md=updated_md,
        base_claims=CL.extract_claims(CL.body_of(base), keep),
        updated_claims=CL.extract_claims(CL.body_of(updated_md), keep),
        audit_rows=rows
        if rows is not None
        else [
            {"key": c["key"], "kind": "real", "verdict": "SUPPORTED"}
            for c in CL.extract_claims(CL.body_of(updated_md), keep)
            if c["key"] not in {b["key"] for b in CL.extract_claims(CL.body_of(base), keep)}
        ],
        composed=composed,
        audit_claims=[
            c
            for c in CL.extract_claims(CL.body_of(updated_md), keep)
            if c["key"] not in {b["key"] for b in CL.extract_claims(CL.body_of(base), keep)}
        ],
        new_exhibits={2},
        **kw,
    )


def test_the_proofs_pass_on_a_clean_update() -> None:
    md, rep = _assemble([NEW_PRE, NEW_POST])
    r = P.run(_inputs(md, [NEW_PRE, NEW_POST]))
    assert r.fails == [], r.lines


def test_proof_10_fails_when_a_composed_value_is_not_in_the_update() -> None:
    """The 2026-10-09 lesson as a gate: an entry composed from a new record
    stated 3 mm; the document that would be filed does not, and the audit
    never named it. The audit cannot see a dropped value, so this fails."""
    md, _ = _assemble([NEW_PRE, NEW_POST.replace("measuring 3 mm ", "")])
    r = P.run(_inputs(md, [NEW_PRE, NEW_POST]))
    assert "10" in r.fails
    named = [{"kind": "real", "key": "x", "verdict": "UNSUPPORTED", "unsupported_assertions": ["measuring 3 mm"]}]
    r2 = P.run(_inputs(md, [NEW_PRE, NEW_POST], rows=_inputs(md, []).audit_rows + named))
    assert "10" not in r2.fails  # a value the audit named may go


def test_proof_3_fails_when_a_delivered_claim_was_audited() -> None:
    from medchron.audit import claims as CL

    md, _ = _assemble([NEW_PRE, NEW_POST])
    x = _inputs(md, [NEW_PRE, NEW_POST])
    delivered_key = CL.extract_claims(CL.body_of(K.base_md()), {1})[0]["key"]
    x.audit_rows.append({"key": delivered_key, "kind": "real", "verdict": "SUPPORTED"})
    assert "3" in P.run(x).fails


def test_proof_1_fails_when_a_delivered_line_is_lost_and_proof_4_on_an_unaudited_claim() -> None:
    md, _ = _assemble([NEW_PRE, NEW_POST])
    broken = md.replace("Return to clinic in four weeks for a recheck of the neck.", "Return to clinic.")
    assert "1" in P.run(_inputs(broken, [NEW_PRE, NEW_POST])).fails
    assert "4" in P.run(_inputs(md, [NEW_PRE, NEW_POST], rows=[])).fails


def test_proof_8_fails_on_an_added_entry_that_cites_only_delivered_exhibits() -> None:
    md, _ = _assemble([NEW_PRE, NEW_POST])
    md = md.replace("(Exhibit 2 - p. 2)", "(Exhibit 1 - p. 2)", 1)
    assert "8" in P.run(_inputs(md, [NEW_PRE])).fails


# ---- the note that pre-incident encounters were not itemized ---------------------
def _assemble_noted(new_ents: list[str], pmh: str | None = None):
    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 2}]
    base = K.base_md(note=K.NOTE)
    return base, A.assemble(
        base,
        C.audit_document(base, new_ents, nex, pmh),
        describe=_describe,
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
        update_note=lambda retitled: "Update.",
    )


def _noted_inputs(base: str, md: str, composed: list[str], **kw) -> P.Inputs:
    x = _inputs(md, composed, **kw)
    x.base_md = base
    x.delivered_lines = [ln for ln in B.chronology_lines(base) if ln.strip()]
    from medchron.audit import claims as CL

    x.base_claims = CL.extract_claims(CL.body_of(base), {1, 2})
    return x


@pytest.mark.parametrize("pmh", [None, ADDED])
def test_the_delivered_scope_note_is_carried_verbatim_and_the_proofs_accept_it(pmh: str | None) -> None:
    """Before: the update deleted the note and proof 2 refused it, so the
    filed document went silent on encounters it never listed. Now it is
    carried verbatim after the Prior Medical History (and any added history)."""
    base, (md, rep) = _assemble_noted([NEW_PRE, NEW_POST], pmh)
    pre, _ = B.entries_of(md)
    assert K.NOTE in pre and md.count("[NTD:") == 1
    assert pre.index(K.NOTE) > pre.index("Prior Medical History")
    assert rep["pre_note_carried"] == 1 and rep["pre_note_itemized"] is False
    r = P.run(_noted_inputs(base, md, [NEW_PRE, NEW_POST]))
    assert r.fails == [], r.lines
    assert "2. pre-incident note: carried verbatim (1)" in r.lines


def test_proof_2_fails_when_the_note_is_dropped_or_changed() -> None:
    base, (md, rep) = _assemble_noted([NEW_PRE, NEW_POST])
    dropped = md.replace(K.NOTE, "")
    assert "2" in P.run(_noted_inputs(base, dropped, [NEW_PRE, NEW_POST])).fails
    changed = md.replace("3 additional", "2 additional")
    assert "2" in P.run(_noted_inputs(base, changed, [NEW_PRE, NEW_POST])).fails
    with pytest.raises(ValueError, match="pre-incident note"):
        A._check_carried(dropped, B.entries_of(base)[1], [K.NOTE])


def test_the_note_goes_only_when_the_update_itself_itemizes_those_encounters() -> None:
    def visit(mm: str, dd: str) -> str:
        return (
            f"{mm}/{dd}/2025\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
            f"Routine check on {mm}/{dd}/2025 recorded pulse 7{mm[1]}. (Exhibit 2 - p. 1)"
        )

    three = [visit("06", "10"), visit("07", "12"), visit("09", "01")]
    assert A.itemizes(K.NOTE, three) and not A.itemizes(K.NOTE, three[:2])
    assert not A.itemizes(K.NOTE, [visit("10", "05")] * 3)  # outside the span it states
    assert not A.itemizes("[NTD: 3 additional pre-incident encounters were reviewed and are not itemized.]", three)
    base, (md, rep) = _assemble_noted([*three, NEW_POST])
    assert "[NTD:" not in md and rep["pre_note_itemized"] is True and rep["pre_note_carried"] == 0
    r = P.run(_noted_inputs(base, md, [*three, NEW_POST], pre_note_itemized=True))
    assert "2" not in r.fails, r.lines


# ---- values ---------------------------------------------------------------------
def test_values_normalise_spellings_and_ignore_citation_pages() -> None:
    assert numbers("209.0 and 02/03/25 (Exhibit 4 - p. 17)") == {"209", "2/3/2025"}
    assert lost("BP 132/96 at 16:05", "BP 132/94 at 16:05") == ["132/96"]
