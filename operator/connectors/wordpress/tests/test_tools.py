"""The wordpress tools against a fake WordPress (httpx.MockTransport) and a stub
advertising gate. No network, no real checker.

What is pinned here, each with the failure it guards against:

* every post write sends status=pending, and a response in any other status is
  an error, not a success;
* update_pending_post refuses a published post and another user's draft, and
  writes nothing when it refuses;
* a gate BLOCK, a gate usage error, an absent checker and an unreadable verdict
  each refuse the write with nothing sent to WordPress (fail closed);
* FLAG findings ride back with the created post;
* upload_media sets alt text and caption, refuses non-images and paths outside
  the upload roots.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import httpx
import pytest

from wordpress_connector import media, server
from wordpress_connector.client import WordPressClient, WordPressConfigError, normalize_site_url

ME = 7
OTHER = 3
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeWP:
    def __init__(self, *, force_status: str | None = None) -> None:
        self.posts: dict[int, dict[str, Any]] = {
            10: {"id": 10, "status": "publish", "author": ME, "title": "Old", "content": "<p>x</p>"},
            11: {"id": 11, "status": "pending", "author": OTHER, "title": "Theirs", "content": "<p>y</p>"},
            12: {"id": 12, "status": "draft", "author": ME, "title": "Mine", "content": "<p>z</p>"},
        }
        self.media: dict[int, dict[str, Any]] = {}
        self.writes: list[tuple[str, str, Any]] = []
        self.force_status = force_status
        self.next_id = 100

    def _post_out(self, p: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": p["id"],
            "status": p["status"],
            "author": p["author"],
            "link": f"https://firm.example/?p={p['id']}",
            "title": {"rendered": p["title"], "raw": p["title"]},
            "content": {"rendered": p["content"], "raw": p["content"]},
            "excerpt": {"rendered": p.get("excerpt", "")},
            "categories": p.get("categories", []),
            "tags": p.get("tags", []),
            "featured_media": p.get("featured_media", 0),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wp/v2")
        assert request.headers.get("authorization", "").startswith("Basic ")
        if request.method == "GET" and path == "/users/me":
            return httpx.Response(200, json={"id": ME, "name": "operator"})
        if request.method == "GET" and path.startswith("/posts/"):
            pid = int(path.rsplit("/", 1)[1])
            if pid == 99:
                # What a real WordPress answers a Contributor opening a post it
                # cannot edit (proven against a local WordPress).
                return httpx.Response(
                    403,
                    json={"code": "rest_forbidden_context", "message": "Sorry, you are not allowed to edit this post."},
                )
            return httpx.Response(200, json=self._post_out(self.posts[pid]))
        if request.method == "GET" and path == "/posts":
            return httpx.Response(200, json=[self._post_out(p) for p in self.posts.values()])
        if request.method == "POST" and path == "/posts":
            body = json.loads(request.content)
            self.writes.append(("POST", path, body))
            pid = self.next_id
            self.next_id += 1
            self.posts[pid] = {
                "id": pid,
                "author": ME,
                "title": body["title"],
                "content": body["content"],
                "status": self.force_status or body["status"],
            }
            return httpx.Response(201, json=self._post_out(self.posts[pid]))
        if request.method == "POST" and path.startswith("/posts/"):
            pid = int(path.rsplit("/", 1)[1])
            body = json.loads(request.content)
            self.writes.append(("POST", path, body))
            self.posts[pid].update({k: v for k, v in body.items() if k in ("title", "content", "status")})
            return httpx.Response(200, json=self._post_out(self.posts[pid]))
        if request.method == "POST" and path == "/media":
            self.writes.append(("POST", path, request.headers.get("content-disposition")))
            mid = self.next_id
            self.next_id += 1
            self.media[mid] = {"id": mid, "source_url": f"https://firm.example/m/{mid}.png", "alt_text": ""}
            return httpx.Response(201, json=self.media[mid])
        if request.method == "POST" and path.startswith("/media/"):
            mid = int(path.rsplit("/", 1)[1])
            body = json.loads(request.content)
            self.writes.append(("POST", path, body))
            m = self.media[mid]
            m.update(
                {
                    "alt_text": body["alt_text"],
                    "caption": {"rendered": body["caption"]},
                    "title": {"rendered": body["title"]},
                }
            )
            return httpx.Response(200, json=m)
        return httpx.Response(404, json={"code": "rest_no_route", "message": "no route"})


@pytest.fixture
def wp(monkeypatch: pytest.MonkeyPatch) -> FakeWP:
    fake = FakeWP()
    client = WordPressClient(site_url="https://firm.example", username="operator", app_password="a b c d")
    client._http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(server, "_client", client)
    return fake


def _stub_gate(tmp_path: Path, *, exit_code: int, payload: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "gate_stub.py"
    out = payload if isinstance(payload, str) else json.dumps(payload)
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            args = sys.argv[1:]
            assert args[args.index("--channel") + 1] == "blog"
            assert "--json" in args
            item = open(args[args.index("--item") + 1], encoding="utf-8").read()
            open({str(tmp_path / "seen.txt")!r}, "w", encoding="utf-8").write(item)
            sys.stdout.write({out!r})
            sys.exit({exit_code})
            """
        )
    )
    monkeypatch.setenv("SMD_ADVERTISING_GATE", str(script))
    monkeypatch.setenv("SMD_ADVERTISING_GATE_PYTHON", sys.executable)
    monkeypatch.setenv("SMD_MARKETING_REGISTERS", str(tmp_path / "registers.yaml"))
    return script


FLAG = {"rule": "R-40", "severity": "FLAG", "span": "best", "message": "Is this superlative supportable?"}
BLOCK = {"rule": "R-01", "severity": "BLOCK", "span": "we guarantee", "message": "No guarantees of outcome."}


# ---- create ---------------------------------------------------------------
def test_create_forces_pending_and_returns_flags(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": [FLAG]}, monkeypatch=monkeypatch)
    out = server.create_pending_post(
        title="Rear-end collisions in Riverside", content_html="<p>What to do <b>next</b>.</p>", excerpt="Steps."
    )
    assert out["status"] == "pending"
    assert out["edit_url"] == f"https://firm.example/wp-admin/post.php?post={out['id']}&action=edit"
    assert out["gate"]["flags"] == [FLAG]
    sent = wp.writes[-1][2]
    assert sent["status"] == "pending"
    seen = (tmp_path / "seen.txt").read_text()
    assert "Rear-end collisions in Riverside" in seen and "What to do next." in seen and "<p>" not in seen


def test_create_refuses_when_wordpress_reports_another_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeWP(force_status="publish")
    client = WordPressClient(site_url="https://firm.example", username="operator", app_password="x")
    client._http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(server, "_client", client)
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": []}, monkeypatch=monkeypatch)
    with pytest.raises(server.StatusInvariantError):
        server.create_pending_post(title="t", content_html="<p>b</p>")


@pytest.mark.parametrize(
    ("exit_code", "payload"),
    [
        (1, {"verdict": "block", "findings": [BLOCK]}),
        (2, ""),
        (0, "not json"),
        (0, {"verdict": "block", "findings": []}),
    ],
)
def test_gate_refusals_write_nothing(
    wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, payload: Any
) -> None:
    _stub_gate(tmp_path, exit_code=exit_code, payload=payload, monkeypatch=monkeypatch)
    out = server.create_pending_post(title="We guarantee results", content_html="<p>we guarantee</p>")
    assert out["status"] == "refused"
    assert wp.writes == []
    if exit_code == 1:
        assert out["findings"] == [BLOCK]


def test_absent_gate_fails_closed(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_ADVERTISING_GATE", str(tmp_path / "missing.py"))
    out = server.create_pending_post(title="t", content_html="<p>b</p>")
    assert out["status"] == "refused"
    assert "absent" in out["reason"]
    assert wp.writes == []


# ---- update ---------------------------------------------------------------
def test_update_refuses_published_post(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": []}, monkeypatch=monkeypatch)
    out = server.update_pending_post(id=10, title="New")
    assert out["status"] == "refused" and "'publish'" in out["reason"]
    assert wp.writes == []


def test_update_refuses_a_post_wordpress_will_not_open_for_edit(
    wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": []}, monkeypatch=monkeypatch)
    out = server.update_pending_post(id=99, title="New")
    assert out["status"] == "refused" and "not editable" in out["reason"]
    assert wp.writes == []


def test_update_refuses_another_users_post(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": []}, monkeypatch=monkeypatch)
    out = server.update_pending_post(id=11, title="New")
    assert out["status"] == "refused" and "another user" in out["reason"]
    assert wp.writes == []


def test_update_own_draft_goes_to_pending(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gate(tmp_path, exit_code=0, payload={"verdict": "pass", "findings": []}, monkeypatch=monkeypatch)
    out = server.update_pending_post(id=12, content_html="<p>revised</p>")
    assert out["status"] == "pending"
    assert wp.writes[-1][2] == {"status": "pending", "content": "<p>revised</p>"}


def test_update_gate_block_writes_nothing(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gate(tmp_path, exit_code=1, payload={"verdict": "block", "findings": [BLOCK]}, monkeypatch=monkeypatch)
    out = server.update_pending_post(id=12, content_html="<p>we guarantee</p>")
    assert out["status"] == "refused"
    assert wp.writes == []


# ---- media ----------------------------------------------------------------
def test_upload_media_sets_alt_text_and_caption(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORDPRESS_UPLOAD_ROOTS", str(tmp_path))
    img = tmp_path / "office.png"
    img.write_bytes(PNG)
    out = server.upload_media(
        file_path=str(img), alt_text="The firm's Riverside office entrance", caption="Our office", title="Office"
    )
    assert out["alt_text"] == "The firm's Riverside office entrance"
    assert out["caption"] == "Our office"
    assert 'filename="office.png"' in wp.writes[0][2]


def test_upload_media_refuses_non_image(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORDPRESS_UPLOAD_ROOTS", str(tmp_path))
    secret = tmp_path / "token.png"
    secret.write_text("ANTHROPIC_API_KEY=sk-ant-not-an-image")
    with pytest.raises(media.MediaRefused):
        server.upload_media(file_path=str(secret), alt_text="a", caption="", title="")
    assert wp.writes == []


def test_upload_media_refuses_outside_roots(wp: FakeWP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv("WORDPRESS_UPLOAD_ROOTS", str(root))
    outside = tmp_path / "x.png"
    outside.write_bytes(PNG)
    link = root / "link.png"
    link.symlink_to(outside)
    for p in (outside, link):
        with pytest.raises(media.MediaRefused):
            server.upload_media(file_path=str(p), alt_text="a", caption="", title="")
    assert wp.writes == []


def test_upload_media_requires_alt_text_and_one_source(wp: FakeWP) -> None:
    with pytest.raises(media.MediaRefused):
        server.upload_media(file_path="/tmp/a.png", url="https://x.example/a.png", alt_text="a", caption="", title="")
    with pytest.raises(media.MediaRefused):
        server.upload_media(file_path="/tmp/a.png", alt_text="  ", caption="", title="")


def test_url_fetch_refuses_http_and_private_hosts() -> None:
    with pytest.raises(media.MediaRefused):
        media.fetch_url("http://example.com/a.png")
    with pytest.raises(media.MediaRefused):
        media.fetch_url("https://127.0.0.1/a.png")


# ---- reads + transport ----------------------------------------------------
def test_list_posts_rejects_unknown_status(wp: FakeWP) -> None:
    with pytest.raises(ValueError):
        server.list_posts(status="trash")


def test_get_post_returns_text_and_edit_url(wp: FakeWP) -> None:
    out = server.get_post(id=12)
    assert out["content_text"] == "z"
    assert out["edit_url"].endswith("post=12&action=edit")


def test_site_url_must_be_https_except_loopback() -> None:
    assert normalize_site_url("https://www.firm.example/") == "https://www.firm.example"
    assert normalize_site_url("http://127.0.0.1:9400") == "http://127.0.0.1:9400"
    for bad in ("http://www.firm.example", "ftp://x", "firm.example", "https://firm.example/?a=1"):
        with pytest.raises(WordPressConfigError):
            normalize_site_url(bad)


def test_errors_never_carry_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "invalid_username", "message": "Unknown username."})

    client = WordPressClient(site_url="https://firm.example", username="operator", app_password="SECRETPASS")
    client._http = httpx.Client(transport=httpx.MockTransport(deny))
    monkeypatch.setattr(server, "_client", client)
    with pytest.raises(Exception) as info:
        server.list_categories()
    assert "SECRETPASS" not in str(info.value)
    assert "invalid_username" in str(info.value)


# ---- the REAL checker, when it is in the tree ------------------------------
_DRAFTING = Path(__file__).resolve().parents[3] / "templates" / "drafting"
_REAL_GATE = _DRAFTING / "advertising_gate_check.py"
_EXAMPLE_REGISTERS = _DRAFTING / "fixtures" / "marketing-registers.example.yaml"


@pytest.mark.skipif(not _REAL_GATE.is_file(), reason="advertising_gate_check.py not in this tree yet")
def test_real_checker_blocks_a_guarantee_and_writes_nothing(wp: FakeWP, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMD_ADVERTISING_GATE_PYTHON", raising=False)  # the venv interpreter, as on a seat
    monkeypatch.setenv("SMD_ADVERTISING_GATE", str(_REAL_GATE))
    monkeypatch.setenv("SMD_MARKETING_REGISTERS", str(_EXAMPLE_REGISTERS))
    out = server.create_pending_post(
        title="We guarantee you will win your case",
        content_html="<p>We guarantee a big settlement for every client.</p>",
    )
    assert out["status"] == "refused", out
    assert any(f["severity"] == "BLOCK" for f in out["findings"]), out
    assert wp.writes == []
