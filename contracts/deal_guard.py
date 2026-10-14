# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
from genlayer import *

# ---------------------------------------------------------------- storage ---


@allow_storage
@dataclass
class EvidenceItem:
    party: str          # "buyer" | "seller"
    url: str
    description: str


@allow_storage
@dataclass
class Dispute:
    buyer: Address
    seller: Address
    escrowed: u256        # GEN actually held by this contract (from gl.message.value)
    description: str
    state: u8             # 0=AWAITING_SELLER 1=ACTIVE 2=JUDGED 3=SETTLED 4=CANCELLED
    seller_accepted: bool
    buyer_ev_count: u256
    seller_ev_count: u256
    winner: str           # "" | "buyer" | "seller" | "split"
    split_percent: u256   # % to buyer when winner == "split"
    ruling_reason: str
    ruling_confidence: str
    settled: bool


# EVM/EOA transfer interface (external message to the chain layer)
@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass


# ------------------------------------------------------------------ contract --

class DealGuard(gl.Contract):
    """Escrowed dispute arbitration for agentic commerce.

    Funds are really held by this contract and really paid out:

      1. create_dispute() is PAYABLE - the buyer escrows GEN into the contract.
      2. accept_dispute() - the seller must opt in; otherwise the buyer refunds.
      3. submit_evidence() - both sides attach evidence URLs.
      4. request_ruling() - an LLM jury fetches every evidence URL on-chain and
         rules who should receive the escrowed GEN.
      5. settle() - the contract PAYS OUT the escrow to the winner (or splits it).

    No unbacked metadata: the amount recorded is the amount actually received,
    and settlement moves real GEN.
    """

    disputes: TreeMap[u256, Dispute]
    evidence: TreeMap[str, EvidenceItem]     # key: "<dispute_id>:<b|s>:<index>"
    next_id: u256

    def __init__(self):
        self.next_id = u256(1)

    # -------------------------------------------------------- internals ----

    def _to_addr(self, value) -> Address:
        if isinstance(value, Address):
            return value
        return Address(value)

    def _addr_bytes(self, value) -> bytes:
        b = getattr(value, "as_bytes", None)
        if b is None:
            b = getattr(value, "_as_bytes", None)
        if callable(b):
            b = b()
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        return bytes(self._to_addr(value))

    def _evidence_list(self, dispute_id: u256, party: str, count: u256) -> list:
        out = []
        i = u256(0)
        while i < count:
            key = str(int(dispute_id)) + ":" + party + ":" + str(int(i))
            if key in self.evidence:
                e = self.evidence[key]
                out.append({"party": e.party, "url": e.url, "description": e.description})
            i += u256(1)
        return out

    def _pay(self, to: Address, amount: u256):
        """Send GEN out of this contract's balance (external message)."""
        if amount <= u256(0):
            return
        # The runtime debits this contract's balance when the message is emitted
        # and fails the transaction if the balance is insufficient.
        _Payee(to).emit_transfer(value=amount)

    # ------------------------------------------------------------- views ----

    @gl.public.view
    def get_dispute(self, dispute_id: u256) -> dict:
        d = self.disputes[dispute_id]
        return {
            "id": dispute_id,
            "buyer": "0x" + self._addr_bytes(d.buyer).hex(),
            "seller": "0x" + self._addr_bytes(d.seller).hex(),
            "escrowed": d.escrowed,
            "escrow_held_now": self.balance,
            "description": d.description,
            "state": d.state,
            "seller_accepted": d.seller_accepted,
            "buyer_evidence": self._evidence_list(dispute_id, "b", d.buyer_ev_count),
            "seller_evidence": self._evidence_list(dispute_id, "s", d.seller_ev_count),
            "winner": d.winner,
            "split_percent": d.split_percent,
            "ruling_reason": d.ruling_reason,
            "ruling_confidence": d.ruling_confidence,
            "settled": d.settled,
        }

    @gl.public.view
    def get_dispute_count(self) -> u256:
        return self.next_id - u256(1)

    @gl.public.view
    def get_contract_balance(self) -> u256:
        return self.balance

    # ------------------------------------------------------------ writes ----

    @gl.public.write.payable
    def create_dispute(self, seller: Address, description: str) -> u256:
        """Open a dispute and escrow GEN into this contract (payable).

        The escrowed amount is whatever GEN was actually sent - it is custody,
        not metadata.
        """
        value = gl.message.value
        if value <= u256(0):
            raise gl.vm.UserError("Escrow must be funded: send GEN with the call")
        if len(description.strip()) == 0:
            raise gl.vm.UserError("Description is required")

        buyer = self._to_addr(gl.message.sender_address)
        seller_addr = self._to_addr(seller)
        if self._addr_bytes(buyer) == self._addr_bytes(seller_addr):
            raise gl.vm.UserError("Buyer and seller must differ")

        d = Dispute(
            buyer=buyer,
            seller=seller_addr,
            escrowed=value,
            description=description,
            state=u8(0),
            seller_accepted=False,
            buyer_ev_count=u256(0),
            seller_ev_count=u256(0),
            winner="",
            split_percent=u256(0),
            ruling_reason="",
            ruling_confidence="",
            settled=False,
        )
        d_id = self.next_id
        self.disputes[d_id] = d
        self.next_id = d_id + u256(1)
        return d_id

    @gl.public.write
    def accept_dispute(self, dispute_id: u256):
        """The named seller opts in. Until then the buyer may refund."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        if d.state != u8(0):
            raise gl.vm.UserError("Dispute is not awaiting seller acceptance")
        if self._addr_bytes(gl.message.sender_address) != self._addr_bytes(d.seller):
            raise gl.vm.UserError("Only the named seller can accept")
        d.seller_accepted = True
        d.state = u8(1)

    @gl.public.write
    def refund_if_unaccepted(self, dispute_id: u256):
        """Buyer reclaims the escrow if the seller never accepted."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        if d.state != u8(0):
            raise gl.vm.UserError("Dispute is not awaiting seller acceptance")
        if self._addr_bytes(gl.message.sender_address) != self._addr_bytes(d.buyer):
            raise gl.vm.UserError("Only the buyer can refund")
        amount = d.escrowed
        d.escrowed = u256(0)
        d.state = u8(4)
        d.settled = True
        self._pay(d.buyer, amount)

    @gl.public.write
    def submit_evidence(self, dispute_id: u256, url: str, description: str):
        """Either party attaches an evidence URL (fetched on-chain at ruling time)."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        if len(url.strip()) == 0 or not (url.startswith("http://") or url.startswith("https://")):
            raise gl.vm.UserError("A valid evidence URL is required")
        if len(description.strip()) == 0:
            raise gl.vm.UserError("Describe what this evidence proves")

        d = self.disputes[dispute_id]
        if d.state != u8(1):
            raise gl.vm.UserError("Dispute is not active for evidence")

        sender_b = self._addr_bytes(gl.message.sender_address)
        if sender_b == self._addr_bytes(d.buyer):
            tag, party = "b", "buyer"
        elif sender_b == self._addr_bytes(d.seller):
            tag, party = "s", "seller"
        else:
            raise gl.vm.UserError("Only the buyer or seller may submit evidence")

        item = EvidenceItem(party=party, url=url, description=description)
        if tag == "b":
            idx = d.buyer_ev_count
            d.buyer_ev_count = idx + u256(1)
        else:
            idx = d.seller_ev_count
            d.seller_ev_count = idx + u256(1)
        self.evidence[str(int(dispute_id)) + ":" + tag + ":" + str(int(idx))] = item

    @gl.public.write
    def request_ruling(self, dispute_id: u256):
        """LLM jury reads every evidence URL on-chain and rules on the escrow.

        Evidence requirement: at least one evidence item must be on file. A party
        that files nothing is judged on the record as it stands.
        """
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")

        d = self.disputes[dispute_id]
        if d.state != u8(1):
            raise gl.vm.UserError("Dispute must be accepted before a ruling")
        if int(d.buyer_ev_count) + int(d.seller_ev_count) == 0:
            raise gl.vm.UserError("No evidence on file: at least one party must submit evidence")

        # Snapshot into plain Python values outside the nondet block.
        desc_mem = str(d.description)
        escrow_mem = int(d.escrowed)
        buyer_ev = self._evidence_list(dispute_id, "b", d.buyer_ev_count)
        seller_ev = self._evidence_list(dispute_id, "s", d.seller_ev_count)

        def _fmt(evs) -> str:
            out = ["- " + e["party"] + " says: " + e["description"] +
                   " (evidence URL: " + e["url"] + ")" for e in evs]
            return "\n".join(out) if out else "- (no evidence filed)"

        def _fetch_text(url: str, limit: int = 1200) -> str:
            try:
                resp = gl.nondet.web.get(url)
                if isinstance(resp, bytes):
                    raw = resp.decode("utf-8", errors="ignore")
                elif hasattr(resp, "body"):
                    raw = resp.body.decode("utf-8", errors="ignore")
                else:
                    raw = str(resp)
                return " ".join(raw.split())[:limit]
            except Exception:
                return "[could not fetch this URL]"

        def leader_fn():
            buyer_pages = "\n".join(
                "URL " + e["url"] + ":\n" + _fetch_text(e["url"]) for e in buyer_ev)
            seller_pages = "\n".join(
                "URL " + e["url"] + ":\n" + _fetch_text(e["url"]) for e in seller_ev)

            prompt = (
                "You are the impartial on-chain judge for an escrowed commerce dispute.\n\n"
                "DISPUTE: " + desc_mem + "\n"
                "ESCROWED AMOUNT: " + str(escrow_mem) + " wei of GEN (held by the contract)\n\n"
                "BUYER's evidence:\n" + _fmt(buyer_ev) + "\n\n"
                "SELLER's evidence:\n" + _fmt(seller_ev) + "\n\n"
                "CONTENT FETCHED FROM BUYER'S EVIDENCE URLS:\n" + buyer_pages + "\n\n"
                "CONTENT FETCHED FROM SELLER'S EVIDENCE URLS:\n" + seller_pages + "\n\n"
                "Decide how the escrowed GEN must be released.\n"
                "Rules:\n"
                "- Judge only on the fetched evidence, not on whoever wrote more text.\n"
                "- A party that filed no evidence must not win on assertion alone.\n"
                "- If fault is shared, use \"split\" with a fair split_percent for the buyer.\n\n"
                'Respond as JSON with exactly these keys:\n'
                '- "winner": one of "buyer", "seller", "split"\n'
                '- "split_percent": integer 0-100 (percent of escrow to the BUYER; use 100 for buyer, 0 for seller)\n'
                '- "reason": concise 2-4 sentence justification\n'
                '- "confidence": one of "high", "medium", "low"'
            )
            return gl.nondet.exec_prompt(prompt, response_format='json')

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = leader_fn()
            if not isinstance(mine, dict) or not isinstance(leaders_res.calldata, dict):
                return False
            a = mine.get("winner")
            b2 = leaders_res.calldata.get("winner")
            return a == b2 and a in ("buyer", "seller", "split")

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        winner = str(result.get("winner", ""))
        if winner not in ("buyer", "seller", "split"):
            raise gl.vm.UserError("Jury returned an invalid verdict")

        try:
            pct = int(result.get("split_percent", 100 if winner == "buyer" else 0))
        except Exception:
            pct = 100 if winner == "buyer" else 0
        if pct < 0:
            pct = 0
        if pct > 100:
            pct = 100

        d.winner = winner
        d.split_percent = u256(pct)
        d.ruling_reason = str(result.get("reason", ""))
        d.ruling_confidence = str(result.get("confidence", ""))
        d.state = u8(2)

    @gl.public.write
    def settle(self, dispute_id: u256):
        """Pay the escrow out to the winner (or split it). Moves real GEN."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        if d.state != u8(2):
            raise gl.vm.UserError("Dispute is not ruled yet")
        if d.settled:
            raise gl.vm.UserError("Escrow already paid out")

        amount = d.escrowed
        if amount <= u256(0):
            raise gl.vm.UserError("Nothing left to pay out")

        winner = str(d.winner)
        buyer_share = u256(0)
        seller_share = u256(0)
        if winner == "buyer":
            buyer_share = amount
        elif winner == "seller":
            seller_share = amount
        else:  # split
            buyer_share = u256(int(amount) * int(d.split_percent) // 100)
            seller_share = amount - buyer_share

        d.escrowed = u256(0)
        d.settled = True
        d.state = u8(3)

        self._pay(d.buyer, buyer_share)
        self._pay(d.seller, seller_share)
