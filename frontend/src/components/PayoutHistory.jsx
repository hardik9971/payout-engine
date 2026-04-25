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
 * PayoutHistory — table of all payout requests with status badges.
 * Polls every 3 seconds via parent component.
 */
export default function PayoutHistory({ payouts }) {
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
