// The Research tab's weekly page card (research-lab 08): when and where the page was last built, and a button that
// builds it again with this week's open questions. Publishing it as a claude.ai artifact and applying its marks are
// the research cycle's steps (oracle lab apply-marks); each mark also has the literature card's buttons.
import { api } from "./api.js";
import { el } from "./dom.js";
import { sendJson } from "./ui.js";

function weeklySection() {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const last = el("div", { class: "small" });
  const questions = el("textarea", { rows: 3, cols: 60, "aria-label": "Open questions",
    placeholder: "open questions for the page, one per line" });

  const shown = (p, fallback) => p
    ? el("span", null, `Last built ${p.at.slice(0, 16).replace("T", " ")} UTC: `, el("span", { class: "mono" }, p.path),
      ` (${p.rows} rows, candidates ${p.candidates.join(", ") || "none"}, ${p.papers} papers, ${p.questions} questions)`)
    : el("span", null, "Not built yet. It will be written to ", el("span", { class: "mono" }, fallback), ".");

  async function load() {
    try {
      const r = await api("/api/lab/page");
      last.replaceChildren(shown(r.last, r.default),
        el("div", { class: "dim" }, `Publish with capabilities ${JSON.stringify(r.capabilities)}`));
    } catch (err) { say(`Could not load the weekly page: ${err.message}`); }
  }

  const build = el("button", { type: "button", onclick: async () => {
    build.disabled = true;
    try {
      const qs = questions.value.split("\n").map((q) => q.trim()).filter(Boolean);
      const p = await sendJson("/api/lab/page", { questions: qs });
      say(`Built ${p.path}.`, false);
      await load();
    } catch (err) { say(err.message); } finally { build.disabled = false; }
  } }, "Build weekly page");

  const node = el("div", { class: "card" }, el("h2", null, "Weekly page"),
    el("p", { class: "dim small" }, "The results table, deaths by stage, the next candidates with go / drop / "
      + "dig-deeper buttons and a notes box, papers I'd like and open questions, as one page the research cycle "
      + "publishes. The next cycle applies its marks with who and when; steer.md overrides any mark."),
    last, el("div", { class: "row small" }, questions), el("div", { class: "row small" }, build), note);
  return { node, start: load };
}

export { weeklySection };
