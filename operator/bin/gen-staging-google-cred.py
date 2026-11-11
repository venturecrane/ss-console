#!/usr/bin/env python3
"""Generate an ISOLATED, throwaway Google service-account JSON for the staging
Operator (the release twin; hermes-ap-staging today).

It is a structurally-valid service account with a freshly generated RSA keypair,
so the Workspace broker boots IDENTICALLY to production (entrypoint.sh's
`materialize_credential` decodes + writes it; `from_service_account_info` can
construct a Credentials object from it). But it is wired to a fictitious
project/account with no domain-wide delegation to anything real, so it can
authenticate against NO Google resource. boot-smoke and the voice gate never
make a live Google call, so the staging broker never needs this to actually
work against Google — only to be a well-formed credential.

This is NEVER a real credential and is NEVER committed. The staging reprovision
wrapper base64-encodes stdout into the GOOGLE_SERVICE_ACCOUNT_JSON Fly secret on
the staging Machine only. A fresh key is generated on every provision, which is
fine — staging holds no persistent Google state.

Run via uv so cryptography is available without a repo dependency:
    uv run --quiet --with cryptography python3 operator/bin/gen-staging-google-cred.py
"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")
    cred = {
        "type": "service_account",
        "project_id": "smd-staging-isolated",
        "private_key_id": "0000000000000000000000000000000000000000",
        "private_key": pem,
        "client_email": "crane-staging@smd-staging-isolated.iam.gserviceaccount.com",
        "client_id": "000000000000000000000",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": (
            "https://www.googleapis.com/robot/v1/metadata/x509/"
            "crane-staging%40smd-staging-isolated.iam.gserviceaccount.com"
        ),
    }
    print(json.dumps(cred))


if __name__ == "__main__":
    main()
