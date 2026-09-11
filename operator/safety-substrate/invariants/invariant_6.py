"""Invariant #6 - citation enforcement for client-facing fact-bearing fields.

Per platform PRD §7.5, invariant #6 governs how facts surface in skill
output. The law-firm vertical implements it as REFUSAL on fabricated legal
citations (handled by ``safety-substrate/citation_filter.py``). This module
implements the platform-universal complement: every fact a skill renders
into a client-facing fact-bearing field must carry a Citation attached to
a real source.

The two layers cooperate. ``citation_filter.py`` refuses any output that
matches a legal-citation pattern (the law-vertical "do not author cites"
rule). ``invariant_6`` here refuses any output where a declared
fact-bearing field renders text but has no Citation attached. Together
they cover the two failure modes the PRD names:

- **Fabricated authority** - the agent invents a case name or statute
  reference. Caught by ``citation_filter``.
- **Unsourced assertion** - the agent renders a fact (dollar amount,
  deadline, named person, scope commitment) into a client-facing field
  without an attached source. Caught here.

The contract is intentionally narrow. This invariant does NOT scan free
prose for citation-shaped strings - that is ``citation_filter``'s job.
It does NOT enforce that source IDs resolve to real records - that is
``fabrication_filter`` (invariant #8) per ``docs/specs/operator/
fabrication-filter.md``. It enforces ONE rule: *if you declared the field
as fact-bearing and you rendered non-empty content into it, the
Citation must be attached and non-empty.*

Design notes
------------

* **Closed set of source kinds.** ``SourceKind`` is a closed enum. A
  Citation that names a kind outside the set is rejected as malformed.
  Per skill-anatomy §8.4 of the platform PRD, every fact a skill emits
  reduces to one of four origins:

  - ``matter_document`` - a document attached to the matter (intake form,
    medical records, contract, etc.). ``source_id`` is the document's
    matter-internal ID.
  - ``memory_rule`` - a rule stored in the per-customer memory under
    ``adapter/memory/``. ``source_id`` is the rule's ULID.
  - ``system_of_record`` - a record fetched from a connector (Filevine,
    LawPay, ShipStation, etc.). ``source_id`` is the connector record's
    primary key.
  - ``verbatim_quote`` - text quoted from one of the above with no
    paraphrase. ``source_id`` is the parent source's ID and ``span``
    locates the quoted substring inside the source.

* **Field tag ``none`` is a separate concern.** Skill anatomy lets a
  field declare ``sourced_from="none"`` to mark itself as load-bearing
  legal-judgment content (the demand-letter liability section, the
  settlement bracket, the closing recommendation). Per fabrication-filter
  spec §3.2, ``none``-tagged fields must render EMPTY - the partner
  fills them in after the draft lands. This invariant therefore SKIPS
  ``none``-tagged fields (the fabrication filter handles them). A
  ``none`` field that renders non-empty is invariant #8's failure, not
  ours.

* **No source-existence check.** This module does NOT verify that
  ``source_id`` resolves to a real record. That is the fabrication
  filter's job. Splitting the concern keeps each invariant
  independently testable and avoids coupling this module to memory
  store access or connector reads.

* **Audit emission.** Every violation writes one
  ``INVARIANT_VIOLATION`` row via ``AuditLogWriter`` with
  ``metadata.invariant=6`` and the per-field violation list. We reuse
  the existing closed-set action type rather than introducing a new
  ``CITATION_VIOLATION`` member, consistent with how ``sticky_stop.py``
  routes WARN/SOFT_STOP transitions.

* **No autonomous send paths.** The contract is a pure function:
  ``enforce_citations`` returns the violation set. The caller decides
  whether to block emission (production callers MUST block; tests
  inspect the result).

Module shape
------------

::

    from invariants.invariant_6 import (
        Citation,
        SourceKind,
        enforce_citations,
    )

    output = {
        "case_value_range": "$80,000-$150,000",
        "client_name": "Maria Diaz",
        "liability_section": "",      # tagged ``none``; must be empty
    }
    citations = {
        "case_value_range": Citation(
            source_kind=SourceKind.MEMORY_RULE,
            source_id="01HQ...",
        ),
        "client_name": Citation(
            source_kind=SourceKind.SYSTEM_OF_RECORD,
            source_id="filevine:contact:12345",
        ),
    }
    expected_fields = {
        "case_value_range": "fact_bearing",
        "client_name": "fact_bearing",
        "liability_section": "none",
    }

    result = enforce_citations(
        output=output,
        citations=citations,
        expected_fields=expected_fields,
    )
    if result.has_violations:
        raise CitationEnforcementError(result)

The ``run()`` callable at the bottom of this module is the per-fixture
shape that ``safety-substrate/run_invariants.py`` invokes at container
boot. The pytest file at ``tests/test_invariant_6.py`` covers the
unit-level cases.
"""

from __future__ import annotations

import enum
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

log = logging.getLogger("aie.invariants.invariant_6")


# ---------------------------------------------------------------------------
# Source taxonomy (closed enum)
# ---------------------------------------------------------------------------


class SourceKind(str, enum.Enum):
    """The four origins of a fact in a skill's output. Closed by design.

    A ``Citation`` that names a kind outside this set is rejected as a
    malformed citation, which itself counts as a violation.
    """

    MATTER_DOCUMENT = "matter_document"
    MEMORY_RULE = "memory_rule"
    SYSTEM_OF_RECORD = "system_of_record"
    VERBATIM_QUOTE = "verbatim_quote"


# Field-tag values. ``fact_bearing`` means the field renders facts from a
# source and therefore requires a Citation. ``none`` means the field is
# load-bearing legal-judgment content that must render empty (handled by
# invariant #8 / fabrication filter). Any other tag is treated as
# "skip" - invariant #6 does not impose requirements on free-prose
# fields the skill author did not declare fact-bearing.
FIELD_TAG_FACT_BEARING = "fact_bearing"
FIELD_TAG_NONE = "none"


# ---------------------------------------------------------------------------
# Citation dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """Attribution attached to one fact-bearing field's rendered value.

    Required:

    source_kind
        One of the four ``SourceKind`` values.
    source_id
        Opaque non-empty string identifying the source within its kind.
        Format is kind-specific (ULID for memory rules, connector record
        key for system-of-record, document ID for matter_document, parent
        source ID for verbatim_quote).

    Optional:

    span
        ``(start_char, end_char)`` half-open interval into the source
        text. Required for VERBATIM_QUOTE; meaningless for other kinds.
        Tuple values must be non-negative with ``start < end``.
    """

    source_kind: SourceKind
    source_id: str
    span: Optional[tuple[int, int]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, SourceKind):
            raise TypeError(f"source_kind must be SourceKind, got {type(self.source_kind).__name__}")
        if not self.source_id:
            raise ValueError("source_id is required and must be non-empty")
        if self.span is not None:
            if (
                not isinstance(self.span, tuple)
                or len(self.span) != 2
                or not all(isinstance(v, int) for v in self.span)
            ):
                raise ValueError("span must be a tuple of two integers")
            start, end = self.span
            if start < 0 or end <= start:
                raise ValueError("span must satisfy 0 <= start < end")
        if self.source_kind is SourceKind.VERBATIM_QUOTE and self.span is None:
            raise ValueError("VERBATIM_QUOTE citations must carry a span identifying the quoted range")


# ---------------------------------------------------------------------------
# Violation dataclasses
# ---------------------------------------------------------------------------


class CitationViolationKind(str, enum.Enum):
    """Why one field failed the invariant. Closed for audit-log clarity."""

    MISSING_CITATION = "missing_citation"
    EMPTY_SOURCE_ID = "empty_source_id"
    MALFORMED_CITATION = "malformed_citation"
    UNEXPECTED_FIELD = "unexpected_field"


@dataclass(frozen=True)
class CitationViolation:
    """One field-level failure. ``CitationViolations`` aggregates them."""

    field_name: str
    kind: CitationViolationKind
    rendered_excerpt: str
    detail: str


@dataclass(frozen=True)
class CitationViolations:
    """Result of :func:`enforce_citations`. Empty list = output passes."""

    violations: tuple[CitationViolation, ...] = field(default_factory=tuple)

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0

    def __len__(self) -> int:
        return len(self.violations)

    def __iter__(self):
        return iter(self.violations)

    def to_audit_metadata(self) -> dict:
        """Render the violation list as audit-log metadata.

        Keeps the structure stable so the compliance-evidence packet's
        invariant-violation roll-up can bucket by ``kind``.
        """
        return {
            "invariant": 6,
            "violations": [
                {
                    "field_name": v.field_name,
                    "kind": v.kind.value,
                    "rendered_excerpt": v.rendered_excerpt,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
        }


# ---------------------------------------------------------------------------
# Source registry (optional, used by callers that want light-weight
# existence checks; the invariant itself does not require this)
# ---------------------------------------------------------------------------


class SourceRegistry:
    """In-memory registry of known source IDs per kind.

    Production callers wire this against the customer's memory store,
    matter document index, and connector caches. The invariant module
    itself does not consult the registry - it is a convenience for
    callers that want to layer existence-checks on top. The fabrication
    filter (invariant #8) is the canonical existence enforcement; this
    class exists so unit tests can mirror that behavior without booting
    the whole memory subsystem.
    """

    def __init__(self) -> None:
        self._known: dict[SourceKind, set[str]] = {kind: set() for kind in SourceKind}

    def register(self, kind: SourceKind, source_id: str) -> None:
        self._known[kind].add(source_id)

    def known(self, kind: SourceKind, source_id: str) -> bool:
        return source_id in self._known[kind]


# ---------------------------------------------------------------------------
# Core enforcement function
# ---------------------------------------------------------------------------


def _excerpt(value: str, limit: int = 80) -> str:
    """Truncate rendered field values for audit metadata. Keeps the log
    row size bounded; the full draft is digested separately."""
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"  # horizontal ellipsis


def _is_nonempty(value: object) -> bool:
    """A rendered value is non-empty iff it is a string with at least
    one non-whitespace character. Non-string values are coerced via
    ``str()`` and re-checked.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return str(value).strip() != ""


def enforce_citations(
    *,
    output: Mapping[str, object],
    citations: Mapping[str, Citation],
    expected_fields: Mapping[str, str],
    allow_extra_output_keys: bool = True,
) -> CitationViolations:
    """Check every fact-bearing field has a valid Citation attached.

    Parameters
    ----------
    output
        The skill's rendered output, keyed by field name. Values are
        typically strings but any type is tolerated; emptiness is the
        only structural test.
    citations
        Citations the skill attached to its output, keyed by the same
        field names used in ``output``. Citations attached to fields
        not declared as ``fact_bearing`` are ignored; the invariant
        does not enforce on free-prose fields.
    expected_fields
        Field-name -> tag map drawn from the skill's
        ``output-format.md`` declaration (per skill anatomy §8.4).
        Only ``fact_bearing`` and ``none`` are enforcement-bearing.
        Any other value is treated as "skip" - the invariant is
        permissive about additional descriptive tags the skill author
        chose for their own taxonomy.
    allow_extra_output_keys
        If True (default), ``output`` keys not in ``expected_fields``
        are not flagged. If False, an unexpected key produces an
        ``UNEXPECTED_FIELD`` violation. Set False for skills whose
        ``output-format.md`` is exhaustive.

    Returns
    -------
    CitationViolations
        Aggregated violation set. ``result.has_violations`` is False
        iff the output passes invariant #6.
    """
    violations: list[CitationViolation] = []

    # Validate each declared fact-bearing field.
    for field_name, tag in expected_fields.items():
        if tag == FIELD_TAG_NONE:
            # ``none``-tagged fields are out of scope for invariant #6.
            # Invariant #8 / fabrication filter governs them.
            continue
        if tag != FIELD_TAG_FACT_BEARING:
            # Skill author tagged the field with something else
            # (descriptive copy, header, fixture). No enforcement.
            continue

        rendered = output.get(field_name)
        if not _is_nonempty(rendered):
            # Empty fact-bearing fields are permissible. The skill
            # may have chosen not to populate optional facts.
            continue

        excerpt = _excerpt(str(rendered))
        citation = citations.get(field_name)
        if citation is None:
            violations.append(
                CitationViolation(
                    field_name=field_name,
                    kind=CitationViolationKind.MISSING_CITATION,
                    rendered_excerpt=excerpt,
                    detail=(
                        "field is declared fact_bearing and rendered non-empty content, but no Citation is attached"
                    ),
                )
            )
            continue

        # Citation present - validate its shape. Citation.__post_init__
        # already enforces structural rules; we restate the checks here
        # so a defensively-constructed Citation (bypassing
        # __post_init__ via dataclasses.replace, etc.) still trips.
        if not citation.source_id:
            violations.append(
                CitationViolation(
                    field_name=field_name,
                    kind=CitationViolationKind.EMPTY_SOURCE_ID,
                    rendered_excerpt=excerpt,
                    detail=(f"Citation present (kind={citation.source_kind.value}) but source_id is empty"),
                )
            )
            continue
        if not isinstance(citation.source_kind, SourceKind):
            violations.append(
                CitationViolation(
                    field_name=field_name,
                    kind=CitationViolationKind.MALFORMED_CITATION,
                    rendered_excerpt=excerpt,
                    detail=(f"Citation.source_kind is not a SourceKind: {type(citation.source_kind).__name__}"),
                )
            )
            continue
        if citation.source_kind is SourceKind.VERBATIM_QUOTE and citation.span is None:
            violations.append(
                CitationViolation(
                    field_name=field_name,
                    kind=CitationViolationKind.MALFORMED_CITATION,
                    rendered_excerpt=excerpt,
                    detail=("VERBATIM_QUOTE Citation missing required span"),
                )
            )
            continue

    # Unexpected keys (off by default; opt-in for exhaustive output schemas).
    if not allow_extra_output_keys:
        for field_name, rendered in output.items():
            if field_name in expected_fields:
                continue
            if not _is_nonempty(rendered):
                continue
            violations.append(
                CitationViolation(
                    field_name=field_name,
                    kind=CitationViolationKind.UNEXPECTED_FIELD,
                    rendered_excerpt=_excerpt(str(rendered)),
                    detail=(
                        "output key is not declared in expected_fields; "
                        "skill output schema is exhaustive (allow_extra_output_keys=False)"
                    ),
                )
            )

    return CitationViolations(violations=tuple(violations))


# ---------------------------------------------------------------------------
# Substrate-runner entrypoint
# ---------------------------------------------------------------------------


def _self_check_fixtures() -> tuple[bool, str]:
    """Boot-time smoke fixtures.

    The substrate runner imports this module's ``run()`` and calls it at
    container boot. The fixtures here exercise the module's own
    enforcement loop against a passing case and a failing case so the
    runner catches accidental import-time breakage of the invariant
    module itself. Comprehensive unit tests live in
    ``tests/test_invariant_6.py``.
    """
    # Passing case: every fact-bearing field has a Citation.
    output_pass = {
        "case_value_range": "$80,000-$150,000 pending medical workup",
        "client_name": "Maria Diaz",
        "liability_section": "",
    }
    citations_pass = {
        "case_value_range": Citation(
            source_kind=SourceKind.MEMORY_RULE,
            source_id="01HQTESTRULE00000000000000",
        ),
        "client_name": Citation(
            source_kind=SourceKind.SYSTEM_OF_RECORD,
            source_id="filevine:contact:42",
        ),
    }
    expected = {
        "case_value_range": FIELD_TAG_FACT_BEARING,
        "client_name": FIELD_TAG_FACT_BEARING,
        "liability_section": FIELD_TAG_NONE,
    }
    result_pass = enforce_citations(
        output=output_pass,
        citations=citations_pass,
        expected_fields=expected,
    )
    if result_pass.has_violations:
        return (
            False,
            f"FAIL: passing fixture produced violations: {[v.kind.value for v in result_pass]}",
        )

    # Failing case: fact-bearing field rendered without a Citation.
    output_fail = {
        "case_value_range": "$80,000-$150,000",
        "client_name": "Maria Diaz",
    }
    result_fail = enforce_citations(
        output=output_fail,
        citations={},
        expected_fields={
            "case_value_range": FIELD_TAG_FACT_BEARING,
            "client_name": FIELD_TAG_FACT_BEARING,
        },
    )
    if not result_fail.has_violations or len(result_fail) != 2:
        return (
            False,
            f"FAIL: failing fixture should produce 2 violations, got {len(result_fail)}",
        )

    return (
        True,
        "PASS: invariant 6 enforces citation attachment on fact-bearing fields (2 of 2 self-check fixtures held)",
    )


def run() -> tuple[bool, str]:
    """Substrate-runner shape - boot-time smoke check.

    Returns ``(ok, message)``. ``ok=True`` iff the module's enforcement
    loop produces the expected behavior on the bundled self-check
    fixtures. Detailed coverage lives in ``tests/test_invariant_6.py``.
    """
    try:
        return _self_check_fixtures()
    except Exception as e:  # noqa: BLE001 - boot self-check: any raise is a FAIL line for the substrate runner, never a crash at boot
        return (False, f"FAIL: invariant 6 self-check raised {type(e).__name__}: {e}")


__all__ = [
    "Citation",
    "CitationViolation",
    "CitationViolationKind",
    "CitationViolations",
    "FIELD_TAG_FACT_BEARING",
    "FIELD_TAG_NONE",
    "SourceKind",
    "SourceRegistry",
    "enforce_citations",
    "run",
]


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
