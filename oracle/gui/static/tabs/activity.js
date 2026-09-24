import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";

const STATE_CLASS = {
  running: "up", "running elsewhere": "up", queued: "dim", done: "", failed: "down", cancelled: "dim", interrupted: "down",
};
let timer = null;

// Today's jobs show the time alone; history from earlier days needs the date too.
function when(iso) {
  if (!iso) return "";
  const at = new Date(iso);
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString(undefined, { timeStyle: "short" })
    : at.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function took(j) {
  if (!j.started_at) return "";
  const end = j.finished_at ? new Date(j.finished_at) : new Date();
  const s = Math.max(0, Math.round((end - new Date(j.started_at)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function cancelButton(j, box) {
  // Work held elsewhere has no id: only the process running it can stop it.
  if (j.id == null || (j.state !== "running" && j.state !== "queued")) return null;
  const button = el("button", { type: "button", class: "small-btn", onclick: async () => {
    button.disabled = true;
    try {
      await api(`/api/activity/${j.id}/cancel`, { method: "POST" });
    } catch (err) {
      showError(`cancel: ${err.message}`);
    }
    loadRunningAndQueued(box);
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
    el("td", { class: "small dim" }, when(j.submitted_at)),
    el("td", { class: "num small" }, took(j)),
    el("td", null, cancelButton(j, box)));
}

function table(jobs, box) {
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...["Job", "State", "Now / why", "Asked", "Took", ""].map((h) => el("th", null, h)))),
    el("tbody", null, ...jobs.map((j) => row(j, box))));
}

async function loadRunningAndQueued(box) {
  let jobs, elsewhere;
  try {
    ({ jobs, elsewhere } = await api("/api/activity"));
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, `Could not load activity: ${err.message}`));
    return;
  }
  if (!box.isConnected) return;
  const active = [...jobs, ...elsewhere.map((e) => ({
    label: `${e.job} (pid ${e.pid} on ${e.host})`, state: "running elsewhere",
    error: "started outside this app window, e.g. from the command line; stop it there",
  }))];
  box.replaceChildren(active.length ? table(active, box) : el("p", { class: "dim small" }, "Nothing running or waiting."));
}

const OPEN_KEY = "activity.finished.open";

function remembered() {
  try {
    return localStorage.getItem(OPEN_KEY) !== "closed";
  } catch {
    return true;
  }
}

function remember(open) {
  try {
    localStorage.setItem(OPEN_KEY, open ? "open" : "closed");
  } catch {
    // Storage blocked: the section still works, it just forgets.
  }
}

// The Finished section is built once; refreshes replace only its list and pager, so the
// search box keeps its text and focus and the page the user is on stays put.
// `q` is the search last sent: the poll never sends what is still being typed.
function finishedSection() {
  const state = { page: 1, q: "", asked: 0 };
  const count = el("span", { class: "dim small" });
  const list = el("div", null, el("p", { class: "dim" }, "Loading..."));
  const pager = el("div", { class: "row" });
  let typing = null;
  const search = el("input", { type: "search", placeholder: "Search by job, kind or outcome (e.g. failed, MSFT)",
    size: "40", oninput: () => {
      clearTimeout(typing);
      typing = setTimeout(() => {
        state.q = search.value.trim();
        go(1);
      }, 250);
    } });
  const details = el("details", { ontoggle: () => { remember(details.open); if (details.open) load(); } },
    el("summary", null, el("h3", { style: "display: inline" }, "Finished "), count),
    el("div", { class: "row" }, search), list, pager);
  details.open = remembered();

  function go(page) {
    state.page = page;
    load();
  }

  async function loadCount() {
    const asked = state.asked;
    let found;
    try {
      found = await api(`/api/activity/finished/count?q=${encodeURIComponent(state.q)}`);
    } catch {
      return; // the count is a courtesy; the next poll tries again
    }
    if (asked === state.asked) count.textContent = `(${found.total})`;
  }

  async function load() {
    const asked = ++state.asked;
    let found;
    try {
      found = await api(`/api/activity/finished?page=${state.page}&q=${encodeURIComponent(state.q)}`);
    } catch (err) {
      if (asked === state.asked) list.replaceChildren(el("p", { class: "dim" }, `Could not load finished jobs: ${err.message}`));
      return;
    }
    if (asked !== state.asked || !details.isConnected) return; // a newer request is on its way
    state.page = found.page;
    count.textContent = `(${found.total})`;
    const empty = state.q ? `No finished job matches "${state.q}".` : "Nothing has finished yet.";
    list.replaceChildren(found.items.length ? table(found.items, list) : el("p", { class: "dim small" }, empty));
    pager.replaceChildren(...(found.pages > 1 ? [
      el("button", { type: "button", class: "small-btn", disabled: found.page <= 1 ? "" : null,
        onclick: () => go(found.page - 1) }, "Previous"),
      el("span", { class: "dim small" }, `Page ${found.page} of ${found.pages}`),
      el("button", { type: "button", class: "small-btn", disabled: found.page >= found.pages ? "" : null,
        onclick: () => go(found.page + 1) }, "Next"),
    ] : []));
  }

  // A later page is left alone: new jobs would shift its rows under the reader.
  const refresh = () => (details.open && state.page === 1 ? load() : loadCount());
  return { node: details, refresh };
}

// Refreshes every 2 seconds while the tab is on screen.
function render(pane) {
  clearInterval(timer);
  const box = el("div", null, el("p", { class: "dim" }, "Loading..."));
  const finished = finishedSection();
  pane.replaceChildren(el("h2", null, "Activity"),
    el("p", { class: "dim small" }, "Everything the app is working on: research runs one at a time, in the order asked."),
    el("h3", null, "Now and next"), box, finished.node);
  const load = () => { loadRunningAndQueued(box); finished.refresh(); };
  load();
  timer = setInterval(() => (box.isConnected ? load() : clearInterval(timer)), 2000);
}

export { finishedSection };
export default { id: "activity", label: "Activity", render };
