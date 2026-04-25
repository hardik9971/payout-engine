import { useState } from "react";
import { createPayout } from "../api";

/**
 * PayoutForm — form to submit a new payout request.
 * Converts ₹ input to paise before sending to the API.
 * Generates a fresh UUID idempotency key per submission.
 */
export default function PayoutForm({ onSuccess }) {
  const [amountRupees, setAmountRupees] = useState("");
  const [bankAccountId, setBankAccountId] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { type: "success"|"error", message }

  const handleSubmit = async (e) => {
    e.preventDefault();
    setResult(null);

    const rupees = parseFloat(amountRupees);
    if (!rupees || rupees <= 0) {
      setResult({ type: "error", message: "Enter a valid amount." });
      return;
    }

    const amount_paise = Math.round(rupees * 100);

    setLoading(true);
    try {
      const { data, status } = await createPayout({
        amount_paise,
        bank_account_id: bankAccountId || "DEFAULT_BANK",
      });

      if (status === 201) {
        setResult({ type: "success", message: `Payout #${data.id} created (${data.amount_rupees}).` });
        setAmountRupees("");
        setBankAccountId("");
        onSuccess?.();
      } else {
        setResult({ type: "error", message: data.error || "Payout failed." });
      }
    } catch {
      setResult({ type: "error", message: "Network error. Is Django running?" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Request Payout
      </h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1" htmlFor="amount">
            Amount (₹)
          </label>
          <input
            id="amount"
            type="number"
            min="0.01"
            step="0.01"
            placeholder="e.g. 600.00"
            value={amountRupees}
            onChange={(e) => setAmountRupees(e.target.value)}
            className="input"
            required
          />
          {amountRupees && (
            <p className="text-xs text-slate-600 mt-1">
              = {Math.round(parseFloat(amountRupees || 0) * 100)} paise
            </p>
          )}
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1" htmlFor="bank">
            Bank Account ID
          </label>
          <input
            id="bank"
            type="text"
            placeholder="HDFC_XXXX1234"
            value={bankAccountId}
            onChange={(e) => setBankAccountId(e.target.value)}
            className="input"
          />
        </div>

        {result && (
          <div
            className={`text-sm rounded-xl px-4 py-3 ${
              result.type === "success"
                ? "bg-green-900/40 text-green-300 border border-green-700/50"
                : "bg-red-900/40 text-red-300 border border-red-700/50"
            }`}
          >
            {result.message}
          </div>
        )}

        <button type="submit" disabled={loading} className="btn-primary w-full">
          {loading ? "Submitting…" : "Submit Payout"}
        </button>
      </form>
    </div>
  );
}
