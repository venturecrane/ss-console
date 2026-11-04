"""Evidence extract, images and index: the model proposes, code caches,
checks page images, and builds the index the drafter sees. The model is
scripted throughout; no test makes a paid call.

Synthetic record only (``evidence_testkit``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_testkit import CHECKER, BILL, CHART, FORM, ScriptedDoorway, candidates, corpus

from jobs.drafting.evidence import extract, images, index, verify
from jobs.drafting.evidence.shapes import DocPages, Ledger


@pytest.fixture(autouse=True)
def _gate_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def _data(tmp_path: Path, docs: dict[str, str], machine: list[str] | None = None) -> Path:
    data = tmp_path / "data"
    (data / "text").mkdir(parents=True)
    rows = []
    for i, (name, text) in enumerate(docs.items()):
        p = data / "text" / f"d{i}.txt"
        p.write_text(text, encoding="utf-8")
        rows.append({"id": f"d{i}", "name": name, "text_path": str(p)})
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if machine:
        (data / "transcribed.json").write_text(json.dumps(machine), encoding="utf-8")
    return data


def _pages(n: int) -> str:
    return "".join(
        f"[p.{p}]\nPage {p} of the synthetic chart. The knee was swollen on visit {p}.\n" for p in range(1, n + 1)
    )


def test_the_corpus_loads_page_by_page_with_machine_pages_marked(tmp_path):
    data = _data(
        tmp_path,
        {"a.pdf": "[p.1]\nNative.\n[p.2] (machine transcription)\nRead from the image.\n", "b.txt": "No markers here."},
    )
    c = extract.load_corpus(data)
    assert c["a.pdf"].pages == {1: "Native.", 2: "Read from the image."} and c["a.pdf"].machine == {2}
    assert c["b.txt"].pages == {1: "No markers here."}


def test_windows_are_ten_pages_of_one_document(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(23), "b.pdf": _pages(3)})
    ws = extract.windows(extract.load_corpus(data), "m")
    assert [(w.name, w.pages[0], w.pages[-1]) for w in ws] == [
        ("a.pdf", 1, 10),
        ("a.pdf", 11, 20),
        ("a.pdf", 21, 23),
        ("b.pdf", 1, 3),
    ]


ANSWER = json.dumps(
    [{"page": 2, "kind": "fact", "excerpt": "The knee was swollen on visit 2.", "claim": "Swollen knee"}]
)


def test_each_window_is_read_once_and_a_resume_pays_for_nothing(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(12)})
    door = ScriptedDoorway(ANSWER, "[]")
    got = extract.extract(data, door, "claude-sonnet-5", log=lambda _m: None, workers=1)
    assert len(door.calls) == 2 and {c["stage"] for c in door.calls} == {"evidence_extract"}
    assert got[0].file == "a.pdf" and got[0].claim == "Swollen knee"  # the window's file filled in
    again = ScriptedDoorway()
    assert extract.extract(data, again, "claude-sonnet-5", log=lambda _m: None) == got and again.calls == []


def test_a_changed_page_is_read_again(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(3)})
    extract.extract(data, ScriptedDoorway(ANSWER), "m", log=lambda _m: None)
    (data / "text" / "d0.txt").write_text(_pages(3).replace("visit 3", "visit three"), encoding="utf-8")
    door = ScriptedDoorway(ANSWER)
    extract.extract(data, door, "m", log=lambda _m: None)
    assert len(door.calls) == 1


def test_a_non_json_answer_gets_one_retry_then_fails_and_caches_nothing(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(2)})
    door = ScriptedDoorway("Here are the facts I found.", f"Sure:\n{ANSWER}")
    assert extract.extract(data, door, "m", log=lambda _m: None)[0].claim == "Swollen knee"
    assert door.calls[1]["messages"][-1]["content"].startswith("Your answer was not a JSON array")
    data2 = _data(tmp_path / "x", {"a.pdf": _pages(2)})
    with pytest.raises(extract.ExtractError):
        extract.extract(data2, ScriptedDoorway("no", "still no"), "m", log=lambda _m: None)
    assert (
        not list((data2 / "evidence" / "extract").glob("*.json")) if (data2 / "evidence" / "extract").exists() else True
    )


def test_an_answer_at_the_ceiling_is_read_again_in_halves(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(4)})
    door = ScriptedDoorway(('[{"page": 1', "max_tokens"), ANSWER, "[]")
    got = extract.extract(data, door, "m", log=lambda _m: None, workers=1)
    assert len(door.calls) == 3 and len(got) == 1
    one = _data(tmp_path / "y", {"a.pdf": _pages(1)})
    with pytest.raises(extract.ExtractError):
        extract.extract(one, ScriptedDoorway(("[", "max_tokens")), "m", log=lambda _m: None)


# ---- images ----------------------------------------------------------------------------


def _form_ledger() -> tuple[Ledger, dict[str, DocPages]]:
    corp = corpus()
    return verify.verify(candidates(), corp), corp


@pytest.mark.parametrize(
    "answer, kind",
    [
        ('{"answer": "no", "reading": "signature line blank, date blank"}', "inference"),
        ('{"answer": "yes", "reading": "signed and dated"}', "fact"),
        ("I cannot tell", "inference"),
    ],
)
def test_a_signed_claim_on_a_form_page_is_confirmed_from_its_image(tmp_path, answer, kind):
    """Taxonomy item 2: text extraction prints a blank verification as if it
    were complete; only the image shows the empty signature line."""
    led, corp = _form_ledger()
    door = ScriptedDoorway(answer)
    seen = []
    out = images.confirm(
        led,
        corp,
        tmp_path,
        door,
        "m",
        log=lambda _m: None,
        renderer=lambda src, p, log, n: seen.append((n, p)) or "iVBORw0KGgo=",
    )
    f = next(x for x in out if x.claim == "Plaintiff verified the responses")
    assert f.kind == kind and (FORM, 1) in seen
    assert door.calls[0]["messages"][0]["content"][0]["type"] == "image"
    again = ScriptedDoorway()
    images.confirm(led, corp, tmp_path, again, "m", log=lambda _m: None, renderer=lambda *a: "iVBORw0KGgo=")
    assert again.calls == []  # cached per page and question


def test_no_page_image_means_the_fact_is_unconfirmed(tmp_path):
    led, corp = _form_ledger()
    door = ScriptedDoorway()
    out = images.confirm(led, corp, tmp_path, door, "m", log=lambda _m: None)  # the form's PDF is not on disk
    f = next(x for x in out if x.claim == "Plaintiff verified the responses")
    assert f.kind == "inference" and door.calls == []


def test_only_pages_whose_text_cannot_carry_the_fact_are_sent_for_images():
    led, corp = _form_ledger()
    q = {
        f.claim: images.question(f, corp[f.file].pages[f.page], f.page in corp[f.file].machine) for f in led if f.usable
    }
    assert q["Plaintiff verified the responses"] is not None  # a form behind a verified claim
    assert q["Knee still swollen"] is None  # a full chart page
    assert q["Surgery charges"] is not None  # a figure on a thin bill page: OCR garbles digits
    machine = DocPages(CHART, corp[CHART].pages, frozenset({3}))
    fut = next(f for f in led if f.claim == "Knee still swollen").with_(kind="figure", value="25000.00", unit="usd")
    assert images.question(fut, machine.pages[2], True) is not None  # a figure on a machine-read page


# ---- index -----------------------------------------------------------------------------


def test_the_index_shows_usable_facts_only_and_marks_the_unconfirmed():
    led, _ = _form_ledger()
    form = next(f for f in led if f.claim == "Plaintiff verified the responses")
    led = Ledger(tuple(f.with_(kind="inference") if f.id == form.id else f for f in led))
    idx = index.build(led)
    rejected = led.rejected()[0]
    assert rejected.id not in idx.text and rejected.id not in idx.digest
    assert f"{form.id} | UNCONFIRMED (name it only with {{{{A:{form.id}}}}})" in idx.text
    billed = next(f for f in led if f.claim == "Surgery charges")
    assert f"{billed.id} | figure:billed | FILE: {BILL}, p. 2" in idx.text and "value=$8,300.00" in idx.text
    assert "(Example Dep. 12:4-5)" not in idx.text and "Example Dep. 12:4-5" in idx.text
    assert idx.digest.startswith("## 1 | ") and f"(FILE: {BILL}, p. 2)" in idx.digest


def test_over_budget_the_index_trims_plain_facts_first_and_logs_every_id():
    led, _ = _form_ledger()
    full = index.build(led)
    logs: list[str] = []
    small = index.build(led, budget_chars=len(full.text) - 200, log=logs.append)
    assert len(small.text) <= len(full.text) - 200 or small.trimmed
    assert small.trimmed and all(led.get(i).kind in ("fact", "quote", "inference") for i in small.trimmed[:1])
    assert all(i in logs[0] for i in small.trimmed)
    assert set(small.kept) | set(small.trimmed) == set(full.kept)
    figures = {f.id for f in led if f.kind == "figure" and f.usable}
    assert figures <= set(small.kept)
    assert CHART in full.text


def test_the_estimate_counts_windows_pages_and_input_before_anything_is_paid(tmp_path):
    data = _data(tmp_path, {"a.pdf": _pages(23), "b.pdf": _pages(3)})
    est = extract.estimate(extract.load_corpus(data), "m")
    assert est["windows"] == 4 and est["pages"] == 26 and est["input_chars"] > 26 * 40
