// The Company tab's Laya section: each stored headline Laya scored for this company (relevant,
// direction, event type), badged uncalibrated, and "Score with Laya" for the ones not yet scored.
import { api } from "./api.js";
import { el, showError, clearError, safeUrl } from "./dom.js";
import { repaint } from "./repaint.js";
import { busyButton, runJob } from "./ui.js";

const path = (symbol) => `/api/companies/${encodeURIComponent(symbol)}/laya`;
const pct = (p) => `${Math.round(p * 100)}%`;

function scoreButton(symbol, enabled, changed) {
  if (!enabled) return el("span", { class: "dim small" }, "Laya is off (Settings, [laya] enabled).");
  return busyButton("Score with Laya", async (onState) => {
    try {
      clearError();
      const r = await runJob(`/api/jobs/laya?symbol=${encodeURIComponent(symbol)}`, onState);
      if (r?.stopped) showError(`Laya stopped after ${r.scored} of ${r.articles - r.already}; press again to go on`);
    } catch (err) {
      showError(err.message);
    }
    changed();
  });
}

function scoreRow(s) {
  const link = safeUrl(s.link);
  const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, s.title) : s.title;
  const when = s.known_at.slice(0, 10);
  return el("tr", null,
    el("td", { title: s.dated ? "published" : "no publish date: the day it was fetched, left out of history tests" },
      s.dated ? when : `${when} (fetched)`),
    el("td", null, title, s.outlet ? el("span", { class: "dim" }, ` (${s.outlet})`) : null),
    el("td", null, pct(s.relevant)),
    el("td", null, `${s.direction} ${pct(s.direction_probs[s.direction])}`),
    el("td", null, `${s.event.replace("_", " ")} ${pct(s.event_prob)}`));
}

// `changed` runs after a scoring job ends, whatever its outcome.
function layaTable(symbol, body, changed) {
  const head = el("div", { class: "row" }, scoreButton(symbol, body.enabled, changed),
    el("span", { class: "badge warn", title: "Laya's probabilities are not yet fitted to how often it is right" },
      "uncalibrated"),
    el("span", { class: "dim small" }, ` ${body.model}`));
  if (!body.scores.length) {
    return [head, el("p", { class: "dim" }, "No headlines scored yet. Laya reads each stored headline about this "
      + "company (from its research and filings) and says whether it is about it, which way it points and what kind "
      + "of event it is.")];
  }
  return [head, el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Known"), el("th", null, "Headline"), el("th", null, "About it"),
      el("th", null, "Direction"), el("th", null, "Event"))),
    el("tbody", null, ...body.scores.map(scoreRow)))];
}

// Fills `box` once the scores arrive, and again after each scoring; a late answer for a box no
// longer on screen is dropped.
async function showLaya(box, symbol) {
  let body;
  try {
    body = await api(path(symbol));
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  if (box.isConnected) repaint(box, layaTable(symbol, body, () => showLaya(box, symbol)));
}

export { layaTable, showLaya };
