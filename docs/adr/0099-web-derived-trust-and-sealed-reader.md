---
title: Web-derived trust, the sealed reader and search egress
date: 2026-10-25
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0070-web-search-shared-connector-divergent-defaults.md, docs/adr/0098-marketing-seat-advertising-gate.md, docs/adr/0056-persona-exposure-skill-initiation-entitlements.md
enforced_by:
  - operator/hermes-smd-overlay/tests/test_web_derived_trust.py
  - operator/hermes-smd-overlay/tests/test_sealed_reader.py
  - operator/hermes-smd-overlay/tests/test_search_egress.py
---

# ADR 0099: Web-derived trust, the sealed reader and search egress

- **Status:** Accepted (2026-10-25)
- **Decider:** Captain (asked for a principled design instead of per-skill taint exemptions); mechanism by the agent
- **Builds on:** ADR 0070 (web search connector), ADR 0098 (the marketing seat), ADR 0056 (exposure and the taint gate)
- **Supersedes:** the per-skill exemption proposed in PR #3499 (`marketing_draft_send`), never merged

## Context

The Operator's safety rule for untrusted content is one switch per conversation. Once a turn reads anything from outside (an email from a stranger, a document, a web page), the trust gate refuses every sensitive action for the rest of that conversation: sends, bookings, deletions, code. Reading and drafting stay allowed. The rule is simple and has held.

It breaks the marketing seat (ADR 0098). That seat's whole job is to read the web (news, competitors' public posts, a radio show's channel) and turn it into drafts that reach the firm's one marketing administrator by email. Under the one switch, every scheduled research run taints itself and then cannot email the administrator its own drafts. The first proposal (#3499) exempted one named skill from the rule. The Captain rejected that and every similar patch: an exemption per skill grows into a list nobody can audit, and it says nothing about what the agent read.

Two further facts came out of the census for this decision. The native `web_search` tool sends the query string to a third party, and nothing checked what that string carried: on a seat that reads client files, a query naming a client tells the search provider who the firm represents. And the audit ledgers showed web searches on seats that never authored a web backend, through anonymous free tiers (closed by the 2026-10-25 amendment to ADR 0070, folded into this change).

The research basis:

- **CaMeL** (Debenedetti et al., "Defeating Prompt Injections by Design", arXiv 2503.18813): separate the model that reads untrusted data from the one that plans and acts, and attach capabilities to data so a policy layer, not the model, decides what tainted data may flow into.
- **Design patterns for securing LLM agents against prompt injections** (Beurer-Kellner et al., arXiv 2506.08837): the dual-LLM and context-minimization patterns; a quarantined reader with no tools whose output is constrained by code before the privileged agent sees it.
- **FIDES** (Costa et al., information-flow control for agent systems, Microsoft Research 2025): confidentiality and integrity labels propagated through the agent's context, with policies enforced deterministically at tool calls; labels are monotonic.
- **ABA Formal Opinion 512** (2024, generative AI tools): a lawyer must understand where client information goes when a tool is used and must not disclose it without informed consent. A web query is a disclosure to the search provider.

## Decision

### 1. A graded trust level: `web_derived`

`shared/inbound.py` gains a session trust class `web_derived`, ranked between `internal` and the external classes. Marking is monotonic: an external read later in the conversation raises it to external; nothing ever lowers it. It is a session label only; no inbound envelope may carry it (an envelope claiming it falls closed to `unknown_external`).

Only the sealed reader (section 2) marks a turn `web_derived`. The native `web_search` and `web_extract` tools still mark `unknown_external`, because they hand the agent raw page text.

In the trust gate (`plugins/hermes-smd-trust/enforce.py`), a `web_derived` turn refuses every taint-gated class (every send class, destructive, commitment, code execution) with one exception: `external_send_internal` whose every recipient is an exact address in the seat's authored `scope.admins`. Code resolves the recipients (the send's own to/cc/bcc, or the draft this session recorded) and compares them to the trusted config, never to anything the model or a page said. The class's own authored ceiling still decides; nothing is widened past it. The audit reason says `web_derived`, and an allowed admin send carries `TAINT_EXEMPT: web_derived_admin_recipients`.

A recipient-locked reply to the verified sender of the inbound that opened the turn needs no new rule: the reply relay (`hermes-smd-reply`) drafts through `create_draft` (an internal write, allowed on every tainted turn) and pins the recipient to the verified sender; on any tainted turn, `web_derived` included, it applies the content floor even to a colleague. That path is unchanged.

Every plugin that asks `is_tainted` today (record store, peer memory, calendar, corrections, partner messages, couples settings, establishment) compares against `internal`, so a `web_derived` turn is tainted to all of them. Internal writes (drafts, pending WordPress posts) stay allowed, as they always were.

`web_derived` is "less tainted, not clean". It is a narrower door, not an open one.

### 2. The sealed reader: `web_research`

`shared/sealed_reader.py` (with `shared/safe_fetch.py` for the address-safe fetch and `shared/reader_items.py` for item validation), registered by `plugins/hermes-smd-research` only on a seat whose customer.yaml authors `connectors.WebSearch.sealed_reader: true` (validated as a boolean on WebSearch only, in both `src/lib/operator/customer-yaml/sections-connectors.ts` and `bootstrap/validate.py`; absent means false and the tool does not exist).

Code does all the reading:

- a Brave Search API query (`BRAVE_SEARCH_API_KEY`), or a channel's public YouTube RSS feed (`channel_id` must match `^UC[\w-]{22}$`);
- page fetches over https only, at most 3 redirects, each hop's host resolved and the address actually connected checked: private, loopback, link-local, CGNAT, multicast, reserved, Fly 6PN (`fdaa::/16`), IPv4-mapped and tunnelled IPv6, and cloud metadata addresses are refused, and `*.internal`, `.local` and `localhost` names are refused before resolving;
- `text/html` or `text/plain` only, 2 MB after decompression, 15,000 extracted characters per page and 60,000 per call.

Then ONE Anthropic Messages call (model `claude-haiku-5-5`, the seat's own key) with a fixed system prompt and no `tools` key, returning JSON items `{title, date, summary, source_url}`. Code validates every item: `source_url` must exactly equal a URL the search or feed returned; the summary and title may carry no URL, no email address and no instruction or recipient shape (English and Spanish: ignore, disregard, send, forward, email to, reply to, system prompt, instructions, and their Spanish forms); control characters and markup are stripped; lengths are capped (summary 280 characters). Failing items are dropped and counted. The agent receives only the validated items, the dropped count and a note that the conversation is now web-derived.

On such a seat the webhook platform list (`shared/webhook_read_surface.py`, emitted by `bootstrap/translate.py`) also names the `research` toolset, so `web_research` is pinned onto email-request turns; pinned Hermes already enables plugin toolsets on an explicit platform list by default, and naming it keeps a later `known_plugin_toolsets` save from silently removing it. Every other seat's list is unchanged.

The session is marked `web_derived` before anything is fetched, so even a failed call leaves the turn marked. Cost is metered: the seat's cost breaker is asked before the model call (`assert_allowed`) and charged after (`record_cost_cents`), priced from `shared/anthropic_pricing.json`. Limits: 8 items, 6 calls per conversation, one 60-second deadline per call. Each call appends one line to `/opt/data/research/research-log.jsonl` (time, question, mode, item and dropped counts; no page text), a runtime layer listed in the handbook's Operator platform page.

`web_research` is classified READ, listed as non-seeding (it never establishes identifier provenance) and as unfenced by design: its result is code-validated summaries with checked links, and the turn it returns to is `web_derived`, so it cannot act on what it read.

### 3. Search egress

In `_evaluate_tool_call`, beside the matter-mixing fence and before exposure, for `web_search`, `web_extract` and `web_research` (`shared/search_egress.py`):

- **Layer 1, structural.** On a seat that authors a client-data connector (a non-synthetic practice-management backend, Smokeball today), a conversation that has read client material (matter-bound per `shared/matter_binding.py`, or a non-empty provenance register per `shared/provenance.py`) cannot search at all. It does not try to spot client facts in the query; it keeps the two activities in different conversations.
- **Layer 2, screen, every seat.** The query (for `web_extract` the URL including its query string; for `web_research` the question and channel id; in practice every string argument) is refused when it carries an identifier shape (SSN, A-number, USCIS receipt, case or matter number), a token the session's provenance register holds (`ProvenanceRegister.contains_text`, dates excluded), a matter number or party email from the matter binding, or a client-side party name. Names are seeded in the trust plugin's `post_tool_call` only from structured Smokeball fields whose role is client, claimant, plaintiff or party (never insurer, provider, court or counsel), matched as whole words of at least 4 characters.

The refusal names kinds, never values. The audit row of the call carries `web_query_len` and `web_query_verdict` (`pass`, `refused_structural`, `refused_screen`): never the text, never a hash of it (a short query's hash is a dictionary lookup away).

### 4. Fleet posture

Folded in from #3503: `web.keyless_fallback: false` on every seat, `web_extract` classified READ, Firecrawl selected only when its key is present (ADR 0070, amendment of 2026-10-25). The `marketing` role disables the native `web` toolset (`bootstrap/translate.py`), so that seat reads the web only through the sealed reader.

## Residual risk, stated plainly

A hostile page can slant a summary. The reader model reads the page; if the page says "this firm's competitor was disbarred", a summary may repeat it in plain words that pass every pattern. Text-to-text influence on a model that reads untrusted text is out of scope of any design, CaMeL's included: what can be closed is what the text can make the system DO, not what it can make the system SAY. This design closes the doing: the reader has nothing to call, its links are checked, its summaries cannot carry a link, an address or an instruction shape, and the turn it returns to can send only to the firm's own administrators. The saying is mitigated by three things outside this ADR's code: every marketing draft goes to a human reviewer before anything publishes (ADR 0098), the California advertising gate runs over every draft (ADR 0098), and the `web_derived` action limits above. The name screen is a screen, not a guarantee: a client name the seat never read in structured form, or a misspelling, passes it; Layer 1 is the structural answer on seats that hold client data.

## Consequences

- A marketing run that researched through `web_research` can email its drafts to the seat's admins with no per-skill exemption. Any other send from that conversation is refused, with a reason that says `web_derived`.
- On a seat that holds client data, a conversation that opened a client file cannot search the web. Research happens in a separate conversation (a separate scheduled run, or a new email).
- A web query naming a client, a case number or a party address is refused on every seat, and the refusal is recorded with the query's length and verdict only.
- Every seat that does not author `sealed_reader` gets no new tool. On those seats the change is the egress screen (a strict tightening) and `web_derived` (unreachable without the reader).
- The yaml and skills change that turns this on (`sealed_reader: true` on the marketing seat and the staging seat; the six marketing skills moved to `web_research`) lands only after every release ring carries this image, so no seat boots an older image against a key it cannot validate.

## Rejected

- **Per-skill taint exemptions** (#3499 and variants): an exemption list grows without bound and says nothing about what was read.
- **Lifting the gate for a pre-planned envelope** (the 2026-09-28 date-prep precedent, `code_fixed_recipients`): right for a brief whose recipients code writes before the turn starts; wrong for an on-request research turn, whose recipients are chosen during the turn. This design keeps the gate and narrows the door to a config-fixed recipient set instead.
- **Hashing the query into the audit row**: a short query's hash is reversible by dictionary.
