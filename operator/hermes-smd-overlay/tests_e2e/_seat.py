"""Materialize a seat home the way a seat's boot does, in a temp dir.

The profile is produced by the REAL ``bootstrap.translate.translate_customer_yaml``
from ``operator/customers/_hosted-template/customer.yaml`` (placeholders filled
with synthetic values), against the repo's real skill catalog and role
packages. The overlay is then laid into the profile's plugins dir the way
``bootstrap.sh`` lays ``/app/overlay-pack`` there, and enabled in
``plugins.enabled``, so Hermes discovers and loads it through its own directory
plugin loader.

Departures from a seat, each because the harness must not reach a real system
or because the surface is not what this harness measures:

* ``mcp_servers`` is dropped: the template authors the vendor AgentMail MCP,
  which Hermes would dial at agent build. MCP connectors are PR 2's reference
  connector, not this file.
* ``platforms`` is dropped: the harness drives ``AIAgent`` directly; there is no
  gateway, so no webhook listener to configure.
* ``model.base_url`` points at the local model stub.
* ``auxiliary.title_generation.enabled`` is false: the session-title side call
  resolves its own provider, which is not the stub, and the first run caught it
  dialing out (the network guard refused it). Its model choice is covered by
  ``tests/test_title_generation_model.py`` against the same pinned Hermes.
* One exposure is added to the persona (see ``_HARNESS_EXPOSURE``).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

OVERLAY_ROOT = Path(__file__).resolve().parents[1]
REPO_OPERATOR = OVERLAY_ROOT.parent
TEMPLATE = REPO_OPERATOR / "customers" / "_hosted-template" / "customer.yaml"
COUPLES_TEMPLATE = REPO_OPERATOR / "customers" / "_couples-template" / "customer.yaml"

SEAT_SLUG = "e2e-seat"
PERSONA_SLUG = "rowan"
OVERLAY_PLUGIN = "hermes-smd-overlay"
OWNER_EMAIL = "owner@firm.example"

#: The harness-only shadow plugin (``_regression/``), and the module name
#: Hermes' directory loader gives it.
SHADOW_PLUGIN = Path(__file__).resolve().parent / "_regression" / "hermes-smd-e2e-shadow"
SHADOW_MODULE = "hermes_plugins.hermes_smd_e2e_shadow"

#: Every bracketed placeholder in the hosted template, filled with synthetic
#: values on a reserved domain. A placeholder the template gains and this map
#: lacks fails loudly in ``_fill`` rather than reaching the validator.
PLACEHOLDERS: dict[str, str] = {
    "'[SLUG]'": SEAT_SLUG,
    "[SLUG]": SEAT_SLUG,
    "'[AGENT NAME from intake]'": "Rowan",
    "'[AGENT NAME, lowercased]'": PERSONA_SLUG,
    "'[BUYER EMAIL]'": OWNER_EMAIL,
    "'[BUYER NAME]'": "Pat Example",
    "'[BUYER TELEGRAM NUMERIC ID \u2014 resolve from the handle at go-live]'": "'100000001'",
    "'[CUSTOMER NAME from intake / Stripe]'": "Example Firm",
    "'[MORNING DIGEST CRON, e.g., 0 14 * * * for 7am PHX]'": "'0 14 * * *'",
    "'[SENDER 1 from intake]'": OWNER_EMAIL,
}

#: The couples template (``people:`` authored, so the seat is in space mode),
#: filled the same way.
COUPLES_PLACEHOLDERS: dict[str, str] = {
    "'[SLUG]'": "e2e-couple",
    "[SLUG]": "e2e-couple",
    "'[COUPLE NAME]'": "Sam and Alex Example",
    "'[PARTNER 1 NAME]'": "Sam Example",
    "'[PARTNER 1 EMAIL, lowercased]'": "sam@home.example",
    "'[PARTNER 2 NAME]'": "Alex Example",
    "'[PARTNER 2 EMAIL, lowercased]'": "alex@home.example",
    "'[AGENT NAME, lowercased]'": "juniper",
    "'[AGENT NAME]'": "Juniper",
    "'[SMD SENTINEL TELEGRAM ID until both partners are recorded]'": "'100000001'",
}

_PLACEHOLDER_RE = re.compile(r"\[[A-Z][^\]]*\]")

#: The one authored change from the template: an autonomous send to a rostered
#: address, the tier pilot-smokeball, scott, brokery, captain-mark and the
#: couples template author. Without it ``smd_send_message`` is refused by the
#: exposure ladder before Hermes dispatches it, and the handler the 2026-08-13
#: incident broke would never be reached. Seats authoring exactly this took the
#: direct tool path that was broken.
_TEMPLATE_EXPOSURE = "        external_send: confirm\n"
_HARNESS_EXPOSURE = _TEMPLATE_EXPOSURE + "        external_send_internal: autonomous\n"

#: What the overlay copy leaves behind: test trees and local build debris are
#: not part of the pack a seat loads.
_PACK_IGNORE = shutil.ignore_patterns(
    "tests",
    "tests_e2e",
    ".venv",
    "*.egg-info",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "*.db",
    "CUSTOMER_DB",
)


def _fill(template: Path, placeholders: dict[str, str]) -> str:
    text = template.read_text(encoding="utf-8")
    for needle, value in placeholders.items():
        text = text.replace(needle, value)
    left = [line for line in text.splitlines() if _PLACEHOLDER_RE.search(line.split("#", 1)[0])]
    if left:
        raise AssertionError(f"{template} has placeholders the harness does not fill: {left!r}")
    return text


def filled_template() -> str:
    """The hosted template, filled, with the one harness exposure added."""
    if TEMPLATE.read_text(encoding="utf-8").count(_TEMPLATE_EXPOSURE) != 1:
        raise AssertionError(
            "the hosted template's persona exposure moved; the harness adds "
            "external_send_internal next to `external_send: confirm`"
        )
    return _fill(TEMPLATE, PLACEHOLDERS).replace(_TEMPLATE_EXPOSURE, _HARNESS_EXPOSURE)


def with_every_lane(text: str) -> str:
    """The seat authors every job lane, by enabling each lane's skill on the one
    persona (ADR 0100: a seat registers only the lane tools it authors). The
    dispatch contract checks every lane tool, so the harness seat is the one that
    has them all; a lane added to ``JOB_LANES`` joins the harness by itself."""
    from shared import capabilities

    doc: dict[str, Any] = yaml.safe_load(text)
    skills = doc["personas"][0]["skills"]
    have = {s.get("name") for s in skills}
    for skill in capabilities.JOB_LANES.values():
        if skill not in have:
            skills.append(
                {
                    "name": skill,
                    "version": "pending",
                    "initiation": {"manual": True, "scheduled": False, "webhook": False},
                    "enabled": True,
                }
            )
    return yaml.safe_dump(doc, sort_keys=False)


def write_couples_seat(path: Path) -> Path:
    """Fill the couples template into ``path`` and prove the real validator
    accepts it, so the space-mode test reads an authored seat config rather
    than a hand-made fragment."""
    from bootstrap.validate import validate_customer_yaml

    path.write_text(_fill(COUPLES_TEMPLATE, COUPLES_PLACEHOLDERS), encoding="utf-8")
    errors = validate_customer_yaml(path)
    if errors:
        raise AssertionError(f"the filled couples template does not validate: {errors}")
    return path


def build_seat_home(root: Path, *, model_base_url: str, extra_plugins: list[Path]) -> Path:
    """Translate the template into ``root/home`` and return the persona's
    profile dir, which is the ``HERMES_HOME`` the persona's runtime reads."""
    from bootstrap.translate import translate_customer_yaml

    customer_yaml = root / "customer.yaml"
    customer_yaml.write_text(with_every_lane(filled_template()), encoding="utf-8")
    hermes_root = root / "home"
    slugs = translate_customer_yaml(
        str(customer_yaml),
        str(hermes_root),
        skills_dir=str(REPO_OPERATOR / "skills"),
        roles_dir=str(REPO_OPERATOR / "roles"),
    )
    if slugs != [PERSONA_SLUG]:
        raise AssertionError(f"template translated to personas {slugs}, expected [{PERSONA_SLUG}]")
    profile = hermes_root / "profiles" / PERSONA_SLUG

    plugins_dir = profile / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(OVERLAY_ROOT, plugins_dir / OVERLAY_PLUGIN, ignore=_PACK_IGNORE)
    enabled = [OVERLAY_PLUGIN]
    for extra in extra_plugins:
        shutil.copytree(extra, plugins_dir / extra.name, ignore=_PACK_IGNORE)
        enabled.append(extra.name)

    config_path = profile / "config.yaml"
    config: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config.pop("mcp_servers", None)
    config.pop("platforms", None)
    model = dict(config.get("model") or {})
    model["base_url"] = model_base_url
    config["model"] = model
    auxiliary = dict(config.get("auxiliary") or {})
    title = dict(auxiliary.get("title_generation") or {})
    title["enabled"] = False
    auxiliary["title_generation"] = title
    config["auxiliary"] = auxiliary
    plugins = dict(config.get("plugins") or {})
    plugins["enabled"] = enabled
    config["plugins"] = plugins
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return profile


#: The environment the harness keeps from its caller. Everything else is
#: dropped before any seat code runs, so a developer shell that carries
#: production credentials (AgentMail keys, webhook secrets, an Anthropic key)
#: can never be materialized into the temp seat or sent anywhere: translate
#: copies env-held connector secrets into the profile config, exactly as it does
#: on a seat.
_KEEP_ENV = re.compile(
    r"^(PATH|HOME|USER|LOGNAME|SHELL|TERM|LANG|LC_[A-Z_]+|TZ|TMPDIR|TMP|TEMP|"
    r"VIRTUAL_ENV|PYTHON[A-Z_]*|PYTEST_[A-Z_]*|HERMES_AGENT_SRC|E2E_SUMMARY_PATH|CI|"
    r"GITHUB_[A-Z_]+|RUNNER_[A-Z_]+|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE|NO_COLOR)$"
)

#: A GitHub token is the one GITHUB_* value worth a credential; the keep-list
#: admits GITHUB_* for the runner's paths, so the token is dropped by name.
_DROP_ANYWAY = frozenset({"GITHUB_TOKEN"})


def install_network_guard() -> None:
    """Refuse every IP connection that is not to loopback.

    The harness's only peers are the local model stub and the Unix-socket
    broker. A Hermes default that silently reaches a real endpoint (the first
    probe of this harness hit api.anthropic.com because a base_url was not
    honoured) must fail the run, not pass it on a vendor's answer."""
    import ipaddress
    import socket

    original = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0] if isinstance(address, tuple) else str(address)
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                raise ConnectionRefusedError(
                    f"e2e harness network guard: refused a connection to {host!r}; "
                    "the harness talks only to its loopback model stub and the stub broker"
                )
        return original(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign]


def scrub_environment() -> list[str]:
    """Drop every variable not on the keep-list; return the dropped names."""
    dropped = [
        name for name in list(os.environ) if not _KEEP_ENV.match(name) or name in _DROP_ANYWAY
    ]
    for name in dropped:
        os.environ.pop(name, None)
    return dropped
