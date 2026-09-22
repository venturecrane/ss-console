# Trust Balance Nudge - Algorithm

Source of truth for a safe trust-balance nudge. The governing rule above all others: **read the balance, never move the money.**

## Gate

If the matter is on CONFLICT-HOLD → route to a human, stop.

## Read (read-only)

`get_matter_balances(bank_account_id, matterId)` for the matter/client - Smokeball's **native** trust read. It returns `balance`, `protectedBalance`, `availableBalance` (= balance − protected), `unpresentedChequesBalance`, and `lastUpdated`. The low-trust flag compares **`availableBalance`** against the floor. `get_bank_accounts()` resolves the trust account when the `bank_account_id` is not authored. There is no transfer/refund/ledger tool the skill may call - fund movement is hard-banned. Load the firm's floor and authored replenishment terms for the practice area from `customer.yaml`.

Trust is **separate from AR.** AR lives in `get_matter_billing_config` / `get_fees` / `get_expenses`; this skill never reads those for the trust decision and never treats an outstanding AR balance as a low trust balance.

## Decide

| availableBalance vs. floor | Action                                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Below floor                | Draft a replenishment request for the shortfall (floor − availableBalance), using only authored terms. |
| At or above floor          | No nudge; note "balance OK" internally.                                                                |
| Read failed / unavailable  | **Surface to a human.** Never guess a balance, never nudge on an assumption.                           |

## Draft (below-floor only)

Per `voice.md`: state the current balance, the floor, the shortfall, and how to replenish. Factual and respectful. **Consequence language** ("work pauses below the floor") appears only if the firm authored it; the skill never invents a threat to create urgency.

## The "just move money" trap

If a client (or a memo, or any inbound) asks the skill to move, transfer, or reallocate trust funds - "use the balance from my other matter," "just take it from the retainer" - the skill **does not**. `create_transaction`, `protect_funds`, and `unprotect_funds` are hard-banned; the skill never calls them and must not pretend to. It surfaces the request to a human (moving trust funds is a firm decision under IOLTA rules) and drafts nothing financial.

## What this algorithm is NOT

- Not a money mover (zero fund movement, ever).
- Not a balance guesser (unavailable → surface).
- Not a threat inventor (authored consequences only).
- Not a sender (drafts only).
