"""The finder on the two shapes that stopped real jobs on 2026-11-21 (``served_find.py``).

1. A served Judicial Council form (DISC-001) whose text layer is the printed
   blank form: its set number, parties and checkmarks are drawn, not text, so
   the one text read returns no set number, count or parties. The form set is
   still found: its count from the $0 pixel read, its number from the file or
   the matter, its parties from the record (``served_formset.py``).
2. A request that names one kind of set in passing, beside image-only exhibit
   files SMD wrote in the discovery folder: the request does not narrow the job
   and SMD's own files are never counted as discovery scans.

Every name, number and date is invented. The read is injected; no model is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.drafting import served, served_find, served_papers
from jobs.drafting.forms import read_stage

from test_drafting_served_find import CLIENT, DEF, THREE, Fake, form, matter, rfp, run, srog, stop_of, values

#: What the text read of a blank form returns: nothing the form's drawn fill-ins hold.
BLANK = values(
    "form_interrogatories", 1, date=None, set_number=None, request_count=None,
    propounding_party=None, responding_party=None,
)  # fmt: skip
CHECKED = ["1.1", "2.1", "6.4", "8.1"]
#: A served text set beside the form: a lane job always has one the Operator drafts.
SROG = {"id": "s1", "name": "s.pdf", "pages": srog(2)}
SI = {"SPECIAL": values("special_interrogatories", 2)}


def _form_doc(tmp_path: Path, rid: str = "f1", name: str = "SM_REQ FOR FI.pdf") -> dict:
    p = tmp_path / f"{rid}.pdf"
    p.write_bytes(b"invented form bytes " + rid.encode())
    return {"id": rid, "name": name, "pages": form(), "path": str(p), "sha": rid}


def _caption(data: Path) -> None:
    (data / "caption.json").write_text(json.dumps({"parties": [{"name": DEF, "side": "other"}]}))


@pytest.fixture
def pixel(monkeypatch):
    """The $0 pixel read, stood in at the real module the finder calls."""
    marks = {"checked": list(CHECKED), "form_revision": "Rev. January 1, 2024"}
    monkeypatch.setattr(read_stage, "pixel_read", lambda _path: dict(marks))
    return marks


def _found(jd: Path) -> list[served.ServedSet]:
    # The plan's own validator reads what the finder wrote; the form set is the one under test.
    return [s for s in served.read(jd, [CLIENT]) if s.set_type == "form_interrogatories"]


def _row(jd: Path, rid: str) -> dict:
    return next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == rid)


# ---- 1. a form whose fill-ins are drawn, not text --------------------------------------------


def test_a_blank_text_form_is_found_from_the_pixel_read_and_the_record_not_stopped(tmp_path, pixel):
    # The special interrogatories are from another filing (other dates, a proof of service naming no form).
    data, jd = matter(tmp_path, [_form_doc(tmp_path), {**SROG, "created": "2026-09-01T09:00:00"}])
    _caption(data)
    served_find.find(data, jd, "Please draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(),
                     clients=[CLIENT], other_side=served_find.other_side(data))  # fmt: skip
    (s,) = _found(jd)
    assert (s.set_type, s.set_number, s.request_count) == ("form_interrogatories", 1, len(CHECKED))
    assert (s.propounding_party, s.responding_party, s.drafted_by) == (DEF, CLIENT, "smd")
    row = _row(jd, "f1")
    assert row["kept"] and row["party_source"] == "record"
    assert "pixel" in row["request_count"] and "only form set" in row["set_number_source"]


def test_a_firm_with_a_form_policy_has_its_found_form_set_drafted_by_the_operator(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), {**SROG, "created": "2026-09-01T09:00:00"}])
    _caption(data)
    served_find.find(data, jd, "Please draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(),
                     clients=[CLIENT], other_side=served_find.other_side(data), form_by_operator=True)  # fmt: skip
    sets = json.loads((jd / served.FILE).read_text())["sets"]
    (s,) = [s for s in sets if s["set_type"] == "form_interrogatories"]
    assert s["drafted_by"] == "operator" and s["source"]  # the box read (read_stage) fills the rest


# ---- 1b. the asking party of a drawn form, from the text sets served with it ------------------

OMEGA = "OMEGA EXAMPLE LLC"


def _srog_from(party: str, n: int = 2, proof: str = "") -> list[str]:
    pages = srog(n)
    return [pages[0].replace(DEF, party), pages[1], pages[2] + proof]


def _find(data: Path, jd: Path, fake: Fake, other_side: list[str] | None = None) -> None:
    served_find.find(data, jd, "Draft responses.", fake.reader(), clients=[CLIENT], other_side=other_side or [])


def test_a_drawn_forms_asking_party_comes_from_the_text_set_served_with_it_when_no_record(tmp_path, pixel):
    """2026-11-21: no caption record and a blank form read stopped the job, though the special
    interrogatories served the same day name the propounding party."""
    data, jd = matter(tmp_path, [_form_doc(tmp_path), SROG])
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    (s,) = _found(jd)
    assert (s.propounding_party, s.responding_party) == (f"Defendant {DEF}", CLIENT)
    assert _row(jd, "f1")["party_source"] == "the text sets served with it (Special Interrogatories, Set One)"


def test_a_text_set_whose_proof_of_service_lists_the_form_is_its_filing_mate_on_other_dates(tmp_path, pixel):
    proof = "Documents served: SPECIAL INTERROGATORIES, SET ONE; FORM INTERROGATORIES - GENERAL, SET ONE.\n"
    srog_ = {"id": "s1", "name": "s.pdf", "pages": _srog_from(DEF, proof=proof), "created": "2026-09-01T09:00:00"}
    data, jd = matter(tmp_path, [_form_doc(tmp_path), srog_])
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    assert [s.propounding_party for s in _found(jd)] == [f"Defendant {DEF}"]


def test_a_text_set_from_another_filing_names_no_asking_party_for_the_form(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), {**SROG, "created": "2026-09-01T09:00:00"}])
    with pytest.raises(served_find.Stop) as exc:
        _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    assert exc.value.code == "served_ambiguous" and "asking party is not on the form's text" in str(exc.value)


def test_text_sets_served_with_the_form_that_name_different_asking_parties_stop_never_pick(tmp_path, pixel):
    docs = [_form_doc(tmp_path), SROG, {"id": "p1", "name": "p.pdf", "pages": [rfp(2)[0].replace(DEF, OMEGA),
                                                                         *rfp(2)[1:]]}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    rfp_v = values("requests_for_production", 2, propounding_party=f"Defendant {OMEGA}")
    with pytest.raises(served_find.Stop) as exc:
        _find(data, jd, Fake({"DISC-001": BLANK, **SI, "DEMAND FOR PRODUCTION": rfp_v}))
    assert exc.value.code == "served_ambiguous" and "different asking parties" in str(exc.value)


def test_a_text_set_served_with_the_form_that_disagrees_with_the_record_stops(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), SROG])
    with pytest.raises(served_find.Stop) as exc:
        _find(data, jd, Fake({"DISC-001": BLANK, **SI}), other_side=[OMEGA])
    assert exc.value.code == "served_ambiguous" and "the record names" in str(exc.value)


def test_a_form_alone_in_its_filing_whose_read_names_a_party_off_the_page_keeps_it_for_smd(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), {**SROG, "created": "2026-09-01T09:00:00"}])
    _find(data, jd, Fake({"DISC-001": {**BLANK, "propounding_party": f"Defendant {OMEGA}"}, **SI}))
    assert [s.propounding_party for s in _found(jd)] == [f"Defendant {OMEGA}"]
    assert _row(jd, "f1")["party_source"].startswith("read (")


# ---- 2b. a form set OUR side propounded is not served on us -----------------------------------


def _ours_then_served(tmp_path: Path, ours_doc: dict, *more: dict) -> tuple[Path, Path]:
    """Our side's form beside the served form and a served text set, all invented."""
    return matter(tmp_path, [ours_doc, _form_doc(tmp_path, "f1", "SM_REQ FOR FI.pdf"), SROG, *more])


def _assert_only_the_served_form(jd: Path, ours_id: str, why: str) -> None:
    (s,) = _found(jd)
    assert (s.set_number, s.propounding_party) == (1, f"Defendant {DEF}")
    assert _row(jd, "f1")["kept"]
    row = _row(jd, ours_id)
    assert not row["kept"] and why in row["why"]


def test_our_sides_form_in_our_clients_own_folder_is_not_counted_as_served(tmp_path, pixel):
    """2026-11-21: our client's form set to the defendant, in the plaintiff's folder, made the served
    form's drawn set number read 'another form set'."""
    ours_doc = {
        **_form_doc(tmp_path, "o1", "FROG - EXAMPLE CASE TYPE.pdf"),
        "folder": "/Discovery/Plaintiff Propounded",
    }
    data, jd = _ours_then_served(tmp_path, ours_doc)
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    _assert_only_the_served_form(jd, "o1", "names our client's side (plaintiff)")


def test_our_sides_form_beside_a_set_our_client_propounded_is_not_counted_as_served(tmp_path, pixel):
    ours_srog = srog(2)
    ours_srog[0] = ours_srog[0].replace(f"REQUESTING PARTY: Defendant {DEF}", f"PROPOUNDING PARTY: Plaintiff {CLIENT}")
    ours_srog[0] = ours_srog[0].replace(
        "RESPONDING PARTY: Plaintiff GAMMA EXAMPLE", f"RESPONDING PARTY: Defendant {DEF}"
    )
    out = "/Discovery/Outgoing"
    ours_doc = {**_form_doc(tmp_path, "o1", "FROG - EXAMPLE CASE TYPE.pdf"), "folder": out}
    data, jd = _ours_then_served(
        tmp_path, ours_doc, {"id": "o2", "name": "SROG.pdf", "pages": ours_srog, "folder": out}
    )
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    _assert_only_the_served_form(jd, "o1", "holds a set our client propounded")


def test_a_form_smd_wrote_is_never_counted_as_served(tmp_path, pixel):
    data, jd = _ours_then_served(tmp_path, _form_doc(tmp_path, "o1", "DRAFT Form Interrogatories to Def.pdf"))
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    _assert_only_the_served_form(jd, "o1", "a file SMD wrote")


@pytest.mark.parametrize("folder", ["/Plaintiff Discovery", "/Discovery/Pltf Received", "/Discovery/Pltf v Def"])
def test_a_form_in_a_folder_that_also_shows_what_came_in_stays_a_served_candidate(tmp_path, pixel, folder):
    """A folder naming our client's side shows nothing when it also holds a set served on us,
    says it holds what came in, or names the other side too."""
    data, jd = matter(tmp_path, [{**_form_doc(tmp_path), "folder": folder}, {**SROG, "folder": "/Plaintiff Discovery"}])
    _find(data, jd, Fake({"DISC-001": BLANK, **SI}))
    assert [s.set_type for s in _found(jd)] == ["form_interrogatories"]


def test_a_blank_text_form_the_pixel_read_cannot_read_stops_for_smd_never_a_guessed_count(tmp_path):
    doc = {**_form_doc(tmp_path), "path": ""}  # no file on disk: no pixel read
    data, jd = matter(tmp_path, [doc, SROG])
    _caption(data)
    fake = Fake({"DISC-001": BLANK, **SI})
    with pytest.raises(served_find.Stop) as exc:
        served_find.find(data, jd, "Draft responses.", fake.reader(), clients=[CLIENT], other_side=[DEF])
    assert exc.value.code == "served_ambiguous" and "pixel read could not read" in str(exc.value)
    assert not (jd / served.FILE).exists()


def test_a_blank_text_forms_set_number_comes_from_its_file_name(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path, name="Def Form Rogs Set Two.pdf"), SROG])
    served_find.find(data, jd, "Draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(), clients=[CLIENT],
                     other_side=[DEF])  # fmt: skip
    assert [s.set_number for s in _found(jd)] == [2]
    assert _row(jd, "f1")["set_number_source"] == "the file name"


def test_a_blank_text_form_with_a_draft_of_it_on_file_is_still_a_revision(tmp_path, pixel):
    """served_prior_draft holds: the one form draft on file names the only form set's number."""
    data, jd = matter(tmp_path, [_form_doc(tmp_path), SROG])
    listing = json.loads((data / "manifest.json").read_text())
    listing.append({"id": "o1", "name": "DRAFT Responses to Form Interrogatories, Set Two.docx"})
    (data / "manifest.json").write_text(json.dumps(listing))
    with pytest.raises(served_find.Stop) as exc:
        served_find.find(data, jd, "Draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(), clients=[CLIENT],
                         other_side=[DEF])  # fmt: skip
    assert exc.value.code == "served_prior_draft"


def test_two_blank_text_forms_stop_for_smd_never_numbered_by_guess(tmp_path, monkeypatch):
    marks = iter([{"checked": ["1.1"], "form_revision": "R"}, {"checked": ["2.1"], "form_revision": "R"}] * 2)
    monkeypatch.setattr(read_stage, "pixel_read", lambda _p: next(marks))
    data, jd = matter(tmp_path, [_form_doc(tmp_path, "fa"), _form_doc(tmp_path, "fb")])
    with pytest.raises(served_find.Stop) as exc:
        served_find.find(data, jd, "Draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(), clients=[CLIENT],
                         other_side=[DEF])  # fmt: skip
    assert exc.value.code == "served_ambiguous" and "another form set" in str(exc.value)


def test_a_blank_responding_party_with_two_clients_on_record_stops_never_picks_one(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), SROG])
    with pytest.raises(served_find.Stop) as exc:
        served_find.find(data, jd, "Draft responses.", Fake({"DISC-001": BLANK, **SI}).reader(),
                         clients=[CLIENT, "Epsilon Sample"], other_side=[DEF])  # fmt: skip
    assert exc.value.code == "served_ambiguous" and "responding party" in str(exc.value)


def test_a_read_that_names_a_count_still_yields_to_the_pixel_reads_checked_numbers(tmp_path, pixel):
    data, jd = matter(tmp_path, [_form_doc(tmp_path), SROG])
    served_find.find(data, jd, "Draft responses.", Fake({"DISC-001": THREE["DISC-001"], **SI}).reader(), clients=[CLIENT],
                     other_side=[DEF])  # fmt: skip
    assert [s.request_count for s in _found(jd)] == [len(CHECKED)]  # the read said 62; the paper checks 4


# ---- 2. a kind named in passing; SMD's own image-only files ------------------------------------

#: The 2026-11-21 request's shape, invented: special interrogatories named in passing,
#: and a sentence about numbered requests in a response paper.
PASSING = (
    "Please draft the discovery responses on matter 100001; the special interrogatories came in with the rest. "
    "Also use the no-documents phrases on Requests 5 and 8 of the Response to Request for Production."
)


def _exhibits() -> list[dict]:
    return [
        {"id": f"x{c}", "name": f"DRAFT Pltf Responses to RFP Set One - Exhibit {c}.pdf", "pages": [""],
         "folder": "/DISCOVERY"}
        for c in "BCDE"
    ] + [{"id": "xo", "name": "Responses to RFPs exhibit.pdf", "pages": [""],
          "folder": "/Discovery Responses 11-20-26 (Operator AB12CD)"}]  # fmt: skip


def test_a_kind_named_in_passing_does_not_narrow_and_smds_exhibits_are_not_scans(tmp_path):
    docs = [{"id": "s1", "name": "s.pdf", "pages": srog(3)}, {"id": "p1", "name": "p.pdf", "pages": rfp(4)},
            {"id": "f1", "name": "f.pdf", "pages": form()}, *_exhibits()]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    fake = Fake({"SPECIAL INTERROGATORIES, SET": values("special_interrogatories", 3),
                 "DEMAND FOR PRODUCTION AND INSPECTION": values("requests_for_production", 4),
                 "DISC-001": THREE["DISC-001"]})  # fmt: skip
    run(data, jd, fake, request=PASSING)
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["special_interrogatories", "requests_for_production",
                                                "form_interrogatories"]  # fmt: skip
    kinds = {r["id"]: r["kind"] for r in json.loads((jd / served_find.REPORT).read_text())["candidates"]}
    assert not any(k == "scan" for k in kinds.values())


def test_smds_image_only_exhibits_alone_in_the_discovery_folder_are_not_a_scan_stop(tmp_path):
    data, jd = matter(tmp_path, _exhibits())
    assert stop_of(lambda: run(data, jd, Fake({}), request=PASSING)) == "served_none"
    # A no-text file of the other side's, named as discovery, is still a scan.
    scan = {"id": "y1", "name": "Def_SROGs-Set_One.pdf", "pages": [""], "folder": "/DISCOVERY"}
    data2, jd2 = matter(tmp_path / "b", [*_exhibits(), scan])
    assert stop_of(lambda: run(data2, jd2, Fake({}), request=PASSING)) == "served_scan"


@pytest.mark.parametrize(
    ("request_text", "kinds"),
    [
        ("Please draft responses to the special interrogatories on matter 100001.", ["special_interrogatories"]),
        ("Can you prepare our RFP and form interrogatory responses for 100001?",
         ["requests_for_production", "form_interrogatories"]),
        ("Please respond to their requests for admission. Thanks.", ["requests_for_admission"]),
        ("Draft responses to the discovery on matter No. 100001.", []),
        (PASSING, []),
        ("The special interrogatories are in. Please draft the responses on 100001.", []),
    ],
)  # fmt: skip
def test_the_request_narrows_only_on_the_kinds_it_asks_to_draft(request_text, kinds):
    assert served_find.request_kinds(request_text) == kinds


def test_a_kind_in_a_sentence_about_numbered_requests_is_not_an_ask():
    assert served_papers.draft_asks("Fix Requests 5 and 8 of the Response to Request for Production.") == ""
