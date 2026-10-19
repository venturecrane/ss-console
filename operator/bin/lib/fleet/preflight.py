"""Per-seat checks that run before anything on the seat is touched.

1. REQUIRED ENV. The release image's REQUIRED_ENV (generated from
   operator/contracts/env-consumption.yaml into _env-arrays.generated.sh, and
   uploaded by `fleet build` as required-env.json) must all be present on the
   seat: as a deployed Fly secret NAME, or in the rendered fly.toml [env]. A
   missing one would boot the new image into its own env check and a crash loop.

2. STAGED SECRETS. `fly deploy` applies staged secrets with the new release
   (provision-customer.sh step 7's ordering rule). A staged secret nobody told
   this release about would ride it silently, so it is refused unless the
   release DECLARES it (`fleet build --declare-secret NAME`).

3. LANE INPUTS. The runner lanes validate firm inputs staged in R2 against the
   image's schema modules. `fleet build` records a digest of those modules
   (lane-inputs.json). If the seat's current image recorded different digests,
   or recorded none (an image from before fleet build), for a lane the seat
   authors, the staged inputs were validated against another schema: refused,
   naming `operator/bin/reprovision.sh <slug>` (which re-validates and
   re-stages), unless --accept-lane-inputs. The stage-*-inputs.sh files are
   NOT called from here: they are sourced fragments of provision-customer.sh
   that require its whole shell context and the engagements checkout, so
   driving them from a rollout would be a second provisioner.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from typing import Any

from . import ids
from .d1 import decode_json_list
from .fly import Fly
from .r2 import R2, image_key

# lane -> what in vaults/<slug>/ shows the seat authors it.
LANE_VAULT_MARKERS: dict[str, str] = {
    "medchron": "medchron-firm.yaml",
    "demand": "demand/",
    "drafting": "drafting/",
    "litigation": "litigation/",
}


@dataclass
class Preflight:
    ok: bool
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def toml_env_names(rendered_toml: str) -> set[str]:
    try:
        doc = tomllib.loads(rendered_toml)
    except tomllib.TOMLDecodeError:
        return set()
    env = doc.get("env") or {}
    return {str(k) for k in env} if isinstance(env, dict) else set()


def check_env(required: list[str], deployed: set[str], env_names: set[str]) -> list[str]:
    missing = sorted(set(required) - deployed - env_names)
    return [f"required env missing on the seat: {', '.join(missing)}"] if missing else []


def check_staged(staged: set[str], declared: list[str]) -> list[str]:
    undeclared = sorted(staged - set(declared))
    if undeclared:
        return [
            f"secrets are STAGED on the seat and this release does not declare them: {', '.join(undeclared)} (fleet build --declare-secret NAME, or apply them first)"
        ]
    return []


def check_lanes(
    seat: str, target: dict[str, Any] | None, current: dict[str, Any] | None, authored: list[str], accept: bool
) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    for lane in authored:
        want = (target or {}).get(lane)
        have = (current or {}).get(lane)
        if want and have and want == have:
            continue
        why = (
            "the seat's current image recorded no lane-input digests"
            if not have
            else "the lane's firm-input schema changed"
        )
        msg = f"lane {lane}: {why}; re-stage with operator/bin/reprovision.sh {seat}"
        (notes if accept else problems).append(msg + (" (accepted by --accept-lane-inputs)" if accept else ""))
    return problems, notes


class PreflightChecker:
    def __init__(self, fly: Fly, r2: R2) -> None:
        self.fly = fly
        self.r2 = r2

    def authored_lanes(self, seat: str) -> list[str]:
        out = []
        for lane, marker in LANE_VAULT_MARKERS.items():
            key = f"vaults/{ids.slug(seat)}/{marker}"
            present = self.r2.exists_prefix(key) if marker.endswith("/") else self.r2.head(key) is not None
            if present:
                out.append(lane)
        return out

    def run(
        self, seat: str, release: dict[str, Any], rendered_toml: str, current_digest: str | None, accept_lanes: bool
    ) -> Preflight:
        digest = release.get("image_digest")
        required = (self.r2.get_json(image_key(digest, "required-env.json")) or {}).get("required") if digest else None
        if required is None:
            return Preflight(
                False, ["the release has no required-env.json in R2 (fleet build did not finish); refusing"]
            )
        deployed, staged = self.fly.secret_names(ids.app_name(seat))
        problems = check_env(required, deployed, toml_env_names(rendered_toml))
        problems += check_staged(staged, decode_json_list(release.get("declared_secrets")))
        target_lanes = self.r2.get_json(image_key(str(digest), "lane-inputs.json"))
        current_lanes = self.r2.get_json(image_key(current_digest, "lane-inputs.json")) if current_digest else None
        lane_problems, notes = check_lanes(seat, target_lanes, current_lanes, self.authored_lanes(seat), accept_lanes)
        problems += lane_problems
        return Preflight(not problems, problems, notes)
