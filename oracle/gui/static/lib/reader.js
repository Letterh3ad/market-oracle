// The Ledgers tab's Stronger reader card: a local Ollama model ([laya] reader_model) reads every
// backfilled headline once for its own company, so the Laya predictive test can run on its scores
// beside Laya's. Count and estimate, start (or resume) the run as a job, and the last run.
import { api } from "./api.js";
import { el } from "./dom.js";

const ACTIVE = ["queued", "running"];

function readerSection() {
  let started = false;
  const note = el("div", { class: "form-note" });
  const model = el("p", { class: "small" });
  const lastRun = el("p", { class: "small" });
  const planLine = el("p", { class: "small" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  function running(job) {
    say(el("span", null, `The stronger reader is ${job.state}${job.progress ? `: ${job.progress}` : ""}. It takes hours, `
      + "waits while model work runs or a scheduled tick is within 30 minutes, and resumes where it stopped. Follow or "
      + "cancel it in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  const start = el("button", { type: "button", onclick: async () => {
    start.disabled = true;
    try {
      running(await api("/api/jobs/reader", { method: "POST" }));
    } catch (err) {
      say(err.message);
    } finally {
      start.disabled = false;
    }
  } }, "Read history with the stronger reader");

  const plan = el("button", { type: "button", onclick: async () => {
    plan.disabled = true;
    planLine.replaceChildren("Counting...");
    try {
      const p = await api("/api/reader/plan");
      planLine.replaceChildren(`${p.unscored} of ${p.subject_articles} headlines left to read (${p.pairs} pairs), `
        + `about ${(p.estimate_s / 3600).toFixed(1)} h at ${p.rate} a second (${p.basis}).`);
    } catch (err) {
      planLine.replaceChildren("");
      say(err.message);
    } finally {
      plan.disabled = false;
    }
  } }, "Count headlines and estimate");

  async function load() {
    try {
      const s = await api("/api/reader");
      model.replaceChildren(`Model: ${s.model} (Settings, [laya] reader_model).`);
      const r = s.last_run;
      lastRun.className = r && r.stopped ? "small down" : "small";
      lastRun.replaceChildren(r ? `Last run, ${String(r.finished_at).slice(0, 16).replace("T", " ")} UTC: ${r.summary}.`
        : "No run yet.");
      const job = (await api("/api/activity")).jobs.find((j) => j.kind === "reader" && ACTIVE.includes(j.state));
      if (job) running(job);
    } catch (err) {
      say(err.message);
    }
  }

  const node = el("section", { class: "card" }, el("h3", null, "Stronger reader"),
    el("p", { class: "small" }, "Reads each backfilled headline once, for its own company, with a local chat model; "
      + "competitor and industry pairs reuse that reading. The predictive test then runs on its scores beside Laya's."),
    model, el("div", { class: "row" }, start, plan), planLine, lastRun, note);

  return {
    node,
    show() {
      if (started) return;
      started = true;
      load();
    },
  };
}

export { readerSection };
