"""Recipient-classifier tests — the outbound internal/external gate.

Deterministic, pure. The load-bearing assertions are the SPOOF vectors: a loose
match here is a path to an autonomous send to an attacker-controlled address, so
each canonicalization rule has an adversarial test. UNKNOWN must never silently
mean "internal" and must never be swallowed into a draft — it is the caller's
hard-error signal (that silent default was the "nothing ever sends" bug, in
reverse).
"""

from __future__ import annotations

import sys
from pathlib import Path

# operator/ root, so `adapter.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from adapter.recipient_classifier import (
    ACTION_CLASS_EXTERNAL_SEND,
    ACTION_CLASS_EXTERNAL_SEND_CLIENT,
    ACTION_CLASS_EXTERNAL_SEND_INTERNAL,
    ACTION_CLASS_EXTERNAL_SEND_VENDOR,
    RecipientClass,
    UnclassifiedRecipientError,
    classify_recipient,
    classify_recipients,
    classify_recipients_typed,
    send_action_class,
)

ROSTER = ["@firm.example", "scott@smd.services"]
# Typed outbound roster: the firm's own client (a consumer on gmail) and a records
# vendor. Exact addresses — a whole-@domain grant at a public provider is rejected
# by the validator; the classifier matches these exactly.
TYPED = [("jane@gmail.com", "client"), ("records@radiology.com", "records_vendor")]


# ---- core behaviour -------------------------------------------------------

def test_exact_address_match_is_internal():
    assert classify_recipient("scott@smd.services", ROSTER) is RecipientClass.INTERNAL


def test_domain_entry_matches_any_local_part():
    assert classify_recipient("amara@firm.example", ROSTER) is RecipientClass.INTERNAL
    assert classify_recipient("devi@firm.example", ROSTER) is RecipientClass.INTERNAL


def test_non_roster_recipient_is_outside():
    assert classify_recipient("client@example.com", ROSTER) is RecipientClass.OUTSIDE


def test_case_insensitive_match():
    assert classify_recipient("Scott@SMD.Services", ROSTER) is RecipientClass.INTERNAL
    assert classify_recipient("AMARA@Firm.Example", ROSTER) is RecipientClass.INTERNAL


# ---- SPOOF vectors (load-bearing) -----------------------------------------

def test_plus_tag_is_not_widened_for_full_address_entry():
    # scott+anything@ must NOT match the full-address roster entry scott@.
    assert classify_recipient("scott+evil@smd.services", ROSTER) is RecipientClass.OUTSIDE


def test_plus_tag_still_matches_a_whole_domain_grant():
    # But it IS genuinely on the firm.example domain, which is granted.
    assert classify_recipient("amara+tag@firm.example", ROSTER) is RecipientClass.INTERNAL


def test_parent_domain_lookalike_is_outside():
    # attacker registers firm.example.evil.com — exact domain equality blocks it.
    assert classify_recipient("x@firm.example.evil.com", ROSTER) is RecipientClass.OUTSIDE


def test_subdomain_is_not_widened_to_parent_grant():
    # @firm.example grants the apex only, not mail.firm.example.
    assert classify_recipient("x@mail.firm.example", ROSTER) is RecipientClass.OUTSIDE


def test_display_name_form_is_unknown_not_parsed():
    # "Scott <scott@smd.services>" must not match on the address inside — reject it.
    assert classify_recipient("Scott <scott@smd.services>", ROSTER) is RecipientClass.UNKNOWN


def test_address_list_in_one_string_is_unknown():
    assert (
        classify_recipient("scott@smd.services, evil@x.com", ROSTER)
        is RecipientClass.UNKNOWN
    )


def test_garbage_and_empty_are_unknown():
    for bad in ["", "   ", "not-an-email", "@nodomain", "no-at-sign.com", "a@b"]:
        assert classify_recipient(bad, ROSTER) is RecipientClass.UNKNOWN, bad


def test_homoglyph_domain_does_not_match_ascii_roster():
    # Cyrillic 'а' (U+0430) in the domain is a different string — never internal.
    homoglyph = "scott@smd.serviсеs"  # с and е are Cyrillic
    assert classify_recipient(homoglyph, ROSTER) is not RecipientClass.INTERNAL


# ---- tainted provenance ---------------------------------------------------

def test_tainted_recipient_matching_roster_is_outside_never_internal():
    # An injected "send to scott@smd.services" must not ride the roster to autonomous.
    assert (
        classify_recipient("scott@smd.services", ROSTER, from_tainted=True)
        is RecipientClass.OUTSIDE
    )


def test_tainted_unresolvable_is_still_unknown():
    assert (
        classify_recipient("garbage", ROSTER, from_tainted=True)
        is RecipientClass.UNKNOWN
    )


# ---- multi-recipient aggregation (most-restrictive wins) -------------------

def test_all_internal_recipients_aggregate_internal():
    rs = ["scott@smd.services", "amara@firm.example"]
    assert classify_recipients(rs, ROSTER) is RecipientClass.INTERNAL


def test_any_outside_recipient_makes_the_send_outside():
    rs = ["scott@smd.services", "client@example.com"]
    assert classify_recipients(rs, ROSTER) is RecipientClass.OUTSIDE


def test_any_unknown_recipient_makes_the_send_unknown_hard_error():
    rs = ["scott@smd.services", "Scott <scott@smd.services>"]
    assert classify_recipients(rs, ROSTER) is RecipientClass.UNKNOWN


def test_empty_recipient_list_is_unknown():
    assert classify_recipients([], ROSTER) is RecipientClass.UNKNOWN


def test_roster_iterable_not_exhausted_across_recipients():
    # A one-shot generator roster must be materialised, not consumed on recipient #1.
    roster_gen = (e for e in ROSTER)
    rs = ["client@example.com", "scott@smd.services"]
    assert classify_recipients(rs, roster_gen) is RecipientClass.OUTSIDE


def test_malformed_roster_entry_never_widens_a_match():
    bad_roster = ["not-a-roster-line", "@", "@evil", "scott@smd.services"]
    assert classify_recipient("scott@smd.services", bad_roster) is RecipientClass.INTERNAL
    # "@evil" is a malformed single-label roster entry; it must not grant the
    # evil.com domain (or any domain). A valid outside address stays OUTSIDE.
    assert classify_recipient("anyone@evil.com", bad_roster) is RecipientClass.OUTSIDE


# ---- the fail-closed router (UNKNOWN → hard error, never a draft) ----------

def test_router_internal_maps_to_external_send_internal():
    assert send_action_class(RecipientClass.INTERNAL) == ACTION_CLASS_EXTERNAL_SEND_INTERNAL


def test_router_outside_maps_to_external_send():
    assert send_action_class(RecipientClass.OUTSIDE) == ACTION_CLASS_EXTERNAL_SEND


def test_router_unknown_is_a_hard_error_not_a_draft():
    # The keystone anti-regression guarantee: an unresolved recipient stops loudly.
    with pytest.raises(UnclassifiedRecipientError):
        send_action_class(RecipientClass.UNKNOWN)


# ---- ADR 0075: typed outbound roster (CLIENT / VENDOR) --------------------


def test_typed_client_and_vendor_resolve_by_class():
    assert classify_recipients_typed(["jane@gmail.com"], ROSTER, TYPED) is RecipientClass.CLIENT
    assert (
        classify_recipients_typed(["records@radiology.com"], ROSTER, TYPED)
        is RecipientClass.VENDOR
    )


def test_typed_internal_outranks_typed_class():
    # An address on the internal roster classifies INTERNAL even if it also appears
    # as a typed entry (internal-first order).
    typed = [("scott@smd.services", "client")]
    assert classify_recipients_typed(["scott@smd.services"], ROSTER, typed) is RecipientClass.INTERNAL


def test_typed_unrostered_is_outside():
    assert (
        classify_recipients_typed(["opposing@counsel.com"], ROSTER, TYPED) is RecipientClass.OUTSIDE
    )


def test_typed_public_domain_exact_matches_but_grant_would_not():
    # EXACT gmail address is a client; a DIFFERENT gmail address is not (exact only).
    assert classify_recipients_typed(["jane@gmail.com"], [], TYPED) is RecipientClass.CLIENT
    assert classify_recipients_typed(["bob@gmail.com"], [], TYPED) is RecipientClass.OUTSIDE


def test_typed_plus_tag_and_display_name_and_homoglyph():
    typed = [("jane@gmail.com", "client")]
    assert classify_recipients_typed(["jane+x@gmail.com"], [], typed) is RecipientClass.OUTSIDE
    assert classify_recipients_typed(["Jane <jane@gmail.com>"], [], typed) is RecipientClass.UNKNOWN
    # U+0430 Cyrillic 'а' in the domain never matches the ASCII roster.
    assert classify_recipients_typed(["jane@gmаil.com"], [], typed) is not RecipientClass.CLIENT


def test_typed_multiclass_address_is_outside_never_guesses():
    typed = [("x@firm-vendor.com", "client"), ("x@firm-vendor.com", "records_vendor")]
    assert classify_recipients_typed(["x@firm-vendor.com"], [], typed) is RecipientClass.OUTSIDE


def test_typed_empty_roster_is_outside():
    assert classify_recipients_typed(["jane@gmail.com"], [], []) is RecipientClass.OUTSIDE


def test_typed_tainted_recipient_is_outside_never_typed():
    assert (
        classify_recipients_typed(["jane@gmail.com"], ROSTER, TYPED, from_tainted=True)
        is RecipientClass.OUTSIDE
    )


def test_typed_empty_recipient_list_is_unknown():
    assert classify_recipients_typed([], ROSTER, TYPED) is RecipientClass.UNKNOWN


# ---- homogeneity aggregation (prevents ceiling-shopping on mixed sends) -----


def test_internal_plus_client_aggregates_client():
    rs = ["scott@smd.services", "jane@gmail.com"]  # internal CC rides along
    assert classify_recipients_typed(rs, ROSTER, TYPED) is RecipientClass.CLIENT


def test_internal_plus_vendor_aggregates_vendor():
    rs = ["scott@smd.services", "records@radiology.com"]
    assert classify_recipients_typed(rs, ROSTER, TYPED) is RecipientClass.VENDOR


def test_client_plus_vendor_mix_is_outside():
    rs = ["jane@gmail.com", "records@radiology.com"]
    assert classify_recipients_typed(rs, ROSTER, TYPED) is RecipientClass.OUTSIDE


def test_any_outside_recipient_makes_typed_send_outside():
    rs = ["jane@gmail.com", "opposing@counsel.com"]
    assert classify_recipients_typed(rs, ROSTER, TYPED) is RecipientClass.OUTSIDE


def test_any_unknown_recipient_makes_typed_send_unknown():
    rs = ["jane@gmail.com", "garbage"]
    assert classify_recipients_typed(rs, ROSTER, TYPED) is RecipientClass.UNKNOWN


def test_typed_iterables_not_exhausted_across_recipients():
    # One-shot generators for both rosters must be materialized, not consumed on #1.
    roster_gen = (e for e in ROSTER)
    typed_gen = (t for t in TYPED)
    rs = ["records@radiology.com", "jane@gmail.com"]  # client + vendor mix → OUTSIDE
    assert classify_recipients_typed(rs, roster_gen, typed_gen) is RecipientClass.OUTSIDE


# ---- router maps the typed classes ----------------------------------------


def test_router_client_and_vendor_map_to_their_action_classes():
    assert send_action_class(RecipientClass.CLIENT) == ACTION_CLASS_EXTERNAL_SEND_CLIENT
    assert send_action_class(RecipientClass.VENDOR) == ACTION_CLASS_EXTERNAL_SEND_VENDOR


# ---- cross-module parity: classifier constants == adapter enum values ------


def test_classifier_constants_match_adapter_enum_values():
    from adapter.trust_ceiling import ActionClass

    assert ACTION_CLASS_EXTERNAL_SEND == ActionClass.EXTERNAL_SEND.value
    assert ACTION_CLASS_EXTERNAL_SEND_INTERNAL == ActionClass.EXTERNAL_SEND_INTERNAL.value
    assert ACTION_CLASS_EXTERNAL_SEND_CLIENT == ActionClass.EXTERNAL_SEND_CLIENT.value
    assert ACTION_CLASS_EXTERNAL_SEND_VENDOR == ActionClass.EXTERNAL_SEND_VENDOR.value
