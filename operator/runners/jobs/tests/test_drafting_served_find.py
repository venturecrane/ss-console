"""The finder: the Operator works out the served sets itself (``served_find.py``).

Each falsifier is a synthetic matter file (every name, number and date
invented). The read is injected: no test calls a model. The shapes follow the
2026-11-20 measurement of real served sets: REQUESTING as often as
PROPOUNDING PARTY, bare "n." numbering under a section heading, blank
"RESPONSE:" lines, the Judicial Council form typed by its footer, and every set
listed twice as identical bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.drafting import outcome, served, served_find
from jobs.drafting.served_find import Reader, Stop

CLIENT = "Gamma Example"
DEF = "DELTA EXAMPLE CORPORATION"
CAPTION = (
    "ALPHA EXAMPLE (SBN 000001)\nAttorneys for Defendant DELTA EXAMPLE CORPORATION\n"
    "SUPERIOR COURT OF THE STATE OF CALIFORNIA\nGAMMA EXAMPLE, Plaintiff,\nv.\n"
    "DELTA EXAMPLE CORPORATION, Defendant.\nCase No. 30-0001\n"
)


def head(title: str, asking: str = "REQUESTING", set_word: str = "ONE") -> str:
    return (
        CAPTION + f"DEFENDANT DELTA EXAMPLE CORPORATION'S {title}, SET {set_word}\n"
        f"{asking} PARTY: Defendant {DEF}\nRESPONDING PARTY: Plaintiff GAMMA EXAMPLE\nSET NO.: {set_word}\n"
    )


def bare(heading: str, n: int) -> str:
    defs = "DEFINITIONS\n1. YOU means the responding party.\n2. INCIDENT means the collision.\n"
    items = "\n".join(f"{i}. State example fact {i}.\nRESPONSE:\n" for i in range(1, n + 1))
    return f"{defs}{heading}\n{items}"


def labelled(n: int) -> str:
    return "\n".join(
        f"REQUEST FOR PRODUCTION NO. {i}:\nAll example writings {i}.\nRESPONSE TO REQUEST FOR PRODUCTION NO. {i}:\n"
        for i in range(1, n + 1)
    )


END = "Dated: October 1, 2026\nBETA EXAMPLE\nPROOF OF SERVICE\nServed by mail on October 1, 2026.\n"
FORM_PAGE = "DISC-001\nFORM INTERROGATORIES - GENERAL\nSec. 17.1 REQUESTS FOR ADMISSION\nPage {n} of 8\n"


def srog(n: int = 22, asking: str = "REQUESTING") -> list[str]:
    return [head("SPECIAL INTERROGATORIES", asking), bare("SPECIAL INTERROGATORIES", n), END]


def rfp(n: int = 19) -> list[str]:
    title = "DEMAND FOR PRODUCTION AND INSPECTION OF DOCUMENTS"
    return [head(title, "PROPOUNDING"), bare(title, n), END]


def form() -> list[str]:
    return [FORM_PAGE.format(n=i) for i in range(1, 9)]


#: What a served response paper of ours shows: its proof of service (``served_papers.was_served``).
SERVED_ON = "PROOF OF SERVICE\nServed by email on October 30, 2026.\n"


def response(kind_title: str, responding: str = "Plaintiff GAMMA EXAMPLE") -> list[str]:
    return [
        CAPTION + f"PLAINTIFF GAMMA EXAMPLE'S RESPONSES TO {kind_title}, SET ONE\n"
        f"PROPOUNDING PARTY: Defendant {DEF}\nRESPONDING PARTY: {responding}\nSET NO.: ONE\n",
        "RESPONSE TO SPECIAL INTERROGATORY NO. 1: Gamma Example.\n",
        SERVED_ON,
    ]


def matter(tmp_path: Path, docs: list[dict], facts: dict | None = None) -> tuple[Path, Path]:
    data, jd = tmp_path / "job" / "data", tmp_path / "job"
    (data / "text").mkdir(parents=True)
    ext, corpus, listing = [], [], []
    for d in docs:
        tp = data / "text" / f"{d['id']}.txt"
        # A Word file's text carries no page marks (``stages/extract``): "text" is written as is.
        body = d["text"] if "text" in d else "\n".join(f"[p.{i}]\n{t}" for i, t in enumerate(d["pages"], 1))
        tp.write_text(body, encoding="utf-8")
        ext.append({"id": d["id"], "name": d["name"], "folder": d.get("folder", "/Discovery"),
                    "kind": d.get("kind", "file"), "ext": d.get("ext", ".pdf"), "text_path": str(tp),
                    "pages": len(d.get("pages", [1])), "transcribe": []})  # fmt: skip
        corpus.append({"id": d["id"], "sha256": d.get("sha", d["id"]), "path": d.get("path", "")})
        listing.append({"id": d["id"], "name": d["name"], "created": d.get("created", "2026-10-02T09:00:00")})
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ext), encoding="utf-8")
    (data / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
    (data / "manifest.json").write_text(json.dumps(listing), encoding="utf-8")
    (data / "facts.json").write_text(json.dumps(facts or {"client_names": [CLIENT]}), encoding="utf-8")
    return data, jd


def values(set_type: str, count: int, *, date: str | None = "2026-10-01", **over) -> dict:
    v = {"set_type": set_type, "set_number": 1, "propounding_party": f"Defendant {DEF}",
         "responding_party": "Plaintiff GAMMA EXAMPLE", "request_count": count, "date_served": date}  # fmt: skip
    return {**v, **over}


class Fake:
    """The injected read: values per document, picked by a word on its pages."""

    def __init__(self, by_word: dict[str, dict], cost: float = 0.0) -> None:
        self.by_word, self.cost, self.seen, self.total = by_word, cost, [], 0.0

    def ask(self, text: str) -> str:
        self.seen.append(text)
        self.total += self.cost
        return json.dumps(next(v for w, v in self.by_word.items() if w in text))

    def reader(self) -> Reader:
        return Reader(self.ask, lambda: self.total, lambda _c: self.cost)


THREE = {
    "SPECIAL INTERROGATORIES, SET": values("special_interrogatories", 22),
    "DEMAND FOR PRODUCTION AND INSPECTION": values("requests_for_production", 19),
    "DISC-001": values(
        "form_interrogatories",
        62,
        date=None,
        propounding_party="Delta Example Corporation",
        responding_party="Gamma Example",
    ),  # fmt: skip
}


def three_docs() -> list[dict]:
    return [
        {"id": "s1", "name": "Def SROGs Set One.pdf", "pages": srog()},
        {"id": "s1b", "name": "Def SROGs Set One.pdf", "pages": srog(), "sha": "s1", "folder": "/Discovery/Served"},
        {"id": "p1", "name": "Def RFPs.pdf", "pages": rfp()},
        {"id": "f1", "name": "Form Rogs.pdf", "pages": form()},
    ]


def run(data: Path, jd: Path, fake: Fake, request: str = "Please draft responses to the discovery on matter 100001."):
    return served_find.find(data, jd, request, fake.reader(), clients=[CLIENT])


def stop_of(fn) -> str:
    with pytest.raises(Stop) as exc:
        fn()
    assert str(exc.value).split(":", 1)[0] == exc.value.code in served.FIND_STOPS + ("served_other_set",)
    return exc.value.code


# ---- found and typed -------------------------------------------------------------------------


def test_three_served_sets_are_found_typed_and_written_as_the_hand_files_shape(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    fake = Fake(THREE)
    run(data, jd, fake)
    got = served.read(jd, [CLIENT])  # the plan's own validator reads what the finder wrote
    assert [(s.set_type, s.request_count, s.drafted_by) for s in got] == [
        ("special_interrogatories", 22, "operator"),
        ("requests_for_production", 19, "operator"),
        ("form_interrogatories", 62, "smd"),
    ]
    assert served.numbering(got[0].request_text) == list(range(1, 23))  # bare "n." under its heading, not DEFINITIONS
    assert "RESPONSE:" not in got[0].request_text and "State example fact 22." in got[0].request_text
    form_set = json.loads((jd / served.FILE).read_text())["sets"][2]
    assert form_set["source"] == "f1" and "request_text" not in form_set  # the form reader's
    report = json.loads((jd / served_find.REPORT).read_text())
    assert report["stop"] is None and report["reads"] == 3 == len(fake.seen)  # the byte copy was not read twice
    s1 = next(r for r in report["candidates"] if r["id"] == "s1")
    assert s1["copies"] == ["/Discovery/Served/Def SROGs Set One.pdf"] and s1["numbered_as"] == "bare"


def test_requesting_and_demanding_party_lines_are_the_asking_side_too(tmp_path):
    pages = srog(3, asking="DEMANDING")
    data, jd = matter(tmp_path, [{"id": "s1", "name": "a.pdf", "pages": pages}])
    run(data, jd, Fake({"SPECIAL": values("special_interrogatories", 3)}))
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["special_interrogatories"]


def test_a_disc_001_page_types_the_file_form_interrogatories_though_its_body_names_admissions(tmp_path):
    data, jd = matter(
        tmp_path, [{"id": "f1", "name": "x.pdf", "pages": form()}, {"id": "s1", "name": "s.pdf", "pages": srog(2)}]
    )
    run(data, jd, Fake({"DISC-001": THREE["DISC-001"], "SPECIAL": values("special_interrogatories", 2)}))
    sets_ = json.loads((jd / served.FILE).read_text())["sets"]
    assert sets_[0]["set_type"] == "form_interrogatories" and sets_[0]["responding_party"] == CLIENT
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "f1")
    assert row["party_source"].startswith("read")  # no party lines as text and no caption record: said so


def test_labelled_requests_with_blank_response_lines_slice_and_number(tmp_path):
    pages = [head("REQUESTS FOR PRODUCTION", "PROPOUNDING"), labelled(11), END]
    data, jd = matter(
        tmp_path, [{"id": "p1", "name": "p.pdf", "pages": pages}, {"id": "f1", "name": "f.pdf", "pages": form()}]
    )
    run(
        data,
        jd,
        Fake({"REQUEST FOR PRODUCTION NO. 1": values("requests_for_production", 11), "DISC-001": THREE["DISC-001"]}),
    )
    p = served.read(jd, [CLIENT])[0]
    assert served.numbering(p.request_text) == list(range(1, 12)) and "RESPONSE TO" not in p.request_text


# ---- answered, prior drafts, the request ------------------------------------------------------


def test_a_later_response_paper_of_ours_pairs_with_and_drops_its_served_set(tmp_path):
    docs = [*three_docs(), {"id": "r1", "name": "Resp SROGs.pdf", "pages": response("SPECIAL INTERROGATORIES"),
                            "created": "2026-10-30T09:00:00"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake(THREE))
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["requests_for_production", "form_interrogatories"]
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "r1")
    assert row["kept"] and "answers Responses to Special Interrogatories, Set One" in row["why"]


def test_the_other_sides_response_to_our_discovery_is_ignored(tmp_path):
    theirs = response("FORM INTERROGATORIES", responding=f"Defendant {DEF}")
    data, jd = matter(tmp_path, [*three_docs(), {"id": "r2", "name": "Def resp.pdf", "pages": theirs}])
    run(data, jd, Fake(THREE))
    assert len(served.read(jd, [CLIENT])) == 3
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "r2")
    assert not row["kept"] and "other side's response" in row["why"]


def test_a_prior_operator_draft_of_a_set_still_owed_is_a_revision(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    listing = json.loads((data / "manifest.json").read_text())
    listing.append(
        {
            "id": "o1",
            "name": "Discovery Responses - Responses to Special Interrogatories, Set One - 100001 - 11-01-26.docx",
        }
    )
    (data / "manifest.json").write_text(json.dumps(listing))
    assert stop_of(lambda: run(data, jd, Fake(THREE))) == "served_prior_draft"
    assert not (jd / served.FILE).exists()


def test_the_request_naming_one_kind_plans_only_that_kind(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    fake = Fake(THREE)
    run(data, jd, fake, request="Please draft responses to the special interrogatories on matter 100001.")
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["special_interrogatories"]
    assert len(fake.seen) == 1  # the kinds not asked for were never read


def test_a_set_our_client_propounded_is_not_served_on_us_and_is_never_read(tmp_path):
    asking = f"REQUESTING PARTY: Defendant {DEF}"
    ours = [p.replace(asking, "REQUESTING PARTY: Plaintiff GAMMA EXAMPLE") for p in srog(2)]
    data, jd = matter(tmp_path, [{"id": "o1", "name": "our srogs.pdf", "pages": ours}])
    fake = Fake({})
    assert stop_of(lambda: run(data, jd, fake)) == "served_none" and fake.seen == []


# ---- the stops ---------------------------------------------------------------------------------


def test_a_hand_served_json_is_left_alone(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    (jd / served.FILE).write_text('{"sets": "SMD\'s own"}')
    fake = Fake(THREE)
    assert run(data, jd, fake) is None
    assert (jd / served.FILE).read_text() == '{"sets": "SMD\'s own"}' and fake.seen == []
    assert not (jd / served_find.REPORT).exists()


def test_no_candidate_stops_served_none_and_spends_nothing(tmp_path):
    data, jd = matter(
        tmp_path, [{"id": "m1", "name": "ER record.pdf", "pages": ["Exampletown ER. Patient seen 01/15/2026."]}]
    )
    fake = Fake({})
    assert stop_of(lambda: run(data, jd, fake)) == "served_none"
    assert fake.seen == [] and json.loads((jd / served_find.REPORT).read_text())["reads"] == 0


def test_a_no_text_discovery_file_stops_served_scan_only_in_a_discovery_folder_with_nothing_found(tmp_path):
    scan = {"id": "x1", "name": "Def_SROGs-Set_One.pdf", "pages": [""], "folder": "/Discovery"}
    data, jd = matter(tmp_path, [scan])
    assert stop_of(lambda: run(data, jd, Fake({}))) == "served_scan"
    # The same name in a provider folder: listed only, never a stop by itself (measured: 14 false of 14).
    data2, jd2 = matter(tmp_path / "b", [{**scan, "folder": "/Medical/Lien Requests"}])
    assert stop_of(lambda: run(data2, jd2, Fake({}))) == "served_none"
    # And beside a set read from text, it is listed and the job goes on.
    data3, jd3 = matter(tmp_path / "c", [scan, {"id": "s1", "name": "s.pdf", "pages": srog(2)}])
    run(data3, jd3, Fake({"SPECIAL": values("special_interrogatories", 2)}))
    assert any(r["kind"] == "scan" for r in json.loads((jd3 / served_find.REPORT).read_text())["candidates"])


def test_two_different_sets_of_one_type_and_number_stop_served_ambiguous(tmp_path):
    other = srog(3)
    data, jd = matter(
        tmp_path, [{"id": "s1", "name": "a.pdf", "pages": srog(22)}, {"id": "s2", "name": "b.pdf", "pages": other}]
    )
    fake = Fake(
        {
            "State example fact 22.": values("special_interrogatories", 22),
            "SPECIAL": values("special_interrogatories", 3),
        }
    )
    assert stop_of(lambda: run(data, jd, fake)) == "served_ambiguous"
    assert not (jd / served.FILE).exists()


@pytest.mark.parametrize(
    "over",
    [
        {"propounding_party": "Defendant OMEGA EXAMPLE LLC"},  # a party not on the page
        {"set_number": 2},  # on no SET line
        {"set_type": "requests_for_admission"},  # not the title code read
        {"responding_party": f"Defendant {DEF}"},  # on the page, but not our client
        {"date_served": "2026-09-09"},  # a date not on the page
    ],
)
def test_a_read_value_code_cannot_confirm_goes_to_smd_never_trusted(tmp_path, over):
    data, jd = matter(tmp_path, [{"id": "s1", "name": "s.pdf", "pages": srog(4)}])
    assert (
        stop_of(lambda: run(data, jd, Fake({"SPECIAL": {**values("special_interrogatories", 4), **over}})))
        == "served_ambiguous"
    )
    assert not (jd / served.FILE).exists()


def test_a_count_off_the_numbered_requests_stops_served_unnumbered(tmp_path):
    data, jd = matter(tmp_path, [{"id": "s1", "name": "s.pdf", "pages": srog(4)}])
    assert (
        stop_of(lambda: run(data, jd, Fake({"SPECIAL": values("special_interrogatories", 5)}))) == "served_unnumbered"
    )
    gap = [srog(4)[0], srog(4)[1].replace("3. State", "5. State"), END]
    data2, jd2 = matter(tmp_path / "b", [{"id": "s1", "name": "s.pdf", "pages": gap}])
    assert (
        stop_of(lambda: run(data2, jd2, Fake({"SPECIAL": values("special_interrogatories", 4)}))) == "served_unnumbered"
    )


def test_more_than_the_finder_reads_stops_served_many_before_any_read(tmp_path):
    # Thirteen DIFFERENT papers (each its own request count): copies alike would fold into one.
    docs = [{"id": f"s{i}", "name": f"{i}.pdf", "pages": srog(2 + i)} for i in range(served_find.FIND_MAX + 1)]
    data, jd = matter(tmp_path, docs)
    fake = Fake({"SPECIAL": values("special_interrogatories", 2)})
    assert stop_of(lambda: run(data, jd, fake)) == "served_many" and fake.seen == []


def test_the_dollar_cap_holds_before_the_call_that_would_pass_it(tmp_path):
    data, jd = matter(tmp_path, three_docs())
    fake = Fake(THREE, cost=0.20)  # two reads fit under 0.50; the third would pass it
    assert stop_of(lambda: run(data, jd, fake)) == "served_many"
    assert len(fake.seen) == 2 and fake.total <= served_find.FIND_CAP_USD


def test_a_set_of_another_kind_stops_served_other_set_before_any_read(tmp_path):
    pages = [head("NOTICE OF INSPECTION OF LAND"), "1. Example premises.\n", END]
    data, jd = matter(tmp_path, [{"id": "o1", "name": "o.pdf", "pages": pages}])
    fake = Fake({})
    assert stop_of(lambda: run(data, jd, fake)) == "served_other_set" and fake.seen == []


def test_every_finder_stop_is_a_reason_code_and_a_plan_stop_with_a_next_step():
    from jobs import job_notes

    assert set(served.FIND_STOPS) <= set(outcome.REASON_CODES) and set(served.FIND_STOPS) <= set(served.PLAN_STOPS)
    assert set(served.FIND_STOPS) <= set(job_notes.DRAFTING_NEXT)


# ---- the 2026-11-20 replay's shapes (every value invented; each shape as the real papers print it) --------


def test_a_request_number_alone_on_its_line_with_the_text_below_slices_and_numbers(tmp_path):
    """L1: "15." alone, the text on the next line; a split citation ("4.1.") never counts."""
    items = "".join(
        f"{i}.\nState example fact {i}"
        + ("\nunder paragraph\n4.1.\nof the example agreement" if i == 2 else "")
        + ".\n"
        for i in range(1, 6)
    )
    pages = [head("SPECIAL INTERROGATORIES"), "DEFINITIONS\n1. YOU means the responding party.\n"
             f"SPECIAL INTERROGATORIES\n{items}", END]  # fmt: skip
    data, jd = matter(tmp_path, [{"id": "s1", "name": "s.pdf", "pages": pages}])
    run(data, jd, Fake({"SPECIAL": values("special_interrogatories", 5)}))
    (s,) = served.read(jd, [CLIENT])
    assert served.numbering(s.request_text) == [1, 2, 3, 4, 5]
    assert "under paragraph 4.1. of the example agreement" in served.numbered(s.request_text)[2]


def _below(pages: list[str]) -> list[str]:
    """The SET line's value one and two lines BELOW its label (the real text sets)."""
    return [p.replace("SET NO.: ONE\n", "SET NO.:\n\nONE\n") for p in pages]


def test_a_set_number_printed_below_its_label_is_read_for_the_check_and_the_pairing(tmp_path):
    """L2: "SET NO.:" with ONE on a line below, on the served set and on our response paper."""
    resp = _below(response("SPECIAL INTERROGATORIES"))
    assert "SET NO.:\n\nONE" in resp[0]
    docs = [{"id": "s1", "name": "s.pdf", "pages": _below(srog(3))},
            {"id": "p1", "name": "p.pdf", "pages": _below(rfp(2))},
            {"id": "r1", "name": "r.pdf", "pages": resp, "created": "2026-10-30T09:00:00"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    fake = Fake({"SPECIAL INTERROGATORIES, SET": values("special_interrogatories", 3),
                 "DEMAND FOR PRODUCTION": values("requests_for_production", 2)})  # fmt: skip
    run(data, jd, fake)
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["requests_for_production"]
    assert served_find.set_numbers("SET NUMBER:\n  2\n") == [2]


def test_our_word_response_paper_with_its_caption_table_last_is_a_response_not_a_served_set(tmp_path):
    """L3: Word text puts the caption table (and its title) AFTER the body paragraphs."""
    body = (f"PROPOUNDING PARTY: | Defendant {DEF}\nRESPONDING PARTY: | Plaintiff GAMMA EXAMPLE\nSET NO.: | ONE\n"
            "PRELIMINARY STATEMENT\nRESPONSE TO SPECIAL INTERROGATORY NO. 1:\nGamma Example.\n"
            "Dated: October 30, 2026\nBy: /s/ Alpha Example\nAttorneys for Plaintiff\n"
            "ALPHA EXAMPLE (SBN 000001) | \nGAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE CORPORATION | Case No. 30-0001\n"
            "PLAINTIFF GAMMA EXAMPLE'S RESPONSES TO SPECIAL INTERROGATORIES, SET ONE")  # fmt: skip
    word = {"id": "w1", "name": "Resp SROGs.docx", "ext": ".docx", "text": body, "created": "2026-10-30T09:00:00"}
    data, jd = matter(tmp_path, [*three_docs(), word])
    fake = Fake(THREE)
    run(data, jd, fake)
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["requests_for_production", "form_interrogatories"]
    assert len(fake.seen) == 3  # the Word response paper was never read as a served set
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "w1")
    assert row["kept"] and "answers Responses to Special Interrogatories, Set One" in row["why"]


def test_an_emailed_copy_with_other_bytes_folds_into_the_filed_set_before_any_read(tmp_path):
    """L4: same type, set and asking party, different sha256: one candidate, the discovery folder's kept."""
    docs = [{"id": "a1", "name": "Def SROGs.pdf", "pages": srog(4), "sha": "emailed", "kind": "attachment",
             "folder": "/Correspondence"},
            {"id": "s1", "name": "Def SROGs Set One.pdf", "pages": srog(4), "sha": "filed",
             "folder": "/DISCOVERY/DEF"}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    fake = Fake({"SPECIAL": values("special_interrogatories", 4)})
    run(data, jd, fake)
    assert len(fake.seen) == 1
    assert json.loads((jd / served.FILE).read_text())["sets"][0]["source"] == "s1"
    rows = {r["id"]: r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"]}
    assert rows["s1"]["copies"] == ["/Correspondence/Def SROGs.pdf"] and "a copy of" in rows["a1"]["why"]


def test_the_running_footer_on_every_page_is_not_inside_a_request_that_crosses_a_page(tmp_path):
    """L6: the paper's title in capitals repeats at each page foot."""
    foot = "DEFENDANT DELTA EXAMPLE CORPORATION'S SPECIAL INTERROGATORIES TO PLAINTIFF\n"
    pages = [head("SPECIAL INTERROGATORIES") + "SPECIAL INTERROGATORIES\n1. State example fact 1.\n2. State example\n",
             "fact 2 in full.\n3. State example\n", "fact 3 in full.\n4. State example fact 4.\n", END]  # fmt: skip
    data, jd = matter(tmp_path, [{"id": "s1", "name": "s.pdf", "pages": [p + foot for p in pages]}])
    run(data, jd, Fake({"SPECIAL": values("special_interrogatories", 4)}))
    (s,) = served.read(jd, [CLIENT])
    got = served.numbered(s.request_text)
    assert got[2] == "State example fact 2 in full." and got[3] == "State example fact 3 in full."
    assert "TO PLAINTIFF" not in s.request_text


# ---- the second $0 replay's shapes (2026-11-20; invented values) -----------------------------


def _two_forms(tmp_path: Path) -> list[dict]:
    paths = []
    for name in ("emailed.pdf", "filed.pdf"):
        p = tmp_path / name
        p.write_bytes(b"invented form bytes " + name.encode())
        paths.append(str(p))
    return [{"id": "fa", "name": "Form Rogs.pdf", "pages": form(), "sha": "emailed", "kind": "attachment",
             "folder": "/Correspondence", "path": paths[0]},
            {"id": "ff", "name": "Form Rogs Set One.pdf", "pages": form(), "sha": "filed", "folder": "/DISCOVERY/DEF",
             "path": paths[1]}]  # fmt: skip


def test_an_emailed_copy_of_a_served_form_folds_by_its_pixel_read_before_any_model_read(tmp_path, monkeypatch):
    """F1: two forms whose $0 pixel read agrees (checked numbers and revision) are one set."""
    from jobs.drafting.forms import read_stage

    read = {"checked": ["1.1", "6.4", "8.1"], "form_revision": "Rev. January 1, 2024"}
    monkeypatch.setattr(read_stage, "pixel_read", lambda path: dict(read))
    data, jd = matter(tmp_path, _two_forms(tmp_path))
    fake = Fake({"DISC-001": THREE["DISC-001"]})
    run(data, jd, fake)
    assert len(fake.seen) == 1
    assert json.loads((jd / served.FILE).read_text())["sets"][0]["source"] == "ff"
    rows = {r["id"]: r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"]}
    assert "a copy of" in rows["fa"]["why"]
    # Two forms whose boxes differ are two papers: both read, and one label stops served_ambiguous.
    marks = iter([read, {**read, "checked": ["1.1", "9.1"]}])
    monkeypatch.setattr(read_stage, "pixel_read", lambda path: next(marks))
    data2, jd2 = matter(tmp_path / "b", _two_forms(tmp_path))
    assert stop_of(lambda: run(data2, jd2, Fake({"DISC-001": THREE["DISC-001"]}))) == "served_ambiguous"


def test_a_form_set_our_client_propounded_is_dropped_never_served_on_us(tmp_path):
    """F2: the asking party is not in the form's text; the read names our client."""
    docs = [{"id": "f1", "name": "Pltf Form Rogs.pdf", "pages": form(), "folder": "/Discovery/Plaintiff"},
            {"id": "s1", "name": "s.pdf", "pages": srog(2)}]  # fmt: skip
    data, jd = matter(tmp_path, docs)
    ours_form = {**THREE["DISC-001"], "propounding_party": "Gamma Example", "responding_party": "Delta Example Corp"}
    run(data, jd, Fake({"DISC-001": ours_form, "SPECIAL": values("special_interrogatories", 2)}))
    assert [s.set_type for s in served.read(jd, [CLIENT])] == ["special_interrogatories"]
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "f1")
    assert not row["kept"] and "propounded by our client" in row["why"]


def test_a_form_whose_read_cannot_establish_our_client_answering_stops_never_defaults(tmp_path):
    """F2: a responding party that is not our client is never replaced by the record's."""
    data, jd = matter(tmp_path, [{"id": "f1", "name": "f.pdf", "pages": form()}])
    other = {**THREE["DISC-001"], "propounding_party": "Omega Example LLC", "responding_party": "Delta Example Corp"}
    assert stop_of(lambda: run(data, jd, Fake({"DISC-001": other}))) == "served_ambiguous"
    assert not (jd / served.FILE).exists()


def _word_response(rid: str, name: str, title: str, *, served_mark: str = "") -> dict:
    body = (f"PROPOUNDING PARTY: Defendant {DEF}\nRESPONDING PARTY: Plaintiff GAMMA EXAMPLE\nSET NO.: ONE\n"
            f"RESPONSE NO. 1:\nGamma Example.\n{served_mark}PLAINTIFF GAMMA EXAMPLE'S RESPONSES TO {title}, SET ONE")  # fmt: skip
    return {"id": rid, "name": name, "ext": ".docx", "text": body, "created": "2026-10-30T09:00:00"}


def test_an_unserved_response_paper_answers_nothing(tmp_path):
    """F3: a firm's Word response with no proof of service and no dated signature answers no set."""
    docs = [*three_docs(), _word_response("w1", "Resp SROGs v2.docx", "SPECIAL INTERROGATORIES")]
    data, jd = matter(tmp_path, docs)
    run(data, jd, Fake(THREE))
    assert len(served.read(jd, [CLIENT])) == 3
    row = next(r for r in json.loads((jd / served_find.REPORT).read_text())["candidates"] if r["id"] == "w1")
    assert not row["kept"] and "not served" in row["why"]


def test_a_draft_awaiting_attorney_review_never_answers_and_makes_the_job_a_revision(tmp_path):
    """F3: "DRAFT for attorney review - ..." is SMD's own draft: the set is still owed, as a revision."""
    name = "DRAFT for attorney review - Responses to Special Interrogatories, Set One - 100001.docx"
    stamped = "Dated: October 30, 2026\nBy: /s/ Alpha Example\n"  # a dated block does not make a draft served
    docs = [*three_docs(), _word_response("w1", name, "SPECIAL INTERROGATORIES", served_mark=stamped)]
    data, jd = matter(tmp_path, docs)
    assert stop_of(lambda: run(data, jd, Fake(THREE))) == "served_prior_draft"
    report = json.loads((jd / served_find.REPORT).read_text())
    assert name in report["reason"] and not (jd / served.FILE).exists()
