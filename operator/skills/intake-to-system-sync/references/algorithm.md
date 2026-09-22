# intake-to-system-sync algorithm

Source of truth for moving a converted lead into Smokeball without duplicates, conflicts, or invented data.

## When this skill is even live

Only when `customer.yaml` binds an **IntakeCRM** connector (`build:clio-grow` or similar - the Clio Grow intake CRM product, the lead front-end, distinct from the PM connector). If Smokeball is the single system of record (the pilot), there is no separate front-end to sync from and the skill stays disabled. This is a configuration fact, not a runtime guess - the skill does not invent a CRM to read.

## Ordering (the invariant comes first)

```
lead = read converted-unsynced lead from IntakeCRM
parties = lead.client + captured parties

# 1. CONFLICT CROSS-CHECK FIRST - before any matter is proposed
hits = conflict_cross_check(parties)          # same read-only check as new-matter-intake
if hits:
    route(hits -> human conflict clearance)
    HALT                                        # no matter proposal past a hit

# 2. DEDUPE
match = find_existing(lead.client)            # get_contacts + list_matters
if match:
    propose link-to-existing                   # never create a duplicate
else:
    propose new contact + matter

# 3. MAP fields (CRM schema -> Smokeball), missing stays empty
# 4. DRAFT proposed records + CRM sync-back
# 5. SURFACE for review; Smokeball create + CRM mark-synced are GATED
```

Conflict-check precedes dedupe and create because advancing a conflicted lead is the gravest failure; a duplicate is recoverable, a missed conflict is not.

## Dedupe rule

A "match" is a confident identity match on the lead's client against existing Smokeball contacts (name + a corroborating field - email/phone). On a match, propose **linking** the new matter to the existing client (or flagging a likely-existing matter) rather than minting a duplicate. A weak/ambiguous match is surfaced for a human to decide - not silently merged and not silently duplicated.

## Field map (CRM → Smokeball)

```
lead.client_name      -> contact (Person/Company)
lead.email / phone    -> contact channels
lead.matter_summary   -> matter.description
lead.practice_area    -> matter.matterTypeId (only if the CRM captured it; resolve to a Smokeball matter type)
lead.parties          -> captured for the conflict check; recorded as authored
```

Fields the CRM did not capture are left empty. The skill never fills a plausible practice area or a guessed party - empty is honest; invented is a fabrication breach.

## Fail-closed writes

Both sides are gated this phase: the Smokeball contact/matter **create** and the CRM **mark-synced** wait for human review. A premature mark-synced would orphan a lead that never actually landed in Smokeball, so the sync-back only fires after the Smokeball create is confirmed.
