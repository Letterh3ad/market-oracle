// Range choices for a price chart backed by a route capped at `maxDays`. Pulled out of the
// Company tab so the picker's logic is testable without a DOM.
const RANGES = [["1y", 365, "1y"], ["2y", 730, "2y"], ["5y", 1825, "5y"]];
const CAPTIONS = { "1y": "Last year's", "2y": "Last 2 years'", "5y": "Last 5 years'", all: "All stored" };

// [key, days, label] triples; "all" resolves to the route's cap.
function chartRangeOptions(maxDays) {
  return [...RANGES, ["all", maxDays, "All"]];
}

function chartRangeCaption(key) {
  return CAPTIONS[key] ?? CAPTIONS["1y"];
}

// The range key for a day count, defaulting to 1y when nothing matches.
function chartRangeKey(days, maxDays) {
  const found = chartRangeOptions(maxDays).find(([, d]) => d === days);
  return found ? found[0] : "1y";
}

export { chartRangeOptions, chartRangeCaption, chartRangeKey };
