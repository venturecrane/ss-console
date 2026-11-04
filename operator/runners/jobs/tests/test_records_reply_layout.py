"""The reply's layout (2026-11-03, dry run 3 on the A&P seat): every record
under exactly ONE heading, no line twice, the "Could not be read" block only
for attached files and never repeating a count a record's entry gives, and
every count written "2,013". Driven through ``RecordsRun.compose`` over planted
stage outputs, so the composer is the job's own. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs import proof_mode
from jobs.records.run import RecordsRun
from records_testkit import MATTER, FakeFinderModel, FakeParseClient, FakeSmokeball, fid, job_dir, setup_env

VISIT, PANOREX, XRAY, DISCHARGE = (
    "the 3/3/2021 Northside Clinic visit",
    "the Westfield Dental panorex",
    "the Northside Clinic x-ray",
    "the discharge summary",
)
A, B, C, D, E, F = (fid(n) for n in range(1, 7))
NAMES = {
    A: "Northside Clinic chart.pdf",
    B: "Northside Clinic index.pdf",
    C: "Westfield Dental.pdf",
    D: "Westfield Dental.pdf",  # the same name as C: two files, one name
    E: "Westfield Dental set.pdf",
    F: "Northside Clinic imaging.pdf",
}
PAGES = {A: 10, B: 4, C: 3, D: 3, E: 2313, F: 60}


@pytest.fixture(autouse=True)
def _no_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)


def _result(f: str, items: list[dict[str, Any]], *, read: int | None = None, unreadable: int = 0) -> dict[str, Any]:
    total = PAGES[f]
    got = total - unreadable if read is None else read
    return {
        "schemaVersion": 2,
        "matterId": MATTER,
        "fileId": f,
        "name": NAMES[f],
        "pageCount": total,
        "counts": {"pages": total, "read": got, "unreadable": unreadable, "notReached": total - got - unreadable},
        "items": items,
    }


def _absent(what: str) -> dict[str, Any]:
    return {"asked": what, "found": False, "established": True, "sections": [], "reason": "not here"}


def _plant(jd: Path) -> None:
    data = jd / "data"
    asks = (VISIT, PANOREX, XRAY, DISCHARGE)
    recs = [{"n": n, "asked": a, "hints": {}, "for_another_person": False} for n, a in enumerate(asks, 1)]
    found = {
        "asked": VISIT,
        "found": True,
        "established": True,
        "ranges": [{"pages": "1-4", "evidence": [{"heading": "Progress Note", "date": "3/3/2021"}]}],
    }
    partly = {
        "asked": XRAY,
        "found": False,
        "established": False,
        "closest": [{"pages": "6-9", "heading": "Imaging", "date": "4/4/2021"}],
    }
    located = {
        (1, A): _result(A, [found], unreadable=3, read=7),
        (1, B): _result(B, [_absent(VISIT)]),
        (2, C): _result(C, [_absent(PANOREX)]),
        (2, D): _result(D, [_absent(PANOREX)]),
        (3, F): _result(F, [partly], read=7, unreadable=53),
        (3, B): _result(B, [_absent(XRAY)]),
        (4, B): _result(B, [_absent(DISCHARGE)]),
    }
    order: dict[str, list[str]] = {}
    for (n, f), result in located.items():
        order.setdefault(str(n), []).append(f)
        p = data / "locate" / str(n) / f"{f}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result))
    order["2"].append(E)
    files = {
        f: {"name": NAMES[f], "sha256": f"{i}" * 64, "page_count": PAGES[f], "size": 1} for i, f in enumerate(NAMES)
    }
    cut = {"n": 1, "k": 1, "file_id": A, "name": "Northside Clinic chart pp. 1-4.pdf", "pages": "1-4"}
    cut.update(page_count=4, sha256="a" * 64, size=1, path="cuts/1.pdf")
    for name, doc in (
        ("records.json", recs),
        ("matter.json", {"matter_id": MATTER}),
        ("files.json", files),
        ("candidates.json", order),
        ("order.json", order),
        ("item_holds.json", {"2": f"per_job_cap_usd: {E[:8]} 2013 pages"}),
        ("cuts.json", [cut]),
    ):
        (data / name).write_text(json.dumps(doc))


def _compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env = setup_env(tmp_path, monkeypatch, FakeFinderModel())
    jd = job_dir(tmp_path)
    _plant(jd)
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=FakeSmokeball(),
        model_client=FakeParseClient(),
        log=lambda _m: None,
    )
    return r.compose(None)


def _blocks(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for para in text.split("\n\n"):
        head, *lines = para.split("\n")
        if lines:
            out[head] = lines
    return out


def test_each_record_is_under_exactly_one_heading_and_no_line_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _compose(tmp_path, monkeypatch)
    text = doc["reply_text"]
    blocks = _blocks(text)
    assert [(i["n"], i["outcome"]) for i in doc["items"]] == [
        (1, "attached"),
        (2, "held"),
        (3, "not_established"),
        (4, "not_found"),
    ]
    for asked in (PANOREX, XRAY, DISCHARGE):
        assert sum(asked in line for lines in blocks.values() for line in lines) == 1, (asked, text)
    assert all("visit" not in line for head, lines in blocks.items() if head != "Attached:" for line in lines)
    lines = [ln for ln in text.split("\n") if ln.startswith("- ")]
    assert len(lines) == len(set(lines)), text
    # The held record: one entry, where it was looked for folded in, the
    # same-named second file said to be one, the count with its comma.
    (held,) = blocks["Not established, still to be read:"]
    assert held == (
        f"- {PANOREX}: not established yet; looked for in Westfield Dental.pdf (all 3 pages read) and a second "
        "file also named Westfield Dental.pdf (all 3 pages read); 2,013 pages of Westfield Dental set.pdf are not "
        "read yet, and SMD is finishing it"
    )
    assert blocks["Not found:"] == [f"- {DISCHARGE}: not found in Northside Clinic index.pdf (all 4 pages were read)"]
    assert blocks["Not established, 53 pages of Northside Clinic imaging.pdf could not be read:"] == [
        f"- {XRAY}: not found on the 7 pages of Northside Clinic imaging.pdf that were read. "
        "Closest: pages 6-9, Imaging, 4/4/2021. Also looked for in Northside Clinic index.pdf (all 4 pages read)."
    ]
    # "Could not be read" names the ATTACHED file's gap only.
    assert blocks["Could not be read:"] == ["- 3 pages of Northside Clinic chart.pdf could not be read"]
    assert "2013" not in text
    assert doc["reply_html"].count(f"<li>{PANOREX}") == 1


@pytest.mark.parametrize("one_upload", [False, True])
def test_two_files_with_one_name_are_never_one_line_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_upload: bool
) -> None:
    """The dry run's duplicate: a record not found in two 3-page files that
    share a name rendered one line twice. Measured on the seat, the two ids
    held ONE sha (the same upload twice): that renders once, under the file
    tried first. Two different files with one name are both named."""
    jd = job_dir(tmp_path)
    _plant(jd)
    data = jd / "data"
    if one_upload:
        files = json.loads((data / "files.json").read_text())
        files[D]["sha256"] = files[C]["sha256"]
        (data / "files.json").write_text(json.dumps(files))
    for f in (C, D):
        (data / "locate" / "4" / f"{f}.json").write_text(json.dumps(_result(f, [_absent(DISCHARGE)])))
    order = json.loads((data / "order.json").read_text())
    order["4"] += [C, D]
    (data / "order.json").write_text(json.dumps(order))
    env = setup_env(tmp_path, monkeypatch, FakeFinderModel())
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=FakeSmokeball(),
        model_client=FakeParseClient(),
        log=lambda _m: None,
    )
    text = r.compose(None)["reply_text"]
    assert (
        _blocks(text)["Not found:"]
        == [
            f"- {DISCHARGE}: not found in Northside Clinic index.pdf (all 4 pages were read)",
            f"- {DISCHARGE}: not found in Westfield Dental.pdf (all 3 pages were read)",
            f"- {DISCHARGE}: not found in a second file also named Westfield Dental.pdf (all 3 pages were read)",
        ][: 2 if one_upload else 3]
    )
    if one_upload:  # the held record's entry names the upload once too
        assert "a second file" not in text and text.count("Westfield Dental.pdf (all 3 pages read)") == 1


def test_an_attached_record_says_the_item_it_asked_for_that_was_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry run 4 (2026-11-04): a record asked for two visits; the first was
    attached and the reply said nothing of the second, looked for and not
    found. It is said once, on the record's attached line, with the closest pages."""
    jd = job_dir(tmp_path)
    _plant(jd)
    data = jd / "data"
    second = "the 5/11/2026 Northside Clinic visit"
    a = json.loads((data / "locate" / "1" / f"{A}.json").read_text())
    a["items"].append(
        {
            "asked": second,
            "found": False,
            "established": False,
            "closest": [{"pages": "8-9", "heading": "Progress Note", "date": "5/1/2026"}],
        }
    )
    (data / "locate" / "1" / f"{A}.json").write_text(json.dumps(a))
    b = json.loads((data / "locate" / "1" / f"{B}.json").read_text())
    b["items"].append(_absent(second))
    (data / "locate" / "1" / f"{B}.json").write_text(json.dumps(b))
    env = setup_env(tmp_path, monkeypatch, FakeFinderModel())
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=FakeSmokeball(),
        model_client=FakeParseClient(),
        log=lambda _m: None,
    )
    doc = r.compose(None)
    text = doc["reply_text"]
    assert _blocks(text)["Attached:"] == [
        "- Northside Clinic chart pp. 1-4.pdf, pages 1-4 (4 pages): Progress Note, 3/3/2021. The 5/11/2026 "
        "Northside Clinic visit was not found on the 7 pages read of Northside Clinic chart.pdf (closest: pages "
        "8-9, Progress Note, 5/1/2026) or in Northside Clinic index.pdf (all 4 pages read)."
    ]
    assert text.count("5/11/2026") == 1 and [i["outcome"] for i in doc["items"]][0] == "attached"
    assert doc["reply_html"].count("5/11/2026") == 1
