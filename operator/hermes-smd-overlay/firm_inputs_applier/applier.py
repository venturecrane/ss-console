"""The firm-inputs applier's core: stage, validate, commit, prune, record.

Everything here is a pure function of an S3-shaped client, a config dir and a
few injected seams (chown, group lookup, the validator runner, the clock), so
the tests drive it against a fake vault and a temp dir.

ORDER OF A COMMIT, and why it is this order:

1. HEAD the marker; its ETag names the version (``etag8``). Done BEFORE the
   copy, and checked again AFTER it: a publish that lands mid-copy changes the
   ETag, so the staged mix of old and new objects is thrown away and the next
   tick starts over.
2. Stage every object under the lane's prefix into a hidden staging path.
3. Own it root:jobs, dirs 0750 and files 0640 (the boot fragments' modes).
4. Validate it with the bundle venv. Exit 0 adopts; exit 1 is a refusal (the
   version is removed and the live copy kept); anything else means the
   validator could not run, which keeps the live copy and retries next tick
   (only a lane with NO live copy adopts unchecked, since the runner's own gate
   re-validates every job and nothing better is on the seat to keep).
5. Rename the staging path to its version name, refresh the broker copy, then
   swap the symlink with one ``os.replace`` (the commit point).
6. Write the lane's verdict word, the state file, prune to three versions, and
   one CONFIG_WRITE audit row.
"""

from __future__ import annotations

import datetime as _dt
import enum
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from shared import seat_layout

logger = logging.getLogger("firm_inputs_applier")

DEFAULT_CONFIG_DIR = seat_layout.SMD_CONFIG
DEFAULT_STATE_DIR = "/run/smd-firm-inputs"
DEFAULT_LANE_STATE_DIR = "/run/smd-lanes"
DEFAULT_VALIDATOR_PYTHON = "/opt/jobs/.venv/bin/python"
STATE_FILE = "state.json"
#: The validator's first line for a lane's refused version, root-only, beside the state file:
#: ``<lane>.rejected``. The state file itself is world-readable (the heartbeat runs as hermes and
#: reads it), so the words that can name a firm's own values never go in it.
REJECTED_DETAIL_SUFFIX = ".rejected"
#: Versions kept on disk per lane (the live one is always among them).
KEEP_VERSIONS = 3
#: The label of a copy that predates versioning (a real dir found at boot).
LEGACY_LABEL = "00000000"
VALIDATOR_TIMEOUT_SECONDS = 120
DETAIL_MAX = 200
_LABEL = re.compile(r"\A[0-9a-f]{8}\Z")
_ETAG_HEAD = re.compile(r'\A"?([0-9a-fA-F]{8})')
_VALIDATOR_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ")


class Outcome(enum.Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    UNCHANGED = "unchanged"
    RETRY = "retry"
    ABSENT = "absent"


@dataclass(frozen=True)
class Lane:
    """One lane's inputs: where they live in the vault and on the seat."""

    name: str
    marker: str
    broker_copy: str | None = None
    #: The chronology's inputs are one file, not a prefix.
    single_file: bool = False
    #: Readable by every uid (dirs 0755, files 0644), still root-owned and written
    #: only here: the attorney roster, which the case-system connector reads in
    #: the gateway as well as the jobs child (no lane's prompts or voice in it).
    public: bool = False

    def marker_key(self, slug: str) -> str:
        if self.single_file:
            return f"vaults/{slug}/{self.marker}"
        return f"vaults/{slug}/{self.name}/{self.marker}"

    def prefix(self, slug: str) -> str:
        return f"vaults/{slug}/{self.name}/"

    def live(self, config_dir: Path) -> Path:
        return config_dir / (self.marker if self.single_file else self.name)

    def version_name(self, label: str) -> str:
        if self.single_file:
            stem = self.marker.removesuffix(".yaml")
            return f"{stem}.v{label}.yaml"
        return f"{self.name}.v{label}"

    def version_re(self) -> re.Pattern[str]:
        if self.single_file:
            stem = re.escape(self.marker.removesuffix(".yaml"))
            return re.compile(rf"\A{stem}\.v([0-9a-f]{{8}})\.yaml\Z")
        return re.compile(rf"\A{re.escape(self.name)}\.v([0-9a-f]{{8}})\Z")

    def staging_prefix(self) -> str:
        return f".{self.marker.removesuffix('.yaml') if self.single_file else self.name}.stage-"

    def validator_argv(self, python: str, path: Path) -> list[str]:
        if self.single_file:
            return [
                python,
                "-c",
                "import sys\nfrom jobs.medchron.config import load\nload(sys.argv[1])",
                str(path),
            ]
        return [python, "-m", f"jobs.{self.name}.firm", str(path)]


#: Every lane whose inputs the vault holds, with its broker copy. negotiation
#: and note have no firm inputs; marketing's registers are read by the gateway
#: and stay a boot-time fetch.
LANES: tuple[Lane, ...] = (
    Lane("demand", "demand-firm.yaml"),
    Lane("drafting", "drafting-firm.yaml"),
    Lane("litigation", "litigation-firm.yaml", broker_copy="litigation-broker-firm.yaml"),
    Lane("casefile", "casefile-firm.yaml", broker_copy="casefile-broker-firm.yaml"),
    Lane("records", "records-firm.yaml", broker_copy="records-broker-firm.yaml"),
    Lane("medchron", "medchron-firm.yaml", single_file=True),
    # The firm's attorney roster (design 2026-11-06, "who an attorney is"): not a
    # job lane, one file every document path reads (operator_connector_sdk
    # library/roster.py, validated by ``python -m jobs.attorneys.firm``).
    Lane("attorneys", "attorneys.json", public=True),
)
LANES_BY_NAME = {lane.name: lane for lane in LANES}
#: Firm inputs that are not a lane: watched on every seat that authors any lane
#: (the only seats with a jobs runner to validate them and a reader to use them).
SHARED_INPUTS = ("attorneys",)


def authored_lanes(raw: str | None) -> tuple[Lane, ...]:
    """The lanes of ``SMD_AUTHORED_LANES`` (a comma list) that have firm inputs,
    and the shared inputs when any lane is authored."""
    names = {part.strip() for part in (raw or "").split(",") if part.strip()}
    if names:
        names |= set(SHARED_INPUTS)
    return tuple(lane for lane in LANES if lane.name in names)


def etag8(etag: object) -> str | None:
    """The first 8 hex of an S3/R2 ETag, lowercased; None when it has none."""
    if not isinstance(etag, str):
        return None
    m = _ETAG_HEAD.match(etag.strip())
    return m.group(1).lower() if m else None


def sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_target(lane: Lane, config_dir: Path) -> str | None:
    """The live symlink's target name, or None when it is absent or not a link."""
    live = lane.live(config_dir)
    if not os.path.islink(live):
        return None
    return os.path.basename(os.readlink(live))


def current_label(lane: Lane, config_dir: Path) -> str | None:
    target = current_target(lane, config_dir)
    if target is None or not (config_dir / target).exists():
        return None
    m = lane.version_re().match(target)
    return m.group(1) if m else None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:DETAIL_MAX]
    return ""


@dataclass
class ValidatorResult:
    code: int
    first_line: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def refused(self) -> bool:
        return self.code == 1


ValidatorRunner = Callable[[list[str]], ValidatorResult]


def run_validator(argv: list[str]) -> ValidatorResult:
    """Run the bundle venv validator with a scrubbed env; 127 when it cannot start."""
    if not os.access(argv[0], os.X_OK):
        return ValidatorResult(127, f"{argv[0]} is not executable")
    env = {k: os.environ[k] for k in _VALIDATOR_ENV_KEYS if os.environ.get(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv from the lane table, no shell
            argv,
            env=env,
            cwd="/",
            capture_output=True,
            text=True,
            timeout=VALIDATOR_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ValidatorResult(126, f"validator did not run: {type(exc).__name__}")
    out = _first_line(proc.stderr) or _first_line(proc.stdout)
    if proc.returncode == 1 and proc.stderr:
        # A traceback's first line is "Traceback ..."; its last says why.
        lines = [ln.strip() for ln in proc.stderr.splitlines() if ln.strip()]
        if lines and lines[0].startswith("Traceback"):
            out = lines[-1][:DETAIL_MAX]
    return ValidatorResult(proc.returncode, out)


def _default_gid(group: str) -> int:
    import grp

    return grp.getgrnam(group).gr_gid


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _public_dir(d: Path) -> None:
    """Create ``d`` and make it 0755 explicitly. ``mkdir(mode=0o755)`` is masked by the process
    umask, and the root poller inherits the entrypoint's ``umask 027``: on 2026-11-05 the state
    dir came out 0750, the hermes-run heartbeat could not read state.json, and the fleet table
    showed no firm inputs for a seat that had adopted them (fleet_status.firm_inputs_json NULL)."""
    d.mkdir(mode=0o755, parents=True, exist_ok=True)
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - the state dir is meant to be world-readable: it holds version labels and verdict words only (the validator's text is 0640 beside it)
    os.chmod(d, 0o755)


def _safe_rel(key: str, prefix: str) -> PurePosixPath | None:
    rel = PurePosixPath(key[len(prefix) :])
    if not rel.parts or rel.is_absolute() or any(p in ("", ".", "..") for p in rel.parts):
        return None
    return rel


@dataclass
class FirmInputsApplier:
    s3_client: Any
    bucket: str
    slug: str
    lanes: tuple[Lane, ...]
    config_dir: Path = Path(DEFAULT_CONFIG_DIR)
    state_dir: Path = Path(DEFAULT_STATE_DIR)
    lane_state_dir: Path = Path(DEFAULT_LANE_STATE_DIR)
    validator_python: str = DEFAULT_VALIDATOR_PYTHON
    validator: ValidatorRunner = run_validator
    chown: Callable[[str, int, int], None] = os.chown
    group_id: Callable[[str], int] = _default_gid
    audit_client: Any = None
    now: Callable[[], str] = utc_now
    #: Whether the validator can start at all. Checked BEFORE staging, so a seat
    #: whose bundle is missing does not re-download a lane every tick.
    validator_ready: Callable[[str], bool] = lambda path: os.access(path, os.X_OK)
    _logged_head: dict[str, str] = field(default_factory=dict)
    _logged_unready: set[str] = field(default_factory=set)

    # ---------- the vault ----------

    def head_label(self, lane: Lane) -> str | None | Outcome:
        """The marker's etag8; ``Outcome.ABSENT`` on a clean 404; None when unreadable."""
        try:
            head = self.s3_client.head_object(Bucket=self.bucket, Key=lane.marker_key(self.slug))
        except Exception as exc:  # noqa: BLE001 - absent or unreachable: skip this tick
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            word = "absent" if code in ("404", "NoSuchKey", "NotFound") else "unreadable"
            if self._logged_head.get(lane.name) != word:
                self._logged_head[lane.name] = word
                logger.info("firm_inputs_applier: %s marker %s (%s)", lane.name, word, exc)
            return Outcome.ABSENT if word == "absent" else None
        self._logged_head.pop(lane.name, None)
        etag = head.get("ETag") if isinstance(head, dict) else getattr(head, "ETag", None)
        return etag8(etag)

    def _keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3_client.list_objects_v2(**kwargs)
            keys += [o["Key"] for o in page.get("Contents") or [] if not o["Key"].endswith("/")]
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    def _get(self, key: str, dest: Path) -> None:
        body = self.s3_client.get_object(Bucket=self.bucket, Key=key)["Body"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(body.read())

    def _stage(self, lane: Lane) -> Path:
        stage = self.config_dir / f"{lane.staging_prefix()}{secrets.token_hex(4)}"
        if lane.single_file:
            stage = stage.with_name(stage.name + ".yaml")
            self._get(lane.marker_key(self.slug), stage)
            return stage
        stage.mkdir(mode=0o700)
        prefix = lane.prefix(self.slug)
        for key in self._keys(prefix):
            rel = _safe_rel(key, prefix)
            if rel is None:
                raise ValueError(f"refusing vault key {key!r}")
            self._get(key, stage.joinpath(*rel.parts))
        if not (stage / lane.marker).is_file():
            raise FileNotFoundError(f"{lane.marker} did not arrive")
        return stage

    # ---------- the seat ----------

    def _own(self, path: Path, group: str, public: bool = False) -> None:
        gid = self.group_id(group)
        dmode, fmode = (0o755, 0o644) if public else (0o750, 0o640)
        if path.is_file():
            self.chown(str(path), 0, gid)
            os.chmod(path, fmode)
            return
        for root, _dirs, files in os.walk(path):
            self.chown(root, 0, gid)
            # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - root:jobs 0750 is the lane inputs' boot mode (entrypoint lane fragments): the jobs child must traverse it and nobody else may; 0755 only for the attorney roster, which the gateway reads and only root writes
            os.chmod(root, dmode)
            for name in files:
                p = os.path.join(root, name)
                self.chown(p, 0, gid)
                os.chmod(p, fmode)

    def _broker_copy(self, lane: Lane, version: Path) -> None:
        if lane.broker_copy is None:
            return
        dest = self.config_dir / lane.broker_copy
        tmp = self.config_dir / f".{lane.broker_copy}.{secrets.token_hex(4)}"
        shutil.copyfile(version / lane.marker, tmp)
        self.chown(str(tmp), 0, self.group_id("workspace-broker"))
        os.chmod(tmp, 0o640)
        os.replace(tmp, dest)

    def _swap(self, lane: Lane, version_name: str) -> None:
        live = lane.live(self.config_dir)
        if os.path.lexists(live) and not os.path.islink(live):
            # A copy that predates versioning: it becomes the legacy version so
            # the swap below can land (the boot fragments convert it first).
            legacy = self.config_dir / lane.version_name(LEGACY_LABEL)
            if os.path.lexists(legacy):
                _remove(legacy)
            os.rename(live, legacy)
        tmp = self.config_dir / f".{live.name}.link-{secrets.token_hex(4)}"
        os.symlink(version_name, tmp)
        try:
            os.replace(tmp, live)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def prune(self, lane: Lane) -> list[str]:
        """Keep the newest ``KEEP_VERSIONS`` versions and the live one; drop stragglers."""
        pattern = lane.version_re()
        live = current_target(lane, self.config_dir)
        versions: list[tuple[float, str]] = []
        removed: list[str] = []
        for entry in os.scandir(self.config_dir):
            if entry.name.startswith(lane.staging_prefix()):
                _remove(Path(entry.path))
                removed.append(entry.name)
            elif pattern.match(entry.name):
                versions.append((entry.stat(follow_symlinks=False).st_mtime, entry.name))
        versions.sort(reverse=True)
        for _mtime, name in versions[KEEP_VERSIONS:]:
            if name == live:
                continue
            try:
                _remove(self.config_dir / name)
                removed.append(name)
            except OSError as exc:
                logger.warning("firm_inputs_applier: could not prune %s: %s", name, exc)
        return removed

    def _record_lane_word(self, lane: Lane, word: str) -> None:
        d = self.lane_state_dir
        try:
            _public_dir(d)
            tmp = d / f".{lane.name}.inputs.{secrets.token_hex(4)}"
            tmp.write_text(word + "\n", encoding="ascii")
            os.chmod(tmp, 0o644)
            os.replace(tmp, d / f"{lane.name}.inputs")
        except OSError as exc:
            logger.warning(
                "firm_inputs_applier: could not record %s inputs word: %s", lane.name, exc
            )

    # ---------- state ----------

    def read_state(self) -> dict[str, dict[str, Any]]:
        try:
            doc = json.loads((self.state_dir / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _write_state(self, state: dict[str, dict[str, Any]]) -> None:
        try:
            _public_dir(self.state_dir)
            tmp = self.state_dir / f".{STATE_FILE}.{secrets.token_hex(4)}"
            tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.state_dir / STATE_FILE)
        except OSError as exc:
            logger.warning("firm_inputs_applier: could not write %s: %s", STATE_FILE, exc)

    def _write_rejected_detail(self, lane: Lane, first_line: str | None) -> None:
        """The validator's first line for ``lane``'s refused version, root-only (0640), beside the
        state file; removed when the lane has no refused version. Never in state.json: that file is
        read by the hermes-run heartbeat, and a validator's words can quote a firm's own values."""
        dest = self.state_dir / f"{lane.name}{REJECTED_DETAIL_SUFFIX}"
        try:
            if not first_line:
                dest.unlink(missing_ok=True)
                return
            _public_dir(self.state_dir)
            tmp = self.state_dir / f".{dest.name}.{secrets.token_hex(4)}"
            tmp.write_text(first_line.rstrip("\n") + "\n", encoding="utf-8")
            os.chmod(tmp, 0o640)
            os.replace(tmp, dest)
        except OSError as exc:
            logger.warning("firm_inputs_applier: could not write %s: %s", dest.name, exc)

    def _applied_at(self, lane: Lane, label: str) -> str:
        try:
            mtime = (self.config_dir / lane.version_name(label)).stat().st_mtime
        except OSError:
            return self.now()
        return _dt.datetime.fromtimestamp(mtime, _dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---------- one lane, one tick ----------

    def tick_lane(self, lane: Lane, state: dict[str, dict[str, Any]]) -> Outcome:
        label = self.head_label(lane)
        live = current_label(lane, self.config_dir)
        entry = state.get(lane.name) or {}
        if isinstance(label, Outcome) or label is None:
            if live is not None and entry.get("etag8") != live:
                state[lane.name] = {
                    "etag8": live,
                    "verdict": "fetched",
                    "applied_at": self._applied_at(lane, live),
                }
            return Outcome.ABSENT if isinstance(label, Outcome) else Outcome.RETRY
        if label == live:
            if entry.get("etag8") != live or entry.get("verdict") != "fetched":
                state[lane.name] = {
                    "etag8": live,
                    "verdict": "fetched",
                    "applied_at": self._applied_at(lane, live),
                }
            return Outcome.UNCHANGED
        if entry.get("verdict") == "rejected" and entry.get("rejected_etag8") == label:
            return Outcome.UNCHANGED
        return self.adopt(lane, label, state)

    def adopt(self, lane: Lane, label: str, state: dict[str, dict[str, Any]]) -> Outcome:
        live = current_label(lane, self.config_dir)
        if live is not None and not self.validator_ready(self.validator_python):
            if lane.name not in self._logged_unready:
                self._logged_unready.add(lane.name)
                logger.error(
                    "firm_inputs_applier: %s has version %s waiting but the validator %s cannot run;"
                    " keeping %s until it can",
                    lane.name,
                    label,
                    self.validator_python,
                    live,
                )
            return Outcome.RETRY
        self._logged_unready.discard(lane.name)
        try:
            stage = self._stage(lane)
        except Exception as exc:  # noqa: BLE001 - a fetch fault retries next tick
            logger.error(
                "firm_inputs_applier: %s stage of %s failed (%s); retrying", lane.name, label, exc
            )
            self.prune(lane)
            return Outcome.RETRY
        after = self.head_label(lane)
        if after != label:
            logger.warning(
                "firm_inputs_applier: %s changed during staging (%s -> %s); retrying",
                lane.name,
                label,
                after,
            )
            _remove(stage)
            return Outcome.RETRY
        try:
            self._own(stage, "jobs", lane.public)
        except (OSError, KeyError) as exc:
            logger.error(
                "firm_inputs_applier: %s could not own the staged copy (%s); retrying",
                lane.name,
                exc,
            )
            _remove(stage)
            return Outcome.RETRY
        verdict = self.validator(lane.validator_argv(self.validator_python, stage))
        if verdict.refused:
            _remove(stage)
            logger.error(
                "firm_inputs_applier: %s inputs %s REJECTED by the validator (%s); keeping %s",
                lane.name,
                label,
                verdict.first_line,
                live,
            )
            prior = state.get(lane.name) or {}
            state[lane.name] = {
                "etag8": live,
                "verdict": "rejected",
                "rejected_etag8": label,
                "applied_at": (prior.get("applied_at") or self._applied_at(lane, live))
                if live
                else None,
                "checked_at": self.now(),
            }
            self._write_rejected_detail(lane, verdict.first_line)
            self._record_lane_word(lane, "rejected")
            self._emit(lane, label, live, "rejected", verdict.first_line)
            return Outcome.REJECTED
        if not verdict.ok:
            if live is not None:
                _remove(stage)
                logger.error(
                    "firm_inputs_applier: %s validator could not run (exit %s: %s); keeping %s, retrying",
                    lane.name,
                    verdict.code,
                    verdict.first_line,
                    live,
                )
                return Outcome.RETRY
            logger.warning(
                "firm_inputs_applier: %s validator could not run (exit %s); this seat has no copy, adopting %s UNCHECKED",
                lane.name,
                verdict.code,
                label,
            )
        version = self.config_dir / lane.version_name(label)
        try:
            if os.path.lexists(version):
                _remove(version)
            os.rename(stage, version)
            os.utime(version)
            if not lane.single_file:
                self._broker_copy(lane, version)
            self._swap(lane, version.name)
        except (OSError, KeyError) as exc:
            logger.error(
                "firm_inputs_applier: %s commit of %s failed (%s); retrying", lane.name, label, exc
            )
            _remove(stage)
            return Outcome.RETRY
        applied_at = self.now()
        state[lane.name] = {"etag8": label, "verdict": "fetched", "applied_at": applied_at}
        self._write_rejected_detail(lane, None)
        self._record_lane_word(lane, "fetched")
        self.prune(lane)
        logger.info(
            "firm_inputs_applier: %s inputs APPLIED %s (previous %s)", lane.name, label, live
        )
        self._emit(lane, label, live, "applied", None)
        return Outcome.APPLIED

    def tick(self) -> dict[str, Outcome]:
        state = self.read_state()
        before = json.dumps(state, sort_keys=True)
        outcomes: dict[str, Outcome] = {}
        for lane in self.lanes:
            try:
                outcomes[lane.name] = self.tick_lane(lane, state)
            except Exception:  # noqa: BLE001 - one lane's fault must not stop the others
                logger.exception("firm_inputs_applier: %s tick failed", lane.name)
                outcomes[lane.name] = Outcome.RETRY
        for name in list(state):
            if name not in {lane.name for lane in self.lanes}:
                del state[name]
        if (
            json.dumps(state, sort_keys=True) != before
            or not (self.state_dir / STATE_FILE).exists()
        ):
            self._write_state(state)
        return outcomes

    def _emit(
        self, lane: Lane, label: str, previous: str | None, outcome: str, detail: str | None
    ) -> None:
        if self.audit_client is None:
            return
        from shared.audit_contract import INSERT_SQL, agent_event_params

        metadata = {
            "firm_inputs_apply": True,
            "customer": self.slug,
            "lane": lane.name,
            "version": label,
            "previous": previous,
            "outcome": outcome,
        }
        if detail:
            metadata["detail"] = detail
        params = agent_event_params(action_type="CONFIG_WRITE", metadata=metadata)
        params[3] = "firm-inputs-applier"
        params[4] = "operator"
        try:
            write = (
                getattr(self.audit_client, "execute_config_write", None)
                or self.audit_client.execute
            )
            write(INSERT_SQL, *params)
        except Exception as exc:  # noqa: BLE001 - audit never undoes an adoption
            logger.warning("firm_inputs_applier: CONFIG_WRITE audit emission failed (%s)", exc)


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_STATE_DIR",
    "KEEP_VERSIONS",
    "LANES",
    "LANES_BY_NAME",
    "SHARED_INPUTS",
    "LEGACY_LABEL",
    "STATE_FILE",
    "FirmInputsApplier",
    "Lane",
    "Outcome",
    "ValidatorResult",
    "authored_lanes",
    "current_label",
    "current_target",
    "etag8",
    "run_validator",
]
