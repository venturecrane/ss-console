"""Tests for invariant #6 - citation enforcement on fact-bearing fields.

Coverage:

* Fires when a fact-bearing field renders non-empty without a Citation.
* Fires when the attached Citation has an empty source_id.
* Fires when the attached Citation has a malformed source_kind.
* Fires when a VERBATIM_QUOTE Citation lacks a span.
* PASSES when every fact-bearing field has a well-formed Citation.
* SKIPS fields tagged ``none`` even if rendered non-empty (PRD edge case -
  ``none``-tagged fields are governed by invariant #8 / fabrication filter).
* SKIPS fields tagged with custom non-enforcement values.
* SKIPS empty fact-bearing fields (skill chose not to populate optional facts).
* ``allow_extra_output_keys=False`` flags unexpected output keys.
* :class:`Citation` constructor enforces its own contract.
* Result objects render stable audit-log metadata.

Run from repo root:

    cd operator && uv run --with pytest python -m pytest \
        safety-substrate/tests/test_invariant_6.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow running from repo root or from operator/.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # safety-substrate/ on path

from invariants.invariant_6 import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    FIELD_TAG_FACT_BEARING,
    FIELD_TAG_NONE,
    Citation,
    CitationViolationKind,
    SourceKind,
    SourceRegistry,
    enforce_citations,
    run as run_module_self_check,
)


# ---------------------------------------------------------------------------
# Citation constructor contract
# ---------------------------------------------------------------------------


def test_citation_requires_non_empty_source_id():
    with pytest.raises(ValueError, match="source_id"):
        Citation(source_kind=SourceKind.MEMORY_RULE, source_id="")


def test_citation_rejects_non_enum_source_kind():
    with pytest.raises(TypeError, match="SourceKind"):
        Citation(source_kind="memory_rule", source_id="01HQ")  # type: ignore[arg-type]


def test_citation_rejects_malformed_span():
    with pytest.raises(ValueError, match="span"):
        Citation(
            source_kind=SourceKind.VERBATIM_QUOTE,
            source_id="doc-1",
            span=(10, 5),
        )


def test_citation_verbatim_quote_requires_span():
    with pytest.raises(ValueError, match="VERBATIM_QUOTE"):
        Citation(
            source_kind=SourceKind.VERBATIM_QUOTE,
            source_id="doc-1",
        )


def test_citation_accepts_well_formed_inputs():
    c = Citation(
        source_kind=SourceKind.VERBATIM_QUOTE,
        source_id="doc-1",
        span=(0, 12),
    )
    assert c.source_kind is SourceKind.VERBATIM_QUOTE
    assert c.span == (0, 12)


# ---------------------------------------------------------------------------
# Passing cases
# ---------------------------------------------------------------------------


def test_passes_when_every_fact_bearing_field_has_citation():
    result = enforce_citations(
        output={
            "client_name": "Maria Diaz",
            "case_value_range": "$80,000-$150,000",
        },
        citations={
            "client_name": Citation(
                source_kind=SourceKind.SYSTEM_OF_RECORD,
                source_id="filevine:contact:42",
            ),
            "case_value_range": Citation(
                source_kind=SourceKind.MEMORY_RULE,
                source_id="01HQTESTRULE00000000000000",
            ),
        },
        expected_fields={
            "client_name": FIELD_TAG_FACT_BEARING,
            "case_value_range": FIELD_TAG_FACT_BEARING,
        },
    )
    assert not result.has_violations


def test_skips_empty_fact_bearing_fields():
    # Empty fact-bearing fields are allowed (skill chose not to populate).
    result = enforce_citations(
        output={"case_value_range": ""},
        citations={},
        expected_fields={"case_value_range": FIELD_TAG_FACT_BEARING},
    )
    assert not result.has_violations


def test_whitespace_only_string_counts_as_empty():
    result = enforce_citations(
        output={"case_value_range": "   \n\t  "},
        citations={},
        expected_fields={"case_value_range": FIELD_TAG_FACT_BEARING},
    )
    assert not result.has_violations


def test_skips_fields_with_custom_non_enforcement_tag():
    # The skill author may tag fields with values outside the enforcement
    # set (e.g., "header", "fixture") - the invariant is permissive.
    result = enforce_citations(
        output={"section_header": "Liability Analysis"},
        citations={},
        expected_fields={"section_header": "header"},
    )
    assert not result.has_violations


# ---------------------------------------------------------------------------
# PRD edge case: ``none``-tagged fields are out of scope
# ---------------------------------------------------------------------------


def test_skips_none_tagged_field_even_when_rendered():
    """``none``-tagged fields are governed by invariant #8 / fabrication
    filter (the field must render empty; a non-empty ``none`` field is
    invariant #8's failure, not ours).

    Cited in the task instructions as the named PRD edge case:
    'field tagged `none` doesn't trigger citation check'.
    """
    result = enforce_citations(
        output={
            "liability_section": "Partner fills in after review.",
            "case_value_range": "$80,000",
        },
        citations={
            "case_value_range": Citation(
                source_kind=SourceKind.MEMORY_RULE,
                source_id="01HQRULE",
            ),
        },
        expected_fields={
            "liability_section": FIELD_TAG_NONE,
            "case_value_range": FIELD_TAG_FACT_BEARING,
        },
    )
    # Even though the ``none`` field rendered non-empty content without
    # a Citation, invariant #6 does NOT fire on it. The fabrication
    # filter would flag that separately.
    assert not result.has_violations


# ---------------------------------------------------------------------------
# Failing cases
# ---------------------------------------------------------------------------


def test_fires_when_fact_bearing_field_has_no_citation():
    result = enforce_citations(
        output={
            "case_value_range": "$80,000-$150,000",
            "client_name": "Maria Diaz",
        },
        citations={},
        expected_fields={
            "case_value_range": FIELD_TAG_FACT_BEARING,
            "client_name": FIELD_TAG_FACT_BEARING,
        },
    )
    assert result.has_violations
    assert len(result) == 2
    kinds = {v.kind for v in result}
    assert kinds == {CitationViolationKind.MISSING_CITATION}
    fields = {v.field_name for v in result}
    assert fields == {"case_value_range", "client_name"}


def test_fires_on_partial_coverage():
    # One field cited; one fact-bearing field missing its citation.
    result = enforce_citations(
        output={
            "client_name": "Maria Diaz",
            "case_value_range": "$80,000-$150,000",
        },
        citations={
            "client_name": Citation(
                source_kind=SourceKind.SYSTEM_OF_RECORD,
                source_id="filevine:contact:42",
            ),
        },
        expected_fields={
            "client_name": FIELD_TAG_FACT_BEARING,
            "case_value_range": FIELD_TAG_FACT_BEARING,
        },
    )
    assert result.has_violations
    assert len(result) == 1
    assert result.violations[0].field_name == "case_value_range"
    assert result.violations[0].kind is CitationViolationKind.MISSING_CITATION


def test_fires_when_allow_extra_output_keys_false_and_extra_present():
    result = enforce_citations(
        output={"client_name": "Maria", "stray_field": "stray text"},
        citations={
            "client_name": Citation(
                source_kind=SourceKind.SYSTEM_OF_RECORD,
                source_id="filevine:contact:1",
            ),
        },
        expected_fields={"client_name": FIELD_TAG_FACT_BEARING},
        allow_extra_output_keys=False,
    )
    assert result.has_violations
    assert len(result) == 1
    v = result.violations[0]
    assert v.field_name == "stray_field"
    assert v.kind is CitationViolationKind.UNEXPECTED_FIELD


def test_allow_extra_output_keys_true_by_default():
    result = enforce_citations(
        output={"client_name": "Maria", "metadata_blob": "internal use"},
        citations={
            "client_name": Citation(
                source_kind=SourceKind.SYSTEM_OF_RECORD,
                source_id="filevine:contact:1",
            ),
        },
        expected_fields={"client_name": FIELD_TAG_FACT_BEARING},
    )
    assert not result.has_violations


# ---------------------------------------------------------------------------
# Excerpt truncation in audit metadata
# ---------------------------------------------------------------------------


def test_violation_excerpt_truncated_for_long_values():
    long_value = "x" * 200
    result = enforce_citations(
        output={"narrative": long_value},
        citations={},
        expected_fields={"narrative": FIELD_TAG_FACT_BEARING},
    )
    assert result.has_violations
    excerpt = result.violations[0].rendered_excerpt
    assert len(excerpt) <= 80
    assert excerpt.endswith("…")


# ---------------------------------------------------------------------------
# Audit metadata shape
# ---------------------------------------------------------------------------


def test_to_audit_metadata_is_stable_shape():
    result = enforce_citations(
        output={"case_value_range": "$80,000"},
        citations={},
        expected_fields={"case_value_range": FIELD_TAG_FACT_BEARING},
    )
    meta = result.to_audit_metadata()
    assert meta["invariant"] == 6
    assert isinstance(meta["violations"], list)
    assert len(meta["violations"]) == 1
    v = meta["violations"][0]
    assert v["field_name"] == "case_value_range"
    assert v["kind"] == "missing_citation"
    assert "rendered_excerpt" in v
    assert "detail" in v


# ---------------------------------------------------------------------------
# SourceRegistry convenience
# ---------------------------------------------------------------------------


def test_source_registry_tracks_known_ids_per_kind():
    reg = SourceRegistry()
    reg.register(SourceKind.MEMORY_RULE, "01HQ")
    assert reg.known(SourceKind.MEMORY_RULE, "01HQ")
    assert not reg.known(SourceKind.MEMORY_RULE, "missing")
    assert not reg.known(SourceKind.SYSTEM_OF_RECORD, "01HQ")


# ---------------------------------------------------------------------------
# Substrate-runner entrypoint smoke check
# ---------------------------------------------------------------------------


def test_module_run_callable_returns_pass_on_clean_state():
    ok, msg = run_module_self_check()
    assert ok, f"module-level run() should pass on a clean import; got: {msg}"
    assert "invariant 6" in msg.lower()


# ---------------------------------------------------------------------------
# Substrate-runner shape (run_invariants.py compatibility)
#
# The substrate runner at safety-substrate/run_invariants.py looks for a
# module-level run() callable. Provide one that aggregates the suite.
# ---------------------------------------------------------------------------


def run() -> tuple[bool, str]:
    """Aggregated harness entrypoint for run_invariants.py.

    Delegates to the invariant module's own self-check fixtures. Detailed
    coverage is in the pytest test functions above.
    """
    return run_module_self_check()


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
