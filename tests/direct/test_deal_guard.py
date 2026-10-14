# -*- coding: utf-8 -*-
"""DealGuard v2 direct-mode tests — real escrow custody and payouts.

Run: pytest tests/direct/ -v
"""
import json


def _deploy(direct_deploy):
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "deal_guard.py"
    code = src.read_text(encoding="utf-8")
    tmp = pathlib.Path(tempfile.gettempdir()) / "deal_guard_local.py"
    tmp.write_text(code, encoding="utf-8")
    return direct_deploy(str(tmp))


def _addr(v):
    from genlayer import Address
    return Address(v)


def _hex(v) -> str:
    if isinstance(v, str):
        return v.lower().replace("0x", "")
    for attr in ("as_bytes", "_as_bytes"):
        if hasattr(v, attr):
            b = getattr(v, attr)
            if callable(b):
                b = b()
            return b.hex()
    return bytes(v).hex()


def _funded_dispute(direct_vm, contract, buyer, seller, amount=1000):
    """Helper: buyer escrows `amount` wei and opens a dispute."""
    direct_vm.sender = buyer
    direct_vm.value = amount
    d_id = contract.create_dispute(_addr(seller), "Agent B was paid but never delivered the dataset")
    direct_vm.value = 0
    return d_id


# ---------------------------------------------------------------- escrow ----

def test_create_requires_funding(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("Escrow must be funded"):
        contract.create_dispute(_addr(direct_bob), "unfunded dispute")


def test_escrow_is_custody_not_metadata(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.deal(direct_alice, 10**18)

    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob, amount=5000)
    d = contract.get_dispute(d_id)

    # the recorded amount is exactly what was sent with the call
    assert int(d["escrowed"]) == 5000
    assert int(d["state"]) == 0                 # awaiting seller acceptance
    assert d["seller_accepted"] is False
    assert _hex(d["buyer"]) == direct_alice.hex()


def test_buyer_and_seller_must_differ(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    with direct_vm.expect_revert("Buyer and seller must differ"):
        contract.create_dispute(_addr(direct_alice), "self dispute")
    direct_vm.value = 0


# ------------------------------------------------- seller acceptance gate ----

def test_only_seller_can_accept(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("Only the named seller can accept"):
        contract.accept_dispute(d_id)

    direct_vm.sender = direct_bob
    contract.accept_dispute(d_id)
    d = contract.get_dispute(d_id)
    assert d["seller_accepted"] is True
    assert int(d["state"]) == 1


def test_evidence_blocked_until_accepted(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Dispute is not active for evidence"):
        contract.submit_evidence(d_id, "https://example.com/x", "too early")


def test_buyer_refund_when_seller_never_accepts(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob, amount=2000)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the buyer can refund"):
        contract.refund_if_unaccepted(d_id)

    direct_vm.sender = direct_alice
    contract.refund_if_unaccepted(d_id)
    d = contract.get_dispute(d_id)
    assert int(d["state"]) == 4        # CANCELLED
    assert int(d["escrowed"]) == 0     # escrow released
    assert d["settled"] is True


# ------------------------------------------------------ evidence + ruling ----

def test_ruling_requires_evidence_on_file(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    contract.accept_dispute(d_id)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("No evidence on file"):
        contract.request_ruling(d_id)


def test_full_flow_pays_the_winner(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob, amount=7000)

    direct_vm.sender = direct_bob
    contract.accept_dispute(d_id)

    direct_vm.sender = direct_alice
    contract.submit_evidence(d_id, "https://example.com/payment", "payment receipt")
    direct_vm.sender = direct_bob
    contract.submit_evidence(d_id, "https://example.com/delivery", "delivery log")

    direct_vm.mock_web(r".*example\.com/payment.*",
                       {"status": 200, "body": "<html>Payment of 7000 wei received</html>"})
    direct_vm.mock_web(r".*example\.com/delivery.*",
                       {"status": 200, "body": "<html>No matching delivery record found</html>"})
    direct_vm.mock_llm(r".*", {
        "winner": "buyer",
        "split_percent": 100,
        "reason": "The payment receipt confirms the buyer paid while the seller produced no delivery record.",
        "confidence": "high",
    })

    contract.request_ruling(d_id)
    d = contract.get_dispute(d_id)
    assert int(d["state"]) == 2
    assert d["winner"] == "buyer"
    assert int(d["split_percent"]) == 100
    assert "payment" in d["ruling_reason"].lower()

    contract.settle(d_id)
    d = contract.get_dispute(d_id)
    assert int(d["state"]) == 3
    assert d["settled"] is True
    assert int(d["escrowed"]) == 0        # escrow released, not left as metadata


def test_split_ruling_records_share(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob, amount=1000)
    direct_vm.sender = direct_bob
    contract.accept_dispute(d_id)
    direct_vm.sender = direct_alice
    contract.submit_evidence(d_id, "https://example.com/a", "buyer side")
    direct_vm.sender = direct_bob
    contract.submit_evidence(d_id, "https://example.com/b", "seller side")

    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>partial evidence</html>"})
    direct_vm.mock_llm(r".*", {"winner": "split", "split_percent": 40,
                               "reason": "Both parties are partly responsible.", "confidence": "medium"})
    contract.request_ruling(d_id)

    d = contract.get_dispute(d_id)
    assert d["winner"] == "split"
    assert int(d["split_percent"]) == 40
    contract.settle(d_id)
    assert int(contract.get_dispute(d_id)["state"]) == 3


def test_no_double_settle_or_early_settle(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    d_id = _funded_dispute(direct_vm, contract, direct_alice, direct_bob, amount=500)

    with direct_vm.expect_revert("Dispute is not ruled yet"):
        contract.settle(d_id)

    direct_vm.sender = direct_bob
    contract.accept_dispute(d_id)
    direct_vm.sender = direct_alice
    contract.submit_evidence(d_id, "https://example.com/e", "evidence")
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>ok</html>"})
    direct_vm.mock_llm(r".*", {"winner": "seller", "split_percent": 0,
                               "reason": "Seller's evidence prevails.", "confidence": "high"})
    contract.request_ruling(d_id)
    contract.settle(d_id)

    # already settled -> the ruling gate rejects further payouts
    with direct_vm.expect_revert("Dispute is not ruled yet"):
        contract.settle(d_id)
