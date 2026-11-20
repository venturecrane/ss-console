"""The update review and the sentence check read page TEXT where it can be
trusted, and only the pages they need (2026-11-18).

Measured basis: a real 509-page update's review cost $46.48, nearly all of it
page pictures (a picture is about three times its text; 493 of the 509 pages
carried a usable text layer), plus delivered entries re-sent in every part,
finisher requests carrying every page their entries cite, and a re-check
re-reading whole exhibits. Each test below fails on the code before the
change. Every name, date and value is invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


import update_testkit as K
from jobs.medchron import page_mode as PM
from jobs.medchron.audit import claims as CL
from jobs.medchron.audit.run import AuditPaths, Round
from jobs.medchron.update import review_pages as RV, review_parts as RP, review_repair as RR
from test_audit import SUPPORTED, Scripted, _sr, tool_msg

PROSE = (
    "The patient returned for follow up of neck and low back pain after the collision.\n"
    "Range of motion remains reduced and home exercises were reviewed in detail today.\n"
    "The plan is to continue therapy twice weekly and reassess at the next visit.\n"
)
GRID = PROSE + "Row Name 1630 1615 1601\nPain 6 -BT 5 -BT 4 -BT\nSedation 2 -BT 2 -BT 1 -BT\n"
RESULT = PROSE + "Hepatitis C antibody reactive, confirmatory testing ordered.\n"
#: (pdf text, text-file marker suffix, a checkbox widget on the page)
PAGES = [
    (PROSE, "", False),  # 1: native text -> TEXT
    ("Fax cover sheet.", "", False),  # 2: short -> picture
    (GRID, "", False),  # 3: a flowsheet time grid -> picture
    (RESULT, "", False),  # 4: states a result -> picture
    (PROSE, " machine transcription", False),  # 5: a vision transcript -> picture
    (PROSE, "", True),  # 6: a form (a widget) -> picture
]
REASONS = ["native", "short", "grid", "result", "vision", "form"]


def _pdf(pages: list[tuple[str, str, bool]]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text, _mark, form in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=10)
        if form:
            w = pymupdf.Widget()
            w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
            w.field_name = "seen"
            w.rect = pymupdf.Rect(72, 600, 90, 618)
            page.add_widget(w)
    data = doc.tobytes()
    doc.close()
    return data


def _matter(slug: Path, pages: list[tuple[str, str, bool]] = PAGES, ex: int = 2) -> Path:
    """One new exhibit with the page map, units, extract row and text a page
    index reads, as ``update_collect`` leaves them."""
    out = slug / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    (slug / "units").mkdir(parents=True, exist_ok=True)
    (slug / "text").mkdir(parents=True, exist_ok=True)
    (out / f"Exhibit {ex} - Sample Clinic - 08-27-2026 (Medical Records).pdf").write_bytes(_pdf(pages))
    (slug / "units" / "alpha.json").write_text(json.dumps([{"id": "f2", "name": "new record", "ext": ".pdf"}]))
    (slug / "extracted.jsonl").write_text(json.dumps({"id": "f2", "name": "new record", "pages": len(pages)}) + "\n")
    (slug / "text" / "f2.txt").write_text("".join(f"[p.{i}]{m}\n{t}\n" for i, (t, m, _f) in enumerate(pages, 1)))
    files = [{"file": "new record.pdf", "start_page": 1, "pages": len(pages)}]
    (out / "page_map.json").write_text(json.dumps([{"exhibit": ex, "total_pages": len(pages), "files": files}]))
    return out


ENTRY = (
    "08/27/2026\nSample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain was reported at the follow up visit after the collision. (Exhibit 2 - p. 1)\n\n"
    "All Other Information\nTherapy twice weekly was planned at the visit. (Exhibit 2 - p. 6)"
)


def _doc(entries: list[str]) -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )


def _image(ex: int, p: int) -> dict[str, Any]:
    return {"type": "image", "page": f"{ex}:{p}"}


def _shown(content: list[dict[str, Any]]) -> dict[int, str]:
    """{page: "text" | "image"} for every page a request shows, by its label."""
    out: dict[int, str] = {}
    for i, b in enumerate(content):
        m = re.match(r"Exhibit \d+ p\. (\d+)", str(b.get("text", "")))
        if m and i + 1 < len(content):
            nxt = content[i + 1]
            out[int(m.group(1))] = "image" if nxt.get("type") == "image" else "text"
    return out


# A1 -------------------------------------------------------------------------------
def test_each_page_is_read_as_text_only_when_its_text_can_be_trusted(tmp_path: Path) -> None:
    """Fails if a flagged or machine-transcribed page is sent as text, or a
    native text-layer page as a picture."""
    _matter(tmp_path)
    modes = PM.Modes.open(tmp_path, "alpha")
    got = [modes(2, p) for p in range(1, len(PAGES) + 1)]
    assert [m.reason for m in got] == REASONS
    assert [m.kind for m in got] == [PM.TEXT] + [PM.IMAGE] * 5
    assert got[0].text and "follow up" in got[0].text
    modes.close()
    # No page index (no page map): every page a picture, as before the rule.
    assert PM.Modes.open(tmp_path / "nowhere", "alpha")(2, 1).kind == PM.IMAGE


# A2 -------------------------------------------------------------------------------
def test_a_review_part_shows_a_text_page_as_its_labelled_text_and_the_rest_as_pictures(tmp_path: Path) -> None:
    _matter(tmp_path)
    ents = RR.anchor_entries(_doc([ENTRY]), {2}, K.HEADINGS)
    part = RP.Part("pages", ents, 2, list(range(1, 7)))
    modes = PM.Modes.open(tmp_path, "alpha")
    content = RP.review_content(part, "CLIENT", _image, ents, [], None, modes)
    assert _shown(content) == {1: "text", 2: "image", 3: "image", 4: "image", 5: "image", 6: "image"}
    i = next(i for i, b in enumerate(content) if b.get("text", "").startswith("Exhibit 2 p. 1 (cited by N1):"))
    assert content[i + 1]["text"].startswith(RV.PAGE_TEXT) and "follow up" in content[i + 1]["text"]
    assert RV.page_reads(content) == {"text_pages": 1, "image_pages": 5, "page_chars": len(modes(2, 1).text or "")}
    # Without the rule (the old path), every page is a picture.
    assert set(_shown(RP.review_content(part, "CLIENT", _image, ents, [], None, None)).values()) == {"image"}
    modes.close()


# A3 -------------------------------------------------------------------------------
NEAR = "08/26/2026\nSample Clinic | Medical Diagnoses\n\nMedical Diagnoses\nA visit the day before. (Exhibit 1 - p. 3)"


def test_nearby_entries_lead_the_request_and_are_cache_marked_only_when_parts_share_them() -> None:
    """Fails if the delivered entries a round repeats across parts are not
    cache-marked, or if a block only one part carries is marked (a pure
    write, ``llm``)."""
    long_ = ENTRY.replace("(Exhibit 2 - p. 6)", "(Exhibit 2 - p. 30)")
    ents = RR.anchor_entries(_doc([long_]), {2}, K.HEADINGS)
    parts = RP.plan(ents, {2}, lambda _x: 50)
    assert [p.pages[0] for p in parts] == [1, 21, 41]  # one entry carries all three windows
    repeats = RP.repeated_contexts(parts, ents, [NEAR])
    contents = [RP.review_content(p, "CLIENT", _image, ents, [NEAR]) for p in parts]
    assert all(c[1]["text"].startswith("NEARBY ENTRIES") and "=== D1 (DELIVERED) ===" in c[1]["text"] for c in contents)
    assert [RP.cache_blocks(c, repeats) for c in contents] == [("system", "user:1")] * 3
    one = RP.plan(ents, {2}, lambda _x: 10)
    assert RP.cache_blocks(
        RP.review_content(one[0], "CLIENT", _image, ents, [NEAR]), RP.repeated_contexts(one, ents, [NEAR])
    ) == ("system",)


# A4 -------------------------------------------------------------------------------
def _finding(page: int, anchor: str = "N1.c1") -> RR.Finding:
    return RR.Finding(RR.MUST, "1 wrong value", anchor, 2, page, "the value differs", "", "", "")


def test_a_repair_request_carries_only_the_pages_its_findings_name_and_one_either_side(tmp_path: Path) -> None:
    """Fails if the finisher is shown a page no finding names (beyond one
    either side): before, it was also shown every page its entries cite."""
    _matter(tmp_path)
    ents = RR.anchor_entries(_doc([ENTRY.replace("p. 6)", "p. 9)")]), {2}, K.HEADINGS)
    modes = PM.Modes.open(tmp_path, "alpha")
    content = RP.finish_content(ents, [_finding(4)], "CLIENT", _image, ents, [], modes)
    assert _shown(content) == {3: "image", 4: "image", 5: "image"}  # not p. 1 or p. 9, which N1 cites
    content = RP.finish_content(ents, [_finding(2)], "CLIENT", _image, ents, [], modes)
    assert _shown(content) == {1: "text", 2: "image", 3: "image"}  # the text page by the same rule
    modes.close()


def test_a_finding_that_names_no_page_still_shows_the_pages_its_entry_cites(tmp_path: Path) -> None:
    """Fails if a finding with no page reaches the finisher with nothing to
    read: it could then only be declined, and would stay open to a hold."""
    _matter(tmp_path)
    ents = RR.anchor_entries(_doc([ENTRY.replace("p. 6)", "p. 9)")]), {2}, K.HEADINGS)
    modes = PM.Modes.open(tmp_path, "alpha")
    nameless = RR.Finding(RR.MUST, "2 omitted content", "N1", None, None, "the plan is missing", "", "", "")
    shown = _shown(RP.finish_content(ents, [nameless], "CLIENT", _image, ents, [], modes))
    assert set(shown) == {p for _x, p in ents[0].cites()} and shown  # every page N1 cites, and not none
    other = RR.Finding(RR.MUST, "2 omitted content", "N11", None, None, "another entry", "", "", "")
    assert _shown(RP.finish_content(ents, [other], "CLIENT", _image, ents, [], modes)) == {}  # N11 is not N1
    modes.close()


# A5 -------------------------------------------------------------------------------
# A6 -------------------------------------------------------------------------------
# B --------------------------------------------------------------------------------
def _audit_doc(sr: Any, claims: list[tuple[str, int]]) -> AuditPaths:
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(
        f"08/2{i}/2026\nSample Clinic | Medical Diagnoses\n\n{c} (Exhibit 2 - p. {p})"
        for i, (c, p) in enumerate(claims)
    )
    (rd / "final-chronology.md").write_text("# Chronology\n## Medical Chronology\n\n" + body + "\n## Exhibit List\n")
    return AuditPaths(sr.slug_dir, "alpha")


def _reply(p: dict, _n: int) -> Any:
    tools = p.get("tools") or []
    if tools and "supporting_pages" in tools[0]["input_schema"]["properties"]:
        return tool_msg({**SUPPORTED, "supporting_pages": [1]})
    return tool_msg(SUPPORTED)


CLAIMS = [
    ("Neck and low back pain was followed up after the collision.", 1),
    ("Pain scores were charted through the afternoon.", 3),
    ("A hepatitis panel was drawn and further testing ordered.", 4),
]


def test_the_sentence_check_never_reads_a_flagged_page_as_text(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Fails if a sentence citing a page the one rule shows as a picture (a
    grid, a stated result) is audited on the text path: before, both pages
    were native text and went to the text window."""
    client = Scripted(_reply)
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _matter(sr.slug_dir)
    paths = _audit_doc(sr, CLAIMS)
    assert Round(sr.doorway, "claude-sonnet-5", paths, lambda _m: None, mode="text", workers=1).execute() == 0
    modes = {r["pages"][0]: r["mode"] for r in CL.read_rows(paths.results) if r["kind"] == "real"}
    assert modes == {1: "text", 3: "image", 4: "image"}


def test_switching_a_job_from_image_to_text_mode_neither_trips_the_guard_nor_rebills(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A firm switched to ``audit_mode: text`` with audit rows already saved
    in image mode: the claim keys do not depend on the mode, so the resumed
    round makes no call and the double-sweep guard (exit 3) stays quiet."""
    client = Scripted(_reply)
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _matter(sr.slug_dir)
    paths = _audit_doc(sr, CLAIMS)
    assert Round(sr.doorway, "claude-sonnet-5", paths, lambda _m: None, mode="image", workers=1).execute() == 0
    paid = len(client.calls)
    assert paid == len(CLAIMS)
    log: list[str] = []
    assert Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="text", workers=1).execute() == 0
    assert len(client.calls) == paid, "a sentence graded in image mode was billed again in text mode"
    assert not any("DOUBLE-SWEEP" in line for line in log)


# the $0 measuring tool ------------------------------------------------------------
