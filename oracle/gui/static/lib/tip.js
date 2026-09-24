import { el } from "./dom.js";

// One plain-English explanation per metric, so every tab says the same thing about it.
// Tabs add their metrics here as they arrive.
const GLOSSARY = {
  source: "A website or data feed Market Oracle fetches from. Each has its own speed limit.",
  source_state:
    "ok: ready. waiting: holding back to stay under the speed limit. " +
    "paused: the source refused or failed several times in a row, so requests stop for a while.",
  source_rate: "The most requests per second Market Oracle lets itself send there, set in oracle.toml.",
  tick:
    "A tick is the daily routine in one go: fetch new prices and exchange rates, fill waiting paper orders, " +
    "analyse every watched listing, then let the strategy account decide. A stage that fails is noted and " +
    "the rest still run.",
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
  manual_account: "Your own paper trades, scored the same way, so you can compare your picks with the strategy.",
  equity: "Cash plus every position at its last closing price, in your base currency.",
  return: "Equity now against the starting amount: +5% means 10,000 has become 10,500.",
  cash: "Money not in any position. Waiting buys have not spent theirs yet.",
  position_cost: "What the shares cost, fees and other costs included (the average if bought more than once).",
  position_value: "The shares at their last closing price, converted at that day's exchange rate.",
  proceeds: "Cash received for the shares sold, after fees, the exchange spread and slippage.",
  realised: "Profit or loss locked in by selling, after every cost.",
  unrealised: "Profit or loss on shares still held, if they were sold at the last close (before selling costs).",
  costs:
    "Everything trading has cost: broker fees, UK stamp duty on London buys, the currency exchange spread " +
    "and slippage (filling a little worse than the opening price).",
  max_fall: "The largest drop from a high point in the daily equity record: how bad it got along the way.",
  pending: "Orders waiting for the next opening price. They fill when Update prices brings in that day.",
  research:
    "An AI model searches news, filings and the web about the company, then a small team of AI roles writes a " +
    "report from what it found. By default the model runs on this machine (Ollama) and takes about a minute; " +
    "[llm] backend in oracle.toml can switch it to Claude.",
  research_unvalidated:
    "Unvalidated: AI research has not yet proven itself. It will count only once the strategy account, using " +
    "it, beats price patterns and company figures alone over enough paper trades. It never changes the lean.",
  bull_case: "The strongest honest case for buying now, from the evidence found.",
  bear_case: "The strongest honest case against buying now, from the same evidence.",
  research_findings: "What each analyst role concluded. Every claim cites the evidence it rests on; uncited claims are dropped.",
  risks: "What could hurt someone holding the shares soon.",
  invalidated_if: "A plain condition that, if it happens, means the bull case was wrong.",
  verdict:
    "The research's own call: buy, hold or sell, with a confidence from 0 to 1. It is written last, from the " +
    "analyst statistics, the lean, the recent price change, every step of the research and the roles above. " +
    "A reply that fails the checks is left out, never guessed at.",
  verdict_unread:
    "The model reads only so much at once. The statistics always fit; after them come the researcher's notes, " +
    "the roles' outputs, the cited evidence and the tool results, and what did not fit is counted here.",
  research_rejections:
    "What was left out of this report. A role's answer is unusable when it is not in the shape asked for; " +
    "a claim is dropped when it has no text, cites nothing, or cites something the research was never shown.",
  evidence:
    "The articles, filings and pages the research was shown, by id. [a3] in the report points here. " +
    "'stored data' is Market Oracle's own prices, fundamentals and analyst signals.",
  research_trace: "Every step the model took: what it thought, which tool it used, and what came back.",
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
