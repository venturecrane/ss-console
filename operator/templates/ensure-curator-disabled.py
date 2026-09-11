#!/usr/bin/env python3
"""Ensure the Hermes curator is disabled in every per-profile config.yaml.

ADR 0017 (2026-05-28) disables the autonomous Hermes curator per-customer. The
curator runs an LLM consolidation pass over agent-authored skills on a 7-day
cron (`agent/curator.py:_run_llm_review()`), rewriting/consolidating skill
content via `skill_manage`. That out-of-band rewrite corrupts our audit
provenance and produces unsupervised structural skill drift, so we turn it off
and run consolidation only under Captain supervision via `--dry-run`. See
docs/adr/0017-skill-curator-disposition.md and venturecrane/ss-console#1135.

This script is the Machine-entrypoint belt. The declarative home for the flag
is the overlay's customer.yaml -> config translation (#1135 task in
venturecrane/hermes-smd-overlay); this guard guarantees the curator is off
*before* Hermes (and its gateway cron ticker) starts, independent of whether
the overlay has shipped the same flag, and closes the fresh-install ticker
footgun documented in NousResearch/hermes-agent#18373.

Config surface: the curator reads `get_hermes_home()/config.yaml`, and its
state file (`.curator_state`) plus the skills it manages both live under the
profile home (`$HERMES_HOME/profiles/<slug>/`). So the curator resolves its
home to the active profile dir, and the authoritative config is each
`profiles/<slug>/config.yaml`.

Modes:
  enforce (default):  merge `curator.enabled: false` into each profile config,
                      preserving all other keys. Idempotent.
  --check:            verify-only; exit non-zero if any profile config is
                      missing the flag (used by the boot smoke test).

Usage:
  ensure-curator-disabled.py [--check] [HERMES_HOME]
  (HERMES_HOME defaults to the $HERMES_HOME env var, then /opt/data.)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


def profile_configs(hermes_home: Path) -> list[Path]:
    """Every per-profile config.yaml under <hermes_home>/profiles/<slug>/."""
    profiles_dir = hermes_home / "profiles"
    if not profiles_dir.is_dir():
        return []
    return sorted(profiles_dir.glob("*/config.yaml"))


def _load(path: Path) -> dict:
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise SystemExit(f"FATAL: {path} is not a YAML mapping")
    return cfg


def is_disabled(path: Path) -> bool:
    """True iff curator.enabled is explicitly False in the config at path."""
    curator = _load(path).get("curator")
    return isinstance(curator, dict) and curator.get("enabled") is False


def disable(path: Path) -> bool:
    """Set curator.enabled=False, preserving other keys. Returns True if changed."""
    cfg = _load(path)
    curator = cfg.get("curator")
    if not isinstance(curator, dict):
        curator = {}
    if curator.get("enabled") is False:
        return False
    curator["enabled"] = False
    cfg["curator"] = curator
    with path.open("w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Disable the Hermes curator per profile.")
    parser.add_argument(
        "--check", action="store_true", help="verify-only; exit non-zero if any profile config is not disabled"
    )
    parser.add_argument(
        "hermes_home",
        nargs="?",
        default=os.environ.get("HERMES_HOME", "/opt/data"),
        help="Hermes home dir (default: $HERMES_HOME or /opt/data)",
    )
    args = parser.parse_args(argv[1:])

    home = Path(args.hermes_home)
    configs = profile_configs(home)
    tag = "ensure-curator-disabled"

    if not configs:
        # No profiles is a failure in --check (the smoke test runs after
        # bootstrap, profiles must exist) and a loud warning in enforce mode.
        print(f"[{tag}] no profile configs found under {home}/profiles/", file=sys.stderr)
        return 1 if args.check else 0

    if args.check:
        bad = [str(p) for p in configs if not is_disabled(p)]
        if bad:
            print(f"[{tag}] CHECK FAILED: curator not disabled in: {', '.join(bad)}", file=sys.stderr)
            return 1
        print(f"[{tag}] CHECK OK: curator disabled in {len(configs)} profile(s)")
        return 0

    changed = 0
    for path in configs:
        if disable(path):
            print(f"[{tag}] curator disabled in {path}")
            changed += 1
        else:
            print(f"[{tag}] already disabled: {path}")
    print(f"[{tag}] done; {changed} of {len(configs)} profile config(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
