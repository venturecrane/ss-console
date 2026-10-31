"""Boot CLI: ``python -I -m bundle_installer <name>`` (root, before the gateway starts).

Run by ``entrypoint-bundles.sh`` only on a seat that authors a job lane
(``shared/capabilities.py``). Prints ONE line on stdout when a verified bundle
should be mounted::

    <version>\\t<version dir>

and nothing when the image copy should run. Every reason goes to stderr, where
the boot log keeps it. Exit 0 in both cases: a bundle that cannot be adopted
must never stop a boot, because the image's copy is a working job runner. Exit
2 is a usage fault (unknown bundle, not root).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from bundle_installer.installer import DEFAULT_HOME, Decision, decide
from bundle_installer.manifest import SPECS

logger = logging.getLogger("bundle_installer")

_R2_ENV = ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_CONFIG")


def build_s3_client() -> Any:
    """A boto3 R2 client, or None when this seat carries no R2 config."""
    if not all(os.environ.get(name) for name in _R2_ENV):
        return None
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def _report(name: str, decision: Decision) -> None:
    why = "; ".join(decision.reasons)
    if decision.path is None:
        logger.error("bundle_installer: %s bundle NOT mounted, the image copy runs (%s)", name, why)
    else:
        logger.info(
            "bundle_installer: %s bundle %s %s%s",
            name,
            decision.version,
            decision.source,
            f" ({why})" if why else "",
        )


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[], Any] = build_s3_client,
    require_root: bool = True,
    **decide_kwargs: Any,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(
        level=os.environ.get("SMD_BUNDLE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    if len(args) != 1 or args[0] not in SPECS:
        print(f"usage: python -m bundle_installer {{{'|'.join(SPECS)}}}", file=sys.stderr)
        return 2
    if require_root and os.geteuid() != 0:
        logger.error("bundle_installer: must run as root (the store and the mount are root's)")
        return 2
    spec = SPECS[args[0]]
    try:
        client = client_factory()
    except Exception as exc:  # noqa: BLE001 — no client is "R2 unreachable"
        logger.warning("bundle_installer: could not build the R2 client: %s", exc)
        client = None
    try:
        decision = decide(
            s3_client=client,
            bucket=os.environ.get("R2_BUCKET_CONFIG", ""),
            slug=os.environ.get("CUSTOMER_SLUG", ""),
            spec=spec,
            home=os.environ.get("HERMES_HOME") or DEFAULT_HOME,
            **decide_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — a crash is a refusal, never a broken boot
        decision = Decision("image", reasons=(f"installer crashed: {exc!r}",))
    _report(spec.name, decision)
    if decision.path is not None:
        print(f"{decision.version}\t{decision.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
