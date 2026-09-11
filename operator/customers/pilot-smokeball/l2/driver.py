#!/usr/bin/env python3
"""L2 scenario driver for the pilot-smokeball rehearsal office (TEST-PLAN §4).

Fires real triggers at the staging seat and reads back what landed, so every
L2 chain run is driven and verified through channels INDEPENDENT of the seat:

  - Smokeball actions run on App 1 (the client_credentials seeding app) —
    the same contract as ``../seed/seed_staging.py``, whose Api client this
    imports. A matter PATCH fires a real ``matter.updated`` webhook at the
    seat; task/document writes stage scenario inputs.
  - Email actions run on the shared AgentMail account: a dedicated
    simulation sender inbox (``sim-opposing-counsel@agentmail.to``) sends
    REAL email (Svix-signed ``message.received`` webhook, real spine
    routing) into the seat's inbox. Attachments are base64 per the
    AgentMail send contract.
  - Read-backs (``read-matter``, ``read-mail``) dump the artifacts a chain
    is expected to produce — tasks, events, memos, files, drafts — for
    grading per ``operator/grading/rubric.md`` without consuming seat turns.

Person-invoked skills are driven through the console MCP door
(``ask_operator``), not this driver. Runs are recorded per TEST-PLAN §7 in
``operator/grading/runs/l2-pilot-smokeball/``.

Usage (env injected by Infisical, never echoed):

    cd operator/customers/pilot-smokeball/l2
    infisical run --env=prod --path=/ss -- python3 driver.py <command> [...]

Commands:
    patch-matter   --matter <seed-key> --note <text>
    create-task    --matter <seed-key> --subject <s> [--due YYYY-MM-DD]
    upload-doc     --matter <seed-key> --name <file.pdf> --lines <l1> <l2> ...
    ensure-inbox   [--username sim-opposing-counsel] [--display <name>]
    send-email     --subject <s> --text <body> [--from-inbox <username>]
                   [--to <addr>] [--attach-name <file.pdf> --attach-lines ...]
    read-matter    --matter <seed-key> [--sections tasks,events,memos,files]
    read-mail      [--inbox pilot-smokeball] [--folder drafts|messages]
    read-doc-sha   --matter <seed-key> --file <fileId>

``read-doc-sha`` is the independent-channel half of the ss#2247 staging proof:
it downloads a matter document on App 1 and prints the sha256 of the text that
document yields, so a broker-recorded staging hash can be compared against a
hash produced by a different credential in a different process. The extractor
is deliberately the SAME one the connector runs
(``smokeball_connector.extract.extract_text``): the claim under test is "the
bytes the broker hashed are the bytes the document yields", not "the extractor
is right", and a second extractor would produce a different-but-equally-valid
text and make the comparison meaningless. The channel is what is independent.

Required env: SMOKEBALL_SEED_CLIENT_ID / SMOKEBALL_SEED_CLIENT_SECRET /
SMOKEBALL_STAGING_API_KEY (App 1) and AGENTMAIL_API_KEY (email commands).
Pure stdlib on purpose: runs anywhere without the operator venv.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "seed"))
from seed_staging import Api, load_manifest, text_pdf

AGENTMAIL_HOST = "https://api.agentmail.to"
SEAT_INBOX = "pilot-smokeball@agentmail.to"
SIM_INBOX_USERNAME = "sim-opposing-counsel"


def _matter_id(key: str) -> str:
    manifest = load_manifest()
    matter_id = manifest.get("matters", {}).get(key)
    if not matter_id:
        sys.exit(f"unknown matter key {key!r}; seeded: {sorted(manifest.get('matters', {}))}")
    return matter_id


# ---- AgentMail (shared SMD account; per-inbox addressing) -------------------


def _agentmail(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    key = os.environ.get("AGENTMAIL_API_KEY")
    if not key:
        sys.exit("missing AGENTMAIL_API_KEY (run under infisical)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{AGENTMAIL_HOST}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme+host are the module-constant AGENTMAIL_HOST (https://); paths are module-authored literals plus url-encoded inbox addresses.
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


def _inbox_path(inbox: str) -> str:
    address = inbox if "@" in inbox else f"{inbox}@agentmail.to"
    return urllib.parse.quote(address, safe="")


# ---- commands ---------------------------------------------------------------


def cmd_patch_matter(args: argparse.Namespace) -> None:
    api = Api()
    matter_id = _matter_id(args.matter)
    code, matter = api.call("GET", f"/matters/{matter_id}")
    if code != 200:
        sys.exit(f"GET matter {code}: {matter}")
    base = (matter.get("description") or "").split(" [l2-trigger")[0]
    code, resp = api.call(
        "PATCH", f"/matters/{matter_id}", {"description": f"{base} [l2-trigger: {args.note}]"}
    )
    print(json.dumps({"command": "patch-matter", "matter": args.matter, "status": code, "resp": resp}))


def cmd_create_task(args: argparse.Namespace) -> None:
    api = Api()
    body: dict = {"matterId": _matter_id(args.matter), "subject": args.subject}
    if args.due:
        body["dueDateOnly"] = args.due
    code, resp = api.call("POST", "/tasks", body)
    print(json.dumps({"command": "create-task", "matter": args.matter, "status": code, "resp": resp}))


def cmd_upload_doc(args: argparse.Namespace) -> None:
    api = Api()
    file_id = api.upload_document(_matter_id(args.matter), args.name, text_pdf(args.lines))
    print(json.dumps({"command": "upload-doc", "matter": args.matter, "file_id": file_id, "name": args.name}))


def cmd_ensure_inbox(args: argparse.Namespace) -> None:
    code, inboxes = _agentmail("GET", "/v0/inboxes")
    listing = inboxes.get("inboxes", inboxes) if isinstance(inboxes, dict) else inboxes
    target = f"{args.username}@agentmail.to"
    if isinstance(listing, list) and any(
        (i.get("inbox_id") or i.get("address") or "").lower() == target for i in listing if isinstance(i, dict)
    ):
        print(json.dumps({"command": "ensure-inbox", "inbox": target, "status": "exists"}))
        return
    code, resp = _agentmail(
        "POST", "/v0/inboxes", {"username": args.username, "display_name": args.display}
    )
    print(json.dumps({"command": "ensure-inbox", "inbox": target, "status": code, "resp": resp}))


def cmd_send_email(args: argparse.Namespace) -> None:
    body: dict = {"to": [args.to], "subject": args.subject, "text": args.text}
    if args.attach_name:
        if not args.attach_lines:
            sys.exit("--attach-name requires --attach-lines")
        body["attachments"] = [
            {
                "filename": args.attach_name,
                "content": base64.b64encode(text_pdf(args.attach_lines)).decode(),
                "content_type": "application/pdf",
            }
        ]
    code, resp = _agentmail(
        "POST", f"/v0/inboxes/{_inbox_path(args.from_inbox)}/messages/send", body
    )
    print(json.dumps({"command": "send-email", "from": args.from_inbox, "to": args.to, "status": code, "resp": resp}))


def cmd_read_matter(args: argparse.Namespace) -> None:
    api = Api()
    matter_id = _matter_id(args.matter)
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    paths = {
        "tasks": f"/tasks?matterId={matter_id}&Limit=100",
        "events": f"/events?matterId={matter_id}&Limit=100",
        "memos": f"/matters/{matter_id}/memos?Limit=100",
        "files": f"/matters/{matter_id}/documents/files?Limit=100",
    }
    out: dict = {"matter": args.matter, "matter_id": matter_id}
    for section in sections:
        if section not in paths:
            sys.exit(f"unknown section {section!r}; valid: {sorted(paths)}")
        code, resp = api.call("GET", paths[section])
        out[section] = {"status": code, "body": resp}
    print(json.dumps(out, indent=1))


def cmd_read_doc_sha(args: argparse.Namespace) -> None:
    # Imported here, not at module scope: extract_text pulls pypdf/python-docx
    # only on the PDF/DOCX branches, and every OTHER command in this driver is
    # pure stdlib on purpose. A missing optional dep must fail this command, not
    # the whole file.
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "connectors", "smokeball"
        ),
    )
    from smokeball_connector.extract import UnsupportedDocumentError, extract_text

    api = Api()
    matter_id = _matter_id(args.matter)
    code, info = api.call("GET", f"/matters/{matter_id}/documents/files/{args.file}/download")
    if code != 200 or not isinstance(info, dict) or not info.get("downloadUrl"):
        sys.exit(f"download {code}: no downloadUrl for file {args.file!r} on matter {matter_id!r}")

    # Presigned S3 GET: NO auth headers (same presign rule as the upload leg).
    # The whole body is read in one pass, which IS reading the document to
    # completion: read_document's paging is over the EXTRACTED text, and the
    # extraction here runs over the full blob, so there is no tail to miss.
    req = urllib.request.Request(info["downloadUrl"])
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — the URL is a Smokeball-minted presigned download URL returned by the API call above, never caller input.
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()

    try:
        text = extract_text(
            blob,
            file_name=str(info.get("name") or ""),
            file_extension=str(info.get("fileExtension") or ""),
        )
    except UnsupportedDocumentError as exc:
        sys.exit(f"extract failed for file {args.file!r}: {exc}")

    encoded = text.encode("utf-8")
    print(
        json.dumps(
            {
                "command": "read-doc-sha",
                "matter": args.matter,
                "matter_id": matter_id,
                "file_id": args.file,
                "name": info.get("name"),
                "file_extension": info.get("fileExtension"),
                "blob_bytes": len(blob),
                "total_chars": len(text),
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            },
            indent=1,
        )
    )


def cmd_read_mail(args: argparse.Namespace) -> None:
    code, resp = _agentmail("GET", f"/v0/inboxes/{_inbox_path(args.inbox)}/{args.folder}")
    print(json.dumps({"inbox": args.inbox, "folder": args.folder, "status": code, "body": resp}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("patch-matter")
    p.add_argument("--matter", required=True)
    p.add_argument("--note", required=True)
    p.set_defaults(func=cmd_patch_matter)

    p = sub.add_parser("create-task")
    p.add_argument("--matter", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--due")
    p.set_defaults(func=cmd_create_task)

    p = sub.add_parser("upload-doc")
    p.add_argument("--matter", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--lines", nargs="+", required=True)
    p.set_defaults(func=cmd_upload_doc)

    p = sub.add_parser("ensure-inbox")
    p.add_argument("--username", default=SIM_INBOX_USERNAME)
    # AgentMail display_name validation rejects punctuation like parentheses
    # and ampersands; keep it plain words.
    p.add_argument("--display", default="Halloran Sload LLP Simulation")
    p.set_defaults(func=cmd_ensure_inbox)

    p = sub.add_parser("send-email")
    p.add_argument("--from-inbox", default=SIM_INBOX_USERNAME)
    p.add_argument("--to", default=SEAT_INBOX)
    p.add_argument("--subject", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--attach-name")
    p.add_argument("--attach-lines", nargs="+")
    p.set_defaults(func=cmd_send_email)

    p = sub.add_parser("read-matter")
    p.add_argument("--matter", required=True)
    p.add_argument("--sections", default="tasks,events,memos,files")
    p.set_defaults(func=cmd_read_matter)

    p = sub.add_parser("read-doc-sha")
    p.add_argument("--matter", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_read_doc_sha)

    p = sub.add_parser("read-mail")
    p.add_argument("--inbox", default="pilot-smokeball")
    p.add_argument("--folder", default="drafts", choices=["drafts", "messages"])
    p.set_defaults(func=cmd_read_mail)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
