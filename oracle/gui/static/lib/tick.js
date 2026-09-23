import { api } from "./api.js";
import { el, showError } from "./dom.js";
import { busyButton } from "./ui.js";

const OUTCOME_CLASS = { ok: "up", partial: "warn-text", failed: "down", skipped: "dim" };

function when(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function outcome(value) {
  return el("span", { class: OUTCOME_CLASS[value] ?? "" }, value);
}

// Built once, so the 30-second refresh never replaces a button mid-run.
function mount(refresh) {
  document.getElementById("tick-controls").replaceChildren(busyButton("Run tick now", async () => {
    try {
      await api("/api/jobs/tick", { method: "POST" });
    } catch (err) {
      showError(`tick: ${err.message}`);
    }
    await refresh();
  }));
}

function show(tick) {
  document.getElementById("tick-state").replaceChildren(
    tick ? el("span", null, "Last tick ", outcome(tick.outcome), ` ${when(tick.started_at)}`) : "No tick yet");
  if (!tick) {
    document.getElementById("tick-last").replaceChildren(el("p", { class: "dim" }, "No tick has run yet."));
    return;
  }
  document.getElementById("tick-last").replaceChildren(
    el("p", null, `Last tick ${when(tick.started_at)} (${tick.trigger}): `, outcome(tick.outcome),
      tick.note ? ` (${tick.note})` : "",
      tick.first_error ? el("span", { class: "small dim" }, `. First error: ${tick.first_error}`) : ""),
    tick.stages.length ? el("table", null, el("tbody", null, ...tick.stages.map((s) => el("tr", null,
      el("td", null, s.name), el("td", null, outcome(s.state)), el("td", null, s.summary),
      el("td", { class: "small" }, s.error || ""))))) : "");
}

export { mount, show };
