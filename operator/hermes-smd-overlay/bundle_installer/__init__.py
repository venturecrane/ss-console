"""Root boot installer for capability bundles (ADR 0100 W3).

A bundle is the runtime of one capability, published on its own so a change to
it ships only to the seats that author that capability. v1 has one bundle,
``jobs``: the job runner's venv at ``/opt/medchron`` and the Smokeball
connector's venv at ``/opt/connectors/smokeball``, built inside the core seat
image at those exact paths.

* :mod:`bundle_installer.manifest` — the bundle pointer/manifest contract and the
  per-bundle spec (members, mount targets, checks).
* :mod:`bundle_installer.installer` — root: verify, extract root-owned into
  ``/opt/data/.smd-root/bundles/<name>/<version>/``, import-check, flip
  ``current``, retention, and the tree check that gates every mount.
* :mod:`bundle_installer.__main__` — the boot CLI ``entrypoint-bundles.sh`` runs.
  It prints the version dir to mount, or nothing (the image copy runs).

BOOT ONLY. There is no poll loop: a new bundle reaches a seat by the release
controller writing its pointer and restarting it (busy-gated). The pointer,
manifest and verification code are :mod:`skills_applier`'s, reused by import.
"""

from bundle_installer.installer import Decision, decide
from bundle_installer.manifest import JOBS, SPECS, BundleManifest, BundleSpec

__all__ = ["JOBS", "SPECS", "BundleManifest", "BundleSpec", "Decision", "decide"]
