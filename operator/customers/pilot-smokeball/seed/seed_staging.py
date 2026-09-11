#!/usr/bin/env python3
"""Hydrate the rehearsal office (Smokeball staging tenant) with the synthetic
matter set — IMPLEMENTATION-PLAN M1, data spec in TEST-PLAN §3.

Runs on App 1 (the original client_credentials staging app):

    cd operator/customers/pilot-smokeball/seed
    infisical run --env=prod --path=/ss -- python3 seed_staging.py

Required env (injected by infisical, never echoed):
    SMOKEBALL_SEED_CLIENT_ID / SMOKEBALL_SEED_CLIENT_SECRET  (App 1)
    SMOKEBALL_STAGING_API_KEY                                (account-scoped)

Idempotent via ``manifest.json`` written next to this script: every created
resource is recorded under a stable key and skipped on re-run. Delete a key
(or the file) to re-create. Seeding is test-infrastructure hydration on OUR
OWN tenant — distinct from standing gate (b), which governs delivery writes
on the client's account (Captain, 2026-07-04).

Pure stdlib on purpose: runs anywhere without the operator venv.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_HOST = "https://datastaging-auth.smokeball.com"
API_HOST = "https://stagingapi.smokeball.com"
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.json")

# ---------------------------------------------------------------- tiny PDF --


def text_pdf(lines: list[str]) -> bytes:
    """Render text lines to a minimal valid multi-page PDF (Helvetica 10pt)."""
    per_page = 54
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[""]]

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    objs: list[bytes] = []  # 1-indexed object bodies, in order
    n_pages = len(pages)
    # obj 1: catalog, obj 2: pages, obj 3: font; pages start at 4 (page, content)*
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page in pages:
        content = ["BT /F1 10 Tf 54 756 Td 13 TL"]
        for ln in page:
            content.append(f"({esc(ln)}) Tj T*")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1", "replace")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {4 + len(objs) - 3 + 1} 0 R >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


# ------------------------------------------------------------------- client --


class Api:
    def __init__(self) -> None:
        cid = os.environ.get("SMOKEBALL_SEED_CLIENT_ID")
        sec = os.environ.get("SMOKEBALL_SEED_CLIENT_SECRET")
        self.api_key = os.environ.get("SMOKEBALL_STAGING_API_KEY")
        if not (cid and sec and self.api_key):
            sys.exit("missing SMOKEBALL_SEED_CLIENT_ID/SECRET or SMOKEBALL_STAGING_API_KEY (run under infisical)")
        basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid}).encode()
        req = urllib.request.Request(
            f"{AUTH_HOST}/oauth2/token",
            data=body,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — URL is the module-constant AUTH_HOST token endpoint (https://); credentials come from Infisical-staged env, never from input.
        with urllib.request.urlopen(req, timeout=30) as r:
            self.token = json.load(r)["access_token"]

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API_HOST}{path}",
            data=data,
            method=method,
            headers={
                "x-api-key": self.api_key,
                "Authorization": f"Bearer {self.token}",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme+host are the module-constant API_HOST (https://); paths are module-authored literals plus Smokeball-issued resource ids.
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"raw": raw.decode("utf-8", "replace")[:300]}
            return e.code, parsed

    def create_async(self, path: str, body: dict, what: str) -> dict:
        """POST that answers 202 + Link; poll the href until the resource exists."""
        code, resp = self.call("POST", path, body)
        if code not in (200, 201, 202):
            raise RuntimeError(f"{what}: POST {path} -> {code}: {json.dumps(resp)[:300]}")
        if isinstance(resp, dict) and resp.get("id") and not resp.get("href"):
            return resp  # synchronous create
        href = resp.get("href") if isinstance(resp, dict) else None
        if not href:
            raise RuntimeError(f"{what}: no href in 202 response")
        poll_path = href[len(API_HOST) :] if href.startswith(API_HOST) else href
        for _ in range(20):
            time.sleep(2)
            code, resource = self.call("GET", poll_path)
            if code == 200 and isinstance(resource, dict) and resource.get("id"):
                return resource
        raise RuntimeError(f"{what}: tracking href never resolved: {poll_path}")

    def upload_document(self, matter_id: str, file_name: str, blob: bytes) -> str:
        code, resp = self.call("POST", f"/matters/{matter_id}/documents/files", {"fileName": file_name})
        if code not in (200, 202) or not isinstance(resp, dict):
            raise RuntimeError(f"add_file {file_name}: {code}: {json.dumps(resp)[:300]}")
        file_id, upload_url = resp.get("fileId"), resp.get("uploadUrl")
        if not upload_url:
            raise RuntimeError(f"add_file {file_name}: no uploadUrl")
        # Presigned S3 PUT: EMPTY Content-Type, NO auth headers (would break the
        # signature — the contract locked in connector tests/test_document_writes.py).
        u = urllib.parse.urlsplit(upload_url)
        # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected — deliberate: the presigned S3 PUT requires an EXACTLY empty header set (no Content-Type, no auth) or the signature breaks; http.client is the only stdlib client that sends no implicit headers. Host is the Smokeball-issued presigned URL; TLS verification is the stdlib default.
        conn = http.client.HTTPSConnection(u.netloc, timeout=60)
        conn.request("PUT", f"{u.path}?{u.query}" if u.query else u.path, body=blob, headers={})
        put = conn.getresponse()
        put.read()
        conn.close()
        if put.status not in (200, 201):
            raise RuntimeError(f"presigned PUT {file_name}: {put.status}")
        return file_id or "unknown"


# ----------------------------------------------------------------- manifest --


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {"created_at": None, "contacts": {}, "matters": {}, "documents": {}, "tasks": {}}


def save_manifest(m: dict) -> None:
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------- main --


def main() -> None:
    from seed_data import CONTACTS, MATTERS, build_documents, TASKS

    api = Api()
    manifest = load_manifest()
    manifest["created_at"] = manifest.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # staff id for tasks (Smokeball requires an owner)
    code, staff = api.call("GET", "/staff?limit=10")
    staff_items = (staff or {}).get("value") or (staff or {}).get("items") or []
    staff_id = next((s["id"] for s in staff_items if not s.get("isDeleted")), None)
    if not staff_id:
        sys.exit(f"no staff found (GET /staff -> {code}); tasks cannot be owned")
    print(f"staff owner: {staff_id}")

    # 1. contacts
    for key, spec in CONTACTS.items():
        if key in manifest["contacts"]:
            print(f"contact {key}: exists ({manifest['contacts'][key]})")
            continue
        resource = api.create_async("/contacts", {**spec, "externalSystemId": f"seed-{key}"}, f"contact {key}")
        manifest["contacts"][key] = resource["id"]
        save_manifest(manifest)
        print(f"contact {key}: created {resource['id']}")

    # 2. matters
    for key, spec in MATTERS.items():
        if key in manifest["matters"]:
            print(f"matter {key}: exists ({manifest['matters'][key]})")
            continue
        body = {
            "matterTypeId": spec["matter_type_id"],
            "clientIds": [manifest["contacts"][c] for c in spec["clients"]],
            "otherSideIds": [manifest["contacts"][c] for c in spec.get("other_side", [])],
            "status": "Open",
            "number": spec["number"],
            "description": spec["description"],
            "openedDate": spec["opened"],
        }
        resource = api.create_async("/matters", body, f"matter {key}")
        manifest["matters"][key] = resource["id"]
        save_manifest(manifest)
        print(f"matter {key}: created {resource['id']} (number {spec['number']})")

    # 3. documents
    for doc_key, (matter_key, file_name, lines) in build_documents().items():
        if doc_key in manifest["documents"]:
            print(f"document {doc_key}: exists ({manifest['documents'][doc_key]})")
            continue
        file_id = api.upload_document(manifest["matters"][matter_key], file_name, text_pdf(lines))
        manifest["documents"][doc_key] = file_id
        save_manifest(manifest)
        print(f"document {doc_key}: uploaded {file_name} ({file_id})")

    # 4. tasks
    for key, spec in TASKS.items():
        if key in manifest["tasks"]:
            print(f"task {key}: exists ({manifest['tasks'][key]})")
            continue
        code, resp = api.call(
            "POST",
            "/tasks",
            {
                "staffId": staff_id,
                "subject": spec["subject"],
                "matterId": manifest["matters"][spec["matter"]],
                "note": spec.get("note"),
                "dueDateOnly": spec.get("due"),
            },
        )
        if code not in (200, 201, 202):
            raise RuntimeError(f"task {key}: {code}: {json.dumps(resp)[:300]}")
        task_id = (resp or {}).get("id") or (resp or {}).get("href", "created")
        manifest["tasks"][key] = task_id
        save_manifest(manifest)
        print(f"task {key}: {task_id}")

    print(
        f"DONE: {len(manifest['contacts'])} contacts, {len(manifest['matters'])} matters, "
        f"{len(manifest['documents'])} documents, {len(manifest['tasks'])} tasks (manifest.json updated)"
    )


if __name__ == "__main__":
    main()
