"""The update's hold rule, version 2 (``stages/entry_audit_update.py``): a line
holds only when a SECOND READER confirms it is wrong and no verified
correction was applied to it, keyed by finding. Ported from a bug review that
ran the real stage on version 1 (each case named by its probe letter), plus
the rate the rule holds at on a synthetic set of updates. Through the real
StageRun, Doorway, planner, parsers and DAG rows, with a scripted SDK client.
Every name, date and value is invented."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from typing import Any

import pytest

from jobs.stages import entry_audit as EA, entry_audit_update as EU
from test_entry_audit import ALL_OK, VALUE, YES, Client
from test_update_entry_read import DOC, E1, E2, NOT_SUPPORTED, UNSUPPORTED, _on, _record, _reply, scene  # noqa: F401 - scene is a pytest fixture the tests request by name

NOT_WRONG = {**YES, "original_wrong": False}


def _snap(sr: Any) -> dict[str, str]:
    return {
        str(p.relative_to(sr.slug_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(sr.slug_dir.rglob("*"))
        if p.is_file()
        and not {"entry_read", "auditpages"} & set(p.parts)  # the answers; a picture cache
        and p.name != "usage-ledger.jsonl"  # what the calls cost
        and p.name != "entry_read.json"
        and not p.name.endswith(".log")
    }


def _lines(verdict: str) -> dict[str, str]:
    return {**ALL_OK, "S1": verdict}


def _with_lines(lines: dict[str, str], *items: dict[str, Any]):
    return lambda t: (
        json.dumps({"lines": lines, "findings": list(items), "complete": True}) if " N1:" in t else _reply()
    )


# ---- A: an applied edit settles only the finding it corrects ------------------------
def test_a_value_corrected_and_another_person_on_the_same_line_holds(scene) -> None:
    other = {**VALUE, "class": "another_person", "quote": "triage", "replacement": "", "problem": "another patient"}
    sr, doc, log = scene(Client(_on("N1", VALUE, other)))
    assert EA.run(sr) == EA.EXIT_HELD
    rec = _record(sr)
    assert rec["unresolved_by_class"] == {"another_person": 1} and doc.read_text(encoding="utf-8") == DOC


def test_a2_a_value_corrected_and_an_unsupported_on_the_same_line_holds(scene) -> None:
    uns = {**UNSUPPORTED, "quote": "during the visit", "replacement": ""}
    sr, doc, log = scene(Client(_on("N1", VALUE, uns)))
    assert EA.run(sr) == EA.EXIT_HELD and _record(sr)["unresolved_by_class"] == {"unsupported": 1}


# ---- B, C: a problem line whose finding was dropped is confirmed ---------------------
def test_b_a_problem_line_whose_finding_had_a_bad_quote_holds_once_confirmed(scene) -> None:
    bad = {**UNSUPPORTED, "quote": "pulse was 72 at  triage"}  # not verbatim
    sr, doc, log = scene(Client(_with_lines(_lines("problem"), bad), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD
    assert _record(sr)["dropped"] == {"quote": 1} and _record(sr)["unresolved_by_class"] == {"unsupported": 1}


def test_c_an_unreadable_line_a_finding_named_holds_once_confirmed(scene) -> None:
    sr, doc, log = scene(Client(_with_lines(_lines("unreadable"), UNSUPPORTED), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD and _record(sr)["unresolved_by_class"] == {"unsupported": 1}


def test_b_falsifier_the_same_line_confirmed_right_files(scene) -> None:
    bad = {**UNSUPPORTED, "quote": "pulse was 72 at  triage"}
    sr, doc, log = scene(Client(_with_lines(_lines("problem"), bad), NOT_WRONG))
    assert EA.run(sr) == 0, log
    assert _record(sr)["confirmed_not_wrong_by_class"] == {"unsupported": 1}


# ---- D: a class never edited holds when confirmed -----------------------------------
DATE = {"line": "D", "class": "date", "exhibit": 1, "page": 2, "quote": "01/05/2025", "action": "redate",
        "replacement": "01/06/2025", "problem": "the page is dated 01/06/2025"}  # fmt: skip


def test_d_a_wrong_date_confirmed_holds_and_not_confirmed_files(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", DATE)))
    assert EA.run(sr) == EA.EXIT_HELD and _record(sr)["unresolved_by_class"] == {"date": 1}
    shutil.rmtree(sr.slug_dir / "runs" / "alpha" / "entry_read")
    sr2, _d, log2 = scene(Client(_on("N1", DATE), NOT_WRONG))
    assert EA.run(sr2) == 0, log2


# ---- E: a hold writes nothing outside the answers ------------------------------------
def test_e_a_hold_leaves_every_file_identical(scene) -> None:
    s2 = {**UNSUPPORTED, "line": "S2", "quote": "Therapy twice weekly", "page": 3}

    def verify(t: str) -> dict[str, Any]:
        return NOT_SUPPORTED if "Therapy" in t.split("PROPOSED")[-1][:200] else YES

    sr, doc, log = scene(Client(_on("N1", VALUE, s2), verify))
    (sr.slug_dir / "runs" / "alpha" / "entries_scoped_final.md").write_text(E1 + "\n\n" + E2)
    before = _snap(sr)
    assert EA.run(sr) == EA.EXIT_HELD
    assert _snap(sr) == before and doc.read_text(encoding="utf-8") == DOC


# ---- F: a second finding on a line is confirmed too ----------------------------------
def test_f_a_second_finding_on_a_line_found_right_twice_files(scene) -> None:
    uns2 = {**UNSUPPORTED, "quote": "during the visit", "replacement": "at the visit"}
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED, uns2), NOT_WRONG))
    assert EA.run(sr) == 0, log
    assert _record(sr)["confirmed_not_wrong_by_class"] == {"unsupported": 2}


def test_f_falsifier_a_second_finding_confirmed_wrong_holds(scene) -> None:
    uns2 = {**UNSUPPORTED, "quote": "during the visit", "replacement": "at the visit"}
    sr, doc, log = scene(Client(_on("N1", UNSUPPORTED, uns2), NOT_SUPPORTED))
    assert EA.run(sr) == EA.EXIT_HELD and _record(sr)["unresolved_by_class"] == {"unsupported": 2}


# ---- the confirm that never answers ----------------------------------------------------
def test_a_confirm_that_never_parses_holds_as_unconfirmed(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", DATE), {"verdict": "maybe"}))
    assert EA.run(sr) == EA.EXIT_HELD
    rec = _record(sr)
    assert rec["unconfirmed"] == 1 and rec["unresolved_by_class"] == {"date": 1}


def test_a_resume_asks_no_second_reader_twice(scene) -> None:
    sr, doc, log = scene(Client(_on("N1", DATE)))
    assert EA.run(sr) == EA.EXIT_HELD
    again = Client(_on("N1", DATE))
    sr2, _d, _l = scene(again, fresh=False)
    assert EA.run(sr2) == EA.EXIT_HELD and again.calls == []  # every answer on file: nothing paid twice


# ---- G: the rate the rule holds at ------------------------------------------------------
def _hold_rate(scene: Any, verify: dict[str, Any]) -> tuple[int, int]:
    """10 updates of 50 entries, one sentence in 100 flagged as a wrong value,
    every proposed fix refused: (updates held, updates with a flag)."""
    rng = random.Random(7)
    ents = [
        f"03/{(k % 28) + 1:02d}/2025\nSample Therapy | All Other Information\n\nAll Other Information\n"
        + " ".join(f"Item {k} note {w} was recorded at the visit." for w in "abcd")
        + f" (Exhibit 1 - p. {(k % 6) + 1})"
        for k in range(50)
    ]
    doc = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + "\n\n".join(ents) + "\n\n## Exhibit List\n"
    )
    held = flagged = 0
    for run in range(10):
        flags = {k + 1: [s for s in range(1, 5) if rng.random() < 0.01] for k in range(50)}
        flags = {k: v for k, v in flags.items() if v}
        flagged += bool(flags)

        def read(t: str, flags: dict[int, list[int]] = flags) -> str:
            for k, ss in flags.items():
                if f"ENTRY N{k}:" in t or f"ENTRY N{k} (" in t:
                    w = "abcd"[ss[0] - 1]
                    return _reply({"line": f"S{ss[0]}", "class": "value", "exhibit": 1, "page": ((k - 1) % 6) + 1,
                                   "quote": f"note {w}", "action": "replace", "replacement": f"note {w}{w}",
                                   "problem": "x"})  # fmt: skip
            return _reply()

        sr, d, _log = scene(Client(read, verify), doc=doc, fresh=(run == 0))
        shutil.rmtree(sr.slug_dir / "runs" / "alpha" / "entry_read", ignore_errors=True)
        d.write_text(doc)
        held += EA.run(sr) == EA.EXIT_HELD
    return held, flagged


@pytest.mark.parametrize(("verify", "want"), [(NOT_SUPPORTED, "every flagged update"), (NOT_WRONG, 0)])
def test_g_the_hold_rate_follows_the_second_reader(scene, verify, want) -> None:
    held, flagged = _hold_rate(scene, verify)
    assert held == (flagged if want == "every flagged update" else 0), (held, flagged)
    print(
        f"G: held {held} of 10 updates ({flagged} had a flag), second reader {'confirms' if want else 'confirms none'}"
    )


def test_hold_classes_default_to_every_class() -> None:
    from jobs.medchron.update import entry_read as ER

    assert set(EU.HOLD_CLASSES) == set(ER.CLASSES) | {ER.UNCLASSIFIED}


# ---- the one write is atomic --------------------------------------------------------------
def test_a_write_that_fails_midway_leaves_the_document_whole(scene, monkeypatch) -> None:
    """The corrected document is written to a temp file and renamed
    (``matter.store.write_atomic``): a failure before the rename leaves the
    document as it was, never half written."""
    from jobs.matter import store

    def fail(_a: Any, _b: Any) -> None:
        raise OSError("the disk went away")

    sr, doc, log = scene(Client(_on("N1", VALUE)))
    monkeypatch.setattr(store.os, "replace", fail)
    assert EA.run(sr) == EA.EXIT_DEFECT
    assert doc.read_text(encoding="utf-8") == DOC
