/**
 * BalanceCard — shows available and held balance.
 * Displays both paise and ₹ (formatted string from API).
 */
export default function BalanceCard({ balance }) {
  if (!balance) {
    return (
      <div className="card animate-pulse">
        <div className="h-4 bg-slate-700 rounded w-24 mb-4" />
        <div className="h-8 bg-slate-700 rounded w-40" />
      </div>
    );
  }

  return (
    <div className="card flex flex-col gap-6 md:flex-row md:items-center md:gap-12">
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">
          Available
        </p>
        <p className="text-4xl font-bold text-emerald-400">{balance.available_rupees}</p>
        <p className="text-xs text-slate-600 mt-1">{balance.available_paise} paise</p>
      </div>
      <div className="w-px h-12 bg-slate-800 hidden md:block" />
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-1">
          On Hold
        </p>
        <p className="text-4xl font-bold text-amber-400">{balance.held_rupees}</p>
        <p className="text-xs text-slate-600 mt-1">{balance.held_paise} paise</p>
      </div>
    </div>
  );
}
