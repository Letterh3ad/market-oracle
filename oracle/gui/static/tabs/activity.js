import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";

const STATE_CLASS = { running: "up", queued: "dim", done: "", failed: "down", cancelled: "dim" };
let timer = null;

function time(iso) {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { timeStyle: "short" }) : "";
}

function took(j) {
  if (!j.started_at) return "";
  const end = j.finished_at ? new Date(j.finished_at) : new Date();
  const s = Math.max(0, Math.round((end - new Date(j.started_at)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function cancelButton(j, box) {
  if (j.state !== "running" && j.state !== "queued") return null;
  const button = el("button", { type: "button", class: "small-btn", onclick: async () => {
    button.disabled = true;
    try {
      await api(`/api/activity/${j.id}/cancel`, { method: "POST" });
    } catch (err) {
      showError(`cancel: ${err.message}`);
    }
    load(box);
  } }, "Cancel");
  return button;
}

function row(j, box) {
  const what = j.link ? el("a", { href: j.link }, j.label) : j.label;
  const detail = j.state === "running" ? j.progress ?? "" : j.error ?? "";
  return el("tr", null,
    el("td", null, what),
    el("td", { class: STATE_CLASS[j.state] ?? "" }, j.state),
    el("td", { class: "small" }, detail),
    el("td", { class: "small dim" }, time(j.submitted_at)),
    el("td", { class: "num small" }, took(j)),
    el("td", null, cancelButton(j, box)));
}

function section(title, jobs, empty, box) {
  return [
    el("h3", null, title),
    jobs.length
      ? el("table", { class: "results" },
        el("thead", null, el("tr", null, ...["Job", "State", "Now / why", "Asked", "Took", ""].map((h) => el("th", null, h)))),
        el("tbody", null, ...jobs.map((j) => row(j, box))))
      : el("p", { class: "dim small" }, empty),
  ];
}

async function load(box) {
  let jobs;
  try {
    ({ jobs } = await api("/api/activity"));
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, `Could not load activity: ${err.message}`));
    return;
  }
  if (!box.isConnected) return;
  const active = jobs.filter((j) => j.state === "running" || j.state === "queued");
  const done = jobs.filter((j) => !active.includes(j));
  box.replaceChildren(
    ...section("Now and next", active, "Nothing running or waiting.", box),
    ...section("Finished", done, "Nothing has finished since the app started.", box));
}

// Refreshes every 2 seconds while the tab is on screen.
function render(pane) {
  clearInterval(timer);
  const box = el("div", null, el("p", { class: "dim" }, "Loading..."));
  pane.replaceChildren(el("h2", null, "Activity"),
    el("p", { class: "dim small" }, "Everything the app is working on: research runs one at a time, in the order asked."),
    box);
  load(box);
  timer = setInterval(() => (box.isConnected ? load(box) : clearInterval(timer)), 2000);
}

export default { id: "activity", label: "Activity", render };
