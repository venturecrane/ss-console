"""flyctl, wrapped: the Fly calls a release makes, and nothing else.

Every method either returns a parsed answer or raises ToolingError; none
returns a guess. ``deploy`` is the exception on purpose: it returns the raw
Result, because whether a failed deploy means "the seat is broken" (roll back)
or "the tool broke" (halt, touch nothing) is the rollout's decision
(:func:`classify_deploy`).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

from . import ids
from .runner import Result, Runner, ToolingError, looks_like_transport_failure, require_ok

SNAPSHOT_ID_RE = re.compile(r"\bvs_[A-Za-z0-9]+\b")
DIGEST_IN_OUTPUT_RE = re.compile(r"sha256:[0-9a-f]{64}")

# A failed `fly deploy` whose output says the NEW release would not come up is a
# seat failure (roll back). Anything else, including a transport failure, is a
# tooling error: nobody can say which image the Machine is on.
DEPLOY_SEAT_FAILURE_MARKERS = (
    "health checks",
    "failed to start",
    "smoke checks",
    "exited with non-zero",
    "machine failed",
    "crash",
)


def _json(res: Result, what: str) -> Any:
    require_ok(res, what)
    try:
        return json.loads(res.stdout or "null")
    except json.JSONDecodeError as exc:
        raise ToolingError(f"{what}: output is not JSON") from exc


class Fly:
    def __init__(self, runner: Runner, repo_root: str) -> None:
        self.runner = runner
        self.repo_root = repo_root

    def _fly(self, *args: str, timeout: float = 180, input_text: str | None = None) -> Result:
        return self.runner.run(["fly", *args], timeout=timeout, input_text=input_text, cwd=self.repo_root)

    # -- apps ------------------------------------------------------------------
    def app_exists(self, app: str) -> bool:
        apps = _json(self._fly("apps", "list", "--json"), "fly apps list")
        return any(isinstance(a, dict) and (a.get("Name") or a.get("name")) == app for a in apps or [])

    def create_app(self, app: str, org: str = "personal") -> None:
        require_ok(self._fly("apps", "create", app, "--org", org), f"fly apps create {app}")

    # -- secrets (NAMES only) ------------------------------------------------------
    def secret_names(self, app: str) -> tuple[set[str], set[str]]:
        """(deployed, staged) secret NAMES. Values never leave Fly."""
        rows = _json(self._fly("secrets", "list", "-a", app, "--json"), f"fly secrets list -a {app}")
        deployed: set[str] = set()
        staged: set[str] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or row.get("name") or "")
            status = str(row.get("Status") or row.get("status") or "").lower()
            if name:
                (staged if "stag" in status else deployed).add(name)
        return deployed, staged

    # -- machine -------------------------------------------------------------------
    def machines(self, app: str) -> list[dict[str, Any]]:
        rows = _json(self._fly("machines", "list", "-a", app, "--json"), f"fly machines list -a {app}")
        return [m for m in rows or [] if isinstance(m, dict)]

    def machine_image(self, app: str) -> str:
        """The image the seat's Machine runs now (its rollback target)."""
        machines = self.machines(app)
        if not machines:
            raise ToolingError(f"{app} has no Machine to read an image from")
        for m in machines:
            ref = (m.get("config") or {}).get("image") or ""
            if not ref:
                img = m.get("image_ref") or {}
                if img.get("registry") and img.get("repository"):
                    ref = f"{img['registry']}/{img['repository']}:{img.get('tag') or 'latest'}"
                    if img.get("digest"):
                        ref += f"@{img['digest']}"
            if ref:
                return ref
        raise ToolingError(f"{app}: no Machine reports its image")

    def guest(self, app: str) -> dict[str, Any]:
        """The running Machine's guest: {cpu_kind, cpus, memory_mb}."""
        machines = self.machines(app)
        if not machines:
            raise ToolingError(f"{app} has no Machine to read a guest from")
        guest = (machines[0].get("config") or {}).get("guest") or {}
        if not isinstance(guest, dict) or not guest:
            raise ToolingError(f"{app}: the Machine reports no guest")
        return {"cpu_kind": guest.get("cpu_kind"), "cpus": guest.get("cpus"), "memory_mb": guest.get("memory_mb")}

    def volume_id(self, app: str, name: str = "hermes_state") -> str:
        rows = _json(self._fly("volumes", "list", "-a", app, "--json"), f"fly volumes list -a {app}")
        vols = [v for v in rows or [] if isinstance(v, dict) and v.get("name") == name]
        # An app can carry an orphan volume of the same name (hermes-brokery,
        # 2026-10-19); the one that matters is the one the Machine mounts.
        attached = [v for v in vols if v.get("attached_machine_id")]
        if len(vols) > 1 and len(attached) == 1:
            vols = attached
        if len(vols) != 1:
            raise ToolingError(f"{app}: expected one {name} volume, found {len(vols)}")
        return str(vols[0].get("id"))

    def snapshot(self, app: str, volume: str) -> str:
        res = self._fly("volumes", "snapshots", "create", volume, "-a", app, timeout=300)
        require_ok(res, f"fly volumes snapshots create {volume}")
        found = SNAPSHOT_ID_RE.search(res.text)
        return found.group(0) if found else "created (id not printed)"

    def saved_config(self, app: str) -> str:
        """The fly.toml the app is deployed with now (its rollback toml)."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "fly.toml")
            # flyctl refuses `--config <path>` unless the file already exists.
            open(dest, "w", encoding="utf-8").close()
            require_ok(self._fly("config", "save", "-a", app, "--config", dest, "--yes"), f"fly config save -a {app}")
            with open(dest, encoding="utf-8") as fh:
                return fh.read()

    # -- deploy ---------------------------------------------------------------------
    def deploy(self, app: str, image: str, config_path: str) -> Result:
        return self._fly(
            "deploy", "-a", app, "--config", config_path, "--image", ids.image_ref(image), "--yes", timeout=1200
        )

    # -- ssh --------------------------------------------------------------------------
    def ssh_python(self, app: str, source: str, timeout: float = 90) -> Result:
        """Run a Python program on the Machine as root, read from stdin (no quoting layers).

        The program travels on stdin to ``python3 -I -`` with ``--pty=false``, so
        nothing in it is re-parsed by a shell on either side. It runs under the
        image's system python3, which every seat image has carried since the
        first one, so this works on images older than any fleet tooling.
        """
        return self._fly(
            "ssh", "console", "-a", app, "--pty=false", "-C", "python3 -I -", timeout=timeout, input_text=source
        )

    # -- build -------------------------------------------------------------------------
    def build_push(self, config_path: str, label: str, ss_sha: str) -> Result:
        return self._fly(
            "deploy", "--build-only", "--push", "--remote-only", "-a", ids.FLEET_REGISTRY_APP,
            "--config", config_path, "--image-label", label, "--build-arg", f"SS_CONSOLE_SHA={ss_sha}",
            timeout=3600,
        )  # fmt: skip


def classify_deploy(res: Result) -> str:
    """'ok', 'fail' (the new release would not come up) or 'tooling_error'."""
    if res.ok:
        return "ok"
    if looks_like_transport_failure(res):
        return "tooling_error"
    low = res.text.lower()
    return "fail" if any(m in low for m in DEPLOY_SEAT_FAILURE_MARKERS) else "tooling_error"


def digest_from_build_output(text: str) -> str | None:
    found = DIGEST_IN_OUTPUT_RE.findall(text)
    return found[-1] if found else None
