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
   re-stages), unless --accept-lane-inputs, or unless a person DECLARED the
   change in the repo: operator/fleet/lane-inputs-accepted.yaml lists, per
   commit the schema modules changed at (`since`), the lanes whose staged
   inputs still fit, with who and why. A declaration applies when the release
   was built at or after `since` and the seat's current image before it; the
   refusal becomes a note naming the call. That is the tick's path (it never
   passes --accept-lane-inputs, ADR 0096): a person's call, merged once and
   read from the checkout by every seat's preflight, instead of a hand release
   per seat or a laptop command. Added 2026-11-04 when ADR 0103 renamed the
   runner's package and every lane's digest changed with its env names and
   import lines while the schemas accepted exactly what they did; the tick
   halted at staging. The stage-*-inputs.sh files are
   NOT called from here: they are sourced fragments of provision-customer.sh
   that require its whole shell context and the engagements checkout, so
   driving them from a rollout would be a second provisioner.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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
    "casefile": "casefile/",
    "records": "records/",
}


ACCEPTED_LANE_INPUTS_PATH = "operator/fleet/lane-inputs-accepted.yaml"


def accepted_lanes(declaration: dict[str, Any] | None) -> set[str]:
    """The lanes a declaration covers: a set of lane keys, ``{"*"}`` for every lane, or an
    empty set when there is no usable declaration."""
    if not isinstance(declaration, dict):
        return set()
    lanes = declaration.get("lanes")
    if lanes == "*":
        return {"*"}
    if isinstance(lanes, list):
        return {str(x) for x in lanes}
    return set()


def covers(declarations: list[dict[str, Any]], lane: str) -> dict[str, Any] | None:
    for d in declarations:
        lanes = accepted_lanes(d)
        if lane in lanes or "*" in lanes:
            return d
    return None


class LaneInputDeclarations:
    """operator/fleet/lane-inputs-accepted.yaml, read from the checkout the release runs in.

    Each entry: ``since`` (the full commit the schema modules changed at), ``lanes``
    (``"*"`` or a list), ``why``, ``by``, ``date``. An entry applies to a seat when the
    release was built at or after ``since`` and the seat's current image before it
    (ancestry asked of git; a commit this clone has never seen is "not after").
    """

    def __init__(self, runner: Any, repo: Path) -> None:
        self.runner = runner
        self.repo = repo

    def load(self) -> list[dict[str, Any]]:
        path = self.repo / ACCEPTED_LANE_INPUTS_PATH
        if not path.is_file():
            return []
        doc = yaml.safe_load(path.read_text()) or []
        return [d for d in doc if isinstance(d, dict) and ids.SHA_RE.match(str(d.get("since", "")))]

    def _at_or_after(self, older: str, newer: str) -> bool | None:
        """Whether ``older`` is ``newer`` or one of its ancestors; None when either commit is
        one this clone has never seen (then nothing is declared: unknown is not "before")."""
        if older == newer:
            return True
        for sha in (older, newer):
            present = self.runner.run(["git", "-C", str(self.repo), "cat-file", "-e", f"{sha}^{{commit}}"], timeout=60)
            if present.rc != 0:
                return None
        res = self.runner.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", older, newer], timeout=60)
        return res.rc == 0

    def applying(self, target_sha: str | None, current_sha: str | None) -> list[dict[str, Any]]:
        if not target_sha or not current_sha or not ids.SHA_RE.match(target_sha) or not ids.SHA_RE.match(current_sha):
            return []
        out = []
        for d in self.load():
            since = str(d["since"])
            if self._at_or_after(since, target_sha) is True and self._at_or_after(since, current_sha) is False:
                out.append(d)
        return out


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
    seat: str,
    target: dict[str, Any] | None,
    current: dict[str, Any] | None,
    authored: list[str],
    accept: bool,
    declarations: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    for lane in authored:
        want = (target or {}).get(lane)
        have = (current or {}).get(lane)
        if want and have and want == have:
            continue
        declaration = covers(declarations or [], lane) if want else None
        if declaration is not None:
            notes.append(
                f"lane {lane}: its firm-input digest differs from the seat's image; declared unchanged since "
                f"{str(declaration['since'])[:8]} by {declaration.get('by', '?')} ({declaration.get('date', '?')}): "
                f"{declaration.get('why', '')}"
            )
            continue
        if want and not have and isinstance(current, dict) and current:
            # A lane the current image recorded other lanes' digests beside but
            # never ran (casefile, 2026-10-26): no old-schema input can be read
            # by the new image, and its inputs were validated when staged.
            notes.append(f"lane {lane}: new on this release; staged inputs taken as they are")
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
    def __init__(self, fly: Fly, r2: R2, declarations: LaneInputDeclarations | None = None) -> None:
        self.fly = fly
        self.r2 = r2
        self.declarations = declarations

    def authored_lanes(self, seat: str) -> list[str]:
        out = []
        for lane, marker in LANE_VAULT_MARKERS.items():
            key = f"vaults/{ids.slug(seat)}/{marker}"
            present = self.r2.exists_prefix(key) if marker.endswith("/") else self.r2.head(key) is not None
            if present:
                out.append(lane)
        return out

    def run(
        self,
        seat: str,
        release: dict[str, Any],
        rendered_toml: str,
        current_digest: str | None,
        accept_lanes: bool,
        current_sha: str | None = None,
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
        target_sha = release.get("ss_console_sha")
        declared = (
            self.declarations.applying(str(target_sha) if target_sha else None, current_sha)
            if self.declarations is not None
            else []
        )
        lane_problems, notes = check_lanes(
            seat, target_lanes, current_lanes, self.authored_lanes(seat), accept_lanes, declared
        )
        problems += lane_problems
        return Preflight(not problems, problems, notes)
