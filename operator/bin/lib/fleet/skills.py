"""One seat's skills release: no restart, no busy gate, a capability gate instead.

The seat's skills_applier (overlay) polls its own pointer,
vaults/<slug>/skills-pointer.json = {"version", "manifest_key",
"manifest_sha256"}, verifies the manifest and every object all-or-nothing, and
swaps the skill tree in place; the heartbeat then reports skills_version. Open
sessions keep their prompt; new turns read the new files. So the only thing
that can make a skills version wrong for a seat is the seat's IMAGE lacking
something a skill needs.

CAPABILITY GATE. The manifest's ``needs`` (built in CI by
scripts/lib/skill_needs.py) lists, per skill, the image paths it cites, the
tools and broker verbs it names, and its declared action class. The seat's
image's provisions are fleet/images/<digest>/image-capabilities.json (baked by
operator/templates/gen-image-capabilities.py, uploaded by `fleet build`). The
digest is found from the seat's heartbeat image_ref. Unknown image, or no
capabilities file for it: refused, never assumed.

FAILURE. Pointer written, but no heartbeat reports the version within the
window: the pointer goes back to what it was (or is removed if there was none),
and the rollout halts.

WHO ROLLS IT. The release controller (release-tick.yml, `fleet release latest
--tick`) since 2026-10-28, ring by ring like an image, each seat only once its
image has the skills' commit (:func:`deferrals`). `fleet release --skills
<version>` stays the by-hand path.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from . import ids, ordering, smoke
from .d1 import FleetStore
from .r2 import R2, image_key, pointer_key, skills_manifest_key
from .rollout import SeatOutcome
from .runner import ToolingError

NEED_KINDS = ("paths", "tools", "verbs", "action_classes")


def path_provided(need: str, paths: set[str], dirs: set[str]) -> bool:
    return need in paths or need.rstrip("/") in dirs


def directories(paths: set[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts)):
            out.add("/".join(parts[:i]))
    return out


def unmet_needs(manifest: dict[str, Any], caps: dict[str, Any]) -> list[str]:
    """Every need of every skill the image does not provide, as 'skill: kind name'."""
    provided = {k: set(caps.get(k) or []) for k in NEED_KINDS}
    dirs = directories(provided["paths"])
    missing: list[str] = []
    for skill, needs in sorted((manifest.get("needs") or {}).items()):
        for kind in NEED_KINDS:
            for need in (needs or {}).get(kind) or []:
                ok = path_provided(need, provided["paths"], dirs) if kind == "paths" else need in provided[kind]
                if not ok:
                    missing.append(f"{skill}: {kind[:-1] if kind.endswith('s') else kind} {need}")
    return missing


def deferrals(
    release: dict[str, Any],
    seats: list[str],
    status: dict[str, dict[str, Any]],
    image_sha_of: Callable[[str | None], str | None],
    at_or_after: Callable[[str, str], bool],
    base_of: Callable[[str], str | None] = lambda sha: sha,
) -> dict[str, str]:
    """The seats that may not take this skills release on this tick, with why.

    ORDERING RULE: ordering.deferrals, shared with the jobs bundle. A skills
    release published at S goes to a seat only once the seat's image commit is
    base(S) or descends from it (``base_of``; the CLI binds ordering.base_commit,
    the last commit at or before S touching an image path). Skills are written
    against the code in the image, and the capability gate sees only what a
    skill NAMES (paths, tools, verbs, action classes), not a changed argument or
    behaviour, so this is the rule that keeps a skill from reaching code older
    than it. Since ADR 0100 W3 operator/skills/** is an artifact path, not an
    image path, so a skills-only merge builds no image and its base is the
    newest image commit before it. A hotfix-branch image counts when its
    commit descends from base(S) (#3657): it carries that code as surely as a
    main image. Waiting is a deferral, never a halt.

    A seat whose heartbeat already reports a NEWER skills version is deferred
    too: promotion never moves a seat backwards, even one pointed by hand at a
    release newer than any the tick weighs.
    """
    return ordering.deferrals(
        "skills",
        release.get("ss_console_sha"),
        release.get("skills_version"),
        seats,
        status,
        lambda row: row.get("skills_version"),
        image_sha_of,
        at_or_after,
        base_of,
    )


def image_capabilities(store: FleetStore, r2: R2, seat: str) -> tuple[dict[str, Any] | None, str]:
    """The seat's image's baked capabilities and its digest, or None and why not.

    Found from the heartbeat's image_ref; an unknown image, or no capabilities
    file for it, is None (refused, never assumed). Shared with bundles.py.
    """
    row = store.seat_status(seat) or {}
    ref = row.get("image_ref")
    if not ref:
        return None, "the seat's heartbeat reports no image_ref"
    digest = ids.digest_in_ref(ref)
    if not digest:
        rel = store.release_for_image(ref)
        digest = rel.get("image_digest") if rel else None
    if not digest:
        return None, f"image {ref} is not a recorded fleet release"
    caps = r2.get_json(image_key(digest, "image-capabilities.json"))
    return (caps, digest) if caps else (None, f"no image-capabilities.json for {digest}")


class SkillsRollout:
    def __init__(
        self,
        store: FleetStore,
        r2: R2,
        log: Callable[[str], None],
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.store, self.r2, self.log, self.sleep, self.clock = store, r2, log, sleep, clock

    def load_manifest(self, version: str) -> tuple[dict[str, Any], str, str]:
        key = skills_manifest_key(version)
        raw = self.r2.get_bytes(key)
        if raw is None:
            raise ToolingError(f"no skills manifest at {key} (has CI published {version}?)")
        manifest = json.loads(raw)
        if manifest.get("version") != version:
            raise ToolingError(f"{key} names version {manifest.get('version')!r}, not {version}")
        return manifest, key, hashlib.sha256(raw).hexdigest()

    def capabilities_for(self, seat: str) -> tuple[dict[str, Any] | None, str]:
        return image_capabilities(self.store, self.r2, seat)

    def run(
        self, seat: str, ring: int, release: dict[str, Any], manifest: dict[str, Any], key: str, sha: str, dry_run: bool
    ) -> SeatOutcome:
        rid = ids.new_id("ro")
        version = manifest["version"]
        previous = self.r2.get_json(pointer_key(seat))
        prev_version = (previous or {}).get("version")
        if not dry_run:
            self.store.start_rollout(
                {"rollout_id": rid, "release_id": release["release_id"], "customer_slug": seat, "ring": ring,
                 "from_skills_version": prev_version, "to_skills_version": version}
            )  # fmt: skip
        try:
            out = self._run(seat, manifest, key, sha, previous, dry_run)
        except ToolingError as exc:
            out = SeatOutcome(seat, "tooling_error", str(exc))
        self.log(f"{seat}: {out.state.upper()} {out.detail}")
        if not dry_run:
            self.store.update_rollout(rid, out.state, detail=out.detail[:2000])
        return out

    def _run(
        self, seat: str, manifest: dict[str, Any], key: str, sha: str, previous: dict[str, Any] | None, dry_run: bool
    ) -> SeatOutcome:
        version = manifest["version"]
        caps, where = self.capabilities_for(seat)
        if caps is None:
            return SeatOutcome(seat, "failed", f"refused: cannot tell what the seat's image provides ({where})")
        missing = unmet_needs(manifest, caps)
        if missing:
            return SeatOutcome(seat, "failed", f"refused: the seat's image ({where}) lacks: " + "; ".join(missing[:12]))
        if dry_run:
            return SeatOutcome(
                seat, "pending", f"dry run: image {where} satisfies every need; would point the seat at {version}"
            )
        since = ids.utc_now()
        self.r2.put_json(pointer_key(seat), {"version": version, "manifest_key": key, "manifest_sha256": sha})
        check = smoke.read_back(
            self.store, seat, lambda row: smoke.judge_skills_row(row, since, version),
            smoke.SKILLS_READBACK_WINDOW_S, self.sleep, self.clock,
        )  # fmt: skip
        if check.cls == "pass":
            return SeatOutcome(seat, "done", check.detail)
        if check.cls == "tooling_error":
            return SeatOutcome(seat, "tooling_error", f"{check.detail}; the pointer stays at {version}")
        self._repoint(seat, previous)
        back = (previous or {}).get("version") or "none (pointer removed)"
        return SeatOutcome(seat, "rolled_back", f"{check.detail}; pointer restored to {back}")

    def _repoint(self, seat: str, previous: dict[str, Any] | None) -> None:
        if previous:
            self.r2.put_json(pointer_key(seat), previous)
        else:
            self.r2.delete(pointer_key(seat))
