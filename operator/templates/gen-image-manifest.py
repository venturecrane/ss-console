"""Bake the image's skill manifest: what this image can run (2026-10-13 A&P outage).

Run once at image build, after the skill catalog is copied to /app/skills and
the overlay is installed. Writes /app/image-manifest.json:

    {"schema_version": 1, "built_from": "<ss-console sha or 'unknown'>",
     "skills": {"<name>": {"pin": "<6hex>", "files": ["SKILL.md", "pre_run.py", ...]}}}

The entrypoint uploads it to R2 (vaults/<slug>/image-manifest.json) on every
boot, and the CI config publisher (scripts/ci-publish-customer-configs.sh ->
scripts/check-config-against-image.mjs) refuses a customer.yaml that names a
skill, pin or pre_run script missing from it.

``pin`` comes from the overlay's own boot hash (``bootstrap.translate.
_skill_content_hash``), imported rather than re-implemented so the manifest can
never disagree with the pin check the boot runs. The import fails the build if
the overlay is absent, which is the point: a manifest that cannot be trusted is
worse than none.

Usage: gen-image-manifest.py <skills-root> <out.json> [built-from-sha]
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path


def build_manifest(skills_root: Path, built_from: str, content_hash: Callable[[Path], str]) -> dict:
    skills: dict[str, dict] = {}
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        files = sorted(f.relative_to(skill_dir).as_posix() for f in skill_dir.rglob("*") if f.is_file())
        skills[skill_dir.name] = {"pin": content_hash(skill_dir)[:6], "files": files}
    if not skills:
        raise SystemExit(f"gen-image-manifest: no skills under {skills_root}; refusing an empty manifest")
    return {"schema_version": 1, "built_from": built_from or "unknown", "skills": skills}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    from bootstrap.translate import _skill_content_hash  # the overlay's boot hash

    manifest = build_manifest(Path(argv[1]), argv[3] if len(argv) == 4 else "", _skill_content_hash)
    Path(argv[2]).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"image manifest: {len(manifest['skills'])} skill(s), built from {manifest['built_from']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
