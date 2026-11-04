## What the business gets

A ranked, file:line map of every place in Operator job processing where the model still carries logic that code should own (counts, page ranges, matter numbers, money and date parsing, arithmetic, status derivation, reply composition), and the design that closes each class rather than the last observed symptom: ADR 0104, the six mechanisms, approved 2026-11-04 and being built by four Opus agents (wave 1: #3741 `token-sdk`, then `fixed-wording`, `connector-values`, `writes-and-conflict`).

## What is in it

- `docs/reviews/model-as-logic-audit-2026-11-04.md`: the pattern in four shapes; 8 Tier A findings on the live client path (post-intake record fields, matter writes with no resolution token, connector money/date parsing by the model, demand specials and expiry, the demand digest as sole record, defendant status asserted without events, delivered-job replies, the conflict halt); 10 Tier B; 7 cost rows; what is already clean; six general mechanisms; an order.
- `docs/reviews/model-as-logic-audit-2026-11-04-agent-reports.md`: the five read-only audit reports verbatim.
- `docs/adr/0104-logic-in-code-six-mechanisms.md`: the six mechanisms, the conflict hit rule table, the proof ladder per lane, the three-wave build order, the residual model inputs and the rejected alternatives. `docs/adr/index.md` entry; `docs/handbook/adr-index.md` regenerated with `npm run handbook:adr-index -- --write`.
- `docs/reviews/index.md`: two entries.

Every Tier A and Tier B line was re-read first-hand at the cited lines after the agents reported; two items are marked as inferred from code, not observed on a seat. Drafting, records and Law 18 are peers' work in flight and are not re-audited.

## Tests

`npx vitest run tests/adr-integrity.test.ts tests/handbook-integrity.test.ts tests/docs-facts.test.ts tests/forbidden-strings.test.ts`: 4 files, 435 passed. Docs only; no seat artifact.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01WxHZSoHD5KPopoAr9CpPSF
