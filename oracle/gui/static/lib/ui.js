import { api } from "./api.js";
import { el } from "./dom.js";

function sendJson(path, body, method = "POST") {
  return api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
}

// A button that shows "running..." and stays disabled while `action` runs, so a slow job
// cannot be started twice from the same page.
function busyButton(label, action) {
  const button = el("button", { type: "button", onclick: async () => {
    button.disabled = true;
    button.textContent = `${label}: running...`;
    try {
      await action();
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  } }, label);
  return button;
}

export { sendJson, busyButton };
