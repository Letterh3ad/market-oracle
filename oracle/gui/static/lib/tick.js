import { el, showError } from "./dom.js";
import { api } from "./api.js";
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
const warnMinutes = el("input", { type: "number", min: "0", max: "120", step: "1", class: "tag-input" });
const whenIdle = el("input", { type: "checkbox" });
const idleMinutes = el("input", { type: "number", min: "1", max: "240", step: "1", class: "tag-input" });
const middayOn = el("input", { type: "checkbox" });
const middayTime = el("input", { type: "time" });
const middayBudget = el("input", { type: "number", min: "1", max: "480", step: "1", class: "tag-input" });
const DEFAULT_MIDDAY = "12:30";
let refreshAll = async () => {};

async function saveSetting(key, value) {
  try {
    await sendJson("/api/settings", { section: "schedule", key, value }, "PUT");
  } catch (err) {
    showError(`${key}: ${err.message}`);
  }
  await refreshAll();
}

function runButton(label, path) {
  return busyButton(label, async (onState) => {
    try {
      await runJob(path, onState);
    } catch (err) {
      showError(`${label}: ${err.message}`);
    }
    await refreshAll();
  });
}

// Built once, so the 30-second refresh never replaces a button mid-run or a field being edited.
function mount(refresh) {
  refreshAll = refresh;
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
  const saveWarning = async () => {
    try {
      await sendJson("/api/settings/tick-warning", { warn_minutes: Number(warnMinutes.value),
        research_when_idle: whenIdle.checked, idle_minutes: Number(idleMinutes.value) }, "PUT");
    } catch (err) {
      showError(`tick warning: ${err.message}`);
    }
    await refresh();
  };
  for (const input of [warnMinutes, whenIdle, idleMinutes]) input.addEventListener("change", saveWarning);
  const saveMidday = () => saveSetting("midday_time", middayOn.checked ? middayTime.value || DEFAULT_MIDDAY : "");
  middayOn.addEventListener("change", saveMidday);
  middayTime.addEventListener("change", () => { if (middayTime.value && middayOn.checked) saveMidday(); });
  middayBudget.addEventListener("change", () => saveSetting("midday_budget_min", Number(middayBudget.value)));
  document.getElementById("tick-controls").replaceChildren(
    runButton("Run tick now", "/api/jobs/tick"),
    el("label", null, toggle, " Run automatically on weekdays at "), time,
    el("span", { id: "schedule-next", class: "dim small" }));
  document.getElementById("tick-options").replaceChildren(
    el("label", null, "Warn ", warnMinutes, " minutes before a scheduled tick (0: no warning)"),
    el("label", null, whenIdle, " Research only after the PC has been idle "), idleMinutes,
    el("span", null, " minutes (otherwise it waits for the next tick)"));
  document.getElementById("midday-options").replaceChildren(
    runButton("Run midday pass now", "/api/jobs/midday"),
    el("label", { title: "Discovery, then research on stale listings. No prices, no strategy or AI steps, no orders." },
      middayOn, " Midday pass on weekdays at "), middayTime,
    el("label", null, " for up to ", middayBudget, " minutes"),
    el("span", { id: "midday-next", class: "dim small" }));
}

function showSchedule(schedule) {
  toggle.checked = schedule.enabled;
  if (document.activeElement !== time) time.value = schedule.time;
  if (document.activeElement !== warnMinutes) warnMinutes.value = schedule.warn_minutes;
  if (document.activeElement !== idleMinutes) idleMinutes.value = schedule.idle_minutes;
  whenIdle.checked = schedule.research_when_idle;
  middayOn.checked = Boolean(schedule.midday_time);
  if (document.activeElement !== middayTime) middayTime.value = schedule.midday_time || middayTime.value || DEFAULT_MIDDAY;
  if (document.activeElement !== middayBudget) middayBudget.value = schedule.midday_budget_min;
  document.getElementById("midday-next").textContent = schedule.midday_next
    ? `Next ${when(schedule.midday_next)}: discovery and research only, never trades.`
    : `Discovery and research only, never trades${schedule.midday_time ? "; runs while Run automatically is on" : ""}.`;
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

// One run, tick or midday pass: its outcome line and a row per stage.
function lastRun(run) {
  return [
    el("p", null, `Last ${run.label.toLowerCase()} ${when(run.started_at)} (${run.trigger}): `, outcome(run.outcome),
      run.note ? ` (${run.note})` : "",
      run.first_error ? el("span", { class: "small dim" }, `. First error: ${run.first_error}`) : ""),
    run.stages.length ? el("table", null, el("tbody", null, ...run.stages.map((s) => el("tr", null,
      el("td", null, s.name), el("td", null, outcome(s.state)), el("td", null, s.summary),
      el("td", { class: "small" }, s.error || ""))))) : "",
  ];
}

function show(tick, schedule, midday) {
  showSchedule(schedule);
  document.getElementById("tick-state").replaceChildren(
    tick ? el("span", null, "Last tick ", outcome(tick.outcome), ` ${when(tick.started_at)}`) : "No tick yet");
  document.getElementById("tick-last").replaceChildren(
    ...(tick ? lastRun(tick) : [el("p", { class: "dim" }, "No tick has run yet.")]),
    ...(midday ? lastRun(midday) : []));
}

function hhmm(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

async function act(what, path) {
  try {
    await api(path, { method: "POST" });
  } catch (err) {
    showError(`${what}: ${err.message}`);
  }
  await refreshAll();
}

let notified = null; // the warning last sent as a browser notification, so each is sent once

function notify(warning) {
  const key = `${warning.due}/${warning.at}`;
  if (notified === key || !("Notification" in window) || Notification.permission !== "granted") return;
  notified = key;
  new Notification("Market Oracle",
    { body: `${warning.label} at ${hhmm(warning.at)}. Postpone or skip it on the Oracle page.` });
}

// Permission may only be asked from a click, so the banner offers it until answered.
function notifyButton() {
  if (!("Notification" in window) || Notification.permission !== "default") return null;
  return el("button", { type: "button", class: "small-btn", title: "Also warn with a desktop notification",
    onclick: async () => { await Notification.requestPermission(); await refreshAll(); } }, "Notify me");
}

// The banner under the header: a scheduled tick coming soon, and any tick running now.
function showBanner(warning, running) {
  const banner = document.getElementById("tick-banner");
  const lines = [];
  if (warning) {
    notify(warning);
    lines.push(el("div", { class: "row" },
      el("span", null, `${warning.label} at ${hhmm(warning.at)}${warning.postponed ? " (postponed)" : ""}.`),
      warning.postponed ? null : el("button", { type: "button", class: "small-btn",
        onclick: () => act("postpone", "/api/schedule/postpone") }, "Postpone 1 hour"),
      el("button", { type: "button", class: "small-btn", onclick: () => act("skip", "/api/schedule/skip") },
        "Skip today"),
      notifyButton()));
  }
  if (running) {
    lines.push(el("div", { class: "row" },
      el("span", null, `${running.label} ${running.state}${running.stage ? `: ${running.stage}` : ""}.`),
      el("button", { type: "button", class: "small-btn", title: "Stop it; it is recorded as interrupted",
        onclick: () => act("stop", `/api/activity/${running.id}/cancel`) }, "Stop")));
  }
  banner.replaceChildren(...lines);
  banner.hidden = !lines.length;
}

export { mount, show, showBanner };
