# conflict-intake-router algorithm

Source of truth for catching a conflict without ever clearing one.

## Ordering (single matter)

```
parties        = capture_party_graph(matter)        # capture-rubric.md - wide
for each party:
    contacts   = get_contacts(party.name)           # Smokeball, read-only
    matters    = list_matters(contactId)            # Smokeball, read-only (incl. isLead for open leads)
hits           = [p for p in parties if adverse(p, existing book)]   # adversity tests
if hits:
    for h in hits:
        packet = { party, existing_matter, nature_of_adversity, responsible_attorney }
        route(packet -> responsible_attorney_or_named_clearance_human)
    surface(matter -> CONFLICT-HOLD)                 # no downstream advance
else:
    surface(matter -> "no conflict detected; clearance pending")   # NOT cleared
```

The cross-check is **read-only and runs before any routing or surfacing**. There is no branch in which the skill writes a clearance.

## Resolving the responsible attorney

The packet routes to the owner of the _conflicting existing matter_ so the right person decides. Per `operator/verticals/law-firm/smokeball-surface.md`, the responsible attorney is returned directly on the matter as `personResponsibleStaffId` (no field-widening needed); resolve it to a name via `get_staff`. If it is absent or cannot be resolved, route to the firm's named conflict-clearance human - **never** drop to a generic queue and never to a wedge skill.

## Cadence re-scan (delta logic)

```
prev_pairs = last_scan.adverse_pairs            # persisted set of (party_a, party_b) already surfaced/cleared
cur_pairs  = compute_adverse_pairs(open_matters)
new_pairs  = cur_pairs - prev_pairs             # only newly-emerged adversity
surface(new_pairs)                              # re-route only the new ones
```

Dedup against the **already-surfaced/cleared** set, not against confirmed-conflicts - otherwise a human-cleared pair re-surfaces every scan and the firm learns to ignore the signal. A pair becomes new when a later matter brings in the other side of an existing party.

## Honest limits

- The check is name/entity matching over Smokeball contacts and matters. It will miss a conflict whose party is recorded under a different name/spelling, or not recorded at all - capture width (capture-rubric.md) is the main defense, and even then this is a surfacing aid, not a guarantee.
- It cannot judge waivability, materiality, or distance of a relationship. Those are clearance judgments; the skill stops at "possible conflict - route to human."
