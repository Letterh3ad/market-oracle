// Line charts in the page's colours. Each returns the chart (call .remove() when replacing
// it), or null when the library did not load.
const colour = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function newChart(holder, height) {
  if (!window.LightweightCharts) return null;
  return window.LightweightCharts.createChart(holder, {
    width: holder.clientWidth, height,
    layout: { background: { color: colour("--bg-inset") }, textColor: colour("--text-dim") },
    grid: { vertLines: { color: colour("--border") }, horzLines: { color: colour("--border") } },
    timeScale: { borderColor: colour("--border") }, rightPriceScale: { borderColor: colour("--border") },
  });
}

function lineChart(holder, points, height = 260) {
  return linesChart(holder, [{ points, colour: "--accent" }], height);
}

// One line, added to an existing chart. l: { points, colour: CSS variable, dashed?, title?, percent? }
function addLine(chart, l) {
  const options = { color: colour(l.colour), lineWidth: 2, lineStyle: l.dashed ? 2 : 0, title: l.title ?? "" };
  if (l.percent) options.priceFormat = { type: "percent" };
  const series = chart.addLineSeries(options);
  series.setData(l.points);
  return series;
}

// lines: [{ points: [{time, value}], colour: CSS variable, dashed?, title?, percent? }]
function linesChart(holder, lines, height = 260) {
  const chart = newChart(holder, height);
  if (!chart) return null;
  for (const l of lines) addLine(chart, l);
  chart.timeScale().fitContent();
  return chart;
}

export { lineChart, linesChart, newChart, addLine };
