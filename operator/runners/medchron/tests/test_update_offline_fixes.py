"""What the offline run of the update lane on real files found (2026-10-13),
fixes 15 to 19: each test fails on the code before the fix (e8d7fc79) and
passes after. Every value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
from medchron.update import body as B, collect as C


# ---- fix 15: a dropped claim leaves no bare "." and no empty paragraph ------------
def test_dropping_a_claim_removes_its_punctuation_and_the_paragraph_it_empties() -> None:
    from medchron.audit import claims as CL
    from medchron.audit.repair import replace_in

    body = (
        "\n02/10/2026\nExample Lab | Diagnostic Studies\n\nDiagnostic Studies\n"
        "Complete blood count drawn at 07:42 reported hemoglobin 11.2 g/dL (Exhibit 2 - p. 1).\n\n"
        "All Other Information\nReturn to clinic in two weeks for results review (Exhibit 2 - p. 2).\n"
    )
    spans = CL.claim_spans(body, {2})
    first = next(s for s in spans.values() if "hemoglobin" in s)
    out, ok = replace_in(body, first, "")
    assert ok and "hemoglobin" not in out
    assert not [ln for ln in out.splitlines() if ln.strip() == "."]  # proof 6's failure shape
    assert "\n\n\n" not in out and "Return to clinic in two weeks" in out


# ---- fix 16: the review reads every character, an update's additions in full ----------
def test_the_review_reads_an_updates_additions_in_full_in_parts_on_the_strongest_tier(
    tmp_path: Path, monkeypatch
) -> None:
    from medchron.proof import review as proof_review
    from medchron.stages import review as R

    rd = tmp_path / "runs" / "alpha"
    (rd / "update").mkdir(parents=True)
    new = [
        f"0{m}/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
        f"Study {m} reviewed with a finding recorded for visit {m}. (Exhibit 2 - p. {m})"
        for m in range(2, 10)
    ]
    (rd / "update" / "base.md").write_text(K.base_md())
    (rd / "update" / "updated-chronology.md").write_text(K.base_md(entries=(K.ENTRY_PRE, K.ENTRY_POST, *new)))
    seen: list[tuple[str, str]] = []

    def fake(doorway, model, system, user, key, holds, **kw):
        seen.append((model, user))
        return []

    monkeypatch.setattr(proof_review, "run_model", fake)
    monkeypatch.setattr(R, "REVIEW_TEXT_MAX_CHARS", 300)
    monkeypatch.setattr(R.chunks, "__defaults__", (300,))
    sr = SimpleNamespace(
        cfg=K.cfg(), slug="example-matter", unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=""),
        job=SimpleNamespace(units=[], incident_date=K.INCIDENT),
        doorway=None,
    )  # fmt: skip
    R._model_findings(sr, rd, R._model(sr), "", [], [])  # type: ignore[arg-type]
    assert len(seen) > 1 and {m for m, _ in seen} == {K.cfg().get("models", "tiers")["judgment"]}
    joined = "".join(u for _, u in seen)
    assert all(f"Study {m} reviewed" in joined for m in range(2, 10))  # every added entry, none cut
    text = R.update_material(rd) or ""
    assert "".join(R.chunks(text, 300)) == text


# ---- fix 17: one lab draw entered from two documents becomes one entry -------------------
def test_one_lab_draw_from_two_documents_is_one_entry_citing_both() -> None:
    a = (
        "02/10/2026\nExample Lab | Diagnostic Studies\n\nDiagnostic Studies\n"
        "CBC collected 07:42: hemoglobin 11.2 g/dL, WBC 6.1. (Exhibit 2 - p. 4)"
    )
    b = (
        "02/10/2026\nExample Clinic | Diagnostic Studies\n\nDiagnostic Studies\n"
        "CBC collected 07:42 reported hemoglobin 11.2 g/dL. (Exhibit 3 - p. 9)"
    )
    _, base = B.entries_of(K.base_md())
    kept, rep = C.collect([a, b], base, K.INCIDENT, str)
    assert (
        len(kept) == 1 and "WBC 6.1" in kept[0] and "(Exhibit 2 - p. 4)" in kept[0] and "(Exhibit 3 - p. 9)" in kept[0]
    )
    assert rep["merged: one lab draw entered from two new documents"] == 1
    other_time = b.replace("07:42", "15:10")
    assert len(C.collect([a, other_time], base, K.INCIDENT, str)[0]) == 2  # two draws stay two


# ---- fix 18: a near provider spelling is settled by the audit, titles included ------------
def test_the_audited_provider_spelling_reaches_the_entries_and_the_exhibit_title() -> None:
    from medchron.update import assemble as A, providers as P

    def ent(p: str, d: str = "02/10/2026") -> str:
        return (
            f"{d}\n{p} | Medical Diagnoses\n\nMedical Diagnoses\nAdjustment performed at the visit. (Exhibit 2 - p. 1)"
        )

    base_ents = [ent("Rosa Bertelli, DC", "01/20/2026")]
    _, log = P.snap([ent("Rosa Bertell, DC")], base_ents)
    lines, composed = P.claims([ent("Rosa Bertell, DC")], log)
    assert lines == ["Provider named on the record: Rosa Bertell, DC. (Exhibit 2 - p. 1)"]
    audited = [lines[0].replace("Bertell,", "Bertelli,")]  # what the audit's repair writes from the page
    nex = [{"exhibit": 2, "title": "Exhibit 2 - X (Medical Records)", "pages": 1}]
    md, rep = A.assemble(
        K.base_md(),
        C.audit_document(K.base_md(), [ent("Rosa Bertell, DC")], nex, icd_claims=audited),
        describe=lambda c, o, t: (c, None, "x"),
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
        provider_names=composed,
    )
    assert "Rosa Bertell, DC" not in md
    assert rep["new_exhibits_titled"]["2"].startswith("Rosa Bertelli, DC - ")
    assert rep["provider_spelling_from_audit"] == {"Rosa Bertell, DC": "Rosa Bertelli, DC"}


# ---- fix 19: Records Reviewed states what was read, never a conclusion --------------------
def test_records_reviewed_states_pages_read_never_that_a_file_records_nothing(tmp_path: Path) -> None:
    from medchron.stages.update_finish import update_note

    ud = tmp_path
    keep = [{"exhibit": 2, "pages": 3, "read": [1, 2, 3], "excluded": {}, "source_name": "a.pdf"}]
    unc = [
        {
            "exhibit": 3,
            "pages": 834,
            "read": list(range(1, 701)),
            "unread": [],
            "excluded": {"repeats an earlier page of the new documents": 70, "no text on the page": 64},
            "source_name": "Big file.pdf",
        }
    ]
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep))
    (ud / "new_exhibits.uncited.json").write_text(json.dumps(unc))
    (ud / "selection.json").write_text("{}")
    text = update_note(SimpleNamespace(), ud)({2: "Example Imaging - 02-10-2026 (Medical Records)"})  # type: ignore[arg-type]
    assert "record no visit" not in text and "did not already list, so none" not in text
    assert "Big file.pdf (834 pages: 700 pages read, 70 repeating pages already read, 64 pages with no text)" in text
    assert "703 pages were read" in text


# ---- fix 20: the pipeline's own note never reaches the document -----------------------
def test_a_pipeline_note_is_stripped_and_proof_6_fails_if_one_is_added() -> None:
    from medchron import meta_markers
    from medchron.update import proofs as P
    from test_update_text import NEW_POST, NEW_PRE, _assemble, _inputs

    line = "Hepatitis panel reported (text continues beyond the page) (Exhibit 2 - p. 3)"
    assert meta_markers.strip(line) == "Hepatitis panel reported (Exhibit 2 - p. 3)"
    assert meta_markers.found("Blood pressure (taken seated) 120/80") == []  # the record's own parentheses stay
    md, _ = _assemble([NEW_PRE, NEW_POST])
    leaked = md.replace("fifth and sixth level.", "fifth and sixth level (text continues beyond the page).")
    assert leaked != md
    assert "6" in P.run(_inputs(leaked, [NEW_PRE, NEW_POST])).fails
    assert "6" not in P.run(_inputs(md, [NEW_PRE, NEW_POST])).fails
