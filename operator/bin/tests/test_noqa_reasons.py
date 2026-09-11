"""bin/noqa-reasons.py: every noqa names its rule and carries a reason.

Two claims. The gate flags the three shapes it exists for (bare noqa, no
separator, a reason under 20 characters) and accepts a reasoned line; and the
real tree is clean, so the substrate job's run of the same script is green
for the same reason this test is.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2]
SCRIPT = OPERATOR / "bin" / "noqa-reasons.py"

_spec = importlib.util.spec_from_file_location("noqa_reasons", SCRIPT)
assert _spec and _spec.loader
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)

H = "# "  # assembled at runtime so this test file does not trip the gate itself


def test_flags_bare_missing_separator_and_short_reasons() -> None:
    text = "\n".join(
        [
            f"a = 1  {H}noqa",
            f"b = 2  {H}noqa: BLE001",
            f"c = 3  {H}noqa: E402 short",
            f"d = 4  {H}noqa: E402 - too short",
            f"e = 5  {H}noqa: E402, F401 - two codes and a reason long enough to pass",
            f"f = 6  {H}noqa: S310 — an em dash separator with a reason long enough to pass",
            f"g = 7  {H}noqa: E731 -- a double dash separator with a reason long enough to pass",
            "h = 8  # a plain comment mentioning that noqa exists is not a directive",
        ]
    )
    found = nr.offenders_in_text(text, "x.py")
    assert [f.split(":")[1] for f in found] == ["1", "2", "3", "4"], found
    assert "bare noqa" in found[0]


def test_self_test_passes() -> None:
    assert nr.self_test() == 0


def test_the_tree_is_clean() -> None:
    found = nr.offenders(OPERATOR)
    assert found == [], "\n".join(found)


def test_walk_skips_vendored_and_cache_dirs(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text(f"a = 1  {H}noqa\n", encoding="utf-8")
    (tmp_path / "y.py").write_text(f"a = 1  {H}noqa: E402 - a reason that is long enough to pass\n", encoding="utf-8")
    assert nr.offenders(tmp_path) == []
