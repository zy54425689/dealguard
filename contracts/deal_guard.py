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
    amount: u256
    description: str
    state: u8            # 0=CREATED 1=EVIDENCE 2=RULED 3=SETTLED
    buyer_ev_count: u256
    seller_ev_count: u256
    winner: str          # "" | "buyer" | "seller" | "split"
    ruling_reason: str


# ------------------------------------------------------------------ helpers --

class DealGuard(gl.Contract):
    """On-chain adjudication for agentic commerce.

    Two parties (e.g. an AI agent and a service provider, or any buyer/seller)
    escrow a deal. If they disagree, the contract reads their evidence URLs
    and an LLM jury decides who is entitled to the funds.
    """

    disputes: TreeMap[u256, Dispute]
    evidence: TreeMap[str, EvidenceItem]     # key: "<dispute_id>:<b|s>:<index>"
    next_id: u256

    def __init__(self):
        self.next_id = u256(1)

    # -------------------------------------------------------- internals ----

    def _to_addr(self, value) -> Address:
        """Normalize an address coming from calldata into an Address."""
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

    # ------------------------------------------------------------- views ----

    @gl.public.view
    def get_dispute(self, dispute_id: u256) -> dict:
        d = self.disputes[dispute_id]
        return {
            "id": dispute_id,
            "buyer": str(self._addr_bytes(d.buyer).hex()),
            "seller": str(self._addr_bytes(d.seller).hex()),
            "amount": d.amount,
            "description": d.description,
            "state": d.state,
            "buyer_evidence": self._evidence_list(dispute_id, "b", d.buyer_ev_count),
            "seller_evidence": self._evidence_list(dispute_id, "s", d.seller_ev_count),
            "winner": d.winner,
            "ruling_reason": d.ruling_reason,
        }

    @gl.public.view
    def get_dispute_count(self) -> u256:
        return self.next_id - u256(1)

    # ------------------------------------------------------------ writes ----

    @gl.public.write
    def create_dispute(self, seller: Address, amount: u256, description: str) -> u256:
        """A buyer opens a dispute against a seller, escrowing `amount` GEN."""
        if amount <= u256(0):
            raise gl.vm.UserError("Amount must be positive")
        if len(description.strip()) == 0:
            raise gl.vm.UserError("Description is required")

        d = Dispute(
            buyer=self._to_addr(gl.message.sender_address),
            seller=self._to_addr(seller),
            amount=amount,
            description=description,
            state=u8(0),
            buyer_ev_count=u256(0),
            seller_ev_count=u256(0),
            winner="",
            ruling_reason="",
        )
        d_id = self.next_id
        self.disputes[d_id] = d
        self.next_id = d_id + u256(1)
        return d_id

    @gl.public.write
    def submit_evidence(self, dispute_id: u256, url: str, description: str):
        """Either party submits an evidence URL (receipt, log, message, ...)."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        if len(url.strip()) == 0 or not (url.startswith("http://") or url.startswith("https://")):
            raise gl.vm.UserError("A valid evidence URL is required")

        d = self.disputes[dispute_id]
        if d.state > u8(1):
            raise gl.vm.UserError("Evidence phase is closed")

        sender_b = self._addr_bytes(gl.message.sender_address)
        if sender_b == self._addr_bytes(d.buyer):
            party = "buyer"
            tag = "b"
        elif sender_b == self._addr_bytes(d.seller):
            party = "seller"
            tag = "s"
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
        if d.state == u8(0):
            d.state = u8(1)

    @gl.public.write
    def request_ruling(self, dispute_id: u256):
        """Ask the LLM jury to adjudicate."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")

        d = self.disputes[dispute_id]
        if d.state == u8(2) or d.state == u8(3):
            raise gl.vm.UserError("Ruling already issued")

        # Snapshot into plain Python values *outside* the nondet block.
        desc_mem = str(d.description)
        amount_mem = int(d.amount)
        buyer_ev = self._evidence_list(dispute_id, "b", d.buyer_ev_count)
        seller_ev = self._evidence_list(dispute_id, "s", d.seller_ev_count)

        def _fmt(evs) -> str:
            out = ["- " + e["party"] + " says: " + e["description"] +
                   " (evidence URL: " + e["url"] + ")" for e in evs]
            return "\n".join(out) if out else "- (none)"

        def _fetch_text(url: str, limit: int = 1200) -> str:
            try:
                resp = gl.nondet.web.get(url)
                if isinstance(resp, bytes):
                    raw = resp.decode("utf-8", errors="ignore")
                elif hasattr(resp, "body"):
                    raw = resp.body.decode("utf-8", errors="ignore")
                else:
                    raw = str(resp)
                raw = " ".join(raw.split())
                return raw[:limit]
            except Exception:
                return "[could not fetch this URL]"

        def leader_fn():
            buyer_txt = _fmt(buyer_ev)
            seller_txt = _fmt(seller_ev)
            buyer_pages = "\n".join(
                "URL " + e["url"] + ":\n" + _fetch_text(e["url"]) for e in buyer_ev)
            seller_pages = "\n".join(
                "URL " + e["url"] + ":\n" + _fetch_text(e["url"]) for e in seller_ev)

            prompt = (
                "You are the impartial on-chain judge for an agentic-commerce dispute.\n\n"
                "Dispute: " + desc_mem + "\n"
                "Escrowed amount: " + str(amount_mem) + " GEN\n\n"
                "BUYER's evidence:\n" + buyer_txt + "\n\n"
                "SELLER's evidence:\n" + seller_txt + "\n\n"
                "CONTENT FETCHED FROM BUYER'S EVIDENCE URLS:\n" + buyer_pages + "\n\n"
                "CONTENT FETCHED FROM SELLER'S EVIDENCE URLS:\n" + seller_pages + "\n\n"
                "Instructions:\n"
                "1. Read the fetched evidence content above carefully.\n"
                "2. Assess which side's claim is better supported by the evidence.\n"
                "3. Rule who should receive the escrowed funds.\n\n"
                'Respond as JSON with exactly these keys:\n'
                '- "winner": one of "buyer", "seller", "split"\n'
                '- "split_percent": integer 0-100 (percent to buyer, when winner is "split")\n'
                '- "reason": a concise 2-4 sentence explanation'
            )
            return gl.nondet.exec_prompt(prompt, response_format='json')

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            my_result = leader_fn()
            if not isinstance(my_result, dict) or not isinstance(leaders_res.calldata, dict):
                return False
            a = my_result.get("winner")
            b = leaders_res.calldata.get("winner")
            return a == b and a in ("buyer", "seller", "split")

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        winner = str(result.get("winner", ""))
        reason = str(result.get("reason", ""))
        if winner not in ("buyer", "seller", "split"):
            raise gl.vm.UserError("Jury returned an invalid verdict")

        d.winner = winner
        d.ruling_reason = reason
        d.state = u8(2)

    @gl.public.write
    def settle(self, dispute_id: u256):
        """Finalize a ruled dispute (escrow released to the winner)."""
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        if d.state != u8(2):
            raise gl.vm.UserError("Dispute is not ruled yet")
        d.state = u8(3)
