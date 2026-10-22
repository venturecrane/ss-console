"""Identifier shapes, SQL literals and time, in one place.

Every value that reaches a wrangler ``--command`` string, an R2 key or a Fly app
name passes one of the validators below first. D1 over wrangler has no bound
parameters, so the defense is a strict shape for every identifier (slug,
release id, version, digest) plus :func:`sql_str` for every value, a hex blob
literal with no escape sequence to break out of. A value that fails its shape is a
:class:`ValueError` before any command runs.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from bin.lib.console_d1 import sql_text
from bin.lib.seat_dirs import SLUG_RE

RELEASE_ID_RE = re.compile(r"^(img|skl)-[A-Za-z0-9-]{6,80}$")
SKILLS_VERSION_RE = re.compile(r"^[0-9]{12}-[0-9a-f]{8}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_REF_RE = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+(?::[A-Za-z0-9._-]{1,128})?(?:@sha256:[0-9a-f]{64})?$"
)
SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")

FLEET_REGISTRY_APP = "hermes-fleet"
FLEET_REGISTRY = f"registry.fly.io/{FLEET_REGISTRY_APP}"


def _check(value: str, rx: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not rx.match(value):
        raise ValueError(f"invalid {what}: {value!r} (must match {rx.pattern})")
    return value


def slug(value: str) -> str:
    return _check(value, SLUG_RE, "slug")


def release_id(value: str) -> str:
    return _check(value, RELEASE_ID_RE, "release id")


def skills_version(value: str) -> str:
    return _check(value, SKILLS_VERSION_RE, "skills version")


def digest(value: str) -> str:
    return _check(value, DIGEST_RE, "image digest")


def image_ref(value: str) -> str:
    return _check(value, IMAGE_REF_RE, "image ref")


def secret_name(value: str) -> str:
    return _check(value, SECRET_NAME_RE, "secret name")


def app_name(seat: str) -> str:
    return f"hermes-{slug(seat)}"


def sql_str(value: object) -> str:
    """A TEXT literal (or NULL) that cannot be escaped out of: console_d1.sql_text.

    A hex blob literal cast back to TEXT, the posture bin/lib/console_d1.py
    documents for this exact path (wrangler has no parameter binding).
    """
    return sql_text(None if value is None else str(value))


def sql_int(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if not isinstance(value, int):
        raise ValueError(f"not an integer: {value!r}")
    return str(value)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    raw = text.strip().replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def digest_hex(value: str) -> str:
    """``sha256:<hex>`` -> ``<hex>``, the form R2 keys use (no colon)."""
    return digest(value).split(":", 1)[1]


def digest_in_ref(ref: str | None) -> str | None:
    """The ``sha256:<hex>`` an image ref pins, or None."""
    if not ref or "@sha256:" not in ref:
        return None
    tail = "sha256:" + ref.rsplit("@sha256:", 1)[1]
    return tail if DIGEST_RE.match(tail) else None


def image_matches(reported: str | None, target_ref: str, target_digest: str | None) -> bool:
    """Does the image a seat reports equal the release image?

    A seat reports ``FLY_IMAGE_REF``, which carries a digest when it was deployed
    by digest and may carry only ``repo:tag`` otherwise. A digest on both sides
    decides; otherwise the full ref or ``repo:tag`` must be equal.
    """
    if not reported:
        return False
    got = digest_in_ref(reported)
    if got and target_digest:
        return got == target_digest
    if reported == target_ref:
        return True
    return reported.split("@", 1)[0] == target_ref.split("@", 1)[0]
