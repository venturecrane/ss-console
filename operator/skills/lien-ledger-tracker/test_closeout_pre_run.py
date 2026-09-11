"""lien-ledger-tracker pre-run gate tests (ss #2455).

``decide()`` is pure, so every branch is exercised with fake inputs. The parse
tests run against the committed wire fixtures, which were generated from the
same authored seed a person keys into the sandbox, so a parser that agrees with
the fixtures agrees with what the sandbox will hold.

What each group is really guarding:

  identity   a provider's history must survive the firm correcting a typo
  grouping   one payer on N matters is ONE outreach, never N
  fencing    a held matter never joins a chase group
  config     firm facts fail closed; a stall threshold degrades instead
  coverage   a partial deep read reports the gap rather than reading as complete
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import date, datetime

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parent
WIRE_DIR = SKILL_DIR.parents[1] / "fixtures/law-firm/pi/lien-ledger-tracker/seed/wire"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ledger = _load("llt_ledger_under_test", SKILL_DIR / "escalation_ledger.py")
gate = _load("llt_pre_run_under_test", SKILL_DIR / "pre_run.py")

TODAY = date(2026, 8, 19)
AUTHORED = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14, stall_days=60)

MATTER_A = "aaaaaaaa-1111-2222-3333-444444444444"
MATTER_B = "bbbbbbbb-1111-2222-3333-444444444444"
ENTITY_1 = "e1a4c0d2-7b31-4a90-9c55-2f8d61b0a331"


def _obligation(matter: str, key: str, display: str, balance: float, **kw) -> object:
    return gate.Obligation(matter_id=matter, provider_key=key, provider_display=display, balance=balance, **kw)


def _pull(obligations, *, cohort=10, deep=10, unreadable=0, name_keyed=0, rows=()):
    return gate.ObligationPull(
        obligations=tuple(obligations),
        cohort_size=cohort,
        deep_read=deep,
        unreadable=unreadable,
        name_keyed=name_keyed,
        cohort=tuple(rows),
    )


def _row(matter, number, opened, client="A Client", responsible="R. Attorney"):
    return gate.CohortRow(
        matter_id=matter,
        number=number,
        title=f"{number} - {client}",
        clients=client,
        responsible=responsible,
        opened=opened,
    )


def _decide(pull, events=(), config=AUTHORED, today=TODAY, refire=3):
    return gate.decide(
        pull,
        config,
        ledger,
        list(events),
        raw_inputs_for_digest=b"x",
        today=today,
        refire_days=refire,
    )


def _event(matter_id, source_id, label, event, ts, attempt=1):
    return {
        "v": 2,
        "ts": ts,
        "skill": gate.SKILL_NAME,
        "matter_id": matter_id,
        "item_key": ledger.item_key(matter_id, source_id, label, None),
        "event": event,
        "attempt": attempt,
        "token": None,
        "id": f"id-{ts}",
    }


# --------------------------------------------------------------------- config


def test_unauthored_firm_facts_fail_closed_and_name_what_is_missing():
    decision = _decide(_pull([]), config=gate.CloseoutConfig())
    assert decision.wake is True
    assert decision.decision_basis == "closeout_config_unauthored_surface"
    assert decision.extra_metadata["missing"] == ["trigger_status", "chase_cadence_days"]


def test_a_partially_authored_config_is_still_unauthored():
    decision = _decide(_pull([]), config=gate.CloseoutConfig(trigger_status="Pending"))
    assert decision.wake is True
    assert decision.extra_metadata["missing"] == ["chase_cadence_days"]


def test_unauthored_stall_days_does_not_stop_the_chase():
    """stall_days degrades; it is not in the fail-closed class."""
    config = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14)
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 1200.0)]), config=config)
    assert decision.wake is True
    assert decision.decision_basis == "closeout_chase_due"
    assert decision.extra_metadata["stall_days_authored"] is False


def test_a_status_that_is_not_a_plain_token_is_treated_as_unauthored(tmp_path):
    """The status crosses into a subprocess environment, so it is shape-checked."""
    yaml = pytest.importorskip("yaml")
    doc = {
        "personas": [
            {
                "skills": [
                    {
                        "name": gate.SKILL_NAME,
                        "settings": {"trigger_status": "Pending; rm -rf /", "chase_cadence_days": 14},
                    }
                ]
            }
        ]
    }
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    config, _ = gate.load_closeout_config(str(path))
    assert config.trigger_status is None
    assert config.authored is False


def test_authored_config_round_trips_from_the_seat_file(tmp_path):
    yaml = pytest.importorskip("yaml")
    doc = {
        "escalation": {"refire_days": 5},
        "personas": [
            {
                "skills": [
                    {
                        "name": gate.SKILL_NAME,
                        "settings": {"trigger_status": "Pending", "chase_cadence_days": 14, "stall_days": 60},
                    }
                ]
            }
        ],
    }
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    config, refire = gate.load_closeout_config(str(path))
    assert (config.trigger_status, config.chase_cadence_days, config.stall_days) == ("Pending", 14, 60)
    assert config.authored is True
    assert refire == 5


# ------------------------------------------------------------------- grouping


def test_one_payer_across_two_matters_is_one_chase_naming_both():
    """The whole point: a payer on N matters gets one message, not N."""
    decision = _decide(
        _pull(
            [
                _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 21400.0),
                _obligation(MATTER_B, ENTITY_1, "Valley Health Plan", 8900.0),
            ]
        )
    )
    chases = [p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER]
    assert len(chases) == 1
    assert chases[0].matters == (MATTER_A, MATTER_B)
    assert chases[0].outstanding_total == 30300.0


def test_a_misspelled_payer_still_groups_with_its_correct_spelling():
    """Grouping is looser than identity on purpose: a typo is a separate contact
    record, so grouping falls back to the normalized name."""
    decision = _decide(
        _pull(
            [
                _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0),
                _obligation(MATTER_B, "9f77b512", "Valley Health Plan, Inc.", 200.0),
            ]
        )
    )
    chases = [p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER]
    assert len(chases) == 1, "corporate suffix and punctuation must not split the group"


def test_near_named_but_distinct_providers_do_not_group():
    decision = _decide(
        _pull(
            [
                _obligation(MATTER_A, "id-a", "Sierra Imaging", 100.0),
                _obligation(MATTER_B, "id-b", "Open Sierra Imaging", 200.0),
            ]
        )
    )
    chases = [p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER]
    assert len(chases) == 2, "two different businesses must not be merged into one chase"


def test_cleared_obligations_never_enter_a_chase_group():
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 0.0)]))
    assert decision.wake is False
    assert decision.decision_basis == "no_closeout_chase_due"


# ------------------------------------------------------------------- identity


def test_obligation_identity_survives_a_provider_name_correction():
    """The defect this design exists to avoid: correcting a typo must not orphan
    the obligation's history."""
    before = _obligation(MATTER_A, ENTITY_1, "Valley Helath Plan", 100.0)
    after = _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)
    assert gate.obligation_key(ledger, before) == gate.obligation_key(ledger, after)


def test_identity_does_not_move_when_a_sibling_plaintiff_is_removed():
    """plaintiff_index is an attribute, never part of the key."""
    first = _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0, plaintiff_index=1)
    renumbered = _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0, plaintiff_index=0)
    assert gate.obligation_key(ledger, first) == gate.obligation_key(ledger, renumbered)


def test_two_providers_on_one_matter_are_two_identities():
    a = _obligation(MATTER_A, "id-a", "Cedar Ridge Orthopedics", 100.0)
    b = _obligation(MATTER_A, "id-b", "Mercy General ER", 100.0)
    assert gate.obligation_key(ledger, a) != gate.obligation_key(ledger, b)


def test_the_same_provider_on_two_matters_are_two_identities():
    a = _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)
    b = _obligation(MATTER_B, ENTITY_1, "Valley Health Plan", 100.0)
    assert gate.obligation_key(ledger, a) != gate.obligation_key(ledger, b)


def test_the_name_fallback_keeps_the_raw_string():
    """No punctuation stripping and no suffix dropping in identity."""
    assert gate.fallback_provider_key("  Dr. Zawada, Inc. ") == "dr. zawada, inc."
    assert gate.fallback_provider_key("Dr Zawada") != gate.fallback_provider_key("Dr. Zawada")


def test_sentinel_namespaces_do_not_collide_with_a_sibling_skill():
    """item_key ignores label, so a shared source_id would share one identity."""
    ours = ledger.item_key("", gate._CONFIG_SENTINEL_SOURCE_ID, "sct-config-missing", "")
    theirs = ledger.item_key("", "__mrc_chase_config__", "sct-config-missing", "")
    assert ours != theirs


# -------------------------------------------------------------------- fencing


def test_a_held_matter_never_joins_a_chase_group():
    """Fencing is unconditional; re-surfacing the hold is on the window."""
    events = [_event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-18T00:00:00Z")]
    decision = _decide(
        _pull(
            [
                _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0),
                _obligation(MATTER_B, ENTITY_1, "Valley Health Plan", 200.0),
            ]
        ),
        events=events,
    )
    chases = [p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER]
    assert len(chases) == 1
    assert chases[0].matters == (MATTER_B,), "the held matter is fenced out of the group"
    assert chases[0].outstanding_total == 200.0
    assert not any(p.action == gate.ACTION_SURFACE_HOLD for p in decision.plans), (
        "the hold was raised yesterday; re-surfacing it today would be spam"
    )


def test_a_stale_hold_re_surfaces_once_its_window_has_passed():
    """A held matter must not go permanently dark on one missed notice (#1899)."""
    events = [_event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-01T00:00:00Z")]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    holds = [p for p in decision.plans if p.action == gate.ACTION_SURFACE_HOLD]
    assert len(holds) == 1
    assert holds[0].matter_id == MATTER_A
    assert not any(p.action == gate.ACTION_CHASE_PROVIDER for p in decision.plans)


def test_a_resolved_hold_stops_fencing():
    events = [
        _event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-01T00:00:00Z"),
        _event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "resolved", "2026-08-02T00:00:00Z"),
    ]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    chases = [p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER]
    assert len(chases) == 1 and chases[0].matters == (MATTER_A,)


# -------------------------------------------------------------------- cadence


def test_a_chase_inside_the_cadence_window_does_not_re_fire():
    events = [
        _event(
            "",
            gate.PROVIDER_SOURCE_PREFIX + "valley health plan",
            "sct-provider-chase",
            "chased",
            "2026-08-18T00:00:00Z",
        )
    ]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    assert decision.wake is False


def test_a_chase_past_the_cadence_window_carries_its_attempt_and_last_chased():
    events = [
        _event(
            "",
            gate.PROVIDER_SOURCE_PREFIX + "valley health plan",
            "sct-provider-chase",
            "chased",
            "2026-07-01T00:00:00Z",
        )
    ]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    chase = next(p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER)
    assert chase.attempt == 2
    assert chase.last_chased == "2026-07-01"


def test_a_stall_raise_does_not_inflate_the_chase_attempt_count():
    """Stalls live on their own matter-level sentinel for exactly this reason."""
    events = [
        _event(
            "",
            gate.PROVIDER_SOURCE_PREFIX + "valley health plan",
            "sct-provider-chase",
            "chased",
            "2026-07-01T00:00:00Z",
        ),
        _event(MATTER_A, gate.STALL_SOURCE_PREFIX + MATTER_A, "sct-stall", "fired", "2026-07-15T00:00:00Z"),
    ]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    chase = next(p for p in decision.plans if p.action == gate.ACTION_CHASE_PROVIDER)
    assert chase.attempt == 2, "the stall must not be counted as a chase"


# ------------------------------------------------------------------- coverage


def test_every_decision_reports_what_it_actually_looked_at():
    decision = _decide(
        _pull(
            [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)],
            cohort=165,
            deep=12,
            unreadable=1,
            name_keyed=2,
        )
    )
    meta = decision.extra_metadata
    assert meta["cohort_size"] == 165 and meta["deep_read"] == 12
    assert meta["unreadable"] == 1 and meta["name_keyed_obligations"] == 2


def test_a_cohort_with_no_readable_obligations_surfaces_rather_than_going_quiet():
    decision = _decide(_pull([], cohort=165, deep=12))
    assert decision.wake is True
    assert decision.decision_basis == "no_obligations_read_surface"


def test_an_empty_cohort_is_simply_quiet():
    decision = _decide(_pull([], cohort=0, deep=0))
    assert decision.wake is False
    assert decision.decision_basis == "no_closeout_chase_due"


# ---------------------------------------------------------------------- parse


def _wire(number: str):
    return json.loads((WIRE_DIR / f"{number}.json").read_text(encoding="utf-8"))


def _parse(number: str, cohort=6):
    raw = {"cohort": [{"id": f"m-{i}"} for i in range(cohort)], "layouts": {number: _wire(number)}, "layoutErrors": {}}
    pull, problem = gate.parse_pull(raw)
    assert problem is None, problem
    return pull


def test_parse_reads_the_modal_matter_from_the_committed_fixture():
    pull = _parse("2026-SC-201")
    assert len(pull.obligations) == 4
    assert all(o.id_source == "entity_id" for o in pull.obligations)
    assert pull.name_keyed == 0


def test_parse_keeps_multi_plaintiff_providers_apart():
    """Flattening the per-plaintiff items is the collapse this guards."""
    pull = _parse("2026-SC-203")
    assert len(pull.obligations) == 2
    assert {o.plaintiff_index for o in pull.obligations} == {0, 1}
    assert len({o.provider_key for o in pull.obligations}) == 2


def test_parse_of_the_empty_medicals_matter_yields_no_obligations():
    pull = _parse("2026-SC-202")
    assert pull.obligations == ()
    assert pull.deep_read == 1, "the matter WAS read; it simply holds nothing"


def test_parse_reads_a_real_zero_as_cleared_not_missing():
    pull = _parse("2026-SC-206")
    assert len(pull.obligations) == 2
    assert all(o.balance == 0.0 and not o.outstanding for o in pull.obligations)


def test_a_full_cohort_page_is_a_problem_not_a_view():
    raw = {"cohort": [{"id": f"m-{i}"} for i in range(500)], "layouts": {}}
    _, problem = gate.parse_pull(raw)
    assert problem and "truncated" in problem


def test_a_cohort_error_is_a_problem():
    _, problem = gate.parse_pull({"cohortError": "boom"})
    assert problem and "cohortError" in problem


def test_a_provider_row_with_no_entity_id_falls_back_and_says_so():
    item = {
        "layoutDesignId": "PersonalInjurySettlementDetailsItem",
        "parentIndex": 0,
        "values": [
            {"key": "Providers[0]/Provider/DisplayName", "value": "Nameless Clinic"},
            {"key": "Providers[0]/InvoiceBalance", "value": "500.00"},
        ],
    }
    pull, problem = gate.parse_pull({"cohort": [{"id": "m-1"}], "layouts": {MATTER_A: [item]}, "layoutErrors": {}})
    assert problem is None
    assert pull.name_keyed == 1
    assert pull.obligations[0].id_source == "display_name"


# ------------------------------------------------------- deep-read targeting


def test_open_obligations_are_recovered_from_the_ledgers_own_matter_field():
    """The key is a hash and cannot be reversed, so targeting reads matter_id."""
    events = [
        _event(MATTER_A, "sct:" + ENTITY_1, "sct-obligation", "fired", "2026-08-01T00:00:00Z"),
        _event(MATTER_B, "sct:" + ENTITY_1, "sct-obligation", "fired", "2026-08-01T00:00:00Z"),
        _event(MATTER_B, "sct:" + ENTITY_1, "sct-obligation", "resolved", "2026-08-05T00:00:00Z"),
    ]
    assert gate.matters_with_open_obligations(events) == [MATTER_A]


def test_targeting_ignores_another_skills_events():
    foreign = _event(MATTER_A, "x", "y", "fired", "2026-08-01T00:00:00Z")
    foreign["skill"] = "medical-records-chaser"
    assert gate.matters_with_open_obligations([foreign]) == []


def test_targeting_rejects_a_matter_id_that_is_not_id_shaped():
    bad = _event("../../etc/passwd", "sct:x", "sct-obligation", "fired", "2026-08-01T00:00:00Z")
    assert gate.matters_with_open_obligations([bad]) == []


# ------------------------------------------------------------------- register


REGISTERED = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14, stall_days=60, register_days=7)


def _register(obligations, rows, config=REGISTERED, cohort=None):
    pull = _pull(obligations, cohort=cohort if cohort is not None else len(rows), rows=rows)
    return gate.build_register(pull, config, TODAY)


def test_a_matter_whose_detail_was_not_read_shows_blank_never_zero():
    """The single most dangerous rounding in this feature: an unread matter that
    reports 0.00 reads as a cleared file."""
    reg = _register([], [_row(MATTER_A, "2026-SC-202", "2007-06-11")])
    row = reg["oldest"][0]
    assert row["detail"] == "not read"
    assert row["outstanding"] is None and row["obligations"] is None


def test_a_read_matter_with_nothing_owed_shows_a_real_zero():
    reg = _register(
        [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 0.0)],
        [_row(MATTER_A, "2026-SC-206", "2024-02-05")],
    )
    row = reg["oldest"][0]
    assert row["detail"] == "read"
    assert row["outstanding"] == 0.0, "read-and-clear is a zero, not a blank"


def test_the_cohort_is_ranked_oldest_first_and_says_so():
    reg = _register(
        [],
        [
            _row(MATTER_A, "new", "2024-01-01"),
            _row(MATTER_B, "old", "2007-06-11"),
        ],
    )
    assert [r["matter"] for r in reg["oldest"]] == ["old", "new"]
    assert "oldest opened first" in reg["ranking_rule"]


def test_coverage_states_how_much_of_the_set_was_actually_opened():
    reg = _register(
        [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)],
        [_row(MATTER_A, "read", "2020-01-01"), _row(MATTER_B, "unread", "2019-01-01")],
        cohort=165,
    )
    cov = reg["coverage"]
    assert cov["matters_at_status"] == 165
    assert cov["detail_read"] == 1
    assert cov["detail_not_read"] == 164


def test_the_register_names_what_it_could_not_see():
    reg = _register([], [_row(MATTER_A, "m", "2020-01-01")])
    joined = " | ".join(reg["unavailable"])
    assert "quiet time" in joined, "the matter record carries no last-activity field"
    assert "trust ledger" in joined, "trust balances are not in the practice-management system"


def test_an_unauthored_stall_threshold_is_declared_not_defaulted():
    config = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14)
    reg = _register([], [_row(MATTER_A, "m", "2020-01-01")], config=config)
    assert any("stall" in u for u in reg["unavailable"])


def test_an_unauthored_register_cadence_is_declared_and_the_register_still_appears():
    config = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14)
    reg = _register([], [_row(MATTER_A, "m", "2020-01-01")], config=config)
    assert any("periodic cadence" in u for u in reg["unavailable"])
    assert reg["oldest"], "the register still renders; only its cadence is unauthored"


def test_providers_are_ranked_by_exposure_across_matters():
    reg = _register(
        [
            _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 21400.0),
            _obligation(MATTER_B, ENTITY_1, "Valley Health Plan", 8900.0),
            _obligation(MATTER_A, "id-c", "Cedar Ridge Orthopedics", 18400.0),
        ],
        [_row(MATTER_A, "a", "2021-01-01"), _row(MATTER_B, "b", "2022-01-01")],
    )
    top = reg["providers_by_exposure"][0]
    assert top["provider"] == "Valley Health Plan"
    assert top["matters"] == 2 and top["outstanding"] == 30300.0


def test_a_cleared_provider_does_not_occupy_the_exposure_ranking():
    reg = _register(
        [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 0.0)],
        [_row(MATTER_A, "a", "2021-01-01")],
    )
    assert reg["providers_by_exposure"] == []


def test_the_recorded_total_counts_only_what_was_read():
    reg = _register(
        [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 1234.56)],
        [_row(MATTER_A, "read", "2021-01-01"), _row(MATTER_B, "unread", "2020-01-01")],
    )
    assert reg["recorded_outstanding_total"] == 1234.56


def test_the_register_slices_are_bounded():
    rows = [_row(f"m-{i}", f"n-{i}", "2020-01-01") for i in range(60)]
    reg = _register([], rows)
    assert len(reg["oldest"]) == gate.REGISTER_TOP_N


def test_a_periodic_register_wakes_on_its_own_cadence():
    decision = _decide(_pull([], cohort=0, deep=0), config=REGISTERED)
    assert decision.wake is True
    assert any(p.action == gate.ACTION_EMIT_REGISTER for p in decision.plans)


def test_the_periodic_register_does_not_re_fire_inside_its_window():
    events = [_event("", gate.REGISTER_SOURCE_ID, "sct-register", "fired", "2026-08-18T00:00:00Z")]
    decision = _decide(_pull([], cohort=0, deep=0), events=events, config=REGISTERED)
    assert decision.wake is False


def test_without_an_authored_cadence_there_is_no_periodic_register_wake():
    config = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14)
    decision = _decide(_pull([], cohort=0, deep=0), config=config)
    assert decision.wake is False, "no invented reporting cadence"


def test_a_waking_decision_carries_the_register_payload():
    decision = _decide(
        _pull(
            [_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)],
            rows=[_row(MATTER_A, "2026-SC-201", "2021-03-15")],
        ),
        config=REGISTERED,
    )
    assert decision.wake is True
    assert decision.register is not None
    assert decision.register["coverage"]["detail_read"] == 1


def test_names_are_taken_from_the_record_never_composed():
    assert gate._names([{"displayName": "Dean Halverson"}]) == "Dean Halverson"
    assert gate._names([{"id": "x"}]) == "", "an unnamed entry contributes nothing"
    assert gate._names([{"name": "A"}, {"displayName": "B"}]) == "A, B"


def test_the_register_reads_the_committed_wire_fixtures_end_to_end():
    raw = {
        "cohort": [
            {
                "id": "m-201",
                "number": "2026-SC-201",
                "title": "t",
                "clients": [{"displayName": "Dean Halverson"}],
                "personResponsible": [{"displayName": "R. Attorney"}],
                "openedDate": "2021-03-15",
            },
            {
                "id": "m-202",
                "number": "2026-SC-202",
                "title": "t",
                "clients": [{"displayName": "Adaeze Okonkwo"}],
                "personResponsible": [{"displayName": "R. Attorney"}],
                "openedDate": "2007-06-11",
            },
        ],
        "layouts": {"m-201": _wire("2026-SC-201")},
        "layoutErrors": {},
    }
    pull, problem = gate.parse_pull(raw)
    assert problem is None
    reg = gate.build_register(pull, REGISTERED, TODAY)
    assert reg["coverage"] == {
        "matters_at_status": 2,
        "detail_read": 1,
        "detail_not_read": 1,
        "unreadable": 0,
        "obligations": 4,
        "name_keyed_obligations": 0,
    }
    oldest = reg["oldest"][0]
    assert oldest["matter"] == "2026-SC-202" and oldest["detail"] == "not read"
    assert oldest["client"] == "Adaeze Okonkwo"
    assert reg["largest_recorded_exposure"][0]["matter"] == "2026-SC-201"


# ------------------------------------------- decision summary (found on seat)


def test_a_register_only_wake_is_not_reported_as_a_held_matter():
    """Found by running the gate on the real seat: the hold count was derived by
    subtracting chases from the total, so the periodic register counted itself as
    a held matter, in a number the audit heartbeat keeps."""
    decision = _decide(_pull([], cohort=0, deep=0), config=REGISTERED)
    assert decision.wake is True
    assert decision.extra_metadata["hold_surface_due"] == 0
    assert decision.extra_metadata["register_due"] == 1
    assert decision.extra_metadata["provider_chases_due"] == 0


def test_a_register_only_wake_says_so_in_its_basis():
    decision = _decide(_pull([], cohort=0, deep=0), config=REGISTERED)
    assert decision.decision_basis == "closeout_register_due"


def test_a_hold_only_wake_says_so_in_its_basis():
    events = [_event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-01T00:00:00Z")]
    config = gate.CloseoutConfig(trigger_status="Pending", chase_cadence_days=14, stall_days=60)
    decision = _decide(
        _pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]),
        events=events,
        config=config,
    )
    assert decision.decision_basis == "closeout_hold_surface_due"
    assert decision.extra_metadata["hold_surface_due"] == 1


def test_a_chase_takes_precedence_in_the_basis():
    decision = _decide(
        _pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]),
        config=REGISTERED,
    )
    assert decision.decision_basis == "closeout_chase_due"
    assert decision.extra_metadata["provider_chases_due"] == 1
    assert decision.extra_metadata["register_due"] == 1, (
        "the register still rides along; it simply is not what the wake is called after"
    )


def test_every_plan_action_is_counted_by_itself():
    """No count is derived by subtracting another from the total."""
    events = [_event(MATTER_B, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-01T00:00:00Z")]
    decision = _decide(
        _pull(
            [
                _obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0),
                _obligation(MATTER_B, "id-b", "Cedar Ridge Orthopedics", 200.0),
            ]
        ),
        events=events,
        config=REGISTERED,
    )
    meta = decision.extra_metadata
    counted = meta["provider_chases_due"] + meta["hold_surface_due"] + meta["register_due"]
    assert counted == len(decision.plans), "every plan is accounted for by its own action"


# ---------------------------------------------------------------------------
# Pre-run handoff (ss#2547)
# ---------------------------------------------------------------------------
# The same block every bespoke pre_run carries. This gate emits no authored
# record date today (a chase plan carries balances and matter ids, not dates),
# and the projection is asserted to be empty for exactly that reason: an empty
# `dates` list is the honest reading of what the wake line said, and the day
# this gate starts emitting a date the assertion below starts failing rather
# than silently seeding nothing.

_HANDOFF_KEYS = {"skill", "started_at", "dates", "matter_ids"}


def _authored_dates_in(node, found=None) -> list:
    """Every authored_date in the wake payload, first-seen order.

    Written out again here rather than calling the module's own walker: a test
    that reuses the projection it is checking agrees with that projection's bugs.
    """
    if found is None:
        found = []
    if isinstance(node, dict):
        value = node.get("authored_date")
        if isinstance(value, str) and value and value not in found:
            found.append(value)
        for child in node.values():
            _authored_dates_in(child, found)
    elif isinstance(node, list):
        for child in node:
            _authored_dates_in(child, found)
    return found


def _wake_stdout(capsys) -> str:
    """A stale-hold wake, chosen because its plan carries a per-matter id.

    A payer-grouped chase plan deliberately carries `matter_id=""` and puts its
    matters under `matters`, so projecting it would prove nothing about whether
    the projection works.
    """
    events = [_event(MATTER_A, gate.HOLD_SOURCE_ID, "sct-chase-hold", "fired", "2026-08-01T00:00:00Z")]
    decision = _decide(_pull([_obligation(MATTER_A, ENTITY_1, "Valley Health Plan", 100.0)]), events=events)
    assert decision.wake is True
    assert gate._emit_wake(decision) == 0
    return capsys.readouterr().out.strip()


def _handoff_path(home) -> pathlib.Path:
    return pathlib.Path(home) / ".smd" / "pre_run" / "lien-ledger-tracker.json"


def test_the_wake_writes_a_handoff_projecting_what_it_emitted(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = json.loads(_wake_stdout(capsys))
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert record["dates"] == _authored_dates_in(payload) == []
    assert record["skill"] == "lien-ledger-tracker"
    assert record["matter_ids"] == [MATTER_A]


def test_the_handoff_carries_nothing_but_the_projection(tmp_path, monkeypatch, capsys) -> None:
    """Provider names, balances, item keys and prose never reach the register."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _wake_stdout(capsys)
    record = json.loads(_handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert set(record) == _HANDOFF_KEYS
    assert record["started_at"].endswith("Z")
    datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))


def test_a_handoff_write_failure_leaves_stdout_byte_identical(tmp_path, monkeypatch, capsys) -> None:
    """HERMES_HOME is a FILE, so the write fails for any uid. A read-only
    directory would still be writable by root, and CI containers run as root."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    good = _wake_stdout(capsys)
    assert _handoff_path(tmp_path).exists()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(blocked))
    assert _wake_stdout(capsys) == good
