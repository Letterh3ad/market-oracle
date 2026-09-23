// Number formats shared by every tab.

function money(v, digits = 2) {
  return v == null ? "n/a" : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signed(v) {
  if (v == null) return "n/a";
  return `${v >= 0 ? "+" : ""}${money(v)}`;
}

function pct(v) {
  if (v == null) return "n/a";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

// Large amounts shortened: GBP 170.69B.
function big(v, currency) {
  if (v == null) return "n/a";
  for (const [size, suffix] of [[1e12, "T"], [1e9, "B"], [1e6, "M"]]) {
    if (Math.abs(v) >= size) return `${currency} ${(v / size).toFixed(2)}${suffix}`;
  }
  return `${currency} ${money(v, 0)}`;
}

// Colour class for a gain or loss.
function tone(v) {
  return v > 0 ? "up" : v < 0 ? "down" : "";
}

export { money, signed, pct, big, tone };
