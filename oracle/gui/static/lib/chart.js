// Line charts in the page's colours. Returns the chart (call .remove() when replacing it),
// or null when the library did not load.
function lineChart(holder, points, height = 260) {
  if (!window.LightweightCharts) return null;
  const css = getComputedStyle(document.documentElement);
  const c = (name) => css.getPropertyValue(name).trim();
  const chart = window.LightweightCharts.createChart(holder, {
    width: holder.clientWidth, height,
    layout: { background: { color: c("--bg-inset") }, textColor: c("--text-dim") },
    grid: { vertLines: { color: c("--border") }, horzLines: { color: c("--border") } },
    timeScale: { borderColor: c("--border") }, rightPriceScale: { borderColor: c("--border") },
  });
  chart.addLineSeries({ color: c("--accent"), lineWidth: 2 }).setData(points);
  chart.timeScale().fitContent();
  return chart;
}

export { lineChart };
