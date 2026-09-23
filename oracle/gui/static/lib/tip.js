import { el } from "./dom.js";

// One plain-English explanation per metric, so every tab says the same thing about it.
// Tabs add their metrics here as they arrive.
const GLOSSARY = {
  source: "A website or data feed Market Oracle fetches from. Each has its own speed limit.",
  source_state:
    "ok: ready. waiting: holding back to stay under the speed limit. " +
    "paused: the source refused or failed several times in a row, so requests stop for a while.",
  requests_last_hour: "How many requests went to this source in the last hour, from every Market Oracle process.",
  universe: "Every listed company Market Oracle knows about, from FinanceDatabase. Search and screening run over it.",
  market_cap:
    "Company size by total share value: Mega (over $200bn), Large ($10-200bn), Mid ($2-10bn), " +
    "Small ($300m-2bn), Micro and Nano below that. Bands come from FinanceDatabase, not live prices.",
  listings: "The same company can trade on several exchanges. One row per company shows its main listing.",
  tags: "Your own labels on a company, shared by all its listings. Filter by several to require every one.",
  sort_direction: "Ascending: A to Z, smallest first. Descending: Z to A, biggest first. Relevance has one direction.",
  last_close: "The price at the end of the last trading day stored, in the currency the exchange quotes.",
  in_base: "That price converted to your base currency at the same day's exchange rate.",
  day_change: "How far the last close moved from the one before it.",
  stale_price: "Out of date: the last stored price is more than two weekdays old. Press Update prices.",
  signals:
    "What each analyst concluded, from the latest analysis: + leans up (bullish), - leans down (bearish), " +
    "= no clear direction. Faded ones are out of date. The Company tab explains each.",
  lean:
    "The overall call (strong sell to strong buy) with how likely it is to be right. Baseline means it comes " +
    "from price patterns and company figures only, before any AI research. The strategy account trades on it.",
  lean_stale: "Older than the latest price, so the strategy will not trade on it until you press Analyse.",
  score: "From -1 (strongly down) to +1 (strongly up). Between -0.2 and +0.2 counts as no clear direction.",
  confidence: "How much the analyst's own inputs agree with each other, from 0% to 100%.",
  strategy_account:
    "Trades by itself on the leans, under fixed rules (buy at 60% or more, at most 10 positions of up to 10% " +
    "each, 20% always kept in cash, sell on a sell lean or 8% below cost). Its record is the honest test.",
  manual_account: "Your own paper trades, scored the same way, so you can compare your picks with the strategy.",
  equity: "Cash plus every position at its last closing price, in your base currency.",
  realised: "Profit or loss locked in by selling, after every cost.",
  unrealised: "Profit or loss on shares still held, if they were sold at the last close (before selling costs).",
  costs:
    "Everything trading has cost: broker fees, UK stamp duty on London buys, the currency exchange spread " +
    "and slippage (filling a little worse than the opening price).",
  max_fall: "The largest drop from a high point in the daily equity record: how bad it got along the way.",
  pending: "Orders waiting for the next opening price. They fill when Update prices brings in that day.",
  peers:
    "Value, quality and growth compare the company with similar ones: same industry group, or the whole " +
    "sector when the group is small. More peers give a steadier comparison but take longer to fetch.",
};

// A label followed by a small "?" that explains it on hover or keyboard focus.
function tip(label, key) {
  const text = GLOSSARY[key];
  if (!text) throw new Error(`no glossary entry for "${key}"`);
  return tipText(label, text);
}

// The same, for explanations the server sends (each fundamentals figure carries its own).
function tipText(label, text) {
  const mark = el("span", { class: "tip", tabindex: "0", role: "note", "aria-label": text, "data-tip": text }, "?");
  // Open towards whichever side has room, so the text is never cut off at the window edge.
  const place = () => mark.classList.toggle("tip-left", mark.getBoundingClientRect().right + 300 > window.innerWidth);
  mark.addEventListener("mouseenter", place);
  mark.addEventListener("focus", place);
  return el("span", { class: "tip-wrap" }, label, mark);
}

export { tip, tipText, GLOSSARY };
