/**
 * Centralized API helpers.
 * All requests include X-Merchant-ID: 1 (first seeded merchant).
 * The Vite proxy forwards /api → http://localhost:8000.
 */

const MERCHANT_ID = "1";

const DEFAULT_HEADERS = {
  "Content-Type": "application/json",
  "X-Merchant-ID": MERCHANT_ID,
};

export async function fetchBalance() {
  const res = await fetch("/api/v1/balance/", { headers: DEFAULT_HEADERS });
  if (!res.ok) throw new Error("Failed to fetch balance");
  return res.json();
}

export async function fetchLedger() {
  const res = await fetch("/api/v1/ledger/", { headers: DEFAULT_HEADERS });
  if (!res.ok) throw new Error("Failed to fetch ledger");
  return res.json();
}

export async function fetchPayouts() {
  const res = await fetch("/api/v1/payouts/", { headers: DEFAULT_HEADERS });
  if (!res.ok) throw new Error("Failed to fetch payouts");
  return res.json();
}

export async function createPayout({ amount_paise, bank_account_id }) {
  const idempotency_key = crypto.randomUUID();
  const res = await fetch("/api/v1/payouts/", {
    method: "POST",
    headers: { ...DEFAULT_HEADERS, "Idempotency-Key": idempotency_key },
    body: JSON.stringify({ amount_paise, bank_account_id }),
  });
  const data = await res.json();
  return { data, status: res.status };
}
