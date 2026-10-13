// DealGuard frontend — talks to the deployed Intelligent Contract on GenLayer.
// Uses GenLayerJS (https://github.com/yeagerai/genlayer-js).
//
// Setup:
//   npm install @genlayer/genlayer-js ethers
//   npx vite   (or: npm run dev)
//
// Before running, set CONTRACT_ADDRESS to your deployed contract.

import { GenLayerClient } from "@genlayer/genlayer-js";

const RPC = "https://rpc-bradbury.genlayer.com";
const CHAIN_ID = 4221;
// TODO: fill after deployment — `genlayer deploy`
const CONTRACT_ADDRESS = "0x9adf2dcc59401cb7122b25cbd5741931B2D20039";

// Hand-written ABI matching contracts/deal_guard.py
const ABI = [
  {
    type: "function",
    name: "create_dispute",
    stateMutability: "nonpayable",
    inputs: [
      { name: "seller", type: "address" },
      { name: "amount", type: "uint256" },
      { name: "description", type: "string" },
    ],
    outputs: [{ name: "", type: "uint256" }],
  },
  {
    type: "function",
    name: "submit_evidence",
    stateMutability: "nonpayable",
    inputs: [
      { name: "dispute_id", type: "uint256" },
      { name: "url", type: "string" },
      { name: "description", type: "string" },
    ],
    outputs: [],
  },
  {
    type: "function",
    name: "request_ruling",
    stateMutability: "nonpayable",
    inputs: [{ name: "dispute_id", type: "uint256" }],
    outputs: [],
  },
  {
    type: "function",
    name: "settle",
    stateMutability: "nonpayable",
    inputs: [{ name: "dispute_id", type: "uint256" }],
    outputs: [],
  },
  {
    type: "function",
    name: "get_dispute",
    stateMutability: "view",
    inputs: [{ name: "dispute_id", type: "uint256" }],
    outputs: [
      { name: "id", type: "uint256" },
      { name: "buyer", type: "address" },
      { name: "seller", type: "address" },
      { name: "amount", type: "uint256" },
      { name: "description", type: "string" },
      { name: "state", type: "uint8" },
      { name: "buyer_evidence", type: "tuple[]", components: [
          { name: "party", type: "string" },
          { name: "url", type: "string" },
          { name: "description", type: "string" } ] },
      { name: "seller_evidence", type: "tuple[]", components: [
          { name: "party", type: "string" },
          { name: "url", type: "string" },
          { name: "description", type: "string" } ] },
      { name: "winner", type: "string" },
      { name: "ruling_reason", type: "string" },
      { name: "created_at", type: "string" },
    ],
  },
  {
    type: "function",
    name: "get_dispute_count",
    stateMutability: "view",
    inputs: [],
    outputs: [{ name: "", type: "uint256" }],
  },
];

const STATE_NAMES = ["CREATED", "EVIDENCE", "RULED", "SETTLED"];

let client = null;
let contract = null;

const $ = (id) => document.getElementById(id);

function msg(el, text, kind = "info") {
  el.className = `msg show ${kind}`;
  el.textContent = text;
}

async function connect() {
  if (!window.ethereum) {
    msg($("createMsg"), "No wallet detected. Please install MetaMask or another EIP-1193 wallet.", "err");
    return;
  }
  try {
    await window.ethereum.request({ method: "eth_requestAccounts" });
    const accounts = await window.ethereum.request({ method: "eth_accounts" });
    client = new GenLayerClient({
      endpoint: RPC,
      chainId: CHAIN_ID,
      account: window.ethereum,
    });
    contract = await client.contract({ address: CONTRACT_ADDRESS, abi: ABI });
    $("netLabel").textContent = accounts[0]?.slice(0, 8) + "… · Bradbury";
    $("netDot").classList.add("live");
    $("connectBtn").textContent = "Connected";
    await refreshCount();
  } catch (e) {
    msg($("createMsg"), "Connect failed: " + e.message, "err");
  }
}

async function refreshCount() {
  if (!contract) return;
  try {
    const count = await contract.read.get_dispute_count([]);
    $("newestId").textContent = String(count);
  } catch (_) { /* ignore */ }
}

async function refreshState() {
  const id = Number($("ruleId").value || $("evId").value || 1);
  if (!contract) {
    $("stateBox").textContent = "Connect your wallet first.";
    return;
  }
  try {
    const d = await contract.read.get_dispute([id]);
    const buyerEv = (d.buyer_evidence || []).map((e) => `  • ${e.url} — ${e.description}`).join("\n") || "  (none)";
    const sellerEv = (d.seller_evidence || []).map((e) => `  • ${e.url} — ${e.description}`).join("\n") || "  (none)";
    $("stateBox").textContent =
      `Dispute #${d.id}\n` +
      `State: ${STATE_NAMES[Number(d.state)] ?? d.state}\n` +
      `Buyer:  ${d.buyer}\n` +
      `Seller: ${d.seller}\n` +
      `Escrow: ${d.amount} GEN\n` +
      `Description: ${d.description}\n` +
      `\nBuyer evidence:\n${buyerEv}\n` +
      `\nSeller evidence:\n${sellerEv}\n` +
      (d.winner ? `\nWinner: ${d.winner}\nReason: ${d.ruling_reason}` : "");
  } catch (e) {
    $("stateBox").textContent = "Error loading dispute: " + e.message;
  }
}

$("connectBtn").addEventListener("click", connect);

$("createBtn").addEventListener("click", async () => {
  if (!contract) return msg($("createMsg"), "Connect your wallet first.", "err");
  try {
    const seller = $("seller").value.trim();
    const amount = BigInt($("amount").value || "0");
    const desc = $("desc").value.trim();
    if (!seller || amount <= 0n || !desc) return msg($("createMsg"), "Fill in seller, amount and description.", "err");
    msg($("createMsg"), "Submitting transaction…", "info");
    const receipt = await contract.write.create_dispute([seller, amount, desc]);
    msg($("createMsg"), `Dispute created! tx: ${receipt?.transactionHash ?? receipt}`, "ok");
    await refreshCount();
  } catch (e) {
    msg($("createMsg"), "Create failed: " + e.message, "err");
  }
});

$("evBtn").addEventListener("click", async () => {
  if (!contract) return msg($("evMsg"), "Connect your wallet first.", "err");
  try {
    const id = Number($("evId").value);
    const url = $("evUrl").value.trim();
    const desc = $("evDesc").value.trim();
    if (!id || !url || !desc) return msg($("evMsg"), "Fill in ID, URL and description.", "err");
    msg($("evMsg"), "Submitting evidence…", "info");
    const receipt = await contract.write.submit_evidence([id, url, desc]);
    msg($("evMsg"), `Evidence submitted! tx: ${receipt?.transactionHash ?? receipt}`, "ok");
    await refreshState();
  } catch (e) {
    msg($("evMsg"), "Evidence failed: " + e.message, "err");
  }
});

$("ruleBtn").addEventListener("click", async () => {
  if (!contract) return msg($("ruleMsg"), "Connect your wallet first.", "err");
  try {
    const id = Number($("ruleId").value);
    if (!id) return msg($("ruleMsg"), "Enter a dispute ID.", "err");
    msg($("ruleMsg"), "Asking the LLM jury… (this runs consensus on-chain, may take a minute)", "info");
    const receipt = await contract.write.request_ruling([id]);
    msg($("ruleMsg"), `Ruling requested! tx: ${receipt?.transactionHash ?? receipt}`, "ok");
    await refreshState();
  } catch (e) {
    msg($("ruleMsg"), "Ruling failed: " + e.message, "err");
  }
});

$("settleBtn").addEventListener("click", async () => {
  if (!contract) return msg($("ruleMsg"), "Connect your wallet first.", "err");
  try {
    const id = Number($("ruleId").value || $("evId").value || 1);
    const receipt = await contract.write.settle([id]);
    msg($("ruleMsg"), `Settled! tx: ${receipt?.transactionHash ?? receipt}`, "ok");
    await refreshState();
  } catch (e) {
    msg($("ruleMsg"), "Settle failed: " + e.message, "err");
  }
});

$("refreshBtn").addEventListener("click", refreshState);
