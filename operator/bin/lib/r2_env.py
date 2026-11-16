"""The environment the aws CLI needs to reach Cloudflare R2, built in one place.

R2 signs requests in region "auto". A GitHub runner that sets any other AWS
region (us-west-2) makes every R2 call fail, as a 400 or as InvalidRegionName.
It happened twice before this had one home: release-tick on 2026-10-31, and
the nightly audit-chain watch on 2026-11-16, whose off-box copy of every
seat's audit record failed for a whole night. Every Python caller of the aws
CLI against R2 builds its environment here; the shell callers have the same
rule in scripts/lib/r2-creds.sh.
"""

from __future__ import annotations

from collections.abc import Mapping

R2_REGION = "auto"


def aws_env(key_id: str, secret: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``base`` (often os.environ) plus R2 credentials, with the region pinned to R2's."""
    out = dict(base or {})
    out.update(
        AWS_ACCESS_KEY_ID=key_id,
        AWS_SECRET_ACCESS_KEY=secret,
        AWS_REGION=R2_REGION,
        AWS_DEFAULT_REGION=R2_REGION,
    )
    return out
