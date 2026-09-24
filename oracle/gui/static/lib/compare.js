// Lines compared side by side (the Ledgers tab's accounts, a backtest's strategy), each as
// its return since a common start, with the table of their figures.
import { linesChart } from "./chart.js";
import { el } from "./dom.js";
import { money, pct, tone } from "./fmt.js";
import { tip } from "./tip.js";

const LINES = {
  strategy: { label: "Strategy", colour: "--accent" },
  ai: { label: "AI", colour: "--warn" },
  manual: { label: "Manual", colour: "--text" },
  buy_and_hold: { label: "Buy and hold", colour: "--text-dim", dashed: true },
};

// headers: [label, glossary key or null, numeric?]
function table(headers, rows, empty) {
  if (!rows.length) return el("p", { class: "dim small" }, empty);
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...headers.map(([h, key, num]) =>
      el("th", { class: num ? "num" : null }, key ? tip(h, key) : h)))),
    el("tbody", null, ...rows));
}

function swatch(name) {
  return el("span", { class: LINES[name].dashed ? "swatch dashed" : "swatch",
    style: `background: var(${LINES[name].colour}); color: var(${LINES[name].colour})` });
}

// The chart beside the table and each line's caption. `draw()` fills the chart once `node` is
// on screen (it sizes to its box) and returns it, or null when there is nothing to draw.
function compareLines(lines, base, emptyChart) {
  const holder = el("div", { class: "compare-chart" });
  const rows = lines.map((l) => el("tr", null,
    el("td", null, swatch(l.name), " ", LINES[l.name].label),
    el("td", { class: `num ${tone(l.return)}` }, l.return == null ? "no history yet" : pct(l.return)),
    el("td", { class: `num ${l.max_fall < 0 ? "down" : ""}` }, pct(l.max_fall)),
    el("td", { class: "num" }, String(l.trades)),
    el("td", { class: "num" }, money(l.costs))));
  const node = el("div", { class: "row compare" }, holder,
    el("div", null,
      table([["Line"], ["Return", "return", 1], ["Largest fall", "max_fall", 1], ["Trades", null, 1],
        [`Costs (${base})`, "costs", 1]], rows, ""),
      el("ul", { class: "dim small" }, ...lines.map((l) => el("li", null, l.caption)))));
  const draw = () => {
    const drawn = lines.filter((l) => l.points.length);
    if (!drawn.some((l) => l.points.length > 1)) {
      holder.replaceChildren(el("p", { class: "dim small" }, emptyChart));
      return null;
    }
    return linesChart(holder, drawn.map((l) => ({ ...LINES[l.name], title: LINES[l.name].label, percent: true,
      points: l.points.map(([day, r]) => ({ time: day, value: r * 100 })) })), 220);
  };
  return { node, draw };
}

export { compareLines, table };
