"""The line every runner wake writes right after "Run the <skill> skill's ... mode".

A wake reaches the seat as the ``{task}`` of the overlay's handoff prompt
(``translate._INBOUND_HANDOFF_PROMPT``), a webhook turn with no ``skill_view``
and no skill index. On 2026-10-28 two casefile DELIVER wakes on the pilot never
loaded their skill: the model guessed paths, read a skills root (refused), and
wrote a digest memo instead of the reply. The handoff prompt now names the exact
file; this line repeats the order in the task itself, so the first call is the
load. The runner does not know the seat's persona, so the path is the one the
handoff prompt names (the persona's own skills copy), referred to, not guessed.
"""

from __future__ import annotations


def load_line(skill: str) -> str:
    return (
        f"Load it first: your first call is read_file on {skill}/SKILL.md in the skills "
        "directory the handoff instructions above name. Then follow that file exactly; "
        "if it cannot be read, say so and do nothing else."
    )
