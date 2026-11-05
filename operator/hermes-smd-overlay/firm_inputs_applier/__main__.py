"""Root firm-inputs applier entry point (see the package docstring).

* ``python -m firm_inputs_applier`` — the poll loop, run inside the one root
  poller process (root_pollers). Every ``SMD_FIRM_INPUTS_POLL_SECONDS``
  (default 15) it HEADs each authored lane's marker and adopts a changed one.
* ``python -m firm_inputs_applier --once`` — one tick at boot, after the lane
  fragments ran (entrypoint-supervisor.sh ``smd_firm_inputs_applier``). The
  fragments already fetched; this writes the state file the heartbeat reads and
  catches a publish that landed during the boot. Always exits 0: a boot must
  never fail on it.

Credentials: the R2 config pull credential the skills applier uses
(``R2_ENDPOINT_URL``, ``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``,
``R2_BUCKET_CONFIG``), held only by this root process. The validator child
gets none of them.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from firm_inputs_applier.applier import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_LANE_STATE_DIR,
    DEFAULT_STATE_DIR,
    DEFAULT_VALIDATOR_PYTHON,
    FirmInputsApplier,
    authored_lanes,
)

logger = logging.getLogger("firm_inputs_applier")

EXIT_STOPPED = 130
_DEFAULT_POLL_SECONDS = 15.0


@dataclass
class FirmInputsPollLoop:
    applier: FirmInputsApplier
    poll_seconds: float = _DEFAULT_POLL_SECONDS

    def run_once(self):
        return self.applier.tick()

    def run_forever(self, stop: Callable[[], bool]) -> None:
        logger.info(
            "firm_inputs_applier: poll loop started (slug=%s lanes=%s interval=%ss)",
            self.applier.slug,
            ",".join(lane.name for lane in self.applier.lanes) or "none",
            self.poll_seconds,
        )
        while not stop():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the loop outlives any one tick
                logger.exception("firm_inputs_applier: unexpected error in poll tick; continuing")
            slept = 0.0
            while slept < self.poll_seconds and not stop():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"firm_inputs_applier: required env var {name} is unset")
    return value


def _poll_seconds() -> float:
    raw = os.environ.get("SMD_FIRM_INPUTS_POLL_SECONDS")
    try:
        value = float(raw) if raw else _DEFAULT_POLL_SECONDS
    except ValueError:
        return _DEFAULT_POLL_SECONDS
    return value if value >= 1.0 else _DEFAULT_POLL_SECONDS


def build_loop(*, with_audit: bool = True) -> FirmInputsPollLoop:
    import boto3

    slug = _require_env("CUSTOMER_SLUG")
    bucket = _require_env("R2_BUCKET_CONFIG")
    s3 = boto3.client(
        "s3",
        endpoint_url=_require_env("R2_ENDPOINT_URL"),
        aws_access_key_id=_require_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_require_env("R2_SECRET_ACCESS_KEY"),
    )
    audit = None
    if with_audit:
        from shared.audit_client import audit_client_from_env

        audit = audit_client_from_env(customer_slug=slug)
    applier = FirmInputsApplier(
        s3_client=s3,
        bucket=bucket,
        slug=slug,
        lanes=authored_lanes(os.environ.get("SMD_AUTHORED_LANES")),
        config_dir=Path(os.environ.get("SMD_FIRM_INPUTS_CONFIG_DIR") or DEFAULT_CONFIG_DIR),
        state_dir=Path(os.environ.get("SMD_FIRM_INPUTS_STATE_DIR") or DEFAULT_STATE_DIR),
        lane_state_dir=Path(os.environ.get("SMD_LANE_INPUTS_STATE_DIR") or DEFAULT_LANE_STATE_DIR),
        validator_python=os.environ.get("SMD_FIRM_INPUTS_VALIDATOR_PYTHON")
        or DEFAULT_VALIDATOR_PYTHON,
        audit_client=audit,
    )
    return FirmInputsPollLoop(applier=applier, poll_seconds=_poll_seconds())


def main(argv: list[str] | None = None, *, loop_factory=build_loop) -> int:
    args = sys.argv[1:] if argv is None else argv
    once = "--once" in args
    logging.basicConfig(
        level=os.environ.get("SMD_FIRM_INPUTS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stopped = {"flag": False}

    def _request_stop(signum, _frame) -> None:
        logger.info("firm_inputs_applier: received signal %s; stopping", signum)
        stopped["flag"] = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        loop = loop_factory(with_audit=not once)
    except SystemExit as exc:
        logger.error("firm_inputs_applier: startup failed: %s", exc)
        return 0 if once else 2
    if once:
        try:
            outcomes = loop.run_once()
        except Exception:  # noqa: BLE001 - a boot must never fail on this
            logger.exception("firm_inputs_applier: boot tick failed")
            return 0
        logger.info(
            "firm_inputs_applier: boot tick %s",
            ", ".join(f"{k}={v.value}" for k, v in outcomes.items()) or "no authored lane",
        )
        return 0
    loop.run_forever(stop=lambda: stopped["flag"])
    return EXIT_STOPPED if stopped["flag"] else 0


if __name__ == "__main__":
    sys.exit(main())
