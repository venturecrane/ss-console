"""The mcp:wordpress tool surface.

READ: ``list_posts``, ``get_post``, ``list_categories``, ``list_tags``,
``list_media``. INTERNAL_WRITE: ``upload_media``, ``create_pending_post``,
``update_pending_post``. There is NO publish tool and no delete tool.

The pending-only rule is enforced three times over:

1. No tool takes a ``status``. Every post write sends ``status: pending``,
   fixed here.
2. A response whose status is anything but ``pending`` is returned as an error
   (``StatusInvariantError``), so a site that published on create (an Editor
   credential plus a plugin that rewrites status, say) is reported, not hidden.
3. The firm issues a Contributor credential, so WordPress itself refuses
   publish (proven against a local WordPress; see the connector's tests).

``update_pending_post`` reads the post first and refuses unless it is
``pending`` or ``draft`` AND its author is the authenticated user, so the
Operator never edits a published post or a colleague's draft.

Every create/update runs the advertising gate first (``gate.run_gate``) over the
title, excerpt and body as text. A BLOCK, a usage error or an absent checker
refuses the write; FLAG findings come back with the created post so the drafter
can list them for the reviewer.

The client is built lazily, so the tool surface introspects without credentials.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from operator_connector_sdk.server import ConnectorServer

from . import gate, media
from .client import WordPressApiError, WordPressClient, build_client_from_env

server = ConnectorServer("wordpress")

_client: WordPressClient | None = None

EDITABLE_STATUSES = frozenset({"pending", "draft"})
LISTABLE_STATUSES = frozenset({"publish", "pending", "draft", "future", "private"})
_MAX_PER_PAGE = 50
_POST_FIELDS = "id,date,modified,status,link,title,excerpt,author,categories,tags,featured_media"


class StatusInvariantError(RuntimeError):
    """WordPress returned a post in a status this connector never writes."""


def _get_client() -> WordPressClient:
    global _client
    if _client is None:
        _client = build_client_from_env()
    return _client


# ---- text helpers -----------------------------------------------------------
class _TextExtractor(HTMLParser):
    _BLOCKS = frozenset({"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _rendered(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("rendered") or field.get("raw") or "")
    return str(field or "")


def _post_summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "date": p.get("date"),
        "modified": p.get("modified"),
        "status": p.get("status"),
        "link": p.get("link"),
        "title": html_to_text(_rendered(p.get("title"))),
        "excerpt": html_to_text(_rendered(p.get("excerpt"))),
        "author": p.get("author"),
        "categories": p.get("categories") or [],
        "tags": p.get("tags") or [],
        "featured_media": p.get("featured_media") or 0,
    }


def _per_page(n: int | None, default: int = 10) -> int:
    try:
        v = int(n) if n is not None else default
    except (TypeError, ValueError):
        v = default
    return max(1, min(v, _MAX_PER_PAGE))


def _int_list(values: list[int] | None, name: str) -> list[int] | None:
    if values is None:
        return None
    out: list[int] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"{name} must be a list of positive integer ids (from list_{name})")
        out.append(v)
    return out


# ---- reads ------------------------------------------------------------------
@server.tool()
def list_posts(
    status: str | None = None,
    search: str | None = None,
    per_page: int = 10,
    after: str | None = None,
) -> Any:
    """List the site's posts, newest first. ``status`` is one of publish (the
    default), pending, draft, future or private; WordPress returns only what the
    connector's user may see. ``search`` matches title and body words.
    ``after`` is an ISO-8601 date (``2026-10-01`` or ``2026-10-01T00:00:00``)
    to return only posts published after it. ``per_page`` is capped at 50.
    Returns ``{"posts": [{id, date, status, link, title, excerpt, author,
    categories, tags, featured_media}]}``; use ``get_post`` for a body."""
    st = (status or "publish").strip().lower()
    if st not in LISTABLE_STATUSES:
        raise ValueError(f"status must be one of {sorted(LISTABLE_STATUSES)}")
    if after and "T" not in after:
        after = f"{after}T00:00:00"
    params: dict[str, Any] = {
        "status": st,
        "search": (search or "").strip()[:200] or None,
        "per_page": _per_page(per_page),
        "after": after,
        "orderby": "date",
        "order": "desc",
        "_fields": _POST_FIELDS,
    }
    if st != "publish":
        params["context"] = "edit"
    data = _get_client().request("GET", "/posts", params=params)
    return {"posts": [_post_summary(p) for p in (data or []) if isinstance(p, dict)]}


@server.tool()
def get_post(id: int) -> Any:
    """Read one post by id: title, status, link, author, categories, tags,
    featured_media, excerpt, the body as HTML (``content_html``) and as plain
    text (``content_text``), and ``edit_url`` (the wp-admin edit screen)."""
    client = _get_client()
    try:
        p = client.request("GET", f"/posts/{int(id)}", params={"context": "edit"})
    except WordPressApiError as exc:
        if exc.status not in (401, 403):
            raise
        # Not editable by this user (someone else's post): the public view.
        p = client.request("GET", f"/posts/{int(id)}", params={"context": "view"})
    if not isinstance(p, dict):
        raise WordPressApiError("GET", f"/posts/{id}", 200, "no post object in response")
    out = _post_summary(p)
    content_html = _rendered(p.get("content"))
    out["content_html"] = content_html
    out["content_text"] = html_to_text(content_html)
    out["edit_url"] = client.edit_url(int(p.get("id") or id))
    return out


@server.tool()
def list_categories() -> Any:
    """Every category on the site: ``{"categories": [{id, name, slug, count,
    parent}]}``. Use the ids in create_pending_post's ``categories``."""
    data = _get_client().request(
        "GET", "/categories", params={"per_page": 100, "_fields": "id,name,slug,count,parent", "hide_empty": "false"}
    )
    return {
        "categories": [
            {
                "id": c.get("id"),
                "name": html.unescape(str(c.get("name", ""))),
                "slug": c.get("slug"),
                "count": c.get("count"),
                "parent": c.get("parent"),
            }
            for c in (data or [])
            if isinstance(c, dict)
        ]
    }


@server.tool()
def list_tags(search: str | None = None) -> Any:
    """The site's tags, most used first, optionally matching ``search``:
    ``{"tags": [{id, name, slug, count}]}``. Use the ids in
    create_pending_post's ``tags``."""
    data = _get_client().request(
        "GET",
        "/tags",
        params={
            "per_page": 100,
            "search": (search or "").strip()[:100] or None,
            "orderby": "count",
            "order": "desc",
            "_fields": "id,name,slug,count",
        },
    )
    return {
        "tags": [
            {
                "id": t.get("id"),
                "name": html.unescape(str(t.get("name", ""))),
                "slug": t.get("slug"),
                "count": t.get("count"),
            }
            for t in (data or [])
            if isinstance(t, dict)
        ]
    }


def _media_summary(m: dict[str, Any], client: WordPressClient) -> dict[str, Any]:
    return {
        "id": m.get("id"),
        "date": m.get("date"),
        "title": html_to_text(_rendered(m.get("title"))),
        "alt_text": m.get("alt_text", ""),
        "caption": html_to_text(_rendered(m.get("caption"))),
        "media_type": m.get("media_type"),
        "mime_type": m.get("mime_type"),
        "source_url": m.get("source_url"),
        "edit_url": client.edit_url(int(m["id"])) if m.get("id") else None,
    }


@server.tool()
def list_media(search: str | None = None, per_page: int = 20) -> Any:
    """The site's media library, newest first, optionally matching ``search``:
    ``{"media": [{id, date, title, alt_text, caption, media_type, mime_type,
    source_url, edit_url}]}``. Use an id as a post's ``featured_media``."""
    client = _get_client()
    data = client.request(
        "GET",
        "/media",
        params={
            "per_page": _per_page(per_page, 20),
            "search": (search or "").strip()[:100] or None,
            "_fields": "id,date,title,alt_text,caption,media_type,mime_type,source_url",
        },
    )
    return {"media": [_media_summary(m, client) for m in (data or []) if isinstance(m, dict)]}


# ---- writes -----------------------------------------------------------------
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str | None, ext: str) -> str:
    stem = _FILENAME_RE.sub("-", (filename or "image").strip()).strip("-.") or "image"
    stem = stem[:80]
    lower = stem.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        stem = stem.rsplit(".", 1)[0]
    return f"{stem}{ext}"


@server.tool()
def upload_media(
    alt_text: str,
    caption: str,
    title: str,
    file_path: str | None = None,
    url: str | None = None,
    filename: str | None = None,
) -> Any:
    """Upload ONE image to the site's media library and set its alt text,
    caption and title. Give exactly one of ``file_path`` (a file on this seat,
    inside the upload folders) or ``url`` (an https image URL). Only JPEG, PNG,
    GIF and WebP images are accepted, decided from the file's bytes. ``alt_text``
    is required: describe what the image shows for a reader who cannot see it.
    Returns ``{id, source_url, alt_text, caption, title, edit_url}``; pass the id
    as create_pending_post's ``featured_media``. Uploading does not publish
    anything, but the file becomes reachable at its URL."""
    if bool(file_path) == bool(url):
        raise media.MediaRefused("give exactly one of file_path or url")
    if not (alt_text or "").strip():
        raise media.MediaRefused("alt_text is required for every image")
    blob = media.read_local(file_path) if file_path else media.fetch_url(str(url))
    mime, ext = media.sniff_image(blob)
    name = _safe_filename(filename or (file_path or str(url)).rsplit("/", 1)[-1], ext)
    client = _get_client()
    try:
        created = client.request(
            "POST",
            "/media",
            content=blob,
            headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{name}"'},
        )
    except WordPressApiError as exc:
        if exc.status in (401, 403):
            raise WordPressApiError(
                "POST",
                "/media",
                exc.status,
                f"{exc.message} (the site refused the upload for this user: a Contributor cannot upload "
                "media unless the site grants it the upload_files capability)",
            ) from exc
        raise
    media_id = int(created["id"])
    updated = client.request(
        "POST",
        f"/media/{media_id}",
        json_body={"alt_text": alt_text.strip(), "caption": caption or "", "title": title or name},
    )
    return _media_summary(updated if isinstance(updated, dict) else created, client)


def _gate_text(title: str, excerpt: str | None, content_html: str) -> str:
    parts = [html_to_text(title)]
    if excerpt:
        parts.append(html_to_text(excerpt))
    parts.append(html_to_text(content_html))
    return "\n\n".join(p for p in parts if p) + "\n"


def _refusal(reason: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "findings": findings}


def _post_result(p: dict[str, Any], client: WordPressClient, result: gate.GateResult) -> dict[str, Any]:
    status = str(p.get("status", ""))
    if status != "pending":
        raise StatusInvariantError(
            f"WordPress returned post {p.get('id')} with status {status!r}; this connector writes only "
            "'pending'. Tell SMD: the site or credential is not the Contributor setup this connector expects."
        )
    return {
        "id": p.get("id"),
        "status": status,
        "title": html_to_text(_rendered(p.get("title"))),
        "link": p.get("link"),
        "edit_url": client.edit_url(int(p["id"])),
        "gate": {"verdict": "pass", "flags": result.flags},
    }


@server.tool()
def create_pending_post(
    title: str,
    content_html: str,
    excerpt: str | None = None,
    categories: list[int] | None = None,
    tags: list[int] | None = None,
    featured_media: int | None = None,
    channel: str = "blog",
) -> Any:
    """Create a blog post IN PENDING STATUS for a person at the firm to review
    and publish. This never publishes: the status is fixed to pending and cannot
    be changed here. The draft first passes the California attorney-advertising
    gate; a BLOCK refuses the write and returns ``{"status": "refused",
    "reason", "findings"}`` with nothing created. On success returns ``{id,
    status: "pending", title, link, edit_url, gate: {verdict, flags}}``; list
    every FLAG for the reviewer. ``categories`` / ``tags`` are ids from
    list_categories / list_tags; ``featured_media`` is an id from upload_media."""
    if not (title or "").strip() or not (content_html or "").strip():
        raise ValueError("title and content_html are required")
    body: dict[str, Any] = {"title": title, "content": content_html, "status": "pending"}
    if excerpt:
        body["excerpt"] = excerpt
    cats = _int_list(categories, "categories")
    tgs = _int_list(tags, "tags")
    if cats:
        body["categories"] = cats
    if tgs:
        body["tags"] = tgs
    if featured_media:
        body["featured_media"] = int(featured_media)
    result = gate.run_gate(_gate_text(title, excerpt, content_html), channel or "blog")
    if not result.passed:
        return _refusal(result.reason, result.findings)
    client = _get_client()
    created = client.request("POST", "/posts", json_body=body)
    if not isinstance(created, dict):
        raise WordPressApiError("POST", "/posts", 200, "no post object in response")
    return _post_result(created, client, result)


@server.tool()
def update_pending_post(
    id: int,
    title: str | None = None,
    content_html: str | None = None,
    excerpt: str | None = None,
    categories: list[int] | None = None,
    tags: list[int] | None = None,
    featured_media: int | None = None,
    channel: str = "blog",
) -> Any:
    """Revise a post this connector's user wrote that is still pending or
    draft. Refuses any post that is published, scheduled or private, and any
    post another user wrote. Only the fields given change; the status stays
    pending. The revised draft passes the advertising gate first, exactly as in
    create_pending_post, and the result has the same shape."""
    client = _get_client()
    post_id = int(id)
    try:
        current = client.request("GET", f"/posts/{post_id}", params={"context": "edit"})
    except WordPressApiError as exc:
        if exc.status not in (401, 403):
            raise
        # A Contributor cannot even open a published post or a colleague's
        # draft for editing; WordPress answers 403 rest_forbidden_context
        # (proven against a local WordPress). That is this tool's refusal too.
        return _refusal(
            f"post {post_id} is not editable by this connector's user (a published post, or another "
            "user's draft); only this connector's own pending or draft posts can be revised",
            [],
        )
    if not isinstance(current, dict):
        raise WordPressApiError("GET", f"/posts/{post_id}", 200, "no post object in response")
    status = str(current.get("status", ""))
    if status not in EDITABLE_STATUSES:
        return _refusal(f"post {post_id} is {status!r}; only pending or draft posts can be revised here", [])
    me = client.me()
    if int(current.get("author") or 0) != int(me["id"]):
        return _refusal(
            f"post {post_id} was written by another user; only this connector's own drafts can be revised", []
        )

    new_title = title if title is not None else _rendered(current.get("title"))
    new_content = content_html if content_html is not None else _rendered(current.get("content"))
    new_excerpt = excerpt if excerpt is not None else _rendered(current.get("excerpt"))
    result = gate.run_gate(_gate_text(new_title, new_excerpt, new_content), channel or "blog")
    if not result.passed:
        return _refusal(result.reason, result.findings)

    body: dict[str, Any] = {"status": "pending"}
    if title is not None:
        body["title"] = title
    if content_html is not None:
        body["content"] = content_html
    if excerpt is not None:
        body["excerpt"] = excerpt
    cats = _int_list(categories, "categories")
    tgs = _int_list(tags, "tags")
    if cats is not None:
        body["categories"] = cats
    if tgs is not None:
        body["tags"] = tgs
    if featured_media is not None:
        body["featured_media"] = int(featured_media)
    updated = client.request("POST", f"/posts/{post_id}", json_body=body)
    if not isinstance(updated, dict):
        raise WordPressApiError("POST", f"/posts/{post_id}", 200, "no post object in response")
    return _post_result(updated, client, result)
