"""Every runner wake orders the skill load first (the 2026-10-28 pilot defect).

A wake is the ``{task}`` of the overlay's handoff prompt: a webhook turn with no
``skill_view`` and no skill index. Two casefile DELIVER wakes on the pilot never
loaded their skill and wrote a digest memo instead of the reply. These pin that
the line exists, names the file and the tool, and that EVERY wake builder in the
package writes it straight after its "Run the <skill> skill's ... mode" line, so a
new lane cannot ship a wake without it.
"""

from __future__ import annotations

import re
from pathlib import Path

import medchron
from medchron.litigation.reassign import wake as reassign_wake
from medchron.negotiation_lane import wake_task as negotiation_wake
from medchron.note_lane import wake_task as note_wake
from medchron.skill_load import load_line

PKG = Path(medchron.__file__).parent
_RUN_LINE = re.compile(r"Run the (?:\{[A-Za-z_]+\}|[a-z0-9-]+) skill's [A-Z]+ mode for ")
JOB = "01J0000000000000000000000A"


def test_the_line_names_the_skill_file_and_the_tool() -> None:
    line = load_line("case-file-manager")
    assert "case-file-manager/SKILL.md" in line
    assert "read_file" in line and "first call" in line
    assert "say so and do nothing else" in line
    # Never a "<kind> job <ULID>" or a "Trigger:" line: the overlay parses those.
    assert " job " not in line and not line.startswith("Trigger:")


def test_every_wake_builder_in_the_package_writes_the_load_line_next() -> None:
    sites = 0
    for path in sorted(PKG.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, text in enumerate(lines):
            if not _RUN_LINE.search(text) or text.lstrip().startswith("#"):
                continue
            sites += 1
            same_or_next = text + (lines[i + 1] if i + 1 < len(lines) else "")
            assert "load_line(" in same_or_next, f"{path.name}:{i + 1} wakes without the load line"
    # casefile, chronology, demand, drafting, litigation x2, reassign, negotiation, note
    assert sites >= 9


def test_the_composed_wakes_carry_it_second() -> None:
    assert negotiation_wake(JOB, "notice").splitlines()[1] == load_line("negotiation-watch")
    assert note_wake(JOB).splitlines()[1] == load_line("smd-note")
    task = reassign_wake.compose(
        job_id=JOB, skill="litigation-status", state="delivered", fields={}, env={}, trig="request",
        stage=None, detail={}, hold_line=None,
    )  # fmt: skip
    assert task.splitlines()[1] == load_line("litigation-status")
