---
description: Dan Brown's Eight Principles of Information Architecture and how nav-spec applies them. Use as the review rubric in Phase 7 of the authoring workflow.
---

# IA Principles

Dan Brown's eight principles, from _Eight Principles of Information Architecture_ (ASIS&T Bulletin, 2010), are the canonical framework for evaluating an IA. `nav-spec` uses them as the rubric for the IA architect reviewer in Phase 7 of the authoring workflow.

Each principle below has a one-sentence definition (the source), an application guide (how `nav-spec` applies it), and a check (what question the reviewer asks of a spec).

---

## 1. Principle of Objects

> "Treat content as a living, breathing thing with a lifecycle, behaviors, and attributes."

**Application.** Before writing an IA, name the _objects_ (entities) the venture manipulates — Proposal, Invoice, Engagement, Client, Consultant, Milestone. Each object has:

- **Attributes** — fields users see and act on
- **Lifecycle states** — draft, sent, accepted, paid, expired, archived
- **Behaviors** — what can be done to it, and by whom

The IA map is derived from objects, not from pages. "Pages" are views of objects in particular states.

**Check:** Does the spec's task model and sitemap cleanly decompose into objects, object-states, and object-actions? Are state transitions reflected in URL/IA?

---

## 2. Principle of Choices

> "Create pages that offer meaningful choices to users, keeping the range of choices available focused on a particular task."

**Application.** Each surface in the IA has a small, focused choice set. If a surface presents >7 equally-weighted next actions, it's likely two surfaces masquerading as one. Split.

**Check:** For every surface in the reachability matrix, list the primary + secondary choices. If the list is long or unranked, the surface is overloaded.

---

## 3. Principle of Disclosure

> "Show only enough information to help people understand what kinds of information they'll find as they dig deeper."

**Application.** The IA uses progressive disclosure. Dashboards surface summaries; detail surfaces carry the full payload. Lists show title + status + one number; detail shows the rest.

**Check:** Does the spec define what each surface discloses vs. withholds? Are list items restrained (no full-payload rendering on the index)?

---

## 4. Principle of Exemplars

> "Describe the contents of categories by showing examples of the contents."

**Application.** Labels in the IA show what's inside by example when text alone is ambiguous. "Documents (SOW, meeting notes, deliverables)" is more useful than "Documents" in isolation.

**Check:** Does the taxonomy carry exemplars where labels are ambiguous? Category names should resolve to concrete mental models.

---

## 5. Principle of Front Doors

> "Assume at least half of the website's visitors will come through some page other than the home page."

**Application.** Every surface must work as a front door. Token-auth landings, email deep-links, search-engine entries, bookmarked detail pages — none should assume the user arrived via a linear path. Every surface has self-explanatory title, context, and exits.

**Check:** For every surface, answer: "If the user arrives here cold with no prior context, do they know where they are, what this is, and what they can do next?" If not, the surface fails front-door.

---

## 6. Principle of Multiple Classifications

> "Offer users several different classification schemes to browse the site's content."

**Application.** Users have different mental models. The IA supports multiple entry paths to the same object:

- By time (Recent Activity)
- By type (Invoices, Proposals, Documents)
- By status (Pending, Accepted, Expired — filter facets)
- By context (Engagement-scoped)

**Check:** Can every object be reached by ≥2 classification schemes? Are multiple paths designed, not accidental?

---

## 7. Principle of Focused Navigation

> "Don't mix apples, oranges, and aardvarks in your navigation scheme."

**Application.** Each navigation affordance groups semantically related items. Don't mix "Proposals, Invoices, Documents" (object-kind) with "Account, Help, Log out" (utilities) in the same visual group. Use sections, dividers, or separate chrome regions.

**Second application (v3).** Focused navigation also governs pattern selection: the chosen pattern must support the primary task sequences the user performs, not the designer's aesthetic preference. A pattern that forces cross-section traversal through a hub when the task model declares external termini is unfocused — it serves the hub's sense of place over the user's task. R25 (see [pattern-disqualifiers.md](pattern-disqualifiers.md)) operationalizes this: disqualifiers fire when a declared pattern contradicts the task model's declared `return_locus` distribution. Phase 7's IA architect reviewer should treat pattern ↔ task-model coherence as an application of this principle.

**Check:** Does each navigation affordance have a single semantic category? Are utilities separated from object-navigation? Does the chosen pattern (§4 of the spec) match the `return_locus` distribution of the top-3-by-frequency tasks?

---

## 8. Principle of Growth

> "Assume the content you have today is a small fraction of the content you will have tomorrow."

**Application.** The IA is designed for 10x the current content without restructuring. Patterns (hub-and-spoke, faceted) scale; hardcoded nav lists don't. Labels are category-level, not item-level. URLs use stable slugs that survive renames.

**Check:** If this venture triples its destinations (new engagement types, new object classes), does the IA absorb them without rewrite? Or does it collapse?

---

## Review checklist

When running the IA architect reviewer in Phase 7 of `author.md`, the reviewer evaluates the draft against all eight principles and reports pass/fail per principle with specific evidence:

```
## IA principles review

1. **Objects** — <pass/fail> — <evidence>
2. **Choices** — <pass/fail> — <evidence>
3. **Disclosure** — <pass/fail> — <evidence>
4. **Exemplars** — <pass/fail> — <evidence>
5. **Front doors** — <pass/fail> — <evidence>
6. **Multiple classifications** — <pass/fail> — <evidence>
7. **Focused navigation** — <pass/fail> — <evidence>
8. **Growth** — <pass/fail> — <evidence>

## Critical fixes (ranked)
...
```

A spec with 3+ principle failures is returned to Phase 3 (IA model) before proceeding. A spec with 1–2 failures can proceed after decision-round fixes in Phase 8.
