// The Discover tab's parts: saved questions, and an answer's shortlist with what to do with each
// company. Kept apart from the tab so they can be tested without a page.
import { api } from "./api.js";
import { el, showError, clearError, companyLink, safeUrl } from "./dom.js";
import { sendJson } from "./ui.js";

const day = (iso) => (iso ?? "").slice(0, 10);

const link = (q) => el("a", { href: `#discover/${q.id}` }, q.text);

// One line per saved question, newest first, each opening it.
function questionList(listing, selected) {
  if (!listing.questions.length) {
    return el("p", { class: "dim" }, "No questions yet. Ask one above, e.g. which European companies gain from "
      + "grid batteries?");
  }
  return el("ul", { class: "questions" }, ...listing.questions.map((q) => {
    const last = q.last ? `${q.last.status} ${day(q.last.started_at)}, ${q.last.shortlist} shortlisted` : "not answered yet";
    const title = q.id === selected ? el("strong", null, q.text) : link(q);
    return el("li", null, title, q.refines ? el("span", { class: "dim small" }, ` (follow-up to ${q.refines})`) : null,
      q.auto ? el("span", { class: "badge" }, "auto") : null,
      el("span", { class: "dim small" }, ` ${q.answers} answer${q.answers === 1 ? "" : "s"}; ${last}`));
  }));
}

function evidence(items) {
  return el("ul", { class: "small" }, ...items.map((e) => {
    const link = safeUrl(e.link);
    const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
    return el("li", null, el("span", { class: "dim" }, `${day(e.published_at) || "undated"} `), title,
      e.outlet ? el("span", { class: "dim" }, ` (${e.outlet})`) : null);
  }));
}

// A button that does one thing and then says so in place; `changed` reloads the answer after.
function action(label, hint, send, done, changed) {
  const button = el("button", { type: "button", class: "small-btn", title: hint, onclick: async () => {
    button.disabled = true;
    try {
      clearError();
      await send();
      button.textContent = done;
      changed();
    } catch (err) {
      button.disabled = false;
      showError(err.message);
    }
  } }, label);
  return button;
}

function marks(i) {
  return [i.new ? el("span", { class: "badge up" }, "new") : null,
    i.watched ? el("span", { class: "badge" }, "watched") : null,
    i.discovered ? el("span", { class: "badge" }, "discovered") : null,
    i.validation?.added ? el("span", { class: "badge up" }, "auto-added") : null];
}

// What auto mode's validation made of a company: added, or why it stays a suggestion.
function validationLine(v) {
  if (!v) return null;
  const failed = v.checks.find((c) => !c.passed);
  const text = v.added ? "Auto: added to the discovered list; every check passed."
    : failed ? `Auto: stays a suggestion, failed ${failed.name} (${failed.detail}).`
      : `Auto: stays a suggestion, ${v.note}.`;
  return el("p", { class: v.added ? "small" : "small warn-text" }, text);
}

function buttons(i, answerId, changed) {
  const symbol = encodeURIComponent(i.symbol);
  // A discovered company moves, so it is not left on both lists.
  const watch = i.discovered
    ? action("Move to my watchlist", "Watch it on your main list; it leaves the discovered list",
      () => sendJson(`/api/discovery/${encodeURIComponent(i.discovered_as)}/move`), "Moved", changed)
    : i.watched ? null : action("Watch", "Put it on your main watchlist",
      () => sendJson("/api/watchlists/main/items", { symbol: i.symbol }), "Watched", changed);
  const research = action("Research", "Queue a research run; its report shows on the company page",
    () => api(`/api/jobs/research?symbol=${symbol}`, { method: "POST" }), "Research queued", () => {});
  const discovered = i.watched || i.discovered ? null : action("Add to discovered",
    "Put it on the discovered list, researched (and traded by the AI account if Settings allows) like the others",
    () => sendJson(`/api/questions/answers/${answerId}/items/${i.n}/discovered`), "Added", changed);
  return [watch, research, discovered].filter(Boolean);
}

function shortlistTable(a, changed) {
  if (!a.shortlist.length) return el("p", { class: "dim" }, "No company made the shortlist this time.");
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "#"), el("th", null, "Company"), el("th", null, "Why"),
      el("th", null, ""))),
    el("tbody", null, ...a.shortlist.map((i) => el("tr", null,
      el("td", { class: "num" }, String(i.n)),
      el("td", { class: "name" }, companyLink(i.symbol), " ", i.company ?? i.name, ...marks(i)),
      el("td", null, i.why, validationLine(i.validation), evidence(i.evidence)),
      el("td", null, ...buttons(i, a.id, changed))))));
}

// One answer: what was searched, the shortlist, what changed and what was left off with why.
function answerCard(a, changed = () => {}) {
  if (a.status === "running") {
    return el("div", { class: "card" }, el("p", null, `Answering: ${a.stage ?? "starting"}...`));
  }
  const when = `${day(a.started_at)} ${(a.started_at ?? "").slice(11, 16)}, ${a.model}`;
  if (a.status === "failed") {
    return el("div", { class: "card" }, el("p", { class: "down" }, `Failed (${when}): ${a.error}`));
  }
  const against = a.compared_to == null ? null
    : el("p", { class: "dim small" }, `"new" marks what answer ${a.compared_to} did not have.`);
  return el("div", { class: "card" },
    el("p", { class: "dim small" }, `Answered ${when}. Searched ${a.searches.join("; ") || "nothing"}.`),
    against, shortlistTable(a, changed),
    a.gone.length ? el("p", { class: "small" }, "Gone since then: ",
      a.gone.map((i) => `${i.company ?? i.name} (${i.symbol})`).join(", ")) : null,
    a.skipped.length ? el("details", null, el("summary", { class: "dim small" }, `Left off (${a.skipped.length})`),
      el("ul", { class: "small" }, ...a.skipped.map((i) => el("li", null, `${i.name}: ${i.skipped}`)))) : null,
    a.notes.length ? el("ul", { class: "dim small" }, ...a.notes.map((n) => el("li", null, n))) : null);
}

// Auto mode for the question: its answers from then on add what passes validation.
function autoSwitch(q, changed) {
  const box = el("input", { type: "checkbox" });
  box.checked = q.auto;
  box.addEventListener("change", async () => {
    try {
      clearError();
      await sendJson(`/api/questions/${q.id}/auto`, { auto: box.checked }, "PUT");
      changed();
    } catch (err) {
      box.checked = !box.checked;
      showError(err.message);
    }
  });
  const limits = q.limits ? ` (up to ${q.limits.per_question} from this question and ${q.limits.max_total} on the `
    + "list in all; [discovery] per_question and max_total in the Settings section)" : "";
  return el("label", { class: "small", title: "Each answer (Rerun, or the midday pass) adds shortlisted companies to "
    + "the discovered list only when they pass every check: a live primary line with fresh prices, not on the never "
    + "or removed list, not another line of a company you watch, discovered or removed, named by 2 outlets in 90 "
    + "days (not only the company's own releases), and a separate model check that the news shows it fits the "
    + "question. The rest stay suggestions with the failed check shown." },
  box, ` Auto: add companies that pass validation to the discovered list${limits}`);
}

// The whole question: what it asks (with what it refines), the newest answer, older ones folded.
function questionView(q, changed) {
  const [newest, ...older] = q.answers;
  const links = (label, qs) => (qs.length ? el("p", { class: "dim small" }, label,
    ...qs.flatMap((x, i) => [i ? "; " : "", link(x)])) : null);
  return el("div", null,
    el("h3", null, q.asked), links("Refines: ", q.chain.slice(0, -1)), links("Follow-ups: ", q.follow_ups),
    el("p", null, autoSwitch(q, changed)),
    newest ? answerCard(newest, changed) : el("p", { class: "dim" }, "Not answered yet: press Rerun."),
    ...older.map((a) => el("details", null,
      el("summary", { class: "dim small" }, `Answer ${a.id}, ${day(a.started_at)}: ${a.status}`
        + (a.status === "done" ? `, ${a.shortlist.length} shortlisted` : "")),
      answerCard(a, changed))));
}

export { answerCard, questionList, questionView, validationLine };
