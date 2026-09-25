import { api } from "./api.js";
import { sendJson } from "./ui.js";
import { el, showError } from "./dom.js";
import { holidayCard } from "./holiday.js";

const BACK_WITHIN_MS = 60_000;
let status = null; // the last /api/status answer: server identity, versions, automation
let asking = false; // a confirm, restart or shutdown is on screen: status refreshes leave it alone

function box() {
  return document.getElementById("app-controls");
}

function button(label, onclick, title) {
  return el("button", { type: "button", class: "small-btn", onclick, title }, label);
}

async function activeJobs() {
  try {
    return (await api("/api/activity")).jobs.filter((j) => j.state === "running" || j.state === "queued");
  } catch {
    return []; // the list is a courtesy; still offer the action
  }
}

// Asks on the page, naming the work the action would cut off.
async function confirmStop(verb, action) {
  asking = true;
  const going = (await activeJobs()).map((j) => j.label);
  box().replaceChildren(
    el("span", { class: "small" }, going.length ? `${verb} cuts off: ${going.join(", ")}.` : `${verb} Oracle?`),
    button(`${verb} now`, action), button("Keep running", idle));
}

// Polls until a different server answers, then reloads so its code is the code on screen.
async function waitForNewServer(oldPid) {
  const until = Date.now() + BACK_WITHIN_MS;
  while (Date.now() < until) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      if ((await api("/api/status")).server.pid !== oldPid) {
        location.reload();
        return;
      }
    } catch {
      // Down between the old server stopping and the new one starting.
    }
  }
  box().replaceChildren(el("span", { class: "down" }, "Oracle did not come back within a minute: start it again by hand."));
}

async function restart() {
  try {
    const oldPid = (status ?? await api("/api/status")).server.pid;
    await api("/api/app/restart", { method: "POST" });
    box().replaceChildren(el("span", { class: "warn-text" }, "Restarting..."));
    waitForNewServer(oldPid);
  } catch (err) {
    showError(`restart: ${err.message}`);
    idle();
  }
}

async function shutDown() {
  try {
    await api("/api/app/shutdown", { method: "POST" });
  } catch (err) {
    showError(`shut down: ${err.message}`);
    idle();
    return;
  }
  document.body.dataset.off = "true"; // status polling stops painting
  document.body.replaceChildren(el("main", null,
    el("h2", null, "Oracle is off"),
    el("p", null, "Start it again from the Market Oracle shortcut on the desktop or in the Start menu, " +
      "then reload this page.")));
}

async function setAutomation(enabled, stopRunning = false) {
  try {
    const answer = await sendJson("/api/app/automation", { enabled, stop_running: stopRunning }, "PUT");
    status.automation.enabled = answer.enabled;
  } catch (err) {
    showError(`automation: ${err.message}`);
  }
  idle();
}

// Turning off: automatic work already going may be stopped too, or left to finish.
async function automationOff() {
  asking = true; // a status refresh must not tick the switch back while this is asked
  const automatic = (await activeJobs()).filter((j) => j.automatic).map((j) => j.label);
  if (!automatic.length) return setAutomation(false);
  box().replaceChildren(
    el("span", { class: "small" }, `Automation off. Also stop ${automatic.join(", ")}?`),
    button("Stop it", () => setAutomation(false, true)), button("Let it finish", () => setAutomation(false)));
}

function idle() {
  asking = false;
  if (!status) return;
  const { server, automation } = status;
  const switchBox = el("input", { type: "checkbox" });
  switchBox.checked = automation.enabled;
  switchBox.addEventListener("change", () => (switchBox.checked ? setAutomation(true) : automationOff()));
  const fresh = server.version_on_disk !== server.version;
  box().replaceChildren(...[
    el("label", { class: automation.enabled ? "small" : "small warn-text",
      title: "Off: nothing starts by itself (scheduled ticks); buttons still work" },
    switchBox, automation.enabled ? " Automation on" : " Automation off"),
    fresh ? el("span", { class: "warn-text small" },
      `New version ready (${server.version} \u2192 ${server.version_on_disk})`) : null,
    button(fresh ? "Restart to update" : "Restart", () => confirmStop("Restart", restart),
      "Stop Oracle and start it again, picking up new code"),
    button("Shut down", () => confirmStop("Shut down", shutDown), "Stop Oracle completely"),
  ].filter(Boolean));
}

// The same rows as `oracle status`.
function showAbout(about) {
  const rows = [["schema", about.schema], ["database", about.database],
    ["config", about.config ?? "defaults (no oracle.toml found)"], ["currency", about.currency],
    ["created", about.created], ["version", about.version]];
  document.getElementById("app-about").replaceChildren(el("tbody", null,
    ...rows.map(([name, value]) => el("tr", null, el("th", null, name), el("td", { class: "mono" }, String(value))))));
}

// Called with each status answer.
function show(answer) {
  status = answer;
  showAbout(answer.about);
  if (!asking) idle();
}

// "Start Oracle when Windows starts" (the launcher file's presence is the setting) and the
// desktop / Start-menu shortcuts.
async function mountSettings() {
  const pane = document.getElementById("app-settings");
  const toggle = el("input", { type: "checkbox" });
  const where = el("span", { class: "dim small" });
  const about = el("div", { class: "dim small" });
  const paint = (a) => {
    toggle.checked = a.enabled;
    toggle.disabled = !a.supported;
    where.textContent = !a.supported ? "only set up for Windows so far" : a.enabled ? `launcher: ${a.path}` : "";
    // The launcher and shortcuts start the code in this folder: the one this server runs.
    about.textContent = `Running version ${a.version}. Launcher and shortcuts start Oracle from ${a.folder}.`;
  };
  const repaint = () => api("/api/app/autostart").then(paint).catch((err) => { where.textContent = err.message; });
  toggle.addEventListener("change", async () => {
    try {
      paint(await sendJson("/api/app/autostart", { enabled: toggle.checked }, "PUT"));
    } catch (err) {
      showError(`start with Windows: ${err.message}`);
      repaint();
    }
  });
  const written = el("span", { class: "dim small" });
  const shortcuts = button("Add desktop and Start-menu shortcuts", async () => {
    try {
      written.textContent = `wrote ${(await api("/api/app/shortcuts", { method: "POST" })).written.join(", ")}`;
    } catch (err) {
      showError(`shortcuts: ${err.message}`);
    }
  });
  pane.replaceChildren(
    el("div", { class: "row" }, el("label", null, toggle, " Start Oracle when Windows starts"), where),
    el("div", { class: "row" }, shortcuts, written), about);
  repaint();
}

// The header's Restart confirmation, for a page that saved something needing one.
function offerRestart() {
  confirmStop("Restart", restart);
}

function mount() {
  mountSettings();
  document.getElementById("holiday-card").replaceChildren(holidayCard().node);
}

export { mount, show, offerRestart };
