# -*- coding: utf-8 -*-
"""DealGuard direct-mode tests (run: pytest tests/direct/ -v)"""
import json

def _deploy(direct_deploy):
    """Deploy for local direct-mode tests.

    The contract header uses "py-genlayer:test" (the official form for on-chain
    deployment, see `genlayer new` template). Direct mode resolves dependencies
    from a local cache, so we swap in the concrete SDK hash that ships with
    genlayer-test before deploying.
    """
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "deal_guard.py"
    code = src.read_text(encoding="utf-8")
    code = code.replace(
        '"py-genlayer:test"',
        '"py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"',
        1,
    )
    tmp = pathlib.Path(tempfile.gettempdir()) / "deal_guard_local.py"
    tmp.write_text(code, encoding="utf-8")
    return direct_deploy(str(tmp))


def _addr(v):
    """Wrap raw bytes into a genlayer Address (available at test runtime)."""
    from genlayer import Address
    return Address(v)


def _hex(v) -> str:
    """Normalize an address-ish value to a plain lowercase hex string."""
    if isinstance(v, str):
        return v.lower().replace("0x", "")
    for attr in ("as_bytes", "_as_bytes"):
        if hasattr(v, attr):
            b = getattr(v, attr)
            if callable(b):
                b = b()
            return b.hex()
    return bytes(v).hex()


def test_create_dispute(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice

    d_id = contract.create_dispute(_addr(direct_bob), 100, "Agent B failed to deliver the dataset")
    assert int(d_id) == 1

    info = contract.get_dispute(d_id)
    assert _hex(info["buyer"]) == direct_alice.hex()
    assert _hex(info["seller"]) == direct_bob.hex()
    assert int(info["amount"]) == 100
    assert int(info["state"]) == 0
    assert info["winner"] == ""
    assert int(contract.get_dispute_count()) == 1


def test_create_dispute_requires_amount(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Amount must be positive"):
        contract.create_dispute(_addr(direct_bob), 0, "bad dispute")


def test_submit_evidence_only_parties(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    d_id = contract.create_dispute(_addr(direct_bob), 100, "delivery dispute")

    direct_vm.sender = direct_alice
    contract.submit_evidence(d_id, "https://example.com/invoice", "invoice from buyer")

    direct_vm.sender = direct_bob
    contract.submit_evidence(d_id, "https://example.com/delivery-log", "delivery log from seller")

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("Only the buyer or seller may submit evidence"):
        contract.submit_evidence(d_id, "https://example.com/hack", "not a party")

    info = contract.get_dispute(d_id)
    assert len(info["buyer_evidence"]) == 1
    assert len(info["seller_evidence"]) == 1
    assert int(info["state"]) == 1


def test_full_ruling_flow(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    d_id = contract.create_dispute(
        _addr(direct_bob), 500, "Freelance agent billed twice for the same task")

    direct_vm.sender = direct_alice
    contract.submit_evidence(d_id, "https://example.com/payment-1", "first payment receipt")
    direct_vm.sender = direct_bob
    contract.submit_evidence(d_id, "https://example.com/refund", "refund confirmation")

    # Mock the web fetches (regex on URL) and the LLM jury verdict.
    direct_vm.mock_web(
        r".*example\.com/payment-1.*",
        {"status": 200, "body": "<html>Payment of 500 GEN received from buyer</html>"},
    )
    direct_vm.mock_web(
        r".*example\.com/refund.*",
        {"status": 200, "body": "<html>Refund of 500 GEN sent back to buyer</html>"},
    )
    direct_vm.mock_llm(
        r".*",
        {"winner": "buyer", "split_percent": 100,
         "reason": "The payment receipt shows the buyer already paid; the refund confirms the seller returned it, so the escrow should go back to the buyer."},
    )

    contract.request_ruling(d_id)

    info = contract.get_dispute(d_id)
    assert int(info["state"]) == 2
    assert info["winner"] == "buyer"
    assert "payment" in info["ruling_reason"].lower()

    contract.settle(d_id)
    assert int(contract.get_dispute(d_id)["state"]) == 3


def test_cannot_rule_twice(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    d_id = contract.create_dispute(_addr(direct_bob), 10, "small dispute")
    direct_vm.mock_llm(r".*", {"winner": "split", "split_percent": 50, "reason": "both partly at fault"})
    contract.request_ruling(d_id)
    with direct_vm.expect_revert("Ruling already issued"):
        contract.request_ruling(d_id)
