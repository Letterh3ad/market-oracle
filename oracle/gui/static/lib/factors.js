// Price factors: 12-1 momentum and 60-day volatility, ranked across the watched, held and
// discovered set. For reading only: research and the verdicts never see them.

const rank = (p) => (p == null ? "" : String(Math.round(p * 100)));
const signed = (v) => `${v >= 0 ? "+" : ""}${Math.round(v * 100)}%`;

function factorSentence(r, setSize) {
  if (r.momentum == null && r.volatility == null) return `No price factors: ${r.note}.`;
  const parts = [];
  if (r.momentum != null) {
    parts.push(`12-1 momentum ${signed(r.momentum)}` +
      (r.momentum_pct == null ? "" : `, stronger than ${rank(r.momentum_pct)}% of the set`));
  }
  if (r.volatility != null) {
    parts.push(`60-day volatility ${Math.round(r.volatility * 100)}% a year` +
      (r.low_vol_pct == null ? "" : `, calmer than ${rank(r.low_vol_pct)}%`));
  }
  const set = `${setSize} watched, held and discovered listing${setSize === 1 ? "" : "s"}` +
    (r.in_set === false ? " (this one is not among them)" : "");
  return `${parts.join("; ")}; as of ${r.as_of}, against ${set}.${r.note ? ` Missing: ${r.note}.` : ""}`;
}

// Missing ranks sort last whichever way; ties keep the list's own order.
function sortByRank(items, bySymbol, key, descending = true) {
  const value = (i) => bySymbol[i.symbol]?.[key];
  return items.map((item, n) => ({ item, n })).sort((a, b) => {
    const x = value(a.item), y = value(b.item);
    if (x == null || y == null) return (x == null) - (y == null) || a.n - b.n;
    return (descending ? y - x : x - y) || a.n - b.n;
  }).map((w) => w.item);
}

export { factorSentence, rank, sortByRank };
