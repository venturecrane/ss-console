"""gen-image-manifest.py: the manifest the CI config publisher trusts.

The publisher refuses a config naming a skill or pre_run absent from this
manifest, so a manifest that lists too much would let the 2026-10-13 outage
through. These pin the shape the publisher reads: every skill dir, every file
inside it (pre_run scripts included), the pin from the injected hash.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "gen-image-manifest.py"
REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _module():
    spec = importlib.util.spec_from_file_location("gen_image_manifest", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lists_every_skill_file_and_the_pin(tmp_path: Path) -> None:
    (tmp_path / "with-pre-run" / "references").mkdir(parents=True)
    (tmp_path / "with-pre-run" / "SKILL.md").write_text("x")
    (tmp_path / "with-pre-run" / "pre_run.py").write_text("y")
    (tmp_path / "with-pre-run" / "references" / "a.md").write_text("z")
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "SKILL.md").write_text("x")

    manifest = _module().build_manifest(tmp_path, "abc1234", lambda d: f"{d.name}-hash")

    assert manifest["schema_version"] == 1
    assert manifest["built_from"] == "abc1234"
    assert manifest["skills"] == {
        "plain": {"pin": "plain-", "files": ["SKILL.md"]},
        "with-pre-run": {
            "pin": "with-p",
            "files": ["SKILL.md", "pre_run.py", "references/a.md"],
        },
    }


def test_a_skill_without_its_pre_run_does_not_list_one(tmp_path: Path) -> None:
    """The incident's image: the skill dir exists, the pre_run does not."""
    (tmp_path / "connector-auth-check").mkdir()
    (tmp_path / "connector-auth-check" / "SKILL.md").write_text("x")

    manifest = _module().build_manifest(tmp_path, "", lambda d: "000000")

    assert manifest["built_from"] == "unknown"
    assert manifest["skills"]["connector-auth-check"]["files"] == ["SKILL.md"]


def test_refuses_an_empty_catalog(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _module().build_manifest(tmp_path, "", lambda d: "000000")


def test_the_real_catalog_lists_the_incident_pre_run() -> None:
    manifest = _module().build_manifest(REPO_SKILLS, "", lambda d: "000000")
    assert "pre_run.py" in manifest["skills"]["connector-auth-check"]["files"]
