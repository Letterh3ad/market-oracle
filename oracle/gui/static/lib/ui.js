import { api } from "./api.js";
import { el } from "./dom.js";

function sendJson(path, body, method = "POST") {
  return api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
}

// A button that stays disabled while `action` runs, so a slow job cannot be started twice
// from the same page. `action` gets a callback for runJob that shows the job's state.
function busyButton(label, action) {
  const button = el("button", { type: "button", onclick: async () => {
    button.disabled = true;
    button.textContent = `${label}: running...`;
    try {
      await action((job) => { button.textContent = `${label}: ${job.state}...`; });
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  } }, label);
  return button;
}

// Starts a job and waits for it (polling), telling `onState` each state it passes through.
// Resolves with the job's result; rejects with its error if it failed or was cancelled.
function runJob(path, onState = () => {}) {
  return api(path, { method: "POST" }).then((job) => followJob(job, onState));
}

// The same for a job already started (found running when a page opens).
async function followJob(job, onState = () => {}) {
  while (job.state === "queued" || job.state === "running") {
    onState(job);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    job = await api(`/api/activity/${job.id}`);
  }
  if (job.state !== "done") throw new Error(job.error ?? job.state);
  return job.result;
}

export { sendJson, busyButton, runJob, followJob };
