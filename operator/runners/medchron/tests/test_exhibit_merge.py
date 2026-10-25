"""exhibit_merge (2026-10-25, the Pamplona job): two exhibits holding the same
pages are delivered once, renumbered and disclosed, with nothing invented.

The scene mirrors the real case: Exhibit 3 is Exhibit 2 page for page (one
Kaiser PDF saved twice), Exhibit 5 is Exhibit 4's pages in another order with
the same words drawn elsewhere on the page (two UC Davis exports), one page in
each UC Davis copy carries no text and pairs by its pixels. Every check is a
falsifier pair where it can be: a partial overlap, a citation the merge cannot
re-point, and a run of template pages each stop the merge.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import pymupdf
import pytest

from medchron import config as config_mod, dag, job as job_mod, redo_doc
from medchron.audit import claims as CL, coverage as audit_coverage
from medchron.audit.run import AuditPaths
from medchron.state import RunState, state_path
from medchron.stages import audit_loop, coverage, exhibit_merge as EM, exhibit_merge_io as IO, manifest
from medchron.stages.base import StageRefusal, StageRun
from medchron_testkit import FakeSeat, seed_folders

TITLES = {
    1: "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records)",
    2: "Exhibit 2 - Kaiser - 02-01-2026 (Medical Records)",
    3: "Exhibit 3 - Kaiser - 02-01-2026 (Medical Records)",
    4: "Exhibit 4 - UC Davis - 03-01-2026 (Medical Records)",
    5: "Exhibit 5 - UC Davis - 03-01-2026 (Medical Records)",
    6: "Exhibit 6 - Other Clinic - 04-01-2026 (Medical Records)",
}
FILES = {1: "clinic", 2: "kaiser er", 3: "kaiser er copy", 4: "ucd export", 5: "ucd export 2", 6: "other"}


def T(tag: str, n: int) -> str:
    return f"{tag} record page {n}: the patient was seen and examined in detail on this visit."


def pdf(pages: list, at: tuple[float, float] = (72, 72), title: str = "") -> bytes:
    """A str page carries that text at `at`; an int page carries no text, only
    a filled box placed by the int (distinct pixels, nothing to read)."""
    doc = pymupdf.open()
    for p in pages:
        page = doc.new_page()
        if isinstance(p, str):
            page.insert_text(at, p, fontsize=11)
        else:
            page.draw_rect(
                pymupdf.Rect(50 + p * 30, 60 + p * 25, 200 + p * 30, 160 + p * 25), color=(0, 0, 0), fill=(0, 0, 0)
            )
    if title:
        doc.set_metadata({"title": title})
    data = doc.tobytes()
    doc.close()
    return data


PAGES = {
    1: [T("C", 1), T("C", 2), T("C", 3)],
    2: [T("K", 1), T("K", 2), T("K", 3), T("K", 4)],
    3: [T("K", 1), T("K", 2), T("K", 3), T("K", 4)],
    4: [T("U", 1), 1, T("U", 3)],
    5: [T("U", 3), T("U", 1), 1],
    6: [T("O", 1), T("O", 2)],
}

BODY = """01/20/2026
Example Clinic | Patient Complaints & Limitations

Neck pain after the collision, reported at intake. (Exhibit 1 - p. 1)

02/01/2026
Kaiser | Emergency

Seen in the emergency department for neck pain. (Exhibit 2 - p. 2)

Seen in the emergency department for neck pain. (Exhibit 3 - p. 2)

Medical Diagnoses

Cervical strain with muscle spasm, diagnosed. (Exhibit 2 - p. 3-4)

Medical Diagnoses

Cervical strain with muscle spasm, diagnosed. (Exhibit 3 - p. 3-4)

All Other Information

Discharged home in stable condition per the note. (Exhibit 3 - p. 1)

03/01/2026
UC Davis | Follow-up

Follow-up visit documented range of motion limits. (Exhibit 5 - p. 1)

Follow-up visit documented range of motion limits. (Exhibit 4 - p. 3)

Imaging film reviewed with the treating physician. (Exhibit 5 - p. 2-3)

04/01/2026
Other Clinic | Treatment Recommendations

Continue therapy twice weekly for six weeks. (Exhibit 6 - p. 2)
"""

DOC = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n"
    "| Medical Provider | Treatment Period | Visits | Reference |\n"
    "| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 1 |\n"
    "| Kaiser | 02/01/2026 | 1 | Exhibit 3 - p. 2 |\n"
    "| UC Davis | 03/01/2026 | 1 | Exhibit 5 - p. 1 |\n"
    "| Other Clinic | 04/01/2026 | 1 | Exhibit 6 - p. 2 |\n\n"
    "## Diagnostic Highlights\n\n| ICD Code | Description | First Diagnosed | Reference |\n\n"
    "## Medical Chronology\n\n"
    + BODY
    + "\n## Exhibit List\n\n| Exhibit No. | Description |\n"
    + "".join(f"| {n} | {TITLES[n].split(' - ', 1)[1]} |\n" for n in TITLES)
    + "\n## Records Reviewed and Limitations\n\nThis chronology was prepared from 6 documents.\n"
)


def _sr(job_dir: Path, firm: Path, data_root: Path) -> tuple[StageRun, list[str]]:
    job = job_mod.load(job_dir)
    log: list[str] = []
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        date_stamp="10-25-26",
    )
    return sr, log


def _scene(job_dir: Path, firm: Path, data_root: Path, pages=PAGES, doc: str = DOC) -> tuple[StageRun, list[str]]:
    sr, log = _sr(job_dir, firm, data_root)
    sd = sr.slug_dir
    out, rd = sd / "out" / "alpha", sd / "runs" / "alpha"
    out.mkdir(parents=True)
    rd.mkdir(parents=True)
    for n, ps in pages.items():
        at = (110, 140) if n == 5 else (72, 72)
        (out / f"{TITLES[n]}.pdf").write_bytes(pdf(ps, at, title=FILES[n]))
    pm = [
        {
            "exhibit": n,
            "title": TITLES[n],
            "provider": TITLES[n].split(" - ")[1],
            "record_type": "Medical Records",
            "total_pages": len(pages[n]),
            "files": [{"file": f"{FILES[n]}.pdf", "old_exhibit": n, "start_page": 1, "pages": len(pages[n])}],
        }
        for n in pages
    ]
    (out / "page_map.json").write_text(json.dumps(pm))
    (rd / "final-chronology.md").write_text(doc)
    (rd / "entries_scoped_final.md").write_text(BODY)
    (rd / "exhibit_map.json").write_text(json.dumps({f"{FILES[n]}.pdf": n for n in pages}))
    units = [{"id": f"f{n}", "name": FILES[n], "ext": ".pdf", "chars": 100} for n in pages]
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps(units))
    (sd / "raw_manifest.jsonl").write_text(
        "".join(json.dumps({"id": f"f{n}", "name": FILES[n], "ext": ".pdf", "ok": True}) + "\n" for n in pages)
    )
    (sd / "strip_result.json").write_text(
        json.dumps(
            {
                "drops": {str(n): [] for n in pages},
                "new_page_counts": {str(n): len(pages[n]) for n in pages},
                "unverifiable": [[6, 1, False, "pixel 1.0 on unmoved page"]],
                "unstripped": [],
            }
        )
    )
    (sd / "nonrecord.json").write_text(json.dumps({str(n): {"drop_pages": [], "unknown": []} for n in pages}))
    (sd / "scanned_labels.json").write_text(
        json.dumps(
            {"labels": {"Ex4p2": "RECORD", "Ex5p3": "RECORD", "Ex6p1": "RECORD"}, "nonrecord": {}, "controls_ok": True}
        )
    )
    (sd / "record_control.json").write_text(json.dumps({"exhibit": 5, "page": 2}))
    (out / "repair-edits.jsonl").write_text(
        json.dumps({"key": "gone", "action": "drop-gate", "exhibit": 6, "page_spec": "1", "old": "A removed claim."})
        + "\n"
    )
    (out / "auditpages").mkdir()
    (out / "auditpages" / "ex5-p1.png").write_bytes(b"x")
    _audited(sr)
    return sr, log


def _audited(sr: StageRun) -> None:
    """Every live claim SUPPORTED, as the paid audit left Pamplona, and one
    control that discriminated."""
    paths = AuditPaths(sr.slug_dir, sr.unit.unit)
    body = CL.body_of(paths.doc.read_text())
    keep = set(range(1, 7))
    rows = []
    for c in CL.extract_claims(body, keep):
        rows.append(
            {
                "key": c["key"],
                "kind": "real",
                "exhibit": c["exhibit"],
                "page_spec": c["page_spec"],
                "pages": CL.parse_pages(c["page_spec"]),
                "claim": c["claim"],
                "verdict": "SUPPORTED",
                "unsupported_assertions": [],
                "contradictions": [],
                "doc_sha": CL.doc_sha_of(body),
            }
        )
    rows.append({"key": rows[0]["key"] + "-ctl6", "kind": "control(vs Ex6)", "exhibit": 1, "verdict": "UNSUPPORTED"})
    paths.results.write_text("".join(json.dumps(r) + "\n" for r in rows))


# ---- signatures and the falsifier ------------------------------------------------------
def test_text_pages_pair_by_words_and_textless_pages_by_pixels(tmp_path: Path) -> None:
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(pdf(PAGES[4]))
    b.write_bytes(pdf(PAGES[5], at=(110, 140)))
    sa, sb = EM.Sigs(a), EM.Sigs(b)
    assert sb.text.count(None) == 1  # the drawn page has no text
    mapping, why = EM.contained(sb, sa)
    assert mapping == [2, 0, 1] and "discriminates" in why
    # the falsifier pair: a different drawing has no partner
    c = tmp_path / "c.pdf"
    c.write_bytes(pdf([T("U", 3), T("U", 1), 2]))
    assert EM.contained(EM.Sigs(c), sa)[0] is None


def test_a_run_of_template_pages_cannot_be_falsified_so_nothing_merges(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    same = [T("Template", 0)] * 5
    pages = {1: list(PAGES[1]), 2: same, 3: same}
    doc = DOC.split("## Medical Chronology")[0] + "## Medical Chronology\n\n" + BODY.split("03/01/2026")[0]
    sr, log = _scene(job_dir, firm_config_path, data_root, pages=pages, doc=doc)
    before = (sr.slug_dir / "out" / "alpha" / "page_map.json").read_text()
    assert EM.run(sr) == EM.EXIT_BLIND
    assert any("CANNOT FAIL" in line for line in log), log
    assert (sr.slug_dir / "out" / "alpha" / "page_map.json").read_text() == before
    assert dag.BY_NAME["exhibit_merge"].exit_map[EM.EXIT_BLIND][0] == dag.REFUSED


def test_a_partial_overlap_never_merges(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    pages = dict(PAGES)
    pages[3] = [T("K", 1), T("K", 2), T("X", 9), T("K", 4)]
    sr, log = _scene(job_dir, firm_config_path, data_root, pages=pages)
    assert EM.run(sr) == 0
    assert any(
        "Exhibit 3 against Exhibit 2: partial: 3 of its 4 pages are also there; not merged" in line for line in log
    ), log
    pm = json.loads((sr.slug_dir / "out" / "alpha" / "page_map.json").read_text())
    assert [e["exhibit"] for e in pm] == [1, 2, 3, 4, 5]  # only the UC Davis copy merged


def test_a_citation_past_the_copys_last_page_keeps_the_pair_apart(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    doc = DOC.replace("(Exhibit 3 - p. 1)", "(Exhibit 3 - p. 9)")
    sr, log = _scene(job_dir, firm_config_path, data_root, doc=doc)
    assert EM.run(sr) == 0
    assert any("Exhibit 3: cited at page(s) 9 outside its 4 pages; not merged" in line for line in log)
    assert "(Exhibit 3 - p. 9)" in (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def test_no_duplicates_writes_nothing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    pages = {1: PAGES[1], 2: PAGES[2], 6: PAGES[6]}
    sr, log = _scene(job_dir, firm_config_path, data_root, pages=pages, doc=DOC)
    snap = {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()}
    assert EM.run(sr) == 0
    assert {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()} == snap
    assert "none holds the same pages as another" in log[-1]


# ---- the merge --------------------------------------------------------------------------
def test_the_merge_repoints_renumbers_dedupes_and_discloses(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _scene(job_dir, firm_config_path, data_root)
    sd, out, rd = sr.slug_dir, sr.slug_dir / "out" / "alpha", sr.slug_dir / "runs" / "alpha"
    assert EM.uncached_claims(sr) == []
    assert EM.run(sr) == 0, log
    md = (rd / "final-chronology.md").read_text()
    body = md.split("## Medical Chronology", 1)[1].split("## Exhibit List", 1)[0]
    assert body.strip() == (
        "01/20/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
        "Neck pain after the collision, reported at intake. (Exhibit 1 - p. 1)\n\n"
        "02/01/2026\nKaiser | Emergency\n\n"
        "Seen in the emergency department for neck pain. (Exhibit 2 - p. 2)\n\n"
        "Medical Diagnoses\n\nCervical strain with muscle spasm, diagnosed. (Exhibit 2 - p. 3-4)\n\n"
        "All Other Information\n\nDischarged home in stable condition per the note. (Exhibit 2 - p. 1)\n\n"
        "03/01/2026\nUC Davis | Follow-up\n\n"
        "Follow-up visit documented range of motion limits. (Exhibit 3 - p. 3)\n\n"
        "Imaging film reviewed with the treating physician. (Exhibit 3 - p. 1-2)\n\n"
        "04/01/2026\nOther Clinic | Treatment Recommendations\n\n"
        "Continue therapy twice weekly for six weeks. (Exhibit 4 - p. 2)"
    )
    assert "| Kaiser | 02/01/2026 | 1 | Exhibit 2 - p. 2 |" in md
    assert "| UC Davis | 03/01/2026 | 1 | Exhibit 3 - p. 3 |" in md
    assert "| Other Clinic | 04/01/2026 | 1 | Exhibit 4 - p. 2 |" in md
    assert (
        "## Exhibit List\n\n| Exhibit No. | Description |\n| 1 | Example Clinic - 01-20-2026 (Medical Records) |\n"
        "| 2 | Kaiser - 02-01-2026 (Medical Records) |\n| 3 | UC Davis - 03-01-2026 (Medical Records) |\n"
        "| 4 | Other Clinic - 04-01-2026 (Medical Records) |\n\n## Records Reviewed and Limitations"
    ) in md
    assert (
        '* "kaiser er copy.pdf" and "kaiser er.pdf" hold the same record pages; they are delivered once, as Exhibit 2.'
        in md
    )
    assert (
        '* "ucd export 2.pdf" and "ucd export.pdf" hold the same record pages; they are delivered once, as Exhibit 3.'
        in md
    )
    # the gates' body: renumbered, the same paragraphs gone (pre-strip pages: no strip here)
    ents = (rd / "entries_scoped_final.md").read_text()
    assert "Exhibit 5" not in ents and "Exhibit 6" not in ents and ents.count("Seen in the emergency") == 1
    # the page map, the PDFs and every keyed file agree
    pm = json.loads((out / "page_map.json").read_text())
    assert [(e["exhibit"], e["title"]) for e in pm] == [
        (1, TITLES[1]),
        (2, TITLES[2]),
        (3, TITLES[4].replace("Exhibit 4", "Exhibit 3")),
        (4, TITLES[6].replace("Exhibit 6", "Exhibit 4")),
    ]
    assert pm[1]["files"][1] == {"file": "kaiser er copy.pdf", "old_exhibit": 3, "merged_from": 3, "merged_pages": 4}
    assert sorted(p.name for p in out.glob("Exhibit *")) == sorted(e["title"] + ".pdf" for e in pm)
    assert json.loads((sd / "strip_result.json").read_text())["unverifiable"] == [
        [4, 1, False, "pixel 1.0 on unmoved page"]
    ]
    assert sorted(json.loads((sd / "nonrecord.json").read_text())) == ["1", "2", "3", "4"]
    assert json.loads((sd / "scanned_labels.json").read_text())["labels"] == {"Ex3p2": "RECORD", "Ex4p1": "RECORD"}
    assert json.loads((sd / "record_control.json").read_text()) == {"exhibit": 3, "page": 1}
    assert json.loads((out / "repair-edits.jsonl").read_text())["exhibit"] == 4
    assert list((out / "auditpages").iterdir()) == []
    assert json.loads((rd / "exhibit_merges.json").read_text()) == [
        {
            "kept": 2,
            "kept_title": TITLES[2],
            "dropped_old": 3,
            "files_kept": ["kaiser er.pdf"],
            "files_dropped": ["kaiser er copy.pdf"],
            "pages": 4,
        },
        {
            "kept": 3,
            "kept_title": TITLES[4].replace("Exhibit 4", "Exhibit 3"),
            "dropped_old": 5,
            "files_kept": ["ucd export.pdf"],
            "files_dropped": ["ucd export 2.pdf"],
            "pages": 3,
        },
    ]
    assert not (rd / EM.PART).exists()
    # the gates pass on the merged package, and the audit has nothing to pay for
    assert coverage.run(sr) == 0, log
    assert EM.uncached_claims(sr) == []
    ok, summary = audit_coverage.check(AuditPaths(sd, "alpha"), log.append)
    assert ok and summary["never"] == 0, summary


def test_the_audit_after_a_merge_spends_nothing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    class NoSpend:
        gave_up: list = []

        def call(self, *a, **kw):
            raise AssertionError("the audit called the model after a merge")

    sr, log = _scene(job_dir, firm_config_path, data_root)
    assert EM.run(sr) == 0
    sr._doorway = NoSpend()  # type: ignore[assignment]
    assert audit_loop.run(sr) == 0, log
    assert "GATE PASS" in "\n".join(log)
    dropped = json.loads((sr.slug_dir / "out" / "alpha" / "audit-dropped-claims.json").read_text())
    assert dropped["claims"][0]["exhibit"] == 4  # the attorney's notes read the renumbered exhibit


def test_a_second_run_changes_nothing_and_manifest_lists_the_merged_set(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _scene(job_dir, firm_config_path, data_root)
    assert EM.run(sr) == 0
    snap = {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()}
    assert EM.run(sr) == 0
    assert {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()} == snap
    out = sr.slug_dir / "out" / "alpha"
    (out / "Alpha Example - Medical Chronology 10-25-26.docx").write_bytes(b"docx")
    assert manifest.run(sr) == 0
    names = [m["name"] for m in json.loads((out / "upload_manifest.json").read_text())]
    assert [n.split(" - ")[0] for n in names[1:]] == ["Exhibit 1", "Exhibit 2", "Exhibit 3", "Exhibit 4"]


def test_an_interrupted_merge_refuses(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr, _ = _scene(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "runs" / "alpha" / EM.PART).write_text("{}")
    with pytest.raises(StageRefusal, match="stopped part way"):
        EM.run(sr)


def test_pre_strip_pages_map_through_both_strips() -> None:
    """The entries files carry pre-strip pages: B's pre-strip page -> B's
    current page -> A's current page -> A's pre-strip page."""
    plan = EM.Plan([EM.Merge(2, 3, [2, 1, 3])], {2: [1, 3, 4, 5], 3: [2, 3, 4]})
    assert plan.pre_page(3, 2) == 3  # B p2 is B's current p1 -> A's current p2 -> A's pre-strip p3
    assert plan.pre_page(3, 1) is None  # stripped from B: no partner
    assert plan.pre_page(4, 7) == 7 and plan.new(4) == 3 and plan.new(3) == 2


def test_dedupe_deletes_only_exact_cited_repeats_within_an_entry() -> None:
    text = (
        "01/01/2026\nA | x\n\nSame line here. (Exhibit 1 - p. 1)\n\nSame line here. (Exhibit 1 - p. 1)\n\n"
        "Same line here. (Exhibit 1 - p. 2)\n\n02/01/2026\nA | x\n\nSame line here. (Exhibit 1 - p. 1)\n"
    )
    out, gone = EM.dedupe(text)
    assert gone == 1
    assert out.count("Same line here. (Exhibit 1 - p. 1)") == 2  # the next entry keeps its own
    assert "(Exhibit 1 - p. 2)" in out


# ---- the closed list --------------------------------------------------------------------
NUMBER = re.compile(r'Exhibit \d+|"exhibit"|Ex\d+p\d+')


def _listed(rel: str) -> bool:
    pats = [p.format(u="alpha", s="") for p, _ in IO.ARTEFACTS + IO.NOT_REWRITTEN]
    return any(fnmatch.fnmatch(rel, p) for p in pats)


def _delivered_run(job_dir: Path, firm: Path, pricing: Path, data_root: Path) -> Path:
    from test_decisions_driver import _driver

    seed_folders(data_root, ["MEDICAL"])
    o = _driver(job_dir, firm, pricing).run()[0]
    assert o.outcome == "delivered", o
    return data_root / "example-matter"


def test_every_file_carrying_an_exhibit_number_is_on_one_of_the_two_lists(
    job_dir: Path, firm_config_path: Path, pricing_path: Path, data_root: Path, fake_pipeline: Path
) -> None:
    sd = _delivered_run(job_dir, firm_config_path, pricing_path, data_root)
    found = []
    for p in sorted(sd.rglob("*")):
        rel = p.relative_to(sd).as_posix()
        if (
            not p.is_file()
            or rel.split("/")[0] in ("raw", "text", "units", "groups")
            or "/" in rel
            and not rel.startswith(("runs/alpha/", "out/alpha/"))
        ):
            continue
        named = bool(re.match(r"Exhibit \d+", p.name))
        body = (
            p.read_bytes()[:2_000_000].decode("utf-8", "ignore")
            if p.suffix in (".md", ".json", ".jsonl", ".txt", "")
            else ""
        )
        if named or NUMBER.search(body):
            found.append(rel)
    assert found, "the delivered run produced no exhibit-numbered file; the enumeration measured nothing"
    assert [r for r in found if not _listed(r)] == []
    # the falsifier: a file nobody declared is caught
    assert not _listed("runs/alpha/new_artefact.json") and not _listed("out/alpha/notes.md")


def test_a_resumed_job_runs_the_merge_and_reopens_everything_after_it(
    job_dir: Path, firm_config_path: Path, pricing_path: Path, data_root: Path, fake_pipeline: Path
) -> None:
    from test_decisions_driver import _driver

    sd = _delivered_run(job_dir, firm_config_path, pricing_path, data_root)
    path = state_path(data_root, "example-matter", "alpha")
    st = RunState.load_or_new(path, slug="x", unit="y")
    assert st.is_done("exhibit_merge") and st.is_done("content_dupes")
    before = {n: st.stage(n).attempts for n in dag.ORDER}
    st.stages.pop("exhibit_merge")  # a job begun before the stage existed
    st.outcome = None
    st.save()
    ran: list[str] = []
    d = _driver(job_dir, firm_config_path, pricing_path)
    d._runner_override = {"exhibit_merge": lambda sr: ran.append(sr.unit.unit) or 0}
    assert d.run()[0].outcome == "delivered"
    st = RunState.load_or_new(path, slug="x", unit="y")
    assert ran == ["alpha"]
    reopened = set(dag.BY_NAME["exhibit_merge"].invalidates) | {"review"}
    for n in dag.ORDER:
        want = before[n] + (1 if n in reopened else 0)
        if n == "exhibit_merge":
            want = 1
        if n == "upload":
            continue  # delivered once already; its own record decides
        assert st.stage(n).attempts == want, (n, st.stage(n).attempts, want)
    assert sd.is_dir()


def test_the_dag_places_and_wires_the_two_stages() -> None:
    o = dag.ORDER
    assert dag.validate_dag() == []
    assert o.index("extract_after_fold") < o.index("content_dupes") < o.index("vision")
    assert o.index("strip_apply") < o.index("exhibit_merge") < o.index("coverage_gate")
    assert dag.BY_NAME["exhibit_merge"].invalidates == (
        "coverage_gate",
        "dos_check",
        "billing_chart",
        "billing_docx",
        "audit",
        "render",
        "manifest",
    )
    assert "review" in dag.BY_NAME["manifest"].invalidates
    assert "exhibit_merge" in dag.BY_NAME["build_doc"].invalidates
    assert not dag.BY_NAME["exhibit_merge"].paid and not dag.BY_NAME["content_dupes"].paid


# ---- a rebuild cannot undo a merge ---------------------------------------------------------
def test_redo_doc_refuses_a_merged_job(tmp_path: Path, job_dir: Path, data_root: Path) -> None:
    from test_password_protected import JOB, _seat

    rd = data_root / "example-matter" / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "exhibit_merges.json").write_text("[]")
    d = _seat(tmp_path, job_dir)
    with pytest.raises(redo_doc.Refused, match="duplicate exhibits merged"):
        redo_doc.plan(d, JOB, by="smd", source="x")
    with pytest.raises(redo_doc.Refused, match="not rebuilt"):
        redo_doc.restore_unit(data_root / "example-matter", "alpha", joint=False)
