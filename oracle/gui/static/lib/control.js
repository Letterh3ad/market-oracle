import { api } from "./api.js";
import { sendJson } from "./ui.js";
import { el, showError } from "./dom.js";

const BACK_WITHIN_MS = 60_000;
let server = null; // the last /api/status "server", for the new-version banner
let asking = false; // a confirm or restart is on screen: status refreshes leave it alone

function box() {
  return document.getElementById("app-controls");
}

// Polls until a different server answers, then reloads so its code is the code on screen.
async function waitForNewServer(oldPid) {
  const until = Date.now() + BACK_WITHIN_MS;
  while (Date.now() < until) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const { server } = await api("/api/status");
      if (server.pid !== oldPid) {
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
  asking = true;
  let oldPid;
  try {
    ({ server: { pid: oldPid } } = await api("/api/status"));
    await api("/api/app/restart", { method: "POST" });
  } catch (err) {
    showError(`restart: ${err.message}`);
    idle();
    return;
  }
  box().replaceChildren(el("span", { class: "warn-text" }, "Restarting..."));
  waitForNewServer(oldPid);
}

// Asks on the page, naming the work a restart would cut off.
async function confirmRestart() {
  asking = true;
  let jobs = [];
  try {
    ({ jobs } = await api("/api/activity"));
  } catch {
    // Still offer the restart; the list is a courtesy.
  }
  const going = jobs.filter((j) => j.state === "running" || j.state === "queued").map((j) => j.label);
  box().replaceChildren(
    el("span", { class: "small" }, going.length ? `Restart cuts off: ${going.join(", ")}.` : "Restart Oracle?"),
    el("button", { type: "button", class: "small-btn", onclick: restart }, "Restart now"),
    el("button", { type: "button", class: "small-btn", onclick: idle }, "Keep running"));
}

function idle() {
  asking = false;
  const fresh = server && server.version_on_disk !== server.version;
  const note = fresh ? [el("span", { class: "warn-text small",
    title: `running ${server.version}, on disk ${server.version_on_disk}` }, "New version ready")] : [];
  box().replaceChildren(...note,
    el("button", { type: "button", class: "small-btn", title: "Stop Oracle and start it again, picking up new code",
      onclick: confirmRestart }, fresh ? "Restart to update" : "Restart"));
}

// Called with each status answer.
function show(status) {
  server = status;
  if (!asking) idle();
}

// "Start Oracle when Windows starts": the launcher file's presence is the setting.
async function mountSettings() {
  const pane = document.getElementById("app-settings");
  const toggle = el("input", { type: "checkbox" });
  const where = el("span", { class: "dim small" });
  const paint = (a) => {
    toggle.checked = a.enabled;
    toggle.disabled = !a.supported;
    where.textContent = !a.supported ? "only set up for Windows so far" : a.enabled ? `launcher: ${a.path}` : "";
  };
  toggle.addEventListener("change", async () => {
    try {
      paint(await sendJson("/api/app/autostart", { enabled: toggle.checked }, "PUT"));
    } catch (err) {
      showError(`start with Windows: ${err.message}`);
      paint(await api("/api/app/autostart"));
    }
  });
  pane.replaceChildren(el("div", { class: "row" }, el("label", null, toggle, " Start Oracle when Windows starts"), where));
  try {
    paint(await api("/api/app/autostart"));
  } catch (err) {
    where.textContent = err.message;
  }
}

function mount() {
  idle();
  mountSettings();
}

export { mount, show };
