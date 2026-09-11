"""Is the audit-archive prefix REALLY immutable? (ss#2500 / ss#2516)

Split out of ``audit-chain-watch.py`` at the module-size ceiling. A clean seam:
this file talks to the Cloudflare R2 bucket-lock API and knows nothing about
chains, seats or alert rows.

The claim it checks is client-facing -- the firm is told its audit record is
copied off the Machine and held for seven years -- so it reads the REAL lock
rules from the API rather than trusting that a lifecycle rule was once applied,
and every way of failing to prove it (an API error, no rules at all, a rule
scoped below the prefix, a retention shorter than the commitment, a date already
past, a disabled rule) reports UNPROVEN rather than fine.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

#: The prefix every archived export is written under (see ``archive_export``).
ARCHIVE_PREFIX = "audit/"
#: Seven years, the retention the service agreement commits the record to.
LOCK_MIN_SECONDS = 2555 * 86400
#: Account ids are 32 hex characters and bucket names are lowercase DNS labels.
#: Nothing else is ever interpolated into an api.cloudflare.com URL.
_URL_SEGMENT = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9-]{1,62}\Z")
LockFetcher = Callable[[str], dict]


def bucket_lock_url(account: str, bucket: str) -> str:
    """The bucket-lock endpoint, built only from values that pass the charset gate."""
    for label, value in (("CLOUDFLARE_ACCOUNT_ID", account), ("bucket name", bucket)):
        if not _URL_SEGMENT.match(value or ""):
            raise RuntimeError(f"refusing to build a Cloudflare API URL from an unsafe {label}")
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/r2/buckets/{bucket}/lock"


def _cf_get_lock(url: str) -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN is unset, so the bucket lock cannot be read")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")  # noqa: S310 - bucket_lock_url is the only builder: https constant host, charset-gated segments
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — bucket_lock_url is the only builder and it gates both interpolated segments on a fixed charset.
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — https, charset-gated
        return json.loads(resp.read().decode("utf-8"))


LockFetcher = Callable[[str], dict]


def probe_bucket_lock(bucket: str, *, fetch: LockFetcher = _cf_get_lock) -> tuple[bool, str]:
    """Is the archive prefix actually immutable?

    An off-box copy on a bucket anyone can delete from is a backup, not a
    compliance record, and calling this control done without asking would be the
    built-but-not-wired shape exactly. So it is asked every run, and an
    unconfirmed lock is a HOLD.

    ASKED OF THE RIGHT API. The first live run asked S3
    ``get-object-lock-configuration`` and got
    ``ObjectLockConfigurationNotFoundError`` even with derived credentials and
    the R2 endpoint: R2's bucket-lock feature is NOT surfaced through the S3
    object-lock API. It is its own Cloudflare API resource, and this is the
    shape it answered with on 2026-08-21::

        {"success": true, "result": {"rules": [
          {"id": "audit-7y", "enabled": true, "prefix": "audit/",
           "condition": {"type": "Age", "maxAgeSeconds": 220752000}}]}}

    Confirmed means at least one rule that is enabled, whose prefix COVERS the
    archive prefix (``audit/`` starts with it, so a narrower per-seat rule does
    not count), and whose condition holds the record for at least seven years.
    ``Age`` is the observed condition shape; ``Indefinite`` carries no fields;
    ``Date``'s field name is documented nowhere we could reach, so an
    unrecognized ``Date`` payload fails CLOSED with the raw condition in the
    message rather than being read charitably.
    """
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    try:
        payload = fetch(bucket_lock_url(account, bucket))
    except Exception as exc:  # noqa: BLE001 -- any transport or auth failure is a hold
        return False, _lock_unproven(bucket, f"{type(exc).__name__}: {exc}")
    return evaluate_lock_payload(bucket, payload)


def evaluate_lock_payload(bucket: str, payload: Any) -> tuple[bool, str]:
    """Turn one bucket-lock API body into a verdict. Pure -- this is what tests drive."""
    if not isinstance(payload, dict):
        return False, _lock_unproven(bucket, "the API returned something that is not a JSON object")
    if payload.get("success") is not True:
        errors = json.dumps(payload.get("errors"), sort_keys=True)
        return False, _lock_unproven(bucket, f"the API answered success=false, errors={errors}")

    rules = (payload.get("result") or {}).get("rules")
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        return False, _lock_unproven(bucket, "the API returned a non-list rules field")
    if not rules:
        return False, _lock_unproven(bucket, "the bucket has no lock rules at all")

    rejected: list[str] = []
    for rule in rules:
        covers, why = _rule_covers_archive(rule)
        if covers:
            rule_id = rule.get("id") if isinstance(rule, dict) else None
            return True, (f"Bucket lock rule {rule_id!r} covers {ARCHIVE_PREFIX} on {bucket}: {why}.")
        rejected.append(why)
    return False, _lock_unproven(bucket, "; ".join(rejected))


def _rule_covers_archive(rule: Any) -> tuple[bool, str]:
    """Does this one rule hold every archived object for the full retention?"""
    if not isinstance(rule, dict):
        return False, "a rule that is not an object"
    rule_id = rule.get("id")
    if rule.get("enabled") is not True:
        return False, f"rule {rule_id!r} is not enabled"

    prefix = rule.get("prefix") or ""
    if not isinstance(prefix, str) or not ARCHIVE_PREFIX.startswith(prefix):
        return (
            False,
            f"rule {rule_id!r} covers {prefix!r}, which does not cover {ARCHIVE_PREFIX!r}",
        )

    condition = rule.get("condition")
    if not isinstance(condition, dict):
        return False, f"rule {rule_id!r} has no condition object"
    kind = condition.get("type")

    if kind == "Indefinite":
        return True, "retained indefinitely"

    if kind == "Age":
        seconds = condition.get("maxAgeSeconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return False, f"rule {rule_id!r} has a non-integer maxAgeSeconds"
        if seconds < LOCK_MIN_SECONDS:
            return False, (
                f"rule {rule_id!r} retains for {seconds}s, short of the {LOCK_MIN_SECONDS}s the record is committed to"
            )
        return True, f"retained for {seconds}s"

    if kind == "Date":
        raw = condition.get("date")
        parsed = _parse_lock_date(raw)
        if parsed is None:
            return False, (
                f"rule {rule_id!r} has a Date condition this probe cannot read "
                f"({json.dumps(condition, sort_keys=True, default=str)})"
            )
        if parsed <= datetime.now(timezone.utc):
            return False, f"rule {rule_id!r} retains only until {raw}, which has passed"
        return True, f"retained until {raw}"

    return False, f"rule {rule_id!r} has an unrecognized condition type {kind!r}"


def _parse_lock_date(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _lock_unproven(bucket: str, detail: str) -> str:
    return (
        f"Could not confirm a bucket lock covering {ARCHIVE_PREFIX} on {bucket} ({detail}). "
        "The copy is being written to a prefix whose immutability is unproven, which makes it "
        "a backup and not a compliance record."
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
