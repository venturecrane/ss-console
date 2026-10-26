"""flyctl, wrapped: the Fly calls a release makes, and nothing else.

Every method either returns a parsed answer or raises ToolingError; none
returns a guess. ``deploy`` is the exception on purpose: it returns the raw
Result, because whether a failed deploy means "the seat is broken" (roll back)
or "the tool broke" (halt, touch nothing) is the rollout's decision
(:func:`classify_deploy`).

WHICH CREDENTIAL A CALL CARRIES (:class:`FlyTokens`, ADR 0096). On a laptop
every call uses the operator's own ``fly auth login``: nothing is injected. In
GitHub Actions the release controller holds no org token, only app-scoped deploy
tokens from the protected ``fleet-release`` environment, so each call carries the
token for the app it names: ``FLY_DEPLOY_TOKEN__<SLUG>`` for ``hermes-<slug>``,
``FLY_REGISTRY_TOKEN`` for the ``hermes-fleet`` registry app. A seat deploy pulls
an image out of the registry app's repository, which the seat token alone cannot
read (probe F1, 2026-10-23: "Not authorized to access this imageref"), so every
seat call presents BOTH macaroons, comma-joined in one FLY_API_TOKEN, which
flyctl accepts; the pair is still refused on any other seat app. A missing token
is a ToolingError naming the variable, never a fall back to an ambient login, and
a token value is never part of an argv, a log line or an error.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from typing import Any, Mapping

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


# What Fly answers, to any token, about an app that does not exist (probed
# 2026-10-26 with an hermes-fleet-scoped deploy token and with an org login).
APP_NOT_FOUND_MARKER = "could not find app"
SEAT_TOKEN_PREFIX = "FLY_DEPLOY_TOKEN__"
REGISTRY_TOKEN_ENV = "FLY_REGISTRY_TOKEN"
MACAROON_SCHEME = "FlyV1 "


def seat_token_env_name(app: str) -> str:
    """The secret holding ``app``'s deploy token: hermes-smd-staging -> FLY_DEPLOY_TOKEN__SMD_STAGING."""
    if not app.startswith("hermes-"):
        raise ToolingError(f"{app} is not a seat app, so it has no per-app token name")
    return SEAT_TOKEN_PREFIX + ids.slug(app.removeprefix("hermes-")).upper().replace("-", "_")


def combine_tokens(seat_token: str, registry_token: str) -> str:
    """Two app-scoped macaroons in one FLY_API_TOKEN: the seat's, then the registry's without its scheme."""
    return f"{seat_token.strip()},{registry_token.strip().removeprefix(MACAROON_SCHEME)}"


def app_in_args(args: tuple[str, ...]) -> str | None:
    """The app a fly argv addresses (``-a APP``), or None."""
    for i, arg in enumerate(args[:-1]):
        if arg in ("-a", "--app"):
            return args[i + 1]
    return None


class FlyTokens:
    """Per-call Fly credentials: scoped (CI) or ambient (a laptop's own login).

    Scoped when the run is GitHub Actions (``GITHUB_ACTIONS=true``) or any
    per-app token is present. Then every call must name an app and carries
    exactly that app's credential. Ambient otherwise: :meth:`env_for` returns
    nothing and flyctl uses ``fly auth login`` as it always has.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._env = dict(environ or {})

    @classmethod
    def from_environ(cls) -> "FlyTokens":
        return cls(os.environ)

    def __repr__(self) -> str:
        return f"FlyTokens(scoped={self.scoped})"

    @property
    def scoped(self) -> bool:
        if self._env.get("GITHUB_ACTIONS") == "true":
            return True
        return any(v and (k.startswith(SEAT_TOKEN_PREFIX) or k == REGISTRY_TOKEN_ENV) for k, v in self._env.items())

    def _need(self, name: str, app: str) -> str:
        value = (self._env.get(name) or "").strip()
        if not value:
            raise ToolingError(
                f"no Fly token for {app}: {name} is not set in this run. CI holds only app-scoped tokens "
                "and never falls back to another login"
            )
        return value

    def registry_token(self) -> str:
        return self._need(REGISTRY_TOKEN_ENV, ids.FLEET_REGISTRY_APP)

    def env_for(self, app: str | None) -> dict[str, str]:
        """The child-process environment for one fly call against ``app``."""
        if not self.scoped:
            return {}
        if not app:
            raise ToolingError("this fly call names no app, and CI holds only app-scoped tokens")
        if app == ids.FLEET_REGISTRY_APP:
            return {"FLY_API_TOKEN": self.registry_token()}
        seat = self._need(seat_token_env_name(app), app)
        return {"FLY_API_TOKEN": combine_tokens(seat, self.registry_token())}


def _json(res: Result, what: str) -> Any:
    require_ok(res, what)
    try:
        return json.loads(res.stdout or "null")
    except json.JSONDecodeError as exc:
        raise ToolingError(f"{what}: output is not JSON") from exc


class Fly:
    def __init__(self, runner: Runner, repo_root: str, tokens: FlyTokens | None = None) -> None:
        self.runner = runner
        self.repo_root = repo_root
        # Ambient unless told: only Ctx.default reads the process environment,
        # so a test that builds a Fly never inherits CI's GITHUB_ACTIONS.
        self.tokens = tokens or FlyTokens()

    def _fly(self, *args: str, timeout: float = 180, input_text: str | None = None, app: str | None = None) -> Result:
        env = self.tokens.env_for(app or app_in_args(args))
        return self.runner.run(
            ["fly", *args], timeout=timeout, input_text=input_text, cwd=self.repo_root, env=env or None
        )

    # -- apps ------------------------------------------------------------------
    def app_exists(self, app: str) -> bool:
        """Ask about the one app, never list the org.

        CI holds only app-scoped tokens, and Fly refuses an org-wide ``apps list``
        from those as unauthorized: every CI image build failed that way from
        2026-10-26 16:39Z until this changed. ``status -a`` is allowed to the
        app's own token, and a missing app answers "Could not find App" to any
        token. Anything else is a tooling error, not a "no".
        """
        res = self._fly("status", "-a", app, "--json", app=app)
        if res.ok:
            return True
        if APP_NOT_FOUND_MARKER in res.text.lower():
            return False
        raise ToolingError(f"fly status -a {app} failed (rc={res.rc}): {res.text.strip()[-300:]}")

    def create_app(self, app: str, org: str = "personal") -> None:
        require_ok(self._fly("apps", "create", app, "--org", org, app=app), f"fly apps create {app}")

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
    def run_python(self, app: str, source: str, timeout: float = 90) -> Result:
        """Run a Python program on the app's Machine as root, over the Machines API.

        ``fly machine exec`` is an HTTPS call to the Machines API. It was ``fly
        ssh console`` until 2026-10-26, and ssh needs a WireGuard tunnel into the
        org: every GitHub runner minted a fresh peer that was often not ready in
        time ("tunnel unavailable ... probing personal"), so two of three CI runs
        that day lost the busy probe. exec needs no tunnel, accepts an app-scoped
        deploy token, and runs as root (probed: vfy_01M5X52BH037Y1JBF4JWSJT8MT).

        The program travels base64-encoded inside ``python3 -I -c``: base64 has no
        quote, space or backslash, so the one quoting layer exec applies cannot
        touch it. It runs under the image's system python3, which every seat image
        has carried since the first one.
        """
        encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
        command = f"python3 -I -c \"import base64;exec(base64.b64decode('{encoded}'))\""
        return self._fly(
            "machine", "exec", self.machine_id(app), command, "-a", app, "--timeout", str(int(timeout)),
            timeout=timeout + 30,
        )  # fmt: skip

    def machine_id(self, app: str) -> str:
        """The id of the app's one Machine (a seat runs exactly one)."""
        ids_ = [str(m.get("id")) for m in self.machines(app) if m.get("id")]
        if not ids_:
            raise ToolingError(f"{app} has no Machine to run a command on")
        return ids_[0]

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
