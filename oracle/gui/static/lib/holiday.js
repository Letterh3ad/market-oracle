import { api } from "./api.js";
import { el } from "./dom.js";

// The App section's Holiday watch card: Status runs the checks without side effects; Arm checks,
// backs up, starts the study and registers the tasks, refusing on any failed check; Disarm undoes it.
function holidayCard() {
  const buttons = el("div", { class: "row" });
  const note = el("div", { class: "dim small" }, "Status runs every check without arming.");
  const out = el("div");
  const node = el("div", null, el("h3", null, "Holiday watch"), buttons, note, out);

  function show(report) {
    note.textContent = report.armed ? "Armed: the watchdog and morning-report tasks are registered." : "Not armed.";
    out.replaceChildren(
      el("table", { class: "small" }, el("tbody", null, ...report.checks.map((c) => el("tr", null,
        el("td", { class: c.ok ? "up" : "down" }, c.ok ? "ok" : "FAIL"),
        el("th", null, c.name),
        el("td", null, c.detail, c.ok ? null : el("div", { class: "warn-text" }, `fix: ${c.fix}`)))))),
      report.lines.length ? el("pre", { class: "observation" }, report.lines.join("\n")) : null);
  }

  async function run(label, path, method) {
    idle(true);
    note.textContent = `${label}: working (checks list processes and power settings; arming backs the database up)...`;
    try {
      show(await api(path, { method }));
    } catch (err) {
      note.textContent = `${label}: ${err.message}`;
    }
    idle();
  }

  function confirm(question, label, path) {
    buttons.replaceChildren(el("span", { class: "small" }, question),
      el("button", { type: "button", class: "small-btn", onclick: () => run(label, path, "POST") }, `${label} now`),
      el("button", { type: "button", class: "small-btn", onclick: () => idle() }, "Cancel"));
  }

  function idle(busy = false) {
    const button = (label, onclick, title) =>
      el("button", { type: "button", class: "small-btn", onclick, title, disabled: busy ? "" : null }, label);
    buttons.replaceChildren(
      button("Status", () => run("Status", "/api/holiday", "GET"), "Run the checks and show the tasks; changes nothing"),
      button("Arm", () => confirm("Arm: check everything, back up the database, start the replay study and register " +
        "the watchdog (every 30 min) and morning-report tasks?", "Arm", "/api/holiday/arm"),
      "Refuses if any check fails"),
      button("Disarm", () => confirm("Disarm: pause the replay study and delete both tasks?", "Disarm",
        "/api/holiday/disarm")));
  }

  idle();
  return { node };
}

export { holidayCard };
