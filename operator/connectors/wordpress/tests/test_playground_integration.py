"""The wordpress connector against a REAL local WordPress (manual; skipped in CI).

Everything in test_tools.py runs against a fake. This runs the same tools
against WordPress itself, which is the only way to prove the parts WordPress
enforces: that a Contributor's Application Password authenticates over the REST
API, that a pending post reads back pending in wp-admin's terms, that the
Contributor role cannot publish even by a direct REST call, and what WordPress
answers when a Contributor opens a post it may not edit.

How to run (no Docker needed; WordPress Playground runs PHP in WebAssembly):

    cd operator/connectors/wordpress/tests/playground
    npx -y @wp-playground/cli@3.1.57 server --port=9400 --blueprint=blueprint.json
    # in another shell, with the connector's venv:
    WP_PLAYGROUND_URL=http://127.0.0.1:9400 python -m pytest tests/test_playground_integration.py -v

The blueprint makes two Contributors (``operator``; ``uploader``, which is also
granted ``upload_files``), mints each an Application Password, adds a published
and a pending post by the admin, and writes the ids and passwords to
``/wp-content/proof-creds.json`` on the local site only. The advertising gate is
a stub with the checker's CLI contract; the real checker has its own tests.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

import httpx
import pytest

SITE = os.environ.get("WP_PLAYGROUND_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not SITE, reason="set WP_PLAYGROUND_URL to a local WordPress Playground (see module doc)"
)


@pytest.fixture(scope="module")
def creds() -> dict:
    return httpx.get(f"{SITE}/wp-content/proof-creds.json").raise_for_status().json()


@pytest.fixture(autouse=True)
def stub_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = tmp_path / "gate_stub.py"
    gate.write_text(
        "import json\n"
        "print(json.dumps({'verdict': 'pass', 'findings': [{'rule': 'R-40', 'severity': 'FLAG', "
        "'span': 'best', 'message': 'stub flag'}]}))\n"
    )
    monkeypatch.setenv("SMD_ADVERTISING_GATE", str(gate))
    monkeypatch.setenv("SMD_ADVERTISING_GATE_PYTHON", sys.executable)
    monkeypatch.setenv("SMD_MARKETING_REGISTERS", "/dev/null")
    monkeypatch.setenv("WORDPRESS_UPLOAD_ROOTS", str(tmp_path))


def _as(login: str, creds: dict, monkeypatch: pytest.MonkeyPatch):
    from wordpress_connector import server
    from wordpress_connector.client import WordPressClient

    monkeypatch.setattr(
        server, "_client", WordPressClient(site_url=SITE, username=login, app_password=creds[login]["password"])
    )
    return server


def _png(path: Path) -> None:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_contributor_creates_pending_and_cannot_publish(creds: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _as("operator", creds, monkeypatch)
    created = s.create_pending_post(title="Proof post", content_html="<p>Body.</p>", excerpt="x")
    assert created["status"] == "pending"
    assert created["gate"]["flags"][0]["rule"] == "R-40"
    pid = created["id"]
    assert s.get_post(id=pid)["status"] == "pending"
    assert pid in [p["id"] for p in s.list_posts(status="pending")["posts"]]

    raw = httpx.Client(auth=httpx.BasicAuth("operator", creds["operator"]["password"]))
    r = raw.post(f"{SITE}/wp-json/wp/v2/posts/{pid}", json={"status": "publish"})
    assert (r.status_code, r.json()["code"]) == (403, "rest_cannot_publish")
    r = raw.post(f"{SITE}/wp-json/wp/v2/posts", json={"title": "t", "content": "c", "status": "publish"})
    assert (r.status_code, r.json()["code"]) == (403, "rest_cannot_publish")
    assert s.get_post(id=pid)["status"] == "pending"

    assert s.update_pending_post(id=pid, content_html="<p>Revised.</p>")["status"] == "pending"


def test_update_refuses_published_and_others_posts(creds: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _as("operator", creds, monkeypatch)
    for pid in (creds["published_post"], creds["admin_pending_post"]):
        out = s.update_pending_post(id=pid, title="hijack")
        assert out["status"] == "refused", out


def test_media_upload_needs_upload_files_and_sets_alt_text(
    creds: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img = tmp_path / "office.png"
    _png(img)
    s = _as("operator", creds, monkeypatch)
    with pytest.raises(Exception, match="upload_files"):
        s.upload_media(file_path=str(img), alt_text="Office entrance", caption="Our office", title="Office")
    s = _as("uploader", creds, monkeypatch)
    up = s.upload_media(file_path=str(img), alt_text="Office entrance", caption="Our office", title="Office")
    assert (up["alt_text"], up["caption"], up["mime_type"]) == ("Office entrance", "Our office", "image/png")
    post = s.create_pending_post(title="With image", content_html="<p>Body.</p>", featured_media=up["id"])
    assert s.get_post(id=post["id"])["featured_media"] == up["id"]
