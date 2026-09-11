"""The discovery machinery behind the vendored-copy sync gates can fail.

A gate that discovers copies by glob has a failure mode a tuple does not: a
glob that matches nothing passes every per-copy assertion vacuously. These
tests plant copies in a temporary skills tree and prove each detector finds
what it should, misses what it should, and that the floor turns an empty
discovery red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vendored_sync import assert_byte_identical, discover_copies, discover_stamps, function_body_hashes

_TEMPLATE = '''"""A template."""


def probe(x):
    return x + 1


def main():
    print(probe(1))
'''

_BESPOKE = '''"""A bespoke pre_run that shares nothing with the template."""


def decide(matters):
    return bool(matters)
'''


def _skills_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    skills = tmp_path / "skills"
    for rel, text in files.items():
        target = skills / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return skills


def test_discover_copies_globs_by_name_and_skips_the_canonical(tmp_path: Path) -> None:
    skills = _skills_tree(tmp_path, {"a/mod.py": "x = 1\n", "b/mod.py": "x = 1\n", "c/other.py": "x = 1\n"})
    found = discover_copies("mod.py", skills_dir=skills)
    assert [p.parent.name for p in found] == ["a", "b"]
    found = discover_copies("mod.py", exclude=skills / "a" / "mod.py", skills_dir=skills)
    assert [p.parent.name for p in found] == ["b"]


def test_discover_stamps_finds_pristine_and_edited_stamps_but_not_bespoke_files(tmp_path: Path) -> None:
    template = tmp_path / "template.py"
    template.write_text(_TEMPLATE, encoding="utf-8")
    edited = _TEMPLATE.replace("print(probe(1))", "print(probe(2))")  # main changed, probe still shared
    skills = _skills_tree(
        tmp_path,
        {"pristine/pre_run.py": _TEMPLATE, "edited/pre_run.py": edited, "bespoke/pre_run.py": _BESPOKE},
    )
    stamps = discover_stamps(template, "pre_run.py", skills_dir=skills)
    assert [p.parent.name for p in stamps] == ["edited", "pristine"]


def test_function_body_hashes_ignore_the_name_but_not_the_body(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def probe(x):\n    return x + 1\n", encoding="utf-8")
    b.write_text("def _probe(x):\n    return x + 1\n", encoding="utf-8")
    assert function_body_hashes(a)["probe"] == function_body_hashes(b)["_probe"]
    b.write_text("def probe(x):\n    return x + 2\n", encoding="utf-8")
    assert function_body_hashes(a)["probe"] != function_body_hashes(b)["probe"]


def test_assert_byte_identical_fails_on_drift_and_on_an_empty_discovery(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.py"
    canonical.write_text("x = 1\n", encoding="utf-8")
    skills = _skills_tree(tmp_path, {"a/canonical.py": "x = 1\n", "b/canonical.py": "x = 2\n"})
    with pytest.raises(AssertionError, match="drifted"):
        assert_byte_identical(canonical, discover_copies("canonical.py", skills_dir=skills), floor=2, restamp_hint="")
    with pytest.raises(AssertionError, match="at least 1 vendored copies"):
        assert_byte_identical(canonical, [], floor=1, restamp_hint="")
    (skills / "b" / "canonical.py").write_text("x = 1\n", encoding="utf-8")
    assert_byte_identical(canonical, discover_copies("canonical.py", skills_dir=skills), floor=2, restamp_hint="")
