#!/usr/bin/env python3
"""Enforce customer.yaml personas[].skills_disabled after Hermes bootstrap.

Hermes bundles a broad skill catalog into every profile. For customer Machines,
customer.yaml is the authority: a persona-level ``skills_disabled`` entry must
remove that skill from the generated profile before the gateway starts. This
guard closes the gap between the overlay materializer and Hermes' bundled skill
prompt cache.

IDENTITY (ss#2313). A skill is named FOUR different ways across the artifacts
this script prunes:

  * the profile skills tree      -> the SKILL.md parent directory's basename
  * the snapshot ``manifest``    -> the same basename, derived from the key path
  * the snapshot ``skills`` list -> ``skill_name`` / ``frontmatter_name`` / ``name``
  * ``.bundled_manifest``        -> the text before the first ``:`` on a line

Matching ONE authored name against four independent derivations is the
partial-removal bug: if a bundled skill's directory basename differs from its
frontmatter name, an authored disable prunes the surfaces that agree with the
authored spelling and leaves the rest. A half-disabled skill is worse than an
enabled one, because every surface honestly reports the layer it touched while
the skill is still reachable ("gone means gone").

So the script no longer matches names. It resolves an IDENTITY: every name a
skill is known by on this profile is unioned into one equivalence class (from
the SKILL.md frontmatter, the directory it lives in, and the snapshot entry that
describes it), the authored ``skills_disabled`` entries are expanded to the
closure of those classes, and every surface is pruned with the SAME expanded
set. A prune that removes one alias necessarily removes them all.

Any expansion beyond the authored spelling is reported on stdout, and an enforce
pass re-scans every surface afterwards and FAILS if anything survives. Partial
is therefore impossible; a surface this script cannot converge is loud.

Usage:
  ensure-disabled-skills.py [--check] CUSTOMER_YAML [HERMES_HOME]
  (HERMES_HOME defaults to $HERMES_HOME or /opt/data.)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"FATAL: {path} is not a YAML mapping")
    return data


def disabled_by_profile(customer_yaml: Path) -> dict[str, set[str]]:
    data = _load_yaml(customer_yaml)
    personas = data.get("personas") or []
    if not isinstance(personas, list):
        return {}

    result: dict[str, set[str]] = {}
    for persona in personas:
        if not isinstance(persona, dict):
            continue
        slug = str(persona.get("slug") or "").strip()
        if not slug:
            continue
        raw_disabled = persona.get("skills_disabled") or []
        if not isinstance(raw_disabled, list):
            continue
        disabled = {str(v).strip() for v in raw_disabled if str(v).strip()}
        if disabled:
            result[slug] = disabled
    return result


def _skill_name_from_manifest_path(path: str) -> str:
    suffix = "/SKILL.md"
    if path.endswith(suffix):
        return Path(path[: -len(suffix)]).name
    return Path(path).stem


def _is_disabled_skill_entry(entry: Any, disabled: set[str]) -> bool:
    if not isinstance(entry, dict):
        return False
    return bool(_entry_names(entry) & disabled)


# --------------------------------------------------------------------------
# Skill identity
# --------------------------------------------------------------------------

_ENTRY_NAME_KEYS = ("skill_name", "frontmatter_name", "name")


def _entry_names(entry: Any) -> set[str]:
    """Every name a snapshot ``skills`` entry claims for its skill."""
    if not isinstance(entry, dict):
        return set()
    names = set()
    for key in _ENTRY_NAME_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    return names


def _frontmatter_name(skill_md: Path) -> str | None:
    """The ``name:`` a SKILL.md declares in its YAML frontmatter, if any.

    Parsed defensively: a bundled skill with malformed or absent frontmatter is
    normal (the directory basename is then its only name) and must not abort a
    disable.
    """
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        head = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    if not isinstance(head, dict):
        return None
    value = head.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identity_groups(profile_dir: Path) -> list[set[str]]:
    """Collect the name-equivalence classes observable on this profile.

    Two sources, both authoritative for the skill they describe:

      * the filesystem — a SKILL.md ties its directory basename (what the dir
        prune and the manifest-key derivation see) to its frontmatter name
        (what the snapshot entry may carry);
      * the snapshot ``skills`` entries — each ties its own name fields
        together, which still bridges the two spellings after a previous run
        has already removed the directory.
    """
    groups: list[set[str]] = []

    skills_dir = profile_dir / "skills"
    if skills_dir.is_dir():
        for skill_md in skills_dir.glob("**/SKILL.md"):
            names = {skill_md.parent.name}
            fm = _frontmatter_name(skill_md)
            if fm:
                names.add(fm)
            if len(names) > 1:
                groups.append(names)

    snapshot_path = profile_dir / ".skills_prompt_snapshot.json"
    if snapshot_path.exists():
        try:
            data = _load_snapshot(snapshot_path)
        except (json.JSONDecodeError, OSError):
            data = {}
        entries = data.get("skills")
        if isinstance(entries, list):
            for entry in entries:
                names = _entry_names(entry)
                if len(names) > 1:
                    groups.append(names)

    return groups


def resolve_disabled_aliases(profile_dir: Path, disabled: set[str]) -> set[str]:
    """Expand authored names to the closure of the identities they name.

    Transitive: a directory basename that resolves to a frontmatter name which a
    snapshot entry ties to a third spelling pulls all three in. Pruning every
    surface with the result is what makes a partial removal impossible.
    """
    groups = _identity_groups(profile_dir)
    expanded = set(disabled)
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group & expanded and not group <= expanded:
                expanded |= group
                changed = True
    return expanded


def _load_snapshot(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"FATAL: {path} is not a JSON object")
    return data


def _prune_snapshot(path: Path, disabled: set[str], check: bool) -> int:
    if not path.exists():
        return 0
    data = _load_snapshot(path)
    removed = 0

    manifest = data.get("manifest")
    if isinstance(manifest, dict):
        remove_keys = [key for key in manifest if _skill_name_from_manifest_path(str(key)) in disabled]
        removed += len(remove_keys)
        if not check:
            for key in remove_keys:
                manifest.pop(key, None)

    skills = data.get("skills")
    if isinstance(skills, list):
        keep = [entry for entry in skills if not _is_disabled_skill_entry(entry, disabled)]
        removed += len(skills) - len(keep)
        if not check:
            data["skills"] = keep

    if removed and not check:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return removed


def _prune_bundled_manifest(path: Path, disabled: set[str], check: bool) -> int:
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    keep: list[str] = []
    removed = 0
    for line in lines:
        name = line.split(":", 1)[0].strip()
        if name in disabled:
            removed += 1
        else:
            keep.append(line)
    if removed and not check:
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return removed


def _disabled_skill_dirs(skills_dir: Path, disabled: set[str]) -> list[Path]:
    if not skills_dir.is_dir():
        return []
    matches: list[Path] = []
    for skill_md in skills_dir.glob("**/SKILL.md"):
        if skill_md.parent.name in disabled:
            matches.append(skill_md.parent)
    return sorted(matches)


def residual_artifacts(profile_dir: Path, disabled: set[str]) -> list[str]:
    """Every artifact on this profile that still exposes a disabled identity.

    Read-only. Used to verify convergence AFTER an enforce pass: the prune
    functions report what they removed, which is not the same claim as "nothing
    is left". A surface this script does not know how to converge shows up here
    instead of shipping silently half-removed.
    """
    residue: list[str] = []
    skills_dir = profile_dir / "skills"

    for path in _disabled_skill_dirs(skills_dir, disabled):
        residue.append(f"skill directory {path}")

    bundled = skills_dir / ".bundled_manifest"
    if bundled.exists():
        for line in bundled.read_text(encoding="utf-8").splitlines():
            if line.split(":", 1)[0].strip() in disabled:
                residue.append(f"{bundled}: {line.split(':', 1)[0].strip()}")

    snapshot_path = profile_dir / ".skills_prompt_snapshot.json"
    if snapshot_path.exists():
        data = _load_snapshot(snapshot_path)
        manifest = data.get("manifest")
        if isinstance(manifest, dict):
            for key in manifest:
                if _skill_name_from_manifest_path(str(key)) in disabled:
                    residue.append(f"{snapshot_path} manifest key {key}")
        entries = data.get("skills")
        if isinstance(entries, list):
            for entry in entries:
                if _is_disabled_skill_entry(entry, disabled):
                    residue.append(f"{snapshot_path} skills entry {sorted(_entry_names(entry))}")

    return residue


def enforce_profile(profile_dir: Path, disabled: set[str], check: bool) -> int:
    # Resolve identities BEFORE pruning — the directories carrying the
    # frontmatter that ties two spellings together are about to be deleted.
    disabled = resolve_disabled_aliases(profile_dir, disabled)

    skills_dir = profile_dir / "skills"
    removed = 0
    dirs = _disabled_skill_dirs(skills_dir, disabled)
    removed += len(dirs)
    if not check:
        for path in dirs:
            shutil.rmtree(path)

    removed += _prune_bundled_manifest(skills_dir / ".bundled_manifest", disabled, check)
    removed += _prune_snapshot(profile_dir / ".skills_prompt_snapshot.json", disabled, check)
    return removed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remove customer-disabled bundled skills from Hermes profiles.")
    parser.add_argument("--check", action="store_true", help="verify only")
    parser.add_argument("customer_yaml", help="path to customer.yaml")
    parser.add_argument(
        "hermes_home",
        nargs="?",
        default=os.environ.get("HERMES_HOME", "/opt/data"),
        help="Hermes home dir (default: $HERMES_HOME or /opt/data)",
    )
    args = parser.parse_args(argv[1:])

    tag = "ensure-disabled-skills"
    home = Path(args.hermes_home)
    disabled_map = disabled_by_profile(Path(args.customer_yaml))
    if not disabled_map:
        print(f"[{tag}] no persona skills_disabled entries found")
        return 0

    failures = 0
    touched = 0
    for slug, disabled in sorted(disabled_map.items()):
        profile_dir = home / "profiles" / slug
        if not profile_dir.is_dir():
            print(f"[{tag}] profile not found for persona {slug}: {profile_dir}", file=sys.stderr)
            failures += 1
            continue
        # Report any spelling this profile knows the skill by beyond what
        # customer.yaml authored. Silence here means all four derivations agree;
        # a line here is the exact condition that used to half-prune.
        aliases = resolve_disabled_aliases(profile_dir, disabled)
        if aliases - disabled:
            print(
                f"[{tag}] {slug}: disabled skills are also known as "
                f"{', '.join(sorted(aliases - disabled))} on this profile — "
                f"pruning every alias together"
            )

        removed = enforce_profile(profile_dir, disabled, args.check)
        touched += removed
        if args.check and removed:
            print(
                f"[{tag}] CHECK FAILED: {profile_dir} still exposes disabled skills: {', '.join(sorted(aliases))}",
                file=sys.stderr,
            )
            failures += 1
            continue

        action = "would remove" if args.check else "removed"
        print(f"[{tag}] {slug}: {action} {removed} disabled skill artifact(s)")

        # Convergence check. "Removed N" is a claim about what this pass TOUCHED;
        # it is not a claim that the skill is gone. Re-read every surface and
        # refuse to exit 0 on a partial prune (ss#2313).
        #
        # One reconverge before failing. bootstrap.sh step 7b.1 runs this with
        # `|| die`, and Hermes' gateway startup sync can rehydrate skill
        # directories; a transient must not become a boot crash (the overlay#252
        # lesson). Residue that survives a second full pass is not transient —
        # and a re-exposed bundled skill is a governance-bypass path back to the
        # raw connector, so failing closed there is the correct outcome.
        if not args.check:
            residue = residual_artifacts(profile_dir, aliases)
            if residue:
                print(
                    f"[{tag}] {slug}: {len(residue)} artifact(s) survived the first pass; reconverging once",
                    file=sys.stderr,
                )
                enforce_profile(profile_dir, disabled, check=False)
                residue = residual_artifacts(profile_dir, aliases)
            if residue:
                print(
                    f"[{tag}] FAILED: {slug} is PARTIALLY disabled — these artifacts survived "
                    f"two prune passes:\n  " + "\n  ".join(residue),
                    file=sys.stderr,
                )
                failures += 1

    if failures:
        return 1
    print(f"[{tag}] done; {touched} artifact(s) {'found' if args.check else 'updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
