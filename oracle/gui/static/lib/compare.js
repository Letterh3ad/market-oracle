// Lines compared side by side (the Ledgers tab's accounts, a backtest's strategy, a replay's
// account), each as its return since a common start, with the table of their figures. The
// chart sits full width above the table, resizable by a drag handle on its bottom edge, with
// per-line toggle chips: click hides/shows a line and rescales y to whatever is still visible,
// double-click shows only that line, "All" restores every line. Hidden lines stay in the
// table, greyed. Height and the hidden set are remembered per chart in localStorage, keyed by
// `storageKey` (each caller needs its own, distinct key).
import { addLine, newChart } from "./chart.js";
import { el } from "./dom.js";
import { money, pct, tone } from "./fmt.js";
import { tip } from "./tip.js";

const LINES = {
  index: { label: "Index (ACWI)", colour: "--bench1" },
  strategy: { label: "Strategy", colour: "--accent" },
  ai: { label: "AI", colour: "--warn" },
  manual: { label: "Manual", colour: "--text" },
  regime: { label: "Regime", colour: "--regime" },
  "regime-ai": { label: "Regime-AI", colour: "--regime-ai" },
  "regime-v2": { label: "Regime-v2", colour: "--regime-v2" },
  regime_v2: { label: "Regime-v2", colour: "--regime-v2" },
  "core-sat": { label: "Core-sat", colour: "--core-sat" },
  core_sat: { label: "Core-sat", colour: "--core-sat" },
  core_sat_laya: { label: "Core-sat-laya", colour: "--laya" },
  laya: { label: "Laya", colour: "--laya" },
  buy_and_hold: { label: "Buy and hold (today's list)", colour: "--text-dim", dashed: true },
};
const BENCH_COLOURS = ["--bench1", "--bench2"];

const DEFAULT_HEIGHT = 360;
const MIN_HEIGHT = 120;
const MAX_HEIGHT = 800;

// Every account and buy_and_hold has a fixed entry above; a benchmark's name is whichever
// symbol is configured, so its label and colour come from the line itself and its order
// among the other benchmarks, dashed like buy and hold (another line held, not traded).
function lineInfos(lines) {
  const out = {};
  let bench = 0;
  for (const l of lines) {
    out[l.name] = LINES[l.name] ?? { label: l.label || l.name, colour: BENCH_COLOURS[bench++ % BENCH_COLOURS.length],
      dashed: true };
  }
  return out;
}

// headers: [label, glossary key or null, numeric?]
function table(headers, rows, empty) {
  if (!rows.length) return el("p", { class: "dim small" }, empty);
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...headers.map(([h, key, num]) =>
      el("th", { class: num ? "num" : null }, key ? tip(h, key) : h)))),
    el("tbody", null, ...rows));
}

function swatch(info) {
  return el("span", { class: info.dashed ? "swatch dashed" : "swatch",
    style: `background: var(${info.colour}); color: var(${info.colour})` });
}

// localStorage is third-party state (quota, private browsing, disabled storage): every access
// is wrapped so a failure there never breaks the chart, it just forgets height/hidden on reload.
function readState(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { height: DEFAULT_HEIGHT, hidden: [] };
    const parsed = JSON.parse(raw);
    const height = Number(parsed.height);
    return { height: Number.isFinite(height) ? height : DEFAULT_HEIGHT,
      hidden: Array.isArray(parsed.hidden) ? parsed.hidden : [] };
  } catch {
    return { height: DEFAULT_HEIGHT, hidden: [] };
  }
}

function writeState(key, state) {
  try {
    localStorage.setItem(key, JSON.stringify(state));
  } catch {
    // ignore: nothing to recover, the chart still works this session
  }
}

function clampHeight(height) {
  return Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, height));
}

// Click: toggle one line in or out of the hidden set.
function afterClick(hidden, name) {
  const next = new Set(hidden);
  if (next.has(name)) next.delete(name); else next.add(name);
  return next;
}

// Double-click: show only this line, whatever was hidden before.
function afterDoubleClick(names, name) {
  return new Set(names.filter((n) => n !== name));
}

// The chart above the table and each line's caption. `draw()` fills the chart once `node` is
// on screen (it sizes to its box); the chart itself is created, resized and its series toggled
// entirely inside this closure, so it is never handed out. Call `destroy()` once this card's
// node is about to be discarded (on the next full redraw), not before.
function compareLines(lines, base, emptyChart, storageKey) {
  const info = lineInfos(lines);
  const chartable = lines.filter((l) => l.points.length); // lines with at least one point to plot
  const hasChart = chartable.some((l) => l.points.length > 1);
  const names = chartable.map((l) => l.name);
  const stored = readState(storageKey);
  const state = { height: clampHeight(stored.height), hidden: new Set(stored.hidden.filter((n) => names.includes(n))) };

  let chart = null;
  const series = new Map(); // name -> series currently on the chart

  const box = el("div", { class: "compare-chart" });
  box.style.height = `${state.height}px`;
  const handle = el("div", { class: "compare-chart-resize", "aria-hidden": "true" });
  const chips = el("div", { class: "row compare-chips" });

  const rows = lines.map((l) => el("tr", { dataset: { line: l.name } },
    el("td", null, swatch(info[l.name]), " ", info[l.name].label),
    el("td", { class: `num ${tone(l.return)}` }, l.return == null ? "no history yet" : pct(l.return)),
    el("td", { class: `num ${l.max_fall < 0 ? "down" : ""}` }, pct(l.max_fall)),
    el("td", { class: "num" }, String(l.trades)),
    el("td", { class: "num" }, money(l.costs))));

  function persist() {
    writeState(storageKey, { height: state.height, hidden: [...state.hidden] });
  }

  function applyVisibility() {
    for (const chip of chips.children) {
      if (chip.dataset.line) chip.classList.toggle("chip-off", state.hidden.has(chip.dataset.line));
    }
    for (const tr of rows) tr.classList.toggle("faded", state.hidden.has(tr.dataset.line));
  }

  // Adds or drops series to match the hidden set; never recreates the chart itself, so the
  // one instance this component owns stays valid for the caller to remove when it is done.
  function syncSeries() {
    if (!hasChart) return;
    if (!chart) {
      box.replaceChildren(); // drop any "all hidden"/empty message left over from before
      chart = newChart(box, state.height);
    }
    if (!chart) return; // library not loaded
    let changed = false;
    for (const l of chartable) {
      const visible = !state.hidden.has(l.name);
      const has = series.has(l.name);
      if (visible && !has) {
        series.set(l.name, addLine(chart, { ...info[l.name], title: info[l.name].label, percent: true,
          points: l.points.map(([day, r]) => ({ time: day, value: r * 100 })) }));
        changed = true;
      } else if (!visible && has) {
        chart.removeSeries(series.get(l.name));
        series.delete(l.name);
        changed = true;
      }
    }
    if (changed) chart.timeScale().fitContent();
  }

  function disposeChart() {
    chart?.remove();
    chart = null;
    series.clear();
  }

  function render() {
    if (!hasChart) {
      box.replaceChildren(el("p", { class: "dim small" }, emptyChart));
    } else if (!chartable.some((l) => !state.hidden.has(l.name) && l.points.length > 1)) {
      disposeChart();
      box.replaceChildren(el("p", { class: "dim small" }, "All lines hidden."));
    } else {
      syncSeries();
    }
    applyVisibility();
  }

  function toggle(next) {
    state.hidden = next;
    persist();
    render();
  }

  if (hasChart) {
    chips.append(el("button", { type: "button", class: "chip", onclick: () => toggle(new Set()) }, "All"));
    for (const l of chartable) {
      const chip = el("button", { type: "button", class: "chip", dataset: { line: l.name } },
        swatch(info[l.name]), " ", info[l.name].label);
      chip.addEventListener("click", () => toggle(afterClick(state.hidden, l.name)));
      chip.addEventListener("dblclick", () => toggle(afterDoubleClick(names, l.name)));
      chips.append(chip);
    }

    let dragStartY = 0;
    let dragStartHeight = 0;
    const onPointerMove = (e) => {
      state.height = clampHeight(dragStartHeight + (e.clientY - dragStartY));
      box.style.height = `${state.height}px`;
      chart?.resize?.(box.clientWidth, state.height);
    };
    const onPointerUp = () => {
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerup", onPointerUp);
      persist();
    };
    handle.addEventListener("pointerdown", (e) => {
      dragStartY = e.clientY;
      dragStartHeight = state.height;
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
      e.preventDefault?.();
    });
  }

  applyVisibility(); // so a restored hidden set shows greyed/off immediately, even before draw()

  const node = el("div", { class: "compare" },
    hasChart ? chips : null, box, hasChart ? handle : null,
    table([["Line"], ["Return", "return", 1], ["Largest fall", "max_fall", 1], ["Trades", null, 1],
      [`Costs (${base})`, "costs", 1]], rows, ""),
    el("ul", { class: "dim small" }, ...lines.map((l) => el("li", null, l.caption))));

  return { node, draw: render, destroy: disposeChart };
}

export { compareLines, lineInfos, table };
