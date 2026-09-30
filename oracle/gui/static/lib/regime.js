// A regime line's card on the Ledgers tab: its switch, the last step's mood reading, each
// method's preset and final weight with its warnings, good signs and health, and the recent
// decisions with what each lean was made of. The regime-ai line's card adds the day's model
// tilt beside the rules weights, its reason or why there was none, and the model it runs on; the
// regime-v2 line's says how it rotates out of stale holdings; the core-sat line's shows its two
// sleeves (the equal-weight core and the regime satellite) and the core's next rebalance. A frozen
// line (regime-ai) keeps its card and history but has no switch.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el, showError, clearError, companyLink } from "./dom.js";
import { money } from "./fmt.js";
import { sendJson } from "./ui.js";

const MOODS = {
  calm_up: "calm and rising", volatile_up: "volatile but rising",
  calm_down: "calm but falling", volatile_down: "volatile and falling",
};
const FLAGS = {
  clustered: "stocks moving together", rebound: "sharp rebound after a falling year", choppy: "choppy trend",
};
const METHODS = {
  trend: "Trend", momentum: "Momentum", reversion: "Mean reversion", low_vol: "Low volatility",
  quality_value: "Value and quality", earnings_drift: "Earnings drift", hold: "Buy and hold", ai_verdict: "AI verdict",
};
const LINES = {
  regime: { name: "Regime", path: "/api/ledgers/regime", step: "Regime step",
    about: "Rules only: no model." },
  "regime-ai": { name: "Regime-AI", path: "/api/ledgers/regime-ai", step: "Regime-AI step",
    about: "Then, once a day, a model multiplies each weight within the tilt cap, saying why; "
      + "if it fails the rules weights are traded. Compare its record with the Regime line's to see what the model adds." },
  "regime-v2": { name: "Regime-v2", path: "/api/ledgers/regime-v2", step: "Regime-v2 step",
    about: "Rules only, with two changes: a method without a score for a listing hands its weight to the price "
      + "methods (trend, momentum, reversion, low volatility), not to the steady buy-and-hold score, and it rotates "
      + "out of long-held, clearly weaker holdings. Compare its record with the Regime line's to see what they add." },
  "core-sat": { name: "Core-sat", path: "/api/ledgers/core-sat", step: "Core-sat step",
    about: "Here the blend runs only the satellite, a quarter of the money in its own cash; the other three "
      + "quarters are an equal-weight buy and hold of the main list's companies, set back to equal weight once a year." },
};

const share = (v) => (v == null ? "" : `${Math.round(v * 100)}%`);
const name = (m) => METHODS[m] ?? m;
const lineOf = (card) => LINES[card.account] ?? LINES.regime;

// The largest parts of a lean's score, strongest first: "Momentum +0.25, Trend +0.10".
function contributions(why, top = 3) {
  if (!why?.parts) return "";
  return Object.entries(why.parts).slice(0, top)
    .map(([m, v]) => `${name(m)} ${v >= 0 ? "+" : ""}${v.toFixed(2)}`).join(", ");
}

function lineSwitch(card, done) {
  const line = lineOf(card);
  const box = el("input", { type: "checkbox", "aria-label": `${line.name} account on` });
  box.checked = card.enabled;
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await sendJson(line.path, { enabled: box.checked }, "PUT");
      clearError();
    } catch (err) {
      showError(`${line.name} account switch: ${err.message}`);
    }
    done();
  });
  const step = line.step.replace(" step", "").toLowerCase();
  return el("label", { class: card.enabled ? "small" : "small warn-text" }, box,
    card.enabled ? ` On: each tick's ${step} step trades the blend` : ` Off: the ${step} step decides nothing`);
}

// Which model tilts the regime-ai line: "" is the research slot's, saved to oracle.toml at once.
function modelChoice(card, done) {
  const pick = el("select", { "aria-label": "Regime-AI tilt model" },
    el("option", { value: "" }, `research slot's (${card.research_profile})`));
  pick.value = card.model ?? "";
  api("/api/models").then(({ profiles }) => {
    pick.append(...profiles.map((p) => el("option", { value: p.name }, p.name)));
    pick.value = card.model ?? "";
  }).catch(() => {});
  pick.addEventListener("change", async () => {
    pick.disabled = true;
    try {
      await sendJson("/api/ledgers/regime-ai", { model: pick.value }, "PUT");
      clearError();
    } catch (err) {
      showError(`Regime-AI tilt model: ${err.message}`);
    }
    done();
  });
  return el("label", { class: "small" }, "Tilt model: ", pick);
}

function weights(s, tilt) {
  const methods = Object.keys(s.weights ?? {});
  const list = (m, key) => (s[key]?.[m] ?? []).join(", ");
  const tilted = tilt != null;
  const heads = [["Method"], ["Preset", null, 1], ...(tilted ? [["Rules", null, 1], ["Tilt", null, 1]] : []),
    ["Final", null, 1], ["Health", null, 1], ["Warnings"], ["Good signs"]];
  const x = (v) => (v == null ? "" : `x${v.toFixed(2)}`);
  return table(heads,
    methods.map((m) => el("tr", null, el("td", null, name(m)),
      el("td", { class: "num" }, share(s.base_weights?.[m])),
      tilted ? el("td", { class: "num" }, share(tilt.rules?.[m])) : null,
      tilted ? el("td", { class: "num" }, x(tilt.tilt?.[m])) : null,
      el("td", { class: "num" }, share(s.weights[m])),
      el("td", { class: s.recorded && !s.recorded.includes(m) ? "dim small" : "num" },
        s.recorded && !s.recorded.includes(m) ? "no record yet" : x(s.health?.[m])),
      el("td", { class: "small down" }, list(m, "warnings")), el("td", { class: "small up" }, list(m, "good")))),
    "No weights read yet.");
}

// The day's tilt reason, or why there was none, with the model that gave it.
function tiltNote(tilt) {
  if (!tilt) return null;
  const by = tilt.model ? ` (${tilt.model})` : "";
  if (tilt.error) {
    return el("p", { class: "small warn-text" },
      `No model tilt on ${tilt.day}: ${tilt.error}. The rules weights were traded${by ? `; model${by}` : ""}.`);
  }
  return el("p", { class: "small" }, el("strong", null, `Model tilt on ${tilt.day}${by}: `), tilt.reason ?? "");
}

function reading(card) {
  const s = card.state;
  if (!s?.mood) return [];
  const flags = (s.flags ?? []).map((f) => FLAGS[f] ?? f);
  const tilt = card.account === "regime-ai" ? card.tilt ?? { tilt: {}, rules: {} } : null;
  return [
    el("p", null, el("strong", null, `Mood on ${card.day}: ${MOODS[s.mood] ?? s.mood}`),
      el("span", { class: "dim" }, ` (${s.mood})`), flags.length ? `; ${flags.join(", ")}` : "; no warning flags"),
    el("p", { class: "dim small" }, Object.entries(s.readings ?? {})
      .map(([k, v]) => `${k.replaceAll("_", " ")} ${typeof v === "number" ? v.toFixed(3) : v}`).join(", ")),
    tiltNote(card.tilt),
    weights(s, tilt),
    signs(s.signs ?? {}),
    unscored(s.unscored ?? [], card.account === "regime-v2"),
  ];
}

// Each firing warning and good sign with why it matters and where it comes from.
function signs(byMethod) {
  const rows = Object.entries(byMethod).flatMap(([m, list]) => list.map((x) => el("li", { class: "small" },
    el("span", { class: x.warning ? "down" : "up" }, `${name(m)} ${x.warning ? "warning" : "good sign"}: ${x.name}. `),
    x.reason, x.source ? el("span", { class: "dim" }, ` (${x.source})`) : null)));
  return rows.length ? el("ul", null, ...rows) : null;
}

// Weighted methods no listing had a score from on the last step: their weight went to the others
// (on regime-v2 to the price methods only).
function unscored(methods, v2) {
  if (!methods.length) return null;
  const why = methods.includes("earnings_drift")
    ? " Earnings drift needs stored earnings dates, which are fetched only when a company's After earnings study is opened."
    : "";
  return el("p", { class: "small warn-text" },
    `No listing had a score from ${methods.map(name).join(", ")} on this step: their weight went to the ${v2 ? "price" : "other"} methods.${why}`);
}

function decisions(card, base) {
  return table([["Day"], ["Symbol"], ["Did"], ["Why"], ["Largest parts of its score"]],
    card.decisions.map((d) => el("tr", null, el("td", null, d.day), el("td", null, companyLink(d.symbol)),
      el("td", { class: d.action === "buy" ? "up" : d.action === "sell" ? "down" : "dim" },
        d.action + (d.cash != null ? ` ${money(d.cash)} ${base}` : "")),
      el("td", { class: "small" }, d.reason), el("td", { class: "small" }, contributions(d.explanation)))),
    `The ${lineOf(card).name.toLowerCase()} account has not decided anything yet: press ${lineOf(card).step}.`);
}

// regime-v2's rotation rule as configured; each rotation sale's Why names the buy it made room for.
function rotation(r) {
  if (!r) return null;
  return el("p", { class: "small" }, el("strong", null, "Rotation: "),
    `a holding kept ${r.min_hold} trading days or more whose blended score is ${r.margin.toFixed(2)} or more under `
    + `the best buy not held (after the sector cap) is sold to make room for it, weakest first, at most ${r.most} `
    + "a step. The freed slot is bought by the usual rules once the sale's cash is back.");
}

// core-sat's two sleeves: each one's cash and holdings, and when the core next rebalances.
function sleeves(s, base) {
  if (!s) return null;
  const row = (label, x) => el("tr", null, el("td", null, label), el("td", { class: "num" }, money(x.capital, 0)),
    el("td", { class: "num" }, money(x.cash, 0)), el("td", { class: "num" }, money(x.holdings, 0)),
    el("td", { class: "num" }, String(x.positions)), el("td", { class: "num" }, String(x.pending)));
  const next = s.core.next_rebalance
    ? `The core next sets itself back to equal weight on or after ${s.core.next_rebalance} (trims first, then the laggards once they fill).`
    : "The core buys its equal shares at the next step.";
  return el("div", null,
    table([["Sleeve"], [`Start (${base})`, null, 1], ["Cash", null, 1], ["Holdings", null, 1], ["Positions", null, 1],
      ["Waiting", null, 1]], [row(`Core (${s.core.listings} companies)`, s.core), row("Satellite", s.satellite)]),
    el("p", { class: "dim small" }, next));
}

// `done` repaints the tab after the switch or the model changes.
function regimeCard(card, base, done) {
  const line = lineOf(card);
  const ai = card.account === "regime-ai";
  return el("div", { class: "card" },
    el("h2", null, `${line.name} reading`), card.frozen ? el("span", { class: "badge" }, "frozen")
      : lineSwitch(card, done), ai && !card.frozen ? " " : null, ai && !card.frozen ? modelChoice(card, done) : null,
    el("p", { class: "dim small" }, "The account blends well-studied methods, weighting each by the market's "
      + "mood, its known warning and good signs, and its own recent record (health), then trades the blend under the "
      + `strategy's rules. ${line.about}`),
    rotation(card.rotation),
    sleeves(card.sleeves, base),
    card.note ? el("p", { class: "small warn-text" }, card.note) : null,
    card.lag ? el("p", { class: "small warn-text" }, `Warning: ${card.lag}.`) : null,
    ...reading(card),
    el("h3", null, `${line.name} decisions`), decisions(card, base));
}

export { contributions, regimeCard };
