"""The bundle release contract (ADR 0100): pointer, manifest, and what a bundle holds.

Pure parsing, no R2 and no disk, so every refusal rule is testable on bytes.

R2 LAYOUT (written by ss-console CI ``publish-bundle`` and the release tick):

* ``vaults/<slug>/bundles/<name>-pointer.json`` — what this seat should run::

      {"version": "<v>", "manifest_key": "fleet/bundles/<name>/<v>.json",
       "manifest_sha256": "<hex>"}

* ``fleet/bundles/<name>/<v>.json`` — the manifest::

      {"name": "<name>", "version": "<v>", "sha256": "<tarball hex>",
       "size": <bytes>, "ss_console_sha": "...", "base_image_ref": "...",
       "python": "..."}

* ``fleet/bundles/<name>/<v>.tar.zst`` — the tarball. Its root holds the spec's
  members (for ``jobs``: ``medchron/`` and ``connectors/smokeball/``).

The pointer parser is :func:`skills_applier.manifest.parse_pointer` with the
bundle prefix: one parser, one set of refusal rules, for every artifact a seat
adopts from a pointer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from skills_applier.manifest import (
    ManifestError,
    Pointer,
    _load_object,
    parse_pointer,
    valid_version,
)

POINTER_KEY_TEMPLATE = "vaults/{slug}/bundles/{name}-pointer.json"
PREFIX_TEMPLATE = "fleet/bundles/{name}/"

MAX_MANIFEST_BYTES = 64 * 1024
#: The jobs bundle is two venvs (a few hundred MB); past this is a mistake or an
#: attempt to fill the volume, and the release is refused.
MAX_TARBALL_BYTES = 2 * 1024 * 1024 * 1024

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_NAME = re.compile(r"\A[a-z][a-z0-9-]{0,31}\Z")


@dataclass(frozen=True)
class BundleSpec:
    """What one bundle is on a seat.

    ``mounts`` pairs each tarball member with the image path it is bind-mounted
    over; ``required`` are paths that must exist (links count) after extraction;
    ``import_check`` is the venv python (relative to the version dir) handed to
    the image's own import check, ``/app/jobs-runtime.sh import-check``, the
    same script the image build and the CI bundle build run.
    """

    name: str
    mounts: tuple[tuple[str, str], ...]
    required: tuple[str, ...]
    import_check: str

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(member for member, _target in self.mounts)


JOBS = BundleSpec(
    name="jobs",
    mounts=(
        ("medchron", "/opt/medchron"),
        ("connectors/smokeball", "/opt/connectors/smokeball"),
    ),
    required=(
        "medchron/.venv/bin/python",
        "medchron/.venv/bin/medchron",
        "medchron/anthropic_pricing.json",
        "connectors/smokeball/.venv/bin/python",
        "connectors/smokeball/.venv/bin/smokeball-mcp",
    ),
    import_check="medchron/.venv/bin/python",
)

SPECS: dict[str, BundleSpec] = {JOBS.name: JOBS}


@dataclass(frozen=True)
class BundleManifest:
    name: str
    version: str
    sha256: str
    size: int
    ss_console_sha: str | None = None
    base_image_ref: str | None = None


def pointer_key(slug: str, name: str) -> str:
    if not isinstance(slug, str) or not slug.strip() or "/" in slug:
        raise ManifestError(["customer slug is required and must be one path segment"])
    return POINTER_KEY_TEMPLATE.format(slug=slug.strip(), name=name)


def prefix(name: str) -> str:
    return PREFIX_TEMPLATE.format(name=name)


def tarball_key(name: str, version: str) -> str:
    return f"{prefix(name)}{version}.tar.zst"


def parse_bundle_pointer(data: bytes, name: str) -> Pointer:
    return parse_pointer(data, manifest_prefix=prefix(name), what=f"{name} bundle pointer")


def parse_bundle_manifest(data: bytes, *, name: str, expected_version: str) -> BundleManifest:
    """The manifest, or :class:`ManifestError` with every reason found."""
    doc = _load_object(data, f"{name} bundle manifest", MAX_MANIFEST_BYTES)
    reasons: list[str] = []
    if doc.get("name") != name or not _NAME.match(name):
        reasons.append(f"manifest.name {doc.get('name')!r} is not {name!r}")
    version = doc.get("version")
    if version != expected_version or not valid_version(version):
        reasons.append(
            f"manifest.version {version!r} does not match the pointer's {expected_version!r}"
        )
    sha = doc.get("sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        reasons.append("manifest.sha256 must be 64 lowercase hex characters")
    size = doc.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        reasons.append("manifest.size must be a positive integer")
    elif size > MAX_TARBALL_BYTES:
        reasons.append(f"manifest.size {size} is over the {MAX_TARBALL_BYTES}-byte cap")
    if reasons:
        raise ManifestError(reasons)
    assert isinstance(sha, str) and isinstance(size, int)
    sc_sha, base = doc.get("ss_console_sha"), doc.get("base_image_ref")
    return BundleManifest(
        name=name,
        version=expected_version,
        sha256=sha,
        size=size,
        ss_console_sha=sc_sha if isinstance(sc_sha, str) else None,
        base_image_ref=base if isinstance(base, str) else None,
    )


__all__ = [
    "JOBS",
    "MAX_MANIFEST_BYTES",
    "MAX_TARBALL_BYTES",
    "SPECS",
    "BundleManifest",
    "BundleSpec",
    "parse_bundle_manifest",
    "parse_bundle_pointer",
    "pointer_key",
    "prefix",
    "tarball_key",
]
