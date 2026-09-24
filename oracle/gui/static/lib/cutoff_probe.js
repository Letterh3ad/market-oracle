// The Replay study card's cutoff part: what the model actually knows, measured with dated
// questions, so replays never start on a day whose outcome it may already know.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { modelParams, modelPicker } from "./model_picker.js";
import { tipText } from "./tip.js";
import { busyButton, followJob, runJob } from "./ui.js";

const ACTIVE = ["queued", "running"];
const ABOUT = "The model is asked about dated public events month by month, from a few months before its claimed "
  + "training cutoff to now, and each reply is checked for the known answer's keywords. The cutoff is the end of "
  + "the month after the last month it gets at least half right, and never before the claimed one.";

function months(p) {
  return table([["Month"], ["Right", null, 1], ["Asked", null, 1], ["Knows it"]],
    p.months.map((m) => el("tr", null, el("td", null, m.month), el("td", { class: "num" }, String(m.right)),
      el("td", { class: "num" }, String(m.total)), el("td", { class: m.known ? "up" : "dim" }, m.known ? "yes" : "no"))),
    "No questions were asked.");
}

function answers(p) {
  return table([["Day"], ["Question"], ["Known answer"], ["Reply"], ["Mark"]],
    p.answers.map((a) => el("tr", null, el("td", null, a.day), el("td", { class: "small" }, a.question),
      el("td", { class: "small" }, a.expected), el("td", { class: "small" }, a.reply),
      el("td", { class: a.right ? "up" : "down" }, a.right ? "right" : "wrong"))),
    "No questions were asked.");
}

function readout(b) {
  const who = `${b.provider} ${b.model} (profile ${b.profile})`;
  const p = b.probe;
  if (!p) {
    return [el("p", { class: "dim small" }, `${who} has not been probed yet: press Probe cutoff. Until then replays `
      + `rely on the claimed cutoff, ${b.claimed}.`)];
  }
  return [
    el("p", null, "Measured cutoff: ", el("strong", null, p.cutoff), ` for ${who}, probed ${String(p.probed_at).slice(0, 10)}`
      + ` with question set ${p.question_set}. Last month it knew: ${p.last_known ?? "none"}; claimed cutoff ${p.claimed}.`),
    p.note ? el("p", { class: "down small" }, p.note)
      : el("p", { class: "small" }, `Replays start after it, from ${p.replay_from}.`),
    el("details", null, el("summary", null, "Month by month"), months(p)),
    el("details", null, el("summary", null, `Questions and replies (${p.answers.length})`), answers(p)),
  ];
}

function cutoffSection() {
  let started = false;
  let loads = 0; // only the newest load paints
  const pick = modelPicker();
  const note = el("div", { class: "form-note" });
  const shown = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  // The picked profile, or none for the research slot.
  const withModel = (path) => {
    const q = new URLSearchParams(modelParams(pick)).toString();
    return q ? `${path}?${q}` : path;
  };

  const probe = busyButton("Probe cutoff", async (onState) => {
    say("", false);
    try {
      await runJob(withModel("/api/jobs/cutoff-probe"), (job) => { onState(job); running(job); });
      say("", false);
    } catch (err) {
      say(err.message);
    }
    await load();
  });

  function running(job) {
    say(el("span", null, `Probing the model: ${job.progress ?? job.state}. Each question is one short reply. `
      + "Follow or cancel it in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  async function load() {
    const asked = ++loads;
    let body;
    try {
      body = await api(withModel("/api/replay/cutoff"));
    } catch (err) {
      if (asked === loads) shown.replaceChildren(el("p", { class: "down" }, `Could not load the cutoff: ${err.message}`));
      return;
    }
    if (asked === loads) shown.replaceChildren(...readout(body));
  }

  // A probe started before this page opened is followed to its end.
  async function follow() {
    let body;
    try {
      body = await api("/api/activity");
    } catch {
      return;
    }
    const job = body.jobs.find((j) => j.kind === "probe" && ACTIVE.includes(j.state));
    if (!job) return;
    try {
      await followJob(job, running);
      say("", false);
    } catch (err) {
      say(err.message);
    }
    await load();
  }

  async function start() {
    if (started) return;
    started = true;
    await load();
    follow();
  }

  pick.addEventListener("change", load);
  const node = el("div", null,
    el("h3", null, tipText("Model cutoff", ABOUT)),
    el("p", { class: "dim small" }, "Replays must start after what the model already knows, or it could simply "
      + "remember what happened next. \"per slot\" probes the research slot's model, the one replays run on."),
    el("div", { class: "row" }, "Model ", pick, " ", probe),
    note, shown);
  return { node, start };
}

export { cutoffSection };
