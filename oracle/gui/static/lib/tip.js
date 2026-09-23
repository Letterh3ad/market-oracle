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
};

// A label followed by a small "?" that explains it on hover or keyboard focus.
function tip(label, key) {
  const text = GLOSSARY[key];
  if (!text) throw new Error(`no glossary entry for "${key}"`);
  return el("span", { class: "tip-wrap" },
    label,
    el("span", { class: "tip", tabindex: "0", role: "note", "aria-label": text, "data-tip": text }, "?"));
}

export { tip, GLOSSARY };
