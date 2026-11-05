"""The proof of a revision now lives in ``firmbase.xmlproof``, shared with every
document made in a firm's own Word file. Revise moves onto firmbase in a later PR."""

from __future__ import annotations

from ...firmbase.xmlproof import Allow, body_of, canon, leaves, parts_differ, prove_body

__all__ = ["Allow", "body_of", "canon", "leaves", "parts_differ", "prove_body"]
