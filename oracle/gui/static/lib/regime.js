// The regime account's card on the Ledgers tab: its switch, the last step's mood reading, each
// method's preset and final weight with its warnings, good signs and health, and the recent
// decisions with what each lean was made of.
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

const share = (v) => (v == null ? "" : `${Math.round(v * 100)}%`);
const name = (m) => METHODS[m] ?? m;

// The largest parts of a lean's score, strongest first: "Momentum +0.25, Trend +0.10".
function contributions(why, top = 3) {
  if (!why?.parts) return "";
  return Object.entries(why.parts).slice(0, top)
    .map(([m, v]) => `${name(m)} ${v >= 0 ? "+" : ""}${v.toFixed(2)}`).join(", ");
}

function regimeSwitch(enabled, done) {
  const box = el("input", { type: "checkbox", "aria-label": "Regime account on" });
  box.checked = enabled;
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await sendJson("/api/ledgers/regime", { enabled: box.checked }, "PUT");
      clearError();
    } catch (err) {
      showError(`Regime account switch: ${err.message}`);
    }
    done();
  });
  return el("label", { class: enabled ? "small" : "small warn-text" }, box,
    enabled ? " On: each tick's regime step trades the blend" : " Off: the regime step decides nothing");
}

function weights(s) {
  const methods = Object.keys(s.weights ?? {});
  const list = (m, key) => (s[key]?.[m] ?? []).join(", ");
  return table([["Method"], ["Preset", null, 1], ["Final", null, 1], ["Health", null, 1], ["Warnings"], ["Good signs"]],
    methods.map((m) => el("tr", null, el("td", null, name(m)),
      el("td", { class: "num" }, share(s.base_weights?.[m])), el("td", { class: "num" }, share(s.weights[m])),
      el("td", { class: "num" }, s.health?.[m] == null ? "" : `x${s.health[m].toFixed(2)}`),
      el("td", { class: "small down" }, list(m, "warnings")), el("td", { class: "small up" }, list(m, "good")))),
    "No weights read yet.");
}

function reading(card) {
  const s = card.state;
  if (!s?.mood) return [];
  const flags = (s.flags ?? []).map((f) => FLAGS[f] ?? f);
  return [
    el("p", null, el("strong", null, `Mood on ${card.day}: ${MOODS[s.mood] ?? s.mood}`),
      el("span", { class: "dim" }, ` (${s.mood})`), flags.length ? `; ${flags.join(", ")}` : "; no warning flags"),
    el("p", { class: "dim small" }, Object.entries(s.readings ?? {})
      .map(([k, v]) => `${k.replaceAll("_", " ")} ${typeof v === "number" ? v.toFixed(3) : v}`).join(", ")),
    weights(s),
  ];
}

function decisions(rows, base) {
  return table([["Day"], ["Symbol"], ["Did"], ["Why"], ["Largest parts of its score"]],
    rows.map((d) => el("tr", null, el("td", null, d.day), el("td", null, companyLink(d.symbol)),
      el("td", { class: d.action === "buy" ? "up" : d.action === "sell" ? "down" : "dim" },
        d.action + (d.cash != null ? ` ${money(d.cash)} ${base}` : "")),
      el("td", { class: "small" }, d.reason), el("td", { class: "small" }, contributions(d.explanation)))),
    "The regime account has not decided anything yet: press Regime step.");
}

// `done` repaints the tab after the switch changes.
function regimeCard(card, base, done) {
  return el("div", { class: "card" },
    el("h2", null, "Regime reading"), regimeSwitch(card.enabled, done),
    el("p", { class: "dim small" }, "The regime account blends well-studied methods, weighting each by the market's "
      + "mood, its known warning and good signs, and its own recent record (health), then trades the blend under the "
      + "strategy's rules. Rules only: no model."),
    card.note ? el("p", { class: "small warn-text" }, card.note) : null,
    ...reading(card),
    el("h3", null, "Regime decisions"), decisions(card.decisions, base));
}

export { contributions, regimeCard };
