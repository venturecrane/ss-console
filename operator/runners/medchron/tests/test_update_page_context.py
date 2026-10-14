"""Fixes 21 and 26 of the update lane's offline review (2026-10-13).

21: a negative serology was composed POSITIVE because its "No" sat at the
foot of one page and the result at the head of the next, and compose saw only
the next page. Each page now reaches compose with its unread neighbour's foot
or head as delimited context (update lane: ``update/select.selected_text``;
base lane: a file split into parts, ``stages/chunking.build_chunks``), and a
claim stating a positive result its pages or their neighbours put a negation
beside is re-checked on the images (``audit/negation.py``).

26: a patient's street address and phone number reached added entries.
Composed entries are scrubbed of both (``update/demographics.py``), the
provider line kept as composed, the way the base lane writes it.

Every fixture is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

from test_compose_stages import MAP_OUT, Scripted, _msg, _sr, _unit_files, firm_headings  # noqa: F401 - firm_headings is a pytest fixture used by name

from medchron.audit import negation as NG
from medchron.audit.run import Round
from medchron.stages import chunking, compose as compose_stage
from medchron.update import collect as C, demographics as D, select as S

PAGE_1 = (
    "EXAMPLE LABORATORY  Serology panel\n"
    "Collected 02/03/2026\n"
    "Hepatitis B surface antigen ............ Negative\n"
    "Hepatitis C antibody, reactive?\n"
    "No"
)
PAGE_2 = (
    "Hepatitis C antibody result reported to ordering provider.\n"
    "HIV 1/2 antigen and antibody: Nonreactive\n"
    "Ordering provider: Example Clinic"
)


# ---- 21: compose sees the neighbouring page --------------------------------------
def test_update_compose_receives_the_unread_previous_page_as_context(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    """Page 1 is excluded (the delivery read it); page 2 is read. The model
    must receive page 1's foot, delimited as context, before page 2."""
    text = S.selected_text({1: PAGE_1, 2: PAGE_2}, [2])

    def reply(params: dict, n: int):
        content = params["messages"][0]["content"]
        assert "[context, the foot of the page before, p.1:" in content
        ctx = content.split("[context, the foot of the page before, p.1:", 1)[1].split("[end context]", 1)[0]
        assert "Hepatitis C antibody, reactive?\nNo" in ctx
        assert content.index("[end context]") < content.index("[p.2]")
        return _msg(MAP_OUT)

    client = Scripted(reply)
    sr = _sr(job_dir, firm_headings, data_root, client)
    _unit_files(sr, {"serology": text})
    assert compose_stage.run(sr) == 0
    assert len(client.calls) == 1


def test_context_stays_outside_the_page_it_does_not_belong_to() -> None:
    """A citation still names the page the result is printed on: page 2's own
    segment is page 2's text, and the context labels its own page."""
    text = S.selected_text({1: PAGE_1, 2: PAGE_2, 3: "Page three head line\nmore"}, [2])
    pages = S.pages_of_text(text)
    assert list(pages) == [2]
    assert pages[2].startswith(PAGE_2)
    assert "[context, the head of the page after, p.3:" in pages[2]
    # neighbours both read: no context, the pages are already side by side
    both = S.selected_text({1: PAGE_1, 2: PAGE_2}, [1, 2])
    assert "[context" not in both and both == f"[p.1]\n{PAGE_1}\n[p.2]\n{PAGE_2}"


def test_context_is_bounded_to_fifteen_lines() -> None:
    long_page = "\n".join(f"line {i}" for i in range(1, 41))
    text = S.selected_text({1: long_page, 2: PAGE_2}, [2])
    ctx = text.split("[end context]")[0]
    assert "line 26" in ctx and "line 25\n" not in ctx and "line 40" in ctx


def test_base_lane_part_split_carries_the_neighbouring_page(tmp_path: Path) -> None:
    """A large file split into parts: part 2 opens with the foot of part 1's
    last page, part 1 ends with the head of part 2's first page."""
    big = tmp_path / "big.txt"
    big.write_text("".join(f"[p.{n}]\n" + "y" * 400 + f"\nfoot of page {n}\n" for n in range(1, 8)))
    parts = chunking.build_chunks([{"id": "b", "name": "big", "ext": ".pdf", "text_path": str(big)}], chunk=1000)
    assert len(parts) >= 2
    second = parts[1].split("\n", 1)[1]  # after the FILE header
    assert second.startswith("[context, the foot of the page before, p.")
    n = int(second.split("p.", 1)[1].split(":", 1)[0])
    assert f"foot of page {n}" in second.split("[end context]", 1)[0]
    assert "[context, the head of the page after, p." in parts[0]


def test_map_prompt_names_the_context_and_demographics_rules() -> None:
    text = (Path(compose_stage.__file__).parent.parent / "prompts" / "map-system.md").read_text()
    assert "PAGE CONTEXT" in text and "Never cite a context block" in text
    assert "home street address" in text


# ---- 21: the deterministic guard ---------------------------------------------------
def _claim(text: str, pages: list[int], npages: int = 3) -> dict:
    return {"exhibit": 7, "claim": text, "pages": pages, "npages": npages, "key": "k", "page_spec": "2"}


def test_positive_claim_with_a_negation_on_the_previous_page_is_flagged() -> None:
    pages = {1: PAGE_1, 2: PAGE_2, 3: "Discharge instructions given."}
    c = _claim("Hepatitis C antibody was positive. (Exhibit 7 - p. 2)", [2])
    f = NG.flag(c, lambda ex, p: pages.get(p))
    assert f is not None and f["adjacent"] == [1, 3] and f["word"] in ("Hepatitis", "antibody")
    assert "p.1" in NG.note(f)
    # the same claim without the negating page is not flagged
    clean = {1: "Hepatitis C antibody, reactive?\nYes", 2: PAGE_2.replace("Nonreactive", "see below"), 3: "x"}
    assert NG.flag(c, lambda ex, p: clean.get(p)) is None
    # a negative claim is never flagged
    assert (
        NG.flag(_claim("Hepatitis C antibody was not detected. (Exhibit 7 - p. 2)", [2]), lambda ex, p: pages.get(p))
        is None
    )


def test_flagged_claim_goes_to_the_image_audit_with_its_neighbour() -> None:
    pages = {1: PAGE_1, 2: PAGE_2, 3: "Discharge instructions given."}
    index = NS(
        npages=lambda ex: 3,
        resolve=lambda ex, p: ("f", p),
        segments=lambda fid: {p: (t, False) for p, t in pages.items()},
        classify=lambda ex, p: ("native", pages[p]),
    )
    rnd = Round(doorway=None, model="m", paths=NS(), log=lambda s: None)  # type: ignore[arg-type]
    c = _claim("Hepatitis C antibody was positive. (Exhibit 7 - p. 2)", [2])
    clusters, image, _ = rnd.plan_text([c], index)  # type: ignore[arg-type]
    assert clusters == [] and image == [c]

    ev = NS(page=lambda ex, p: pages.get(p))
    fake = NS(_lock=rnd._lock, paths=NS())
    fake.__dict__["neg_evidence"] = ev
    blocks = NG.evidence(fake, c, {7: Path("x.pdf")}, lambda ex, p: {"type": "image", "page": p})
    assert blocks[0]["type"] == "text" and "RE-CHECK A POSITIVE RESULT" in blocks[0]["text"]
    assert [b["page"] for b in blocks[1:]] == [1, 3]


# ---- 26: no street address or phone number in a composed entry --------------------
ENTRY = (
    "03/04/2026\n"
    "Example Clinic, (555) 555-0100 | Patient Complaints & Limitations\n"
    "\n"
    "Patient Complaints & Limitations\n"
    "The patient, who resides at 1234 W Example Lane, Apt 5, Sampletown, AZ 85001, reported neck pain rated "
    "6 of 10 since the Subject Incident. Her home phone number is (555) 555-0199. (Exhibit 7 - p. 1)\n"
    "\n"
    "All Other Information\n"
    "Contact phone 555-555-0123. (Exhibit 7 - p. 1)\n"
    "\n"
    "Medical Diagnoses\n"
    "Cervical strain; blood pressure 132/86, weight 160 lbs. (Exhibit 7 - p. 2)"
)


def test_scrub_removes_the_patient_address_and_phone_and_keeps_the_clinical_text() -> None:
    out = D.scrub(ENTRY)
    assert "1234" not in out and "Example Lane" not in out and "85001" not in out
    assert "555-0199" not in out and "555-0123" not in out
    assert "The patient reported neck pain rated 6 of 10 since the Subject Incident. (Exhibit 7 - p. 1)" in out
    assert "All Other Information" not in out  # its only paragraph was a phone number
    assert "Cervical strain; blood pressure 132/86, weight 160 lbs. (Exhibit 7 - p. 2)" in out
    # the provider line is as composed: the base lane writes it as compose did
    assert out.splitlines()[1] == ENTRY.splitlines()[1]
    # an entry with neither is returned untouched
    plain = (
        "01/01/2026\nX | Medical Diagnoses\n\nMedical Diagnoses\nHgb 13.2 g/dL, follow up 2 weeks. (Exhibit 1 - p. 3)"
    )
    assert D.scrub(plain) == plain


def test_collect_reads_composed_entries_scrubbed(tmp_path: Path) -> None:
    """The scrub sits where every composed entry is read, so collect and
    proof 10 (no composed value lost) see the same text."""
    rd = tmp_path
    (rd / "exhibit_map.json").write_text(json.dumps({"Exhibit 7 - Example Clinic (Medical Records)": 1}))
    body = ENTRY.replace("(Exhibit 7 - p. 1)", "(Exhibit 1 - p. 1)").replace("(Exhibit 7 - p. 2)", "(Exhibit 1 - p. 2)")
    (rd / "entries.md").write_text("## Medical Chronology\n\n" + body + "\n")
    got = C.composed(rd)
    assert len(got) == 1 and "555-0199" not in got[0] and "1234 W Example Lane" not in got[0]
    assert "(Exhibit 7 - p. 1)" in got[0]
