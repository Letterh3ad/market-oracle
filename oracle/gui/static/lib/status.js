import { api } from "./api.js";
import { el, showError } from "./dom.js";
import { tip } from "./tip.js";
import * as control from "./control.js";
import * as tick from "./tick.js";

// The header line, the ticks panel and the sources panel, refreshed every 30 seconds.
async function refresh() {
  if (document.body.dataset.off) return; // shut down from this page
  let status;
  try {
    status = await api("/api/status");
  } catch (err) {
    showError(`status: ${err.message}`);
    return;
  }
  const u = status.universe;
  document.getElementById("universe-state").replaceChildren(
    tip("Universe", "universe"),
    u.loaded ? ` ${u.listings.toLocaleString()} listings` : ` not loaded (${u.next})`,
  );
  tick.show(status.tick, status.schedule);
  control.show(status);
  const paused = status.sources.filter((s) => s.state === "paused");
  document.getElementById("sources-state").textContent =
    paused.length ? `${paused.length} source(s) paused` : "all sources ok";
  document.getElementById("sources-body").replaceChildren(...status.sources.map((s) =>
    el("tr", { class: `state-${s.state}` },
      el("td", null, s.source),
      el("td", null, s.state + (s.paused_until ? ` until ${new Date(s.paused_until).toLocaleTimeString()}` : "")),
      el("td", null, s.reason || ""),
      el("td", { class: "num" }, `${s.rate_per_second}/s`),
      el("td", { class: "num" }, String(s.requests_last_hour)))));
}

function start() {
  document.getElementById("tick-summary").replaceChildren(tip("Ticks", "tick"));
  tick.mount(refresh);
  document.getElementById("sources-head").replaceChildren(el("tr", null,
    el("th", null, tip("Source", "source")), el("th", null, tip("State", "source_state")),
    el("th", null, "Why"), el("th", null, tip("Allowed", "source_rate")),
    el("th", null, tip("Last hour", "requests_last_hour"))));
  refresh();
  setInterval(refresh, 30_000);
}

export { start, refresh };
