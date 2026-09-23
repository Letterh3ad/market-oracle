import { el, showError } from "./dom.js";
import { busyButton, runJob, sendJson } from "./ui.js";

const OUTCOME_CLASS = { ok: "up", partial: "warn-text", failed: "down", interrupted: "down", skipped: "dim" };

function when(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function outcome(value) {
  return el("span", { class: OUTCOME_CLASS[value] ?? "" }, value);
}

const toggle = el("input", { type: "checkbox", id: "schedule-on" });
const time = el("input", { type: "time", id: "schedule-time", required: "" });

// Built once, so the 30-second refresh never replaces a button mid-run or a field being edited.
function mount(refresh) {
  const save = async () => {
    try {
      await sendJson("/api/settings/schedule", { enabled: toggle.checked, time: time.value }, "PUT");
    } catch (err) {
      showError(`schedule: ${err.message}`);
    }
    await refresh();
  };
  toggle.addEventListener("change", save);
  time.addEventListener("change", () => { if (time.value) save(); });
  document.getElementById("tick-controls").replaceChildren(
    busyButton("Run tick now", async (onState) => {
      try {
        await runJob("/api/jobs/tick", onState);
      } catch (err) {
        showError(`tick: ${err.message}`);
      }
      await refresh();
    }),
    el("label", null, toggle, " Run automatically on weekdays at "), time,
    el("span", { id: "schedule-next", class: "dim small" }));
}

function showSchedule(schedule) {
  toggle.checked = schedule.enabled;
  if (document.activeElement !== time) time.value = schedule.time;
  let text;
  if (schedule.enabled) {
    text = `Next run ${when(schedule.next_run)}, while this app is open.`;
  } else if (schedule.watched) {
    text = "Scheduling is off. Turn it on so the strategy account trades by itself.";
  } else {
    text = "Scheduling is off.";
  }
  document.getElementById("schedule-next").textContent = text;
}

function show(tick, schedule) {
  showSchedule(schedule);
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
