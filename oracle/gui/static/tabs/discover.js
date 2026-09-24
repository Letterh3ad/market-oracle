// Discover: ask which companies fit a question, get a shortlist, refine it or ask again later.
import { api } from "../lib/api.js";
import { el, showError, clearError } from "../lib/dom.js";
import { busyButton, runJob } from "../lib/ui.js";
import { repaint } from "../lib/repaint.js";
import { questionList, questionView } from "../lib/questions.js";

let current = null; // the question shown, reopened when the tab is picked without one
let seq = 0;

async function load(pane) {
  const mine = ++seq;
  let listing, q;
  try {
    [listing, q] = await Promise.all([api("/api/questions"), current ? api(`/api/questions/${current}`) : null]);
  } catch (err) {
    showError(err.message);
    return;
  }
  if (mine !== seq || !pane.querySelector("#q-list")) return;
  repaint(pane.querySelector("#q-list"), [questionList(listing, current)]);
  repaint(pane.querySelector("#q-view"), q ? [answerRow(pane, q), questionView(q, () => load(pane))] : []);
  // An answer still being worked on (asked from elsewhere, or before the page opened) fills in by itself.
  if (q?.answers[0]?.status === "running") setTimeout(() => { if (mine === seq) load(pane); }, 3000);
}

// Runs an ask or rerun job, telling its stage as it goes, then shows the question it answered.
function asking(label, path, pane, before = () => true) {
  const status = pane.querySelector("#q-status");
  return busyButton(label, async (onState) => {
    if (!before()) return;
    try {
      clearError();
      const r = await runJob(path(), (job) => {
        onState(job);
        status.textContent = job.progress ? `Answering: ${job.progress}...` : `${job.state}...`;
      });
      status.textContent = "";
      if (current === r.question_id) load(pane);
      else location.hash = `discover/${r.question_id}`;
    } catch (err) {
      status.textContent = "";
      showError(err.message);
      load(pane);
    }
  });
}

function answerRow(pane, q) {
  const follow = el("input", { size: "40", maxlength: "300", placeholder: "follow-up, e.g. only mid caps",
    "aria-label": "Follow-up" });
  const refine = asking("Refine", () => `/api/jobs/ask?question=${encodeURIComponent(follow.value)}&refines=${q.id}`,
    pane, () => follow.value.trim() !== "");
  return el("div", { class: "row" }, asking("Rerun", () => `/api/jobs/ask/${q.id}/rerun`, pane), follow, refine,
    el("span", { class: "dim small" }, "A follow-up is a new question narrowing this one; Rerun asks this one again "
      + "and marks what is new."));
}

function render(pane, arg) {
  if (arg) current = Number(arg);
  const question = el("input", { size: "60", maxlength: "300", "aria-label": "Question",
    placeholder: "e.g. which European companies gain from grid batteries?" });
  pane.replaceChildren(
    el("h2", null, "Discover"),
    el("p", { class: "dim small" }, "Ask which companies fit a question. The local model plans news and web "
      + "searches, reads what they find and names the companies the news shows; each is then checked in code to be "
      + "a real listing with recent prices, never one on your never list or one you removed. Nothing is added "
      + "until you press a button."),
    el("div", { class: "row" }, question, el("span", { id: "q-ask" })),
    el("div", { id: "q-status", class: "result" }),
    el("div", { id: "q-view" }),
    el("h3", null, "Your questions"),
    el("div", { id: "q-list" }));
  pane.querySelector("#q-ask").replaceChildren(asking("Ask",
    () => `/api/jobs/ask?question=${encodeURIComponent(question.value)}`, pane, () => question.value.trim() !== ""));
  load(pane);
}

export default { id: "discover", label: "Discover", render };
