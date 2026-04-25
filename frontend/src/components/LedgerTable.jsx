/**
 * LedgerTable — displays the merchant's ledger entries.
 * CREDIT/RELEASE appear in green; DEBIT/HOLD appear in red/amber.
 */
const ENTRY_COLORS = {
  CREDIT: "text-emerald-400",
  RELEASE: "text-emerald-400",
  DEBIT: "text-red-400",
  HOLD: "text-amber-400",
};

const ENTRY_SIGN = {
  CREDIT: "+",
  RELEASE: "+",
  DEBIT: "−",
  HOLD: "−",
};

export default function LedgerTable({ entries }) {
  if (!entries) {
    return (
      <div className="card animate-pulse">
        <div className="h-4 bg-slate-700 rounded w-32 mb-4" />
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-7 bg-slate-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Ledger
      </h2>
      {entries.length === 0 ? (
        <p className="text-sm text-slate-600">No ledger entries.</p>
      ) : (
        <div className="overflow-x-auto max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-900 z-10">
              <tr className="text-left text-slate-500 border-b border-slate-800">
                <th className="pb-2 font-medium">Type</th>
                <th className="pb-2 font-medium">Amount</th>
                <th className="pb-2 font-medium">Payout</th>
                <th className="pb-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {entries.map((e) => (
                <tr key={e.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="py-2">
                    <span className={`font-semibold ${ENTRY_COLORS[e.entry_type]}`}>
                      {e.entry_type}
                    </span>
                  </td>
                  <td className={`py-2 font-medium ${ENTRY_COLORS[e.entry_type]}`}>
                    {ENTRY_SIGN[e.entry_type]}
                    {e.amount_rupees}
                  </td>
                  <td className="py-2 text-slate-500 text-xs">
                    {e.payout_id ? `#${e.payout_id}` : "—"}
                  </td>
                  <td className="py-2 text-slate-500 text-xs">
                    {new Date(e.created_at).toLocaleString("en-IN")}
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
