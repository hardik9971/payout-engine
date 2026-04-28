import { useState } from "react";
import { retryPayout } from "../api";

/**
 * Payout status badge.
 */
function StatusBadge({ status }) {
  const classes = {
    PENDING: "badge-pending",
    PROCESSING: "badge-processing",
    COMPLETED: "badge-completed",
    FAILED: "badge-failed",
  };
  return (
    <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full ${classes[status] ?? ""}`}>
      {status}
    </span>
  );
}

/**
 * RetryButton — operator retry for FAILED payouts.
 *
 * Calls POST /api/v1/payouts/<id>/retry/ with X-Operator-Override: true.
 * Shows inline feedback (success / error) without leaving the table.
 * Calls onSuccess() after a successful retry so the parent refreshes all data.
 */
function RetryButton({ payoutId, onSuccess }) {
  const [state, setState] = useState("idle"); // "idle" | "loading" | "ok" | "err"
  const [errorMsg, setErrorMsg] = useState("");

  const handleRetry = async () => {
    setState("loading");
    try {
      const { data, status } = await retryPayout(payoutId);
      if (status === 201) {
        setState("ok");
        // Give the user a moment to see the tick before the table refreshes
        setTimeout(() => {
          onSuccess?.();
          setState("idle");
        }, 800);
      } else {
        setErrorMsg(data?.error ?? "Retry failed.");
        setState("err");
        setTimeout(() => setState("idle"), 3000);
      }
    } catch {
      setErrorMsg("Network error.");
      setState("err");
      setTimeout(() => setState("idle"), 3000);
    }
  };

  if (state === "ok") {
    return <span className="text-xs text-emerald-400 font-semibold">✓ Queued</span>;
  }

  if (state === "err") {
    return (
      <span className="text-xs text-red-400" title={errorMsg}>
        ✗ {errorMsg.length > 24 ? errorMsg.slice(0, 24) + "…" : errorMsg}
      </span>
    );
  }

  return (
    <button
      onClick={handleRetry}
      disabled={state === "loading"}
      className="text-xs px-2.5 py-1 rounded-lg border border-amber-600/50 text-amber-400
                 hover:bg-amber-600/20 hover:border-amber-500 active:scale-95
                 transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {state === "loading" ? "…" : "Retry"}
    </button>
  );
}

/**
 * PayoutHistory — table of all payout requests with status badges.
 * FAILED payouts show an operator Retry button that calls /retry/ endpoint.
 * Polls every 3 seconds via parent component.
 */
export default function PayoutHistory({ payouts, onSuccess }) {
  if (!payouts) {
    return (
      <div className="card animate-pulse">
        <div className="h-4 bg-slate-700 rounded w-32 mb-4" />
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-8 bg-slate-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Payout History
      </h2>
      {payouts.length === 0 ? (
        <p className="text-sm text-slate-600">No payouts yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="pb-2 font-medium">ID</th>
                <th className="pb-2 font-medium">Amount</th>
                <th className="pb-2 font-medium">Bank</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Retries</th>
                <th className="pb-2 font-medium">Created</th>
                <th className="pb-2 font-medium">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {payouts.map((p) => (
                <tr key={p.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="py-2.5 text-slate-500">#{p.id}</td>
                  <td className="py-2.5 font-medium text-slate-200">{p.amount_rupees}</td>
                  <td className="py-2.5 text-slate-400 text-xs">{p.bank_account_id}</td>
                  <td className="py-2.5">
                    <StatusBadge status={p.status} />
                  </td>
                  <td className="py-2.5 text-center text-slate-500">{p.retry_count}</td>
                  <td className="py-2.5 text-slate-500 text-xs">
                    {new Date(p.created_at).toLocaleString("en-IN")}
                  </td>
                  <td className="py-2.5">
                    {p.status === "FAILED" && (
                      <RetryButton payoutId={p.id} onSuccess={onSuccess} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
