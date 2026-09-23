import { api } from "./api.js";
import { el, showError } from "./dom.js";

const BACK_WITHIN_MS = 60_000;

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
  box().replaceChildren(el("button", { type: "button", class: "small-btn", title: "Stop Oracle and start it again, " +
    "picking up new code", onclick: confirmRestart }, "Restart"));
}

export { idle as mount };
