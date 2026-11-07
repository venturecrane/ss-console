"""The jobs bundle an image needs, pointed at BEFORE the image boots (2026-11-07).

THE WINDOW THIS CLOSES. A seat reads its jobs bundle pointer only at boot
(bundles.py). The release tick rolled the image first and the bundle after, so
every image release rebooted each lane seat onto the new image with the bundle
its pointer still named, and the bundle step later restarted it a second time
onto the bundle built for that image. On 2026-11-07 a client seat ran
rel-b4d97f03 with the previous jobs bundle from 08:33Z to 08:43Z: a request in
that window would have run lane code from before the merge on the image from
after it.

THE ORDER NOW. Inside one seat's image rollout (rollout.ImageRollout), after the
busy gate and the volume snapshot and BEFORE ``fly deploy``, the seat's pointer
is written at the newest bundle the new image qualifies for, and read back. A
write or read-back that fails stops that seat there: the image is not deployed,
the seat is a tooling_error, and the seats beside it go on (rings stay
parallel; nothing here waits on another seat). A rollback restores the previous
pointer before it redeploys the from-image, and so does a deploy that decided
nothing. The bundle step that follows finds the seat already reporting that
version and writes its pointer with no restart
(bundles.BundleRollout._point_without_restart), so a release is one restart.

WHICH BUNDLE. floor(X) (ordering.bundle_floor) is the last commit at or before
the image's commit X that changed a bundle path. A bundle built at S qualifies
for X when S is floor(X) or descends from it (it carries every bundle change X
carries) and base(S) is X or an ancestor of it (the image the bundle needs,
ordering.py). The newest qualifying one is chosen. When none qualifies the
bundle for floor(X) is still being published: the image tick defers that seat
(:meth:`BundleAlign.deferrals`) and a one-seat release stops it before the
deploy. A commit that changed no bundle path builds no bundle; its floor is the
earlier commit whose bundle already qualifies, so a bundle that lags the image
is not a mismatch. The seat's self-test checks the same rule from the other side
(operator/templates/jobs-bundle-commit-probe.py, jobs-bundle-built-for-image).

WHO. Only seats that author a job lane (bundles.authoring_seats), and only while
BUNDLE_RELEASE_ENABLED is 'true' (artifact_tick.BUNDLE_SWITCH): off, the tick
rolls no bundle, and an image whose floor moved past a seat's bundle is then
refused by that seat's self-test and rolled back.

NOT SKILLS. A seat applies a skills pointer live, without a restart, so writing
it ahead of the reboot would run the new skills on the old image. Skills keep
following the image (skills.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import bundles, ids
from .r2 import R2, bundle_pointer_key
from .runner import ToolingError


@dataclass(frozen=True)
class Alignment:
    """What one seat's image step does to its bundle pointer.

    ``target`` is the pointer to write (None: leave the pointer as it is);
    ``previous`` is what it held, for a rollback; ``wait`` non-empty means the
    image must not be deployed yet, and says why.
    """

    seat: str
    pointer: str
    previous: dict[str, Any] | None
    target: dict[str, Any] | None
    note: str
    wait: str = ""


class BundleAlign:
    def __init__(
        self,
        r2: R2,
        releases: Callable[[], list[dict[str, Any]]],
        authoring: set[str],
        floor_of: Callable[[str], str | None],
        at_or_after: Callable[[str, str], bool],
        base_of: Callable[[str], str | None],
        name: str = bundles.JOBS,
    ) -> None:
        """``releases()``: the bundle releases published from main, newest first."""
        self.r2, self.releases, self.authoring, self.name = r2, releases, authoring, name
        self.floor_of, self.at_or_after, self.base_of = floor_of, at_or_after, base_of
        self._chosen: dict[str, tuple[dict[str, Any] | None, str]] = {}

    # -- which bundle ------------------------------------------------------------------
    def choose(self, image_sha: str) -> tuple[dict[str, Any] | None, str]:
        """(the newest bundle release the image built at ``image_sha`` qualifies for, why) or (None, why not)."""
        if image_sha not in self._chosen:
            self._chosen[image_sha] = self._choose(image_sha)
        return self._chosen[image_sha]

    def _choose(self, image_sha: str) -> tuple[dict[str, Any] | None, str]:
        if not ids.SHA_RE.match(image_sha or ""):
            return None, f"the image names no source commit ({image_sha!r})"
        floor = self.floor_of(image_sha)
        if not floor:
            return None, f"no bundle floor for the image's commit {image_sha[:8]}"
        for rel in self.releases():
            sha = rel.get("ss_console_sha") or ""
            if not ids.SHA_RE.match(sha) or not self.at_or_after(floor, sha):
                # Newest first: every release after this one is older than the floor too.
                break
            base = self.base_of(sha)
            if base and self.at_or_after(base, image_sha):
                return rel, f"{self.name} {rel['artifact_version']} (built at {sha[:8]}; floor {floor[:8]})"
        return None, (
            f"waiting for the {self.name} bundle this image needs: one built at or after {floor[:8]} "
            f"(the image's last bundle change) whose image is at or before {image_sha[:8]}; none is published yet"
        )

    def deferrals(self, release: dict[str, Any], seats: list[str]) -> dict[str, str]:
        """The image tick's ``defer``: lane seats whose bundle for this image is not published yet."""
        rel, why = self.choose(release.get("ss_console_sha") or "")
        if rel is not None:
            return {}
        return {seat: why for seat in seats if seat in self.authoring}

    # -- one seat ----------------------------------------------------------------------
    def prepare(self, seat: str, release: dict[str, Any]) -> Alignment | None:
        """What the seat's image step will do to its pointer; None for a seat that authors no lane."""
        if seat not in self.authoring:
            return None
        pointer = bundle_pointer_key(seat, self.name)
        rel, why = self.choose(release.get("ss_console_sha") or "")
        if rel is None:
            return Alignment(seat, pointer, None, None, why, wait=why)
        version = rel["artifact_version"]
        _manifest, key, sha = bundles.load_manifest(self.r2, self.name, version)
        target = {"version": version, "manifest_key": key, "manifest_sha256": sha}
        previous = self.r2.get_json(pointer)
        if previous == target:
            return Alignment(seat, pointer, previous, None, f"bundle pointer already names {why}")
        return Alignment(seat, pointer, previous, target, f"bundle pointer -> {why}, before the image boots")

    def point(self, al: Alignment) -> None:
        """Write the target pointer and read it back; ToolingError when the read-back differs."""
        if al.target is None:
            return
        self.r2.put_json(al.pointer, al.target)
        got = self.r2.get_json(al.pointer)
        if got != al.target:
            raise ToolingError(f"{al.pointer} read back {got!r}, not the {al.target['version']} just written")

    def restore(self, al: Alignment | None) -> str:
        """Put the previous pointer back (or remove the one written over nothing); what was done, for a log."""
        if al is None or al.target is None:
            return ""
        if al.previous:
            self.r2.put_json(al.pointer, al.previous)
            return f"bundle pointer restored to {al.previous.get('version')}"
        self.r2.delete(al.pointer)
        return "bundle pointer removed (there was none)"
