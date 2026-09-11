#!/usr/bin/env python3
"""Fail closed if Telegram would run without an allowlist (ADR 0033).

The pinned Hermes ref **fails OPEN** on an empty Telegram allowlist — its
authorizer is ``if not allowed_csv: return True`` (``gateway/platforms/telegram.py``),
so a bot with ``TELEGRAM_BOT_TOKEN`` set but no ``TELEGRAM_ALLOWED_USERS`` answers
*anyone* who finds it. Setting ``TELEGRAM_BOT_TOKEN`` alone also AUTO-ENABLES the
platform (``gateway/config.py:_apply_env_overrides``). This guard makes that
fail-open branch structurally unreachable in our deployment: if the token is
present, an allowlist MUST be resolvable, or the Machine refuses to boot.

This is the Machine-entrypoint belt. The authored source of truth is
``customer.yaml`` ``telegram.allow_from`` -> the overlay translate.py
``_materialize_telegram_platform`` -> each ``profiles/<slug>/config.yaml``
``telegram.allow_from`` (which Hermes maps to ``TELEGRAM_ALLOWED_USERS``). This
guard verifies the *resolved* state independent of whether the overlay shipped
the materializer, and also catches the case where someone sets the
``TELEGRAM_BOT_TOKEN`` Fly secret without authoring any allowlist at all.

An allowlist is "resolvable" when EITHER:
  * the ``TELEGRAM_ALLOWED_USERS`` env var is set and non-empty, OR
  * any per-profile ``config.yaml`` carries a non-empty ``telegram.allow_from``.

Modes:
  enforce (default):  exit non-zero if the token is set but no allowlist resolves.
  --check:            same predicate; for the boot smoke test.

Usage:
  ensure-telegram-allowlist.py [--check] [HERMES_HOME]
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


def _config_allow_from(path: Path) -> list[str]:
    """Non-empty, stringified telegram.allow_from entries from one config.yaml."""
    try:
        with path.open() as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 - an unreadable or invalid config.yaml contributes no allow_from entries; the check reports on what it can read
        return []
    if not isinstance(cfg, dict):
        return []
    tg = cfg.get("telegram")
    if not isinstance(tg, dict):
        return []
    raw = tg.get("allow_from") or []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _env_allowlist() -> list[str]:
    csv = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    return [p.strip() for p in csv.split(",") if p.strip()] if csv else []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse to launch an unrestricted (fail-open) Telegram bot."
    )
    parser.add_argument("--check", action="store_true", help="verify-only (boot smoke test)")
    parser.add_argument(
        "hermes_home",
        nargs="?",
        default=os.environ.get("HERMES_HOME", "/opt/data"),
        help="Hermes home dir (default: $HERMES_HOME or /opt/data)",
    )
    args = parser.parse_args(argv[1:])
    tag = "ensure-telegram-allowlist"

    # No token -> Telegram never auto-enables -> nothing to guard.
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        print(f"[{tag}] TELEGRAM_BOT_TOKEN unset; Telegram platform inert — nothing to guard")
        return 0

    env_allow = _env_allowlist()
    home = Path(args.hermes_home)
    config_allow: list[str] = []
    for path in profile_configs(home):
        config_allow.extend(_config_allow_from(path))

    if env_allow or config_allow:
        src = []
        if env_allow:
            src.append(f"TELEGRAM_ALLOWED_USERS env ({len(env_allow)})")
        if config_allow:
            src.append(f"config.yaml telegram.allow_from ({len(config_allow)})")
        print(f"[{tag}] OK: Telegram allowlist resolved from {', '.join(src)}")
        return 0

    # Token set, but NO allowlist anywhere -> the bot would answer anyone. Refuse.
    print(
        f"[{tag}] FATAL: TELEGRAM_BOT_TOKEN is set but no allowlist is resolvable "
        f"(TELEGRAM_ALLOWED_USERS env empty AND no telegram.allow_from in any profile "
        f"config under {home}/profiles/). The pinned Hermes ref fails OPEN on an empty "
        f"allowlist — refusing to launch an unrestricted Telegram bot. Author "
        f"customer.yaml telegram.allow_from (ADR 0033) or set TELEGRAM_ALLOWED_USERS.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
