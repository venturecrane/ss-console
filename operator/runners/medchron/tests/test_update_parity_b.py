"""Parity fixes 17 (document level) and 22 from the offline run of the update
lane (2026-10-13): each test fails on the code before the fix (f534d279) and
passes after. Every value is invented."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
from medchron.update import body as B, collect as C

VISIT = (
    "Example Orthopedics progress note. Date of service {d}. The patient returns for follow up of neck pain "
    "after the collision. Examination showed {finding}. Pain rated {pain} of 10. Plan: continue therapy "
    "and return in {weeks} weeks for a recheck of the cervical spine."
)
FINDINGS = ["paraspinal spasm", "reduced rotation", "trapezius tenderness", "guarded motion", "full strength"]


def _visit(i: int) -> str:
    return VISIT.format(d=f"03/{i + 1:02d}/2026", finding=FINDINGS[i % 5], pain=2 + i % 7, weeks=1 + i % 4)


def _whole_pages() -> list[str]:
    """Twelve pages, each a distinct visit, footed with the filing's page numbers."""
    return [_visit(i) + f"\nPage {281 + i}" for i in range(12)]


def _extract_pages() -> list[str]:
    """Pages 5 to 9 of the whole record, re-filed alone and renumbered 1 to 5."""
    return [_visit(i) + f"\nPage {i - 3}" for i in range(4, 9)]


def _template_pages() -> list[str]:
    """Other visits on the same template: same words, other dates of service."""
    return [VISIT.format(d=f"04/{i + 10:02d}/2026", finding=FINDINGS[i % 5], pain=2 + i % 7, weeks=1 + i % 4)
            for i in range(4, 9)]  # fmt: skip


# ---- fix 17: a re-filed extract of another new document is not an exhibit ----------
def _seed(tmp: Path, docs: dict[str, list[str]]) -> SimpleNamespace:
    from medchron_testkit import make_pdf

    ud = tmp / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": [], "top_exhibit": 1}))
    (tmp / "text").mkdir()
    (tmp / "raw").mkdir()
    rows = []
    for rid, pages in docs.items():
        pdf = tmp / "raw" / f"{rid}.pdf"
        pdf.write_bytes(make_pdf(pages))
        (tmp / "text" / f"{rid}.txt").write_text("".join(f"[p.{n}]\n{t}\n" for n, t in enumerate(pages, 1)))
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        rows.append({"id": rid, "ok": True, "path": str(pdf), "sha256": sha, "ext": ".pdf", "name": f"{rid} records"})
    (tmp / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    log: list[str] = []
    return SimpleNamespace(
        slug_dir=tmp,
        unit=SimpleNamespace(unit="alpha"),
        job=SimpleNamespace(units=[SimpleNamespace(surname="Example", dob="")]),
        log=log.append,
        logged=log,
    )


def test_a_refiled_extract_of_a_new_document_is_not_an_exhibit_and_is_recorded(tmp_path: Path) -> None:
    from medchron.stages import update_select as US

    # The extract arrives FIRST in pull order: the whole one is still the exhibit.
    sr = _seed(tmp_path, {"ext": _extract_pages(), "whole": _whole_pages(), "tmpl": _template_pages()})
    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    sel = json.loads((tmp_path / "runs" / "alpha" / "update" / "selection.json").read_text())
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert set(by_id) == {"whole", "tmpl"}  # template visits on other dates are never an extract
    assert by_id["whole"]["read"] == list(range(1, 13))  # every page of the whole record is read
    assert by_id["tmpl"]["read"] == [1, 2, 3, 4, 5]
    assert sel["subsets"] == [
        {
            "id": "ext",
            "source_name": "ext records.pdf",
            "pages": 5,
            "matched": 5,
            "subset_of": {"id": "whole", "exhibit": by_id["whole"]["exhibit"], "title": by_id["whole"]["title"]},
        }
    ]


def test_an_extract_with_a_page_stating_its_own_value_is_read() -> None:
    from medchron.update import subsets as SUB

    whole = dict(enumerate(_whole_pages(), 1))
    ext = dict(enumerate(_extract_pages(), 1))
    assert set(SUB.refiled_subsets([("ext", ext), ("whole", whole)])) == {"ext"}
    # Ten pages, nine of them the whole one's, one a visit of its own with a value it alone states.
    nine = {n: _visit(n - 1) for n in range(1, 10)}
    own = {**nine, 10: _visit(0).replace("03/01/2026", "05/20/2026").replace("Pain rated 2", "Pain rated 9")}
    assert SUB.refiled_subsets([("own", own), ("whole", whole)]) == {}
    # A whole copy of the same size stays with select.Earlier (read once, its copy excluded page by page).
    assert SUB.refiled_subsets([("a", whole), ("b", whole)]) == {}
    # A firm-numbered file and a document with an unread page are never extracts.
    assert SUB.refiled_subsets([("ext", ext), ("whole", whole)], firm={"ext"}) == {}
    assert SUB.refiled_subsets([("ext", {**ext, 6: "[page transcription failed]"}), ("whole", whole)]) == {}


# ---- fix 22: a sentence an entry states twice is said once -------------------------
PROC = "Arthroscopic partial meniscectomy of the right knee was performed without complication."
PATH = "Pathology showed degenerative fibrocartilage with no malignancy identified."


def test_a_sentence_an_entry_states_twice_is_said_once_with_its_pages_joined() -> None:
    e = (
        "05/12/2026\nExample Surgery Center | Procedures\n\nProcedures\n"
        f"{PROC} {PATH} (Exhibit 2 - p. 3)\n\n"
        "All Other Information\n"
        f"{PROC} (Exhibit 2 - p. 7) {PATH} (Exhibit 2 - p. 3)\n"
        "Discharged home the same day with a knee brace. (Exhibit 2 - p. 8)"
    )
    _, base = B.entries_of(K.base_md())
    kept, rep = C.collect([e], base, K.INCIDENT, str)
    out = kept[0]
    assert out.count("meniscectomy") == 1 and out.count("fibrocartilage") == 1
    assert "(Exhibit 2 - p. 3, 7)" in out  # the repeat's page joins the first citation
    assert "Discharged home the same day with a knee brace. (Exhibit 2 - p. 8)" in out
    assert rep["removed: a sentence an entry stated twice"] == 2


def test_a_heading_left_empty_goes_and_a_short_or_differing_sentence_stays() -> None:
    from medchron.update import within as W

    e = (
        "05/12/2026\nExample Surgery Center | Procedures\n\nProcedures\n"
        f"{PROC} Normal. (Exhibit 2 - p. 3)\n\n"
        f"Surgical Notes\n{PROC} (Exhibit 2 - p. 3)\n\n"
        "All Other Information\nNormal. Pain rated 3 of 10 after the procedure in recovery. (Exhibit 2 - p. 4)\n"
        "Pain rated 4 of 10 after the procedure in recovery. (Exhibit 2 - p. 5)"
    )
    out, n = W.dedupe(e)
    assert n == 1 and "Surgical Notes" not in out and out.count("meniscectomy") == 1
    assert out.count("Normal.") == 2  # a short line means something under each heading
    assert "Pain rated 3 of 10" in out and "Pain rated 4 of 10" in out  # a value differs: both stay
    assert W.dedupe(out) == (out, 0)
