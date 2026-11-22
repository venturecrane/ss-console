"""What an update set aside, read and charged before 2026-11-22, page by page.

The change that day reads every page the rules set aside as a copy unless it
is pixel-equal to one already covered, and charges for none of them. Two
builds of it passed their own hand-written cases while charging the firm for
pages it had never been charged for (bug reviews, 2026-11-22), so this
compares the two trees themselves: the runner as it stood at ``BASE``, taken
from this repository's history and never a copy of its rules, and this tree,
over the same generated matters (``update_set_aside_driver``).

* a page the base charged is charged;
* a page the base set aside as a copy is set aside by its picture (or as
  blank, a bill or an expert's report, decided on the page itself), or is read
  and not charged;
* every other page has the base's own answer;
* this tree holds, or crashes, only where the base does;
* the page question asks the base's figure, the charge and its cap are the
  base's, and with no page record the charge is the base's less pages now read
  that the base set aside.

``BASE`` is pinned: compared with a moving main it would compare this tree
with itself the day it merged. A later change that means to move one of these
answers removes this test with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
JOBS = TESTS.parent
REPO = JOBS.parents[2]
BASE = "675bc24b7f2cf7fd5d885746837d13cd0360b526"
MATTERS = 200
COPY = {"already_delivered", "repeat", "refiled_extract"}
ON_ITS_OWN = {"blank", "billing", "expert_report"}


def _run(tree: Path, out: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, str(TESTS / "update_set_aside_driver.py"), str(tree), str(MATTERS), str(out)]
    done = subprocess.run(argv, cwd=out.parent, env=env, capture_output=True, text=True)  # noqa: S603 - fixed argv, this repo's own driver
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def both(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict]:
    tmp = tmp_path_factory.mktemp("set-aside")
    tar = subprocess.run(  # noqa: S603 - fixed argv over this repository's own history
        ["git", "archive", BASE, "operator/runners/jobs/jobs"],
        cwd=REPO,
        capture_output=True,
    )
    assert tar.returncode == 0, f"{BASE} is not in this clone (a shallow one?): {tar.stderr.decode()[-300:]}"
    (tmp / "base").mkdir()
    subprocess.run(["tar", "-x", "-C", str(tmp / "base")], input=tar.stdout, check=True)  # noqa: S603 - this repo's own archive
    return _run(tmp / "base" / "operator/runners/jobs", tmp / "base.json"), _run(JOBS, tmp / "now.json")


def differences(base: dict, now: dict) -> list[str]:
    bad: list[str] = []
    for seed, b in base.items():
        n = now[seed]
        if isinstance(n["exit"], str) or (n["exit"] == 2 and b["exit"] != 2):
            bad += [] if n["exit"] == b["exit"] else [f"{seed}: exit {b['exit']} -> {n['exit']}"]
        if "asked" not in b or "asked" not in n:
            continue
        read_now = 0
        # A page the base kept no row for is one it set aside: it numbered a words extract's rows by
        # its pages with text, so a blank page's extract lost its last rows.
        unrecorded = {p: ["excluded", "refiled_extract"] + [False] * 4 for p in n["rows"].keys() - b["rows"].keys()}
        for page, (status, klass, charged, *_rest) in (b["rows"] | unrecorded).items():
            s, k, c, rule, exact, _near = n["rows"].get(page, ["missing", "", False, None, False, False])
            was_copy = status == "excluded" and klass in COPY
            if was_copy and s == "read":
                read_now += 1
            by_pixel = s == "excluded" and (exact or k == "refiled_extract")
            kept = by_pixel or (s == "excluded" and k in ON_ITS_OWN) or (s == "read" and not c and bool(rule))
            if (not kept) if was_copy else ([s, k, c] != [status, klass, charged]):
                bad.append(f"{seed} {page}: {status}/{klass}/{charged} -> {s}/{k}/{c}/{rule}")
        for figure in ("asked", "charged", "capped"):
            if n[figure] != b[figure]:
                bad.append(f"{seed}: {figure} {b[figure]} -> {n[figure]}")
        if not b["fallback"] - read_now <= n["fallback"] <= b["fallback"]:
            bad.append(f"{seed}: fallback {b['fallback']} -> {n['fallback']} with {read_now} now read")
    return bad


def test_every_page_has_the_bases_answer_or_is_read_uncharged(both: tuple[dict, dict]) -> None:
    assert differences(*both) == []


def test_the_matters_reach_every_rule(both: tuple[dict, dict]) -> None:
    """The comparison is worth nothing on matters no rule fires in."""
    base, now = both
    rules = Counter(r[3] for v in now.values() for r in v["rows"].values() if r[3])
    assert set(rules) == {
        "near_picture",
        "text_overlap",
        "run_of_pages",
        "undated_neighbour",
        "twin_by_words",
        "words_extract",
    }, rules
    rows = [r for v in base.values() for r in v["rows"].values()]
    assert sum(r[4] for r in rows) > 50  # set aside by an exact picture
    ours = [r for v in now.values() for r in v["rows"].values()]
    assert sum(r[3] == "near_picture" for r in ours) > 20  # a near picture its words confirm: read, not charged
    assert sum(r[5] and r[2] for r in ours) > 20  # a near picture whose words disagree: read and charged
    for klass in (*COPY, *ON_ITS_OWN, "transcription_failed", "new"):
        assert any(r[1] == klass for r in rows), klass
    assert {v["exit"] for v in base.values()} >= {0, 2}


def test_the_comparison_can_fail(both: tuple[dict, dict]) -> None:
    """Each thing it promises, broken by hand, is reported."""
    base, now = both

    def broken(change) -> list[str]:
        copy = json.loads(json.dumps(now))
        change(copy)
        return differences(base, copy)

    def page(of: dict, want) -> tuple[str, str]:
        return next((s, p) for s, v in of.items() for p, r in v["rows"].items() if want(s, p, r))

    s, p = page(now, lambda _s, _p, r: bool(r[3]))
    assert broken(lambda c: c[s]["rows"][p].__setitem__(2, True))  # a suspect charged
    s2, p2 = page(base, lambda _s, _p, r: r[2])
    assert broken(lambda c: c[s2]["rows"][p2].__setitem__(2, False))  # a page the base charged, not charged
    assert broken(
        lambda c: c[s]["rows"].__setitem__(p, ["excluded", "repeat", False, None, False, False])
    )  # set aside by words
    for figure in ("asked", "charged", "fallback"):
        assert broken(lambda c, f=figure: c[s].__setitem__(f, base[s][f] + 1))
    held = next(k for k, v in base.items() if v["exit"] == 0)
    assert broken(lambda c: c[held].__setitem__("exit", 2))
