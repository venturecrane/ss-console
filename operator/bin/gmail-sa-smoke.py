#!/usr/bin/env python3
"""Gmail service-account + domain-wide-delegation smoke test (tracer bullet).

Proves the Google side in isolation: can a service-account key, via
domain-wide delegation, impersonate a Workspace user and read their Gmail —
BEFORE we wire anything into the customer Machine. This is the
"verify the substrate live before building on it" gate the onboarding retro
called for (see memory feedback_tracer_bullet_before_architecture).

Run locally with the SA key (no install needed via uv):

    uv run --with google-api-python-client --with google-auth \
        operator/bin/gmail-sa-smoke.py \
        --key /path/to/sa-key.json \
        --user smdurgan@smdurgan.com

Or pass the key JSON on stdin (so it never lands on disk):

    pbpaste | uv run --with google-api-python-client --with google-auth \
        operator/bin/gmail-sa-smoke.py --user smdurgan@smdurgan.com --key -

Prints subjects of up to N unread messages. PASS = delegation works and the
scope is authorized; FAIL prints the Google error verbatim (the usual causes
are: Gmail API not enabled, the SA's client_id not authorized for this scope
in the Workspace admin console, or the user is outside the delegated domain).

Scope requested: gmail.modify (read + archive + trash + draft; NO send — the
least-privilege set for the intake inbox, so Crane structurally cannot send
as the principal).
"""

import argparse
import json
import sys

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _load_key(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", required=True, help="path to SA JSON key, or - for stdin")
    ap.add_argument("--user", required=True, help="Workspace user to impersonate, e.g. smdurgan@smdurgan.com")
    ap.add_argument("--max", type=int, default=5, help="max messages to list")
    args = ap.parse_args()

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "FAIL: deps missing. Run via: uv run --with google-api-python-client --with google-auth ...",
            file=sys.stderr,
        )
        return 2

    try:
        info = _load_key(args.key)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: could not read SA key: {exc}", file=sys.stderr)
        return 2

    # Service-account credentials, delegated to impersonate the target user.
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES).with_subject(args.user)

    try:
        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        resp = gmail.users().messages().list(userId="me", q="is:unread", maxResults=args.max).execute()
        ids = [m["id"] for m in resp.get("messages", [])]
        print(f"PASS: delegation works. Impersonated {args.user}; {len(ids)} unread message(s):")
        for mid in ids:
            msg = (
                gmail.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["Subject", "From"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            print(f"  - {headers.get('From', '?')[:40]:40} | {headers.get('Subject', '(no subject)')[:70]}")
        return 0
    except Exception as exc:  # noqa: BLE001 — surface the raw Google error for diagnosis
        print(f"FAIL: Gmail call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
