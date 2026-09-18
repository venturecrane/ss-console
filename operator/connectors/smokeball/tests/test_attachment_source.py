"""Where an attachment's bytes come from, and everything that must be refused.

THE DEFECT (live, 2026-09-18). Every attachment tool here wanted a time-limited
vendor download URL. The mail vendor a seat runs on mints none — it returns raw
bytes to an authenticated caller — so the URL-shaped argument could never be
filled and a forwarded invoice was answered "your message arrived without any
attachments". The mail side now writes the bytes to a seat-local spool and hands
over a token; a reference of the form ``spool:<token>`` turns back into bytes
here, in the same argument a URL goes in.

The reference arrives from the agent loop, on a turn tainted by a stranger's
email. So each test below is a way that string could become a file read it
should not get, and each carries the direction that would be a defect.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
from typing import Any

import pytest

from smokeball_connector import attachment_source as src
from smokeball_connector import server as srv
from smokeball_connector import vendor_invoice as vi
from smokeball_connector.client import SmokeballWriteError

PDF = b"%PDF-1.7 a spooled invoice"
# Built rather than written out: a 32-hex literal reads as a credential to
# gitleaks, and this is a fixture token for a file name on disk.
_HEX16 = "0123456789abcdef"
TOKEN = _HEX16 * 2
REF = f"spool:{TOKEN}"


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A spool directory laid out exactly as the overlay's writer leaves it."""
    directory = tmp_path / "attachment-spool"
    directory.mkdir()
    monkeypatch.setenv(src.SPOOL_DIR_ENV, str(directory))
    (directory / f"{TOKEN}.bin").write_bytes(PDF)
    return directory


class _NoFetchClient:
    """A client that FAILS if the URL path is taken. The spool path must not
    touch the network, and a test that could not tell the difference would not
    be testing anything."""

    def fetch_attachment_url(self, url: str) -> bytes:
        raise AssertionError(f"the URL fetch must not run for a spool read (got {url!r})")


# ---- the round trip ---------------------------------------------------------


def test_a_spool_reference_yields_the_bytes_without_a_fetch(spool: Path) -> None:
    assert src.fetch_bytes(_NoFetchClient(), REF) == PDF


def test_the_two_forms_split_into_exactly_one_side(spool: Path) -> None:
    assert src.parse_source(REF) == ("", TOKEN)
    assert src.parse_source("https://download.agentmail.to/a") == (
        "https://download.agentmail.to/a",
        "",
    )


@pytest.mark.parametrize("ref", ["", "   ", None, 12345, "ftp://x/y", "/opt/data/x.bin", TOKEN])
def test_a_reference_in_neither_form_is_refused(spool: Path, ref: Any) -> None:
    """A bare token is refused too: the prefix is what makes the caller's
    intent explicit, and guessing at an unprefixed string is how the wrong
    source gets picked."""
    with pytest.raises(SmokeballWriteError, match="no attachment source"):
        src.parse_source(ref)


# ---- the token cannot become a path it should not reach ---------------------


@pytest.mark.parametrize(
    "token",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "..",
        "",
        "Z" * 32,
        _HEX16.upper() * 2,  # uppercase is not the issued shape
        TOKEN[:-1],  # one short
        TOKEN + "0",  # one long
        TOKEN + "\x00",
    ],
)
def test_a_token_that_is_not_the_issued_shape_is_refused(spool: Path, token: str) -> None:
    """Refused ON THE SHAPE, and the message says so.

    Matching the message is deliberate. Without it this passes for the wrong
    reason: a traversal token names a file that does not exist, resolution
    raises anyway, and the assertion is satisfied whether or not the shape check
    is there at all. Pinning the reason makes ``TOKEN_RE`` load-bearing."""
    with pytest.raises(SmokeballWriteError, match="32-lowercase-hex"):
        src.fetch_bytes(_NoFetchClient(), f"spool:{token}")


def test_a_traversal_reference_pointing_at_a_real_file_is_still_refused(spool: Path) -> None:
    """The case that would actually leak: ``../outside`` names a file that DOES
    exist, so nothing about a missing path saves us."""
    (spool.parent / "outside.bin").write_bytes(b"not yours")
    with pytest.raises(SmokeballWriteError):
        src.fetch_bytes(_NoFetchClient(), "spool:../outside")


def test_a_symlinked_entry_is_refused(spool: Path) -> None:
    outside = spool.parent / "elsewhere.pdf"
    outside.write_bytes(b"not yours")
    link = "f" * 32
    os.symlink(outside, spool / f"{link}.bin")
    with pytest.raises(SmokeballWriteError, match="symlink"):
        src.read_spool(link)


def test_a_directory_named_like_an_entry_is_refused(spool: Path) -> None:
    token = "a" * 32
    (spool / f"{token}.bin").mkdir()
    with pytest.raises(SmokeballWriteError, match="regular file"):
        src.read_spool(token)


def test_an_expired_or_unknown_token_says_so(spool: Path) -> None:
    with pytest.raises(SmokeballWriteError, match="expires"):
        src.read_spool("b" * 32)


def test_an_oversized_entry_is_refused(spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(src, "MAX_SPOOL_BYTES", len(PDF) - 1)
    with pytest.raises(SmokeballWriteError, match="over the"):
        src.read_spool(TOKEN)


# ---- the tools that consume it ----------------------------------------------


def test_read_attachment_text_reads_from_the_spool(spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the tool the skill calls, with no URL anywhere."""
    from .test_vendor_invoice import INVOICE_PDF, INVOICE_SHA

    (spool / f"{TOKEN}.bin").write_bytes(INVOICE_PDF)
    monkeypatch.setattr(srv, "_get_client", lambda: _NoFetchClient())
    out = srv.read_attachment_text(REF, "invoice.pdf")
    assert out["readable"] is True
    assert out["sha256"] == INVOICE_SHA
    assert "INV-2026-001" in out["text"]


def test_filing_to_a_matter_reads_from_the_spool(spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The served-discovery path: the PDF reaches the matter from the spool."""
    filed: list[Any] = []

    class _Client(_NoFetchClient):
        def add_file(self, matter_id: str, file_name: str, blob: bytes, **k: Any) -> dict[str, Any]:
            filed.append((matter_id, file_name, blob))
            return {"fileId": "file-new"}

    monkeypatch.setattr(srv, "_get_client", lambda: _Client())
    assert srv.file_attachment_to_matter("m-1", REF, "served.pdf") == {"fileId": "file-new"}
    assert filed == [("m-1", "served.pdf", PDF)]


def test_staging_from_the_spool_refuses_when_the_bytes_changed(spool: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The money invariant's integrity half, end to end on the spool path: the
    stage reads the SAME reference again and refuses if what it finds is not
    what was read. Nothing is created."""
    from .test_vendor_invoice import INVOICE_PDF, INVOICE_SHA, Tenant

    (spool / f"{TOKEN}.bin").write_bytes(b"%PDF-1.7 a different document")
    tenant = Tenant()
    out = vi.stage_vendor_invoice(
        tenant.client(),
        matter_id="f220c8e4-eab5-4fd9-8f1d-0becf715b390",
        download_url=REF,
        file_name="invoice.pdf",
        sha256=INVOICE_SHA,
        vendor="Acme Records Inc",
        invoice_number="INV-2026-001",
        invoice_date="2026-09-01",
        amount="1250.00",
        verify_reference=srv._verify_matter_reference,
        stamp=srv._stamp,
        config=vi.ExpenseConfig(),
    )
    assert out["status"] == "refused"
    assert "bytes changed" in out["reason"]
    assert tenant.posted == []
    # The falsifier: with the right bytes spooled, the same call stages.
    (spool / f"{TOKEN}.bin").write_bytes(INVOICE_PDF)
    tenant2 = Tenant()
    ok = vi.stage_vendor_invoice(
        tenant2.client(),
        matter_id="f220c8e4-eab5-4fd9-8f1d-0becf715b390",
        download_url=REF,
        file_name="invoice.pdf",
        sha256=INVOICE_SHA,
        vendor="Acme Records Inc",
        invoice_number="INV-2026-001",
        invoice_date="2026-09-01",
        amount="1250.00",
        verify_reference=srv._verify_matter_reference,
        stamp=srv._stamp,
        config=vi.ExpenseConfig(),
    )
    assert ok["status"] == "staged", ok


def test_the_url_path_still_works(spool: Path) -> None:
    """The regression guard for the other half of the fix: adding a form must
    not remove one. A vendor that DOES mint a URL keeps working."""
    from .test_vendor_invoice import URL, Tenant

    out = vi.read_attachment(Tenant(blob=PDF).client(), URL, "invoice.pdf")
    assert out["sha256"] == hashlib.sha256(PDF).hexdigest()


def test_the_url_allowlist_is_unchanged(spool: Path) -> None:
    from .test_vendor_invoice import Tenant

    with pytest.raises(SmokeballWriteError, match="not an allowed attachment source"):
        vi.read_attachment(Tenant().client(), "https://evil.example.com/invoice.pdf", "x.pdf")


def test_the_staging_tool_gained_no_argument_at_all() -> None:
    """The attachment source rides the argument that already existed, so the
    money invariant's signature guard is not weakened by this change: no new
    parameter, and still nothing named for free text or finalizing."""
    params = set(inspect.signature(srv.stage_vendor_invoice).parameters)
    assert params == {
        "matter_id",
        "download_url",
        "file_name",
        "sha256",
        "vendor",
        "invoice_number",
        "invoice_date",
        "amount",
    }
    assert not params & {"description", "subject", "title", "body", "note", "finalized"}
