"""The bundle release contract (ADR 0100): pointer, manifest, and what a bundle holds.

Pure parsing, no R2 and no disk, so every refusal rule is testable on bytes.

R2 LAYOUT (written by ss-console CI ``publish-bundle`` and the release tick):

* ``vaults/<slug>/bundles/<name>-pointer.json`` — what this seat should run::

      {"version": "<v>", "manifest_key": "fleet/bundles/<name>/<v>.json",
       "manifest_sha256": "<hex>"}

* ``fleet/bundles/<name>/<v>.json`` — the manifest::

      {"name": "<name>", "version": "<v>", "sha256": "<tarball hex>",
       "size": <bytes>, "layout": <n>, "ss_console_sha": "...",
       "base_image_ref": "...", "python": "..."}

* ``fleet/bundles/<name>/<v>.tar.zst`` — the tarball. Its root holds the spec's
  members (for ``jobs``: ``jobs/`` and ``connectors/smokeball/``).

* ``fleet/bundles/<name>/layout-<n>/latest.json`` — the newest published bundle
  of layout ``n``, in the pointer's shape. CI rewrites it on every publish.

THE LAYOUT RULE (ADR 0103): a seat never runs an image with a bundle of another
layout. A bundle's venvs carry absolute paths, so a bundle runs only at the
mount paths it was built for, and the layout number names those paths: layout 1
is the ``medchron/`` root at ``/opt/medchron`` (every manifest published before
the key existed), layout 2 the ``jobs/`` root at ``/opt/jobs``. The image's
:class:`BundleSpec` says which layout it mounts; when the seat pointer names a
bundle of another layout, the installer adopts ``layout-<n>/latest.json`` for
the image's own layout instead (installer.decide). Without this an image and a
bundle could never change layout together: the release tick rolls the image
first and holds the bundle until the image is in place, and a new image on an
old-layout pointer boots with no runner, fails its self-test and is rolled back.

The pointer parser is :func:`skills_applier.manifest.parse_pointer` with the
bundle prefix: one parser, one set of refusal rules, for every artifact a seat
adopts from a pointer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared import seat_layout
from skills_applier.manifest import (
    ManifestError,
    Pointer,
    _load_object,
    parse_pointer,
    valid_version,
)

POINTER_KEY_TEMPLATE = "vaults/{slug}/bundles/{name}-pointer.json"
PREFIX_TEMPLATE = "fleet/bundles/{name}/"
LAYOUT_LATEST_TEMPLATE = "fleet/bundles/{name}/layout-{layout}/latest.json"
#: What a manifest without a ``layout`` key is: every bundle published before
#: the key existed had the ``medchron/`` root.
UNKEYED_LAYOUT = 1

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
    same script the image build and the CI bundle build run. ``layout`` is the
    tarball layout these mounts expect (the layout rule, module docstring); CI
    writes the same number into every manifest (scripts/ci-publish-bundle.sh
    ``LAYOUT``, pinned equal by operator/bin/tests/test_ci_publish_bundle.py).
    """

    name: str
    mounts: tuple[tuple[str, str], ...]
    required: tuple[str, ...]
    import_check: str
    layout: int = 2

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(member for member, _target in self.mounts)


JOBS = BundleSpec(
    name="jobs",
    mounts=(
        ("jobs", seat_layout.JOBS_BUNDLE),
        ("connectors/smokeball", "/opt/connectors/smokeball"),
    ),
    required=(
        "jobs/.venv/bin/python",
        "jobs/.venv/bin/jobs",
        "jobs/anthropic_pricing.json",
        "connectors/smokeball/.venv/bin/python",
        "connectors/smokeball/.venv/bin/smokeball-mcp",
    ),
    import_check="jobs/.venv/bin/python",
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
    layout: int = UNKEYED_LAYOUT


def pointer_key(slug: str, name: str) -> str:
    if not isinstance(slug, str) or not slug.strip() or "/" in slug:
        raise ManifestError(["customer slug is required and must be one path segment"])
    return POINTER_KEY_TEMPLATE.format(slug=slug.strip(), name=name)


def prefix(name: str) -> str:
    return PREFIX_TEMPLATE.format(name=name)


def tarball_key(name: str, version: str) -> str:
    return f"{prefix(name)}{version}.tar.zst"


def layout_latest_key(name: str, layout: int) -> str:
    return LAYOUT_LATEST_TEMPLATE.format(name=name, layout=layout)


def _layout(doc: dict, reasons: list[str]) -> int:
    raw = doc.get("layout", UNKEYED_LAYOUT)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        reasons.append(f"manifest.layout {raw!r} must be a positive integer")
        return UNKEYED_LAYOUT
    return raw


def manifest_layout(data: bytes, name: str) -> int:
    """The layout named by manifest bytes (an installed version dir's verified copy)."""
    doc = _load_object(data, f"{name} bundle manifest", MAX_MANIFEST_BYTES)
    reasons: list[str] = []
    layout = _layout(doc, reasons)
    if reasons:
        raise ManifestError(reasons)
    return layout


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
    layout = _layout(doc, reasons)
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
        layout=layout,
    )


__all__ = [
    "JOBS",
    "MAX_MANIFEST_BYTES",
    "MAX_TARBALL_BYTES",
    "SPECS",
    "UNKEYED_LAYOUT",
    "BundleManifest",
    "BundleSpec",
    "layout_latest_key",
    "manifest_layout",
    "parse_bundle_manifest",
    "parse_bundle_pointer",
    "pointer_key",
    "prefix",
    "tarball_key",
]
