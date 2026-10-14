# DealGuard — Escrowed Dispute Arbitration for Agentic Commerce

> **Funds are really escrowed by the contract and really paid out to the winner.**
> The buyer escrows GEN on creation (payable), the named seller opts in, both sides file
> evidence URLs, and an LLM jury on GenLayer reads those pages **on-chain** to rule how the
> escrow is released.

**Category:** Dispute Resolution · Agentic Commerce

---

## What changed after review (v2)

The first version was rejected with a fair criticism: the contract did not escrow or release
GEN — the amount was unbacked metadata and settlement only flipped a state label.
v2 addresses every point:

| Reviewer requirement | How v2 implements it |
|---|---|
| **Funded custody** | `create_dispute` is `@gl.public.write.payable`; the escrowed amount is `gl.message.value` — the GEN actually sent. The contract **holds** it (verified on-chain: contract balance went 0 → 0.5 GEN). |
| **Seller acceptance** | `accept_dispute()` — only the named seller can opt in; until then `refund_if_unaccepted()` lets the buyer reclaim the escrow. |
| **Evidence requirements** | `submit_evidence()` is gated to the active parties and requires a URL **and** a description; `request_ruling()` refuses to run with **zero** evidence on file. |
| **Enforceable payouts** | `settle()` computes the shares and calls `emit_transfer` on the winner's address, which the runtime debits from the contract balance — the escrow is released as real GEN, not a label. |

---

## Lifecycle

```
OPEN(payable, funds locked) → seller accepts → EVIDENCE → RULED → SETTLED (GEN paid out)
                    ↘ seller never accepts → buyer refunds
```

| State | Meaning |
|---|---|
| `0 AWAITING SELLER` | escrow funded, waiting for the seller to opt in |
| `1 ACTIVE` | seller accepted, both sides may file evidence |
| `2 RULED` | LLM jury decided (winner / split) |
| `3 SETTLED` | escrow paid out via `emit_transfer` |
| `4 CANCELLED` | buyer refunded because the seller never accepted |

---

## Contract: `contracts/deal_guard.py`

| Method | Type | Description |
|---|---|---|
| `create_dispute(seller, description)` | **payable** write | Buyer escrows GEN into the contract |
| `accept_dispute(id)` | write | Named seller opts in (required before evidence) |
| `refund_if_unaccepted(id)` | write | Buyer reclaims escrow if the seller never accepts |
| `submit_evidence(id, url, description)` | write | Either party files evidence (active disputes only) |
| `judge` → `request_ruling(id)` | write | Fetches evidence on-chain, LLM jury rules how to release |
| `settle(id)` | write | **Pays out** the escrow to winner (or splits) |
| `get_dispute(id)` | view | Full state incl. `escrow_held_now` (live contract balance) |
| `get_contract_balance()` | view | GEN currently under custody |
| `get_dispute_count()` | view | Total disputes |

### The payout path

```python
@gl.evm.contract_interface
class _Payee:
    class View: pass
    class Write: pass

def _pay(self, to: Address, amount: u256):
    if amount <= u256(0):
        return
    _Payee(to).emit_transfer(value=amount)      # external message → real GEN out

# settle():
buyer_share / seller_share computed from winner + split_percent,
d.escrowed = 0 ; d.settled = True ; d.state = SETTLED
self._pay(d.buyer, buyer_share)
self._pay(d.seller, seller_share)
```

`settle()` emits a message the network executes on finalization. The emitted message is
visible on-chain:

```json
{ "recipient": "0x6045843b…8d95", "value": "500000000000000000", "data": "0x" }
```

### The ruling path

`request_ruling()` snapshots the dispute, fetches **every** evidence URL with
`gl.nondet.web.get`, and asks the jury (`gl.nondet.exec_prompt`) for
`{winner, split_percent, reason, confidence}`. Validators re-run the same reasoning via
`gl.vm.run_nondet_unsafe` and must agree on the winner before state is written.
A party that files no evidence is judged on the record — it cannot win on assertion alone.

---

## Frontend: `index.html`

Single-file, build-free (loads `genlayer-js` from esm.sh), GitHub Pages ready:
- live **escrow vault** readout (GEN held by the contract, dispute count)
- read any dispute with **no wallet**; connect a wallet to act
- escrow & open a dispute (sends GEN with the call), seller accept, buyer refund,
  file evidence, request the ruling, settle the payout

---

## Local tests (10 passing)

```bash
pip install genlayer-test pytest
pytest tests/direct/ -v
```

Covers: funding required to create, escrow recorded as custody, buyer≠seller, seller-only
acceptance, evidence blocked before acceptance, ruling blocked with no evidence, buyer refund
path, full flow ending in payout, split rulings, and no double-settle.

---

## Deploy

```bash
npm install -g genlayer
genlayer network set testnet-bradbury        # chain 4221, GEN
genlayer deploy --contract contracts/deal_guard.py
genlayer call <CONTRACT> get_contract_balance
```

> **Header rule:** line 1 must be the runner directive
> `# { "Depends": "py-genlayer:<hash>" }`. `test`/`latest` are rejected outside debug mode.

---

## Verified end-to-end on Bradbury

| Step | Result |
|---|---|
| deploy v2 | ACCEPTED · FINISHED_WITH_RETURN |
| `create_dispute` payable (0.5 GEN) | contract balance **0 → 0.5 GEN** (real custody) |
| `accept_dispute` (seller) | state → ACTIVE |
| `submit_evidence` (both sides) | 2 items on file |
| `request_ruling` | ACCEPTED · LLM ruled `winner = buyer` |
| `settle` | ACCEPTED · payout message emitted to the buyer |
| final state | `SETTLED`, `settled = true`, `escrowed = 0` |

Layout:

```
dealguard/
├── contracts/deal_guard.py           the Intelligent Contract (v2, escrowed)
├── tests/direct/test_deal_guard.py   10 direct-mode tests
├── index.html                        build-free frontend (GitHub Pages ready)
├── web/                              optional Vite frontend
├── genlayer.config.yaml
└── README.md
```

Built on [GenLayer](https://docs.genlayer.com) Intelligent Contracts.
