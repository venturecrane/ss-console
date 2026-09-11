from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ensure-disabled-skills.py"


def _module():
    """Import the script by path (its filename is not a valid module name)."""
    spec = importlib.util.spec_from_file_location("ensure_disabled_skills", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_customer_yaml(path: Path) -> None:
    path.write_text(
        """
personas:
  - slug: crane
    skills_disabled:
      - himalaya
      - google-workspace
""".lstrip(),
        encoding="utf-8",
    )


def seed_profile(home: Path) -> Path:
    profile = home / "profiles" / "crane"
    skills = profile / "skills"
    (skills / "email" / "himalaya").mkdir(parents=True)
    (skills / "productivity" / "google-workspace").mkdir(parents=True)
    (skills / "productivity" / "notion").mkdir(parents=True)
    (skills / "email" / "himalaya" / "SKILL.md").write_text("himalaya", encoding="utf-8")
    (skills / "productivity" / "google-workspace" / "SKILL.md").write_text("google", encoding="utf-8")
    (skills / "productivity" / "notion" / "SKILL.md").write_text("notion", encoding="utf-8")
    (skills / ".bundled_manifest").write_text(
        "himalaya:abc\ngoogle-workspace:def\nnotion:ghi\n",
        encoding="utf-8",
    )
    (profile / ".skills_prompt_snapshot.json").write_text(
        json.dumps(
            {
                "version": 1,
                "manifest": {
                    "email/himalaya/SKILL.md": [1, 2],
                    "productivity/google-workspace/SKILL.md": [1, 2],
                    "productivity/notion/SKILL.md": [1, 2],
                },
                "skills": [
                    {"skill_name": "himalaya", "category": "email"},
                    {"skill_name": "google-workspace", "category": "productivity"},
                    {"skill_name": "notion", "category": "productivity"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return profile


def run_script(customer_yaml: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(customer_yaml), str(home)],
        check=False,
        text=True,
        capture_output=True,
    )


def test_check_fails_when_disabled_skills_are_exposed(tmp_path: Path) -> None:
    customer_yaml = tmp_path / "customer.yaml"
    write_customer_yaml(customer_yaml)
    seed_profile(tmp_path / "hermes")

    result = run_script(customer_yaml, tmp_path / "hermes", "--check")

    assert result.returncode == 1
    assert "CHECK FAILED" in result.stderr


def test_enforce_removes_disabled_skill_artifacts(tmp_path: Path) -> None:
    customer_yaml = tmp_path / "customer.yaml"
    write_customer_yaml(customer_yaml)
    profile = seed_profile(tmp_path / "hermes")

    result = run_script(customer_yaml, tmp_path / "hermes")

    assert result.returncode == 0, result.stderr
    assert not (profile / "skills" / "email" / "himalaya").exists()
    assert not (profile / "skills" / "productivity" / "google-workspace").exists()
    assert (profile / "skills" / "productivity" / "notion").is_dir()
    assert (profile / "skills" / ".bundled_manifest").read_text(encoding="utf-8") == "notion:ghi\n"

    snapshot = json.loads((profile / ".skills_prompt_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["manifest"] == {"productivity/notion/SKILL.md": [1, 2]}
    assert snapshot["skills"] == [{"skill_name": "notion", "category": "productivity"}]

    check = run_script(customer_yaml, tmp_path / "hermes", "--check")
    assert check.returncode == 0, check.stderr


# ---------------------------------------------------------------------------
# Identity mismatch: directory basename != frontmatter name (ss#2313)
# ---------------------------------------------------------------------------
#
# The fixture above gives every skill the SAME name on all four surfaces, so all
# four derivations agree by construction and the suite passed on both correct
# and half-pruning code. These seed the disagreement instead.
#
# `gcal` is the directory; `google-calendar` is the frontmatter name, which is
# also what the snapshot entry and .bundled_manifest carry. Disabling by EITHER
# spelling must remove BOTH — a prune that takes the snapshot entry and leaves
# the directory (or the reverse) ships a half-disabled skill, which is the
# "gone means gone" failure class: the directory still holds the prompt the
# gateway loads.


def write_mismatch_customer_yaml(path: Path, disabled_name: str) -> None:
    path.write_text(
        f"""
personas:
  - slug: crane
    skills_disabled:
      - {disabled_name}
""".lstrip(),
        encoding="utf-8",
    )


def seed_mismatch_profile(home: Path) -> Path:
    """A profile where one skill's directory basename != its frontmatter name."""
    profile = home / "profiles" / "crane"
    skills = profile / "skills"
    (skills / "productivity" / "gcal").mkdir(parents=True)
    (skills / "productivity" / "notion").mkdir(parents=True)
    (skills / "productivity" / "gcal" / "SKILL.md").write_text(
        "---\nname: google-calendar\ndescription: calendar things\n---\n\nbody\n",
        encoding="utf-8",
    )
    (skills / "productivity" / "notion" / "SKILL.md").write_text(
        "---\nname: notion\ndescription: notes\n---\n\nbody\n", encoding="utf-8"
    )
    (skills / ".bundled_manifest").write_text("google-calendar:abc\nnotion:ghi\n", encoding="utf-8")
    (profile / ".skills_prompt_snapshot.json").write_text(
        json.dumps(
            {
                "version": 1,
                "manifest": {
                    "productivity/gcal/SKILL.md": [1, 2],
                    "productivity/notion/SKILL.md": [1, 2],
                },
                "skills": [
                    {"skill_name": "google-calendar", "category": "productivity"},
                    {"skill_name": "notion", "category": "productivity"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return profile


def assert_fully_disabled(profile: Path) -> None:
    """No surface may still expose the skill. Partial is the defect."""
    assert not (profile / "skills" / "productivity" / "gcal").exists(), (
        "skill DIRECTORY survived — the gateway still loads this prompt"
    )
    assert (profile / "skills" / "productivity" / "notion").is_dir(), "an unrelated skill was pruned"
    assert (profile / "skills" / ".bundled_manifest").read_text(encoding="utf-8") == "notion:ghi\n"

    snapshot = json.loads((profile / ".skills_prompt_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["manifest"] == {"productivity/notion/SKILL.md": [1, 2]}
    assert snapshot["skills"] == [{"skill_name": "notion", "category": "productivity"}]


def test_disable_by_frontmatter_name_also_removes_the_directory(tmp_path: Path) -> None:
    customer_yaml = tmp_path / "customer.yaml"
    write_mismatch_customer_yaml(customer_yaml, "google-calendar")
    profile = seed_mismatch_profile(tmp_path / "hermes")

    result = run_script(customer_yaml, tmp_path / "hermes")

    assert result.returncode == 0, result.stderr
    assert_fully_disabled(profile)

    check = run_script(customer_yaml, tmp_path / "hermes", "--check")
    assert check.returncode == 0, check.stderr


def test_disable_by_directory_name_also_removes_snapshot_and_manifest(tmp_path: Path) -> None:
    customer_yaml = tmp_path / "customer.yaml"
    write_mismatch_customer_yaml(customer_yaml, "gcal")
    profile = seed_mismatch_profile(tmp_path / "hermes")

    result = run_script(customer_yaml, tmp_path / "hermes")

    assert result.returncode == 0, result.stderr
    assert_fully_disabled(profile)

    check = run_script(customer_yaml, tmp_path / "hermes", "--check")
    assert check.returncode == 0, check.stderr


def test_alias_expansion_is_reported(tmp_path: Path) -> None:
    """A disagreement between derivations must be visible, not silently absorbed."""
    customer_yaml = tmp_path / "customer.yaml"
    write_mismatch_customer_yaml(customer_yaml, "google-calendar")
    seed_mismatch_profile(tmp_path / "hermes")

    result = run_script(customer_yaml, tmp_path / "hermes")

    assert result.returncode == 0, result.stderr
    assert "also known as gcal" in result.stdout, result.stdout


def test_check_detects_a_half_disabled_skill(tmp_path: Path) -> None:
    """--check must fail on a profile a previous partial prune left behind.

    This is the state the old code could produce: the snapshot entry and the
    bundled manifest were pruned by the authored spelling, the DIRECTORY was not.
    Nothing then reported the skill as still loadable.
    """
    customer_yaml = tmp_path / "customer.yaml"
    write_mismatch_customer_yaml(customer_yaml, "google-calendar")
    profile = seed_mismatch_profile(tmp_path / "hermes")

    # Hand-build the half-pruned state: only the directory survives.
    (profile / "skills" / ".bundled_manifest").write_text("notion:ghi\n", encoding="utf-8")
    (profile / ".skills_prompt_snapshot.json").write_text(
        json.dumps(
            {
                "version": 1,
                "manifest": {"productivity/notion/SKILL.md": [1, 2]},
                "skills": [{"skill_name": "notion", "category": "productivity"}],
            }
        ),
        encoding="utf-8",
    )

    result = run_script(customer_yaml, tmp_path / "hermes", "--check")

    assert result.returncode == 1, (
        "a surviving skill directory is a loadable skill; --check must fail\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "CHECK FAILED" in result.stderr


def test_residual_artifacts_can_actually_fire(tmp_path: Path) -> None:
    """The convergence instrument must observe residue, not just report zero.

    A post-prune check that returns [] on every input would make an enforce pass
    look converged forever (Law 12: a check that cannot fail measured nothing).
    Exercise it directly on both a half-pruned and a clean profile.
    """
    mod = _module()
    profile = seed_mismatch_profile(tmp_path / "hermes")
    aliases = mod.resolve_disabled_aliases(profile, {"google-calendar"})
    assert aliases == {"google-calendar", "gcal"}, aliases

    residue = mod.residual_artifacts(profile, aliases)
    assert residue, "residual_artifacts saw nothing on a fully un-pruned profile"
    joined = "\n".join(residue)
    assert "gcal" in joined and "google-calendar" in joined, joined

    mod.enforce_profile(profile, {"google-calendar"}, check=False)
    assert mod.residual_artifacts(profile, aliases) == []
