import { useEffect, useState, useCallback } from "react";
import { fetchBalance, fetchLedger, fetchPayouts } from "./api";
import BalanceCard from "./components/BalanceCard";
import PayoutForm from "./components/PayoutForm";
import PayoutHistory from "./components/PayoutHistory";
import LedgerTable from "./components/LedgerTable";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  const [balance, setBalance] = useState(null);
  const [payouts, setPayouts] = useState(null);
  const [ledger, setLedger] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [bal, pouts, entries] = await Promise.all([
        fetchBalance(),
        fetchPayouts(),
        fetchLedger(),
      ]);
      setBalance(bal);
      setPayouts(pouts);
      setLedger(entries);
      setLastRefreshed(new Date());
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  // Initial load + poll every 3 seconds
  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div className="min-h-screen bg-slate-950 px-4 py-10">
      <div className="max-w-5xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">
              Payout Engine
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {lastRefreshed
                ? `Last refreshed: ${lastRefreshed.toLocaleTimeString("en-IN")}`
                : "Loading…"}
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-indigo-400">
            <span className="h-2 w-2 rounded-full bg-indigo-400 animate-pulse" />
            Polling every 3s
          </div>
        </div>

        {error && (
          <div className="bg-red-900/30 border border-red-700/50 rounded-xl px-4 py-3 text-sm text-red-300">
            ⚠ {error} — Is Django running on port 8000?
          </div>
        )}

        {/* Balance Row */}
        <BalanceCard balance={balance} />

        {/* Payout Form + History side by side on wider screens */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <PayoutForm onSuccess={refresh} />
          </div>
          <div className="lg:col-span-2">
            <PayoutHistory payouts={payouts} onSuccess={refresh} />
          </div>
        </div>

        {/* Ledger */}
        <LedgerTable entries={ledger} />

        {/* Footer note */}
        <p className="text-center text-xs text-slate-700">
          All amounts stored as paise (BigInteger). Balances computed via DB aggregation only.
        </p>
      </div>
    </div>
  );
}
