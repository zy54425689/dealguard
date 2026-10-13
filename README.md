# DealGuard — Agentic Commerce Dispute Arbitration

> An Intelligent Contract on GenLayer that resolves disputes between AI agents
> (or humans) using LLM consensus: it reads real evidence URLs on-chain, weighs
> both sides, and rules who gets the escrowed funds.

**Category:** Dispute Resolution · Agentic Commerce

---

## Why this project

GenLayer's thesis is the **adjudication layer for agentic commerce** — the moment
an agent-to-agent transaction becomes ambiguous, someone needs machine-speed
judgment. DealGuard is exactly that primitive:

- Two parties (buyer & seller — either could be an AI agent) escrow a deal.
- If they disagree, each side submits **evidence URLs** (receipts, logs, chat
  transcripts, delivery records…).
- The contract **fetches every evidence page on-chain** (`gl.nondet.web.get`)
  and asks an **LLM jury** (`gl.nondet.exec_prompt`) to weigh the evidence and
  rule who receives the funds.
- The verdict is reached through GenLayer's **validated consensus**
  (`gl.vm.run_nondet_unsafe`), not a single oracle.

This is the core "subjective resolution" use case GenLayer was built for —
no ordinary smart contract can read a PDF receipt and decide who is telling the
truth. An Intelligent Contract can.

---

## Contract: `contracts/deal_guard.py`

| Method | Type | Description |
|---|---|---|
| `create_dispute(seller, amount, description)` | write | Buyer opens a dispute, escrowing `amount` GEN |
| `submit_evidence(dispute_id, url, description)` | write | Either party attaches an evidence URL |
| `request_ruling(dispute_id)` | write | LLM jury reads evidence pages & issues verdict |
| `settle(dispute_id)` | write | Finalize a ruled dispute (release escrow) |
| `get_dispute(dispute_id)` | view | Full dispute state |
| `get_dispute_count()` | view | Total disputes |

**Lifecycle:** `CREATED → EVIDENCE → RULED → SETTLED`

### How the AI ruling works

```python
def leader_fn():
    # 1. Fetch every evidence URL on-chain
    # 2. Ask the LLM to weigh both sides
    return gl.nondet.exec_prompt(prompt, response_format='json')

def validator_fn(leaders_res):
    # 3. Validators run the same logic and compare verdicts
    return my_result["winner"] == leaders_res.calldata["winner"]

result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
```

The jury returns `{"winner": "buyer"|"seller"|"split", "split_percent", "reason"}`.

---

## Frontend: `web/`

A Vite + GenLayerJS single page app:
- connect a wallet (MetaMask / any EIP-1193)
- open a dispute, attach evidence URLs
- request the AI ruling and watch the verdict

```bash
cd web
npm install
# set CONTRACT_ADDRESS in app.js to your deployed address
npm run dev
```

---

## Local tests (no network needed)

```bash
pip install genlayer-test pytest
pytest tests/direct/ -v
```

Direct mode runs the contract in-memory with mocked web fetches and LLM
responses. Tests cover: creating disputes, party-only evidence access,
the full ruling flow, and double-ruling protection.

---

## Deployment status

| Network | Contract address | Tx | Status |
|---|---|---|---|
| **Bradbury testnet** | [`0x9adf2dcc59401cb7122b25cbd5741931B2D20039`](https://explorer-bradbury.genlayer.com/address/0x9adf2dcc59401cb7122b25cbd5741931B2D20039) | `0x6c356dcbc3d35a6922312cd97b6c85af20e11f3d4e27e97ce032472e1b19c270` | **live** · ACCEPTED · AGREE (5 validators) |

### Verified end-to-end on Bradbury

Full lifecycle executed against the deployed contract:

| Step | Tx | Result |
|---|---|---|
| `create_dispute(seller, 100 GEN, …)` | `0xb507a0f7…` → fixed → `create` | dispute #1 created |
| `submit_evidence(1, "https://example.com/", …)` | — | stored, state → `EVIDENCE` |
| `request_ruling(1)` | `0x43d6d6b5fd28c5428660ac4a5c05e23d7726141d90217d65357bdae64046d142` | **ACCEPTED** · `FINISHED_WITH_RETURN` |
| `settle(1)` | `0xce5e1806a381ffd3dfca043197c77b5b8be985c412acdccc65bce75ac3dfe2fc` | state → `SETTLED` |

**Real LLM verdict returned on-chain** (`winner = "buyer"`):

> "The buyer's evidence URL only displays a generic Example Domain page and does
> not show the agreed schema, the delivered dataset, or any comparison between
> them. The seller provided no evidence to establish compliance with the
> specification. Because the seller cannot substantiate that the dataset matches
> the agreed schema, the buyer has the better-supported claim and should receive
> the escrowed funds."

This proves the contract **fetched the evidence URL on-chain**, reasoned about it,
and reached validator consensus.

### Deploy it yourself

```bash
# 0. install the CLI (verified working)
npm install -g genlayer            # v0.39.2

# 1. import the deployer account
genlayer account import --name dealguard \
  --private-key <YOUR_PRIVATE_KEY> --password '<KEYSTORE_PASSWORD>'

# 2. point at Bradbury
genlayer network set testnet-bradbury

# 3. get testnet GEN from the faucet
#    https://testnet-faucet.genlayer.foundation
#    (GitHub account >3 months old with ≥1 public repo + 0.01 ETH on mainnet)

# 4. deploy (CLI prompts for the keystore password)
genlayer deploy --contract contracts/deal_guard.py

# 5. verify
genlayer call <CONTRACT_ADDRESS> get_dispute_count
#    explorer: https://explorer-bradbury.genlayer.com/address/<CONTRACT_ADDRESS>
```

> **Contract header gotcha (learned the hard way):** the first line of the contract
> must be the runner comment `# { "Depends": "py-genlayer:<hash>" }`. Special ids
> like `test` / `latest` are **rejected outside debug mode** (`invalid runner id`),
> so real deployments need a concrete hash:
> `# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }`

The contract address is already wired into `web/app.js` (`CONTRACT_ADDRESS`).



---

## Project layout

```
dealguard/
├── contracts/
│   └── deal_guard.py          # the Intelligent Contract
├── tests/direct/
│   └── test_deal_guard.py     # in-memory tests (mocked web + LLM)
├── web/                       # GenLayerJS frontend
│   ├── index.html
│   ├── app.js
│   └── package.json
├── genlayer.config.yaml       # network config (Bradbury default)
└── README.md
```

Built with [GenLayer](https://docs.genlayer.com) — Intelligent Contracts that
can read the web and reason in natural language.
