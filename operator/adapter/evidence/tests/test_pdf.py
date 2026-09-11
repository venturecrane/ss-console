"""Tests for adapter.evidence.pdf.render_summary_pdf."""

from __future__ import annotations

import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))

from adapter.evidence.pdf import render_summary_pdf  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def _counts():
    return {
        "audit_events": 42,
        "drafts_created": 10,
        "drafts_approved": 8,
        "drafts_rejected": 2,
        "memory_rule_events": 5,
        "skills_enabled": 6,
        "boot_checks": 12,
        "invariant_violations": 0,
        "escalations": 1,
    }


def test_render_summary_pdf_returns_valid_pdf_bytes():
    pdf = render_summary_pdf(
        customer_slug="acme",
        customer_name="Acme Co.",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        matter="all",
        signer_key_id="key-abc",
        manifest_sha256="deadbeef" * 8,
        counts=_counts(),
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_render_summary_pdf_includes_signature_and_manifest_sha():
    sha = "abc123" * 8
    pdf = render_summary_pdf(
        customer_slug="acme",
        customer_name="Acme Co.",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        matter="m-9",
        signer_key_id="key-xyz",
        manifest_sha256=sha,
        counts=_counts(),
    )
    # PDF text is inside content streams as literal `(text) Tj`. The
    # signature line and manifest sha are emitted verbatim in the
    # verification section.
    assert b"key-xyz" in pdf
    assert sha.encode("ascii") in pdf


def test_render_summary_pdf_strips_em_dashes():
    pdf = render_summary_pdf(
        customer_slug="acme",
        customer_name="Acme — Subsidiary",  # em dash in name
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        matter="all",
        signer_key_id="key-abc",
        manifest_sha256="0" * 64,
        counts=_counts(),
    )
    # Em-dash byte sequence must not appear; the renderer rewrites it as `--`.
    assert "—".encode("utf-8") not in pdf


def test_render_summary_pdf_renders_counts_truthfully_for_zeros():
    zeroed = {k: 0 for k in _counts()}
    pdf = render_summary_pdf(
        customer_slug="acme",
        customer_name="Acme Co.",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        matter="all",
        signer_key_id="key-abc",
        manifest_sha256="0" * 64,
        counts=zeroed,
    )
    # Truthful "0 ingested" path: the literal `Audit events recorded: 0` line.
    assert b"Audit events recorded: 0" in pdf
    assert b"Invariant violations: 0" in pdf


def test_render_summary_pdf_is_deterministic_for_same_inputs():
    args = dict(
        customer_slug="acme",
        customer_name="Acme Co.",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        matter="all",
        signer_key_id="key-abc",
        manifest_sha256="deadbeef" * 8,
        counts=_counts(),
    )
    a = render_summary_pdf(**args)
    b = render_summary_pdf(**args)
    assert a == b
