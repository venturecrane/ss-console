"""Every shared-module copy in a skill folder is its source's bytes, and the yaml lists it.

operator/contracts/skill-shared-modules.yaml names one source per shared module
and the skills that carry a copy; operator/bin/gen-skill-shared.py writes the
copies. This is the CI guard that a source edit cannot reach a seat without its
copies, that nobody edits a copy instead of the source, and that a copy pasted
into a new skill by hand is declared. It replaced nine hand-kept sync tests
(test_broker_writer_sync, test_case_alert_routing_sync, test_casework_ledger_sync,
test_casework_view_sync, test_digest_items_sync, test_done_since_sync,
test_escalation_ledger_sync, test_skill_helpers_sync and the stamp half of
test_pre_run_gate), each of which globbed for its own copies.

Run::

    cd operator && python3 -m pytest bin/tests/test_skill_shared_generation.py -v
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from bin.lib import skill_shared_gen as gen

ROOT = gen.repo_root()

_GATE = '''"""An empty-seat gate."""


def probe(x):
    return x + 1


def main():
    print(probe(1))
'''

_BESPOKE = '''"""A bespoke pre_run that shares nothing with the gate."""


def decide(matters):
    return bool(matters)
'''

_YAML = """modules:
  gate:
    source: operator/templates/gate.py
    copy_as: pre_run.py
    skills: [alpha, beta]
  helpers:
    source: operator/skills/alpha/helpers.py
    copy_as: helpers.py
    skills: [beta]
"""


def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_skill_shared_under_test", ROOT / gen.GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small repo: one gate stamped as pre_run.py, one helper whose source is in a skill folder."""
    files = {
        gen.CONTRACT_REL: _YAML,
        "operator/templates/gate.py": _GATE,
        "operator/skills/alpha/helpers.py": "def helper():\n    return 1\n",
        "operator/skills/gamma/pre_run.py": _BESPOKE,
        "operator/skills/gamma/SKILL.md": "gamma\n",
    }
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (tmp_path / "operator/skills/beta").mkdir()
    monkeypatch.setattr(gen, "repo_root", lambda: tmp_path)
    return tmp_path


def _check(cli: ModuleType) -> int:
    return cli.main(["gen-skill-shared.py", "--check"])


def test_the_committed_tree_is_in_sync() -> None:
    # The CI gate: --check on the real tree, exactly what a person runs.
    assert _check(_cli()) == 0


def test_the_real_yaml_declares_copies_that_exist() -> None:
    # Not vacuous: the yaml names real copies, and each is on disk now.
    modules = gen.load(ROOT / gen.CONTRACT_REL)
    declared = gen.copies(ROOT, modules)
    assert declared, "the yaml declares no copies"
    assert all(path.is_file() for path in declared)


def test_generate_writes_the_source_bytes_and_check_then_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path, monkeypatch)
    cli = _cli()
    assert _check(cli) == 1, "three copies are missing, so --check must fail first"
    assert cli.main(["gen-skill-shared.py"]) == 0
    assert (root / "operator/skills/alpha/pre_run.py").read_bytes() == _GATE.encode()
    assert (root / "operator/skills/beta/pre_run.py").read_bytes() == _GATE.encode()
    assert (root / "operator/skills/beta/helpers.py").read_bytes() == b"def helper():\n    return 1\n"
    # The source inside a skill folder is read, never written; the bespoke file is left alone.
    assert (root / "operator/skills/gamma/pre_run.py").read_text(encoding="utf-8") == _BESPOKE
    assert _check(cli) == 0


def test_editing_a_copy_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path, monkeypatch)
    cli = _cli()
    cli.main(["gen-skill-shared.py"])
    copy = root / "operator/skills/beta/pre_run.py"
    copy.write_text(_GATE.replace("x + 1", "x + 2"), encoding="utf-8")
    assert _check(cli) == 1
    assert any(p.startswith("differs: operator/skills/beta/pre_run.py") for p in gen.problems(root, _load(root)))
    # Regenerating restores the source's bytes over the hand edit.
    cli.main(["gen-skill-shared.py"])
    assert copy.read_bytes() == _GATE.encode()


def test_editing_the_source_without_regenerating_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path, monkeypatch)
    cli = _cli()
    cli.main(["gen-skill-shared.py"])
    (root / "operator/skills/alpha/helpers.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    assert _check(cli) == 1
    cli.main(["gen-skill-shared.py"])
    assert _check(cli) == 0


def test_a_copy_pasted_into_an_unlisted_skill_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path, monkeypatch)
    cli = _cli()
    cli.main(["gen-skill-shared.py"])
    delta = root / "operator/skills/delta"
    delta.mkdir()
    # An edited paste still shares probe()'s body with the gate, so it is a copy.
    (delta / "pre_run.py").write_text(_GATE.replace("print(probe(1))", "print(probe(9))"), encoding="utf-8")
    assert _check(cli) == 1
    assert gen.undeclared(root, _load(root)) == [delta / "pre_run.py"]
    # The bespoke pre_run.py in gamma shares no body, so it is never called a copy.
    (delta / "pre_run.py").unlink()
    assert gen.undeclared(root, _load(root)) == []
    assert _check(cli) == 0


def _load(root: Path) -> list[gen.Module]:
    return gen.load(root / gen.CONTRACT_REL)


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("modules: {}\n", "at least one module"),
        ("modules:\n  x:\n    source: a.py\n    copy_as: a.py\n", "exactly the fields"),
        ("modules:\n  x:\n    source: /abs.py\n    copy_as: a.py\n    skills: [s]\n", "repo-relative"),
        ("modules:\n  x:\n    source: a.py\n    copy_as: sub/a.py\n    skills: [s]\n", "plain .py file name"),
        ("modules:\n  x:\n    source: a.py\n    copy_as: a.py\n    skills: [_shared]\n", "valid skill folder"),
        ("modules:\n  x:\n    source: a.py\n    copy_as: a.py\n    skills: [s, s]\n", "more than one module"),
        (
            "modules:\n  x:\n    source: operator/skills/s/a.py\n    copy_as: a.py\n    skills: [s]\n",
            "copy of itself",
        ),
    ],
)
def test_a_bad_yaml_is_refused(tmp_path: Path, body: str, why: str) -> None:
    bad = tmp_path / "skill-shared-modules.yaml"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=why):
        gen.load(bad)
