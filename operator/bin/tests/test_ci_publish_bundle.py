"""scripts/ci-publish-bundle.sh: the content check reads a listing file, never a pipe.

2026-10-31, the first real publish (run 41238271283): `tar -tf | grep -qx` under
`set -o pipefail` reported "the tarball holds no medchron/.venv/bin/medchron"
right after `tar: stdout: write error`. grep -q exits at its first match, tar
then fails writing the rest of the listing to the closed pipe, and pipefail
turns a FOUND entry into a failed check.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci-publish-bundle.sh"


def _code() -> str:
    return "\n".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )


def test_no_tar_listing_is_piped_into_an_early_exit_grep() -> None:
    assert not re.search(r"tar\s+-t\S*[^\n|]*\|\s*grep\s+-\S*q", _code())


def test_the_check_still_requires_both_entry_points() -> None:
    code = _code()
    assert 'tar -tf "${work}/out/bundle.tar" > "${work}/listing.txt"' in code
    for entry in ("medchron/.venv/bin/medchron", "connectors/smokeball/.venv/bin/smokeball-mcp"):
        assert entry in code
    assert 'grep -qxF "${entry}" "${work}/listing.txt"' in code
