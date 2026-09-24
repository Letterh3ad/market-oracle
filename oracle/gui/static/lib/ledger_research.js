import { api } from "./api.js";
import { el } from "./dom.js";
import { busyButton } from "./ui.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const toActivity = (text) => el("span", null, text, el("a", { href: "#activity" }, "Activity tab"), ".");

// What pressing "Research all stale" did, as lines for the page's result box.
function queuedLines(r) {
  const lines = [];
  const symbols = (list) => list.join(", ");
  if (r.queued.length) {
    lines.push(toActivity(`Queued research on ${plural(r.queued.length, "listing")} `
      + `(${symbols(r.queued.map((j) => j.subject))}). They run one after another: follow or cancel them in the `));
  } else if (!r.waiting.length && !r.refused.length) {
    lines.push("Nothing to research: every watched listing's research is up to date.");
  }
  if (r.waiting.length) lines.push(`Already queued, so not added again: ${symbols(r.waiting)}.`);
  for (const [symbol, why] of r.refused) lines.push(`Could not queue ${symbol}: ${why}`);
  return lines;
}

// Queues research on every watched listing a tick would research now (missing, too old or
// behind a price move). `stale` is /api/ledgers/ai/stale; `done` gets the lines to show.
function staleButton(stale, done) {
  const n = stale.due.length - stale.waiting.length;
  const button = busyButton(`Research all stale (${n})`, async () => {
    try {
      done(queuedLines(await api("/api/ledgers/ai/research-stale", { method: "POST" })));
    } catch (err) {
      done([`Could not queue research: ${err.message}`]);
    }
  });
  button.disabled = n === 0;
  let hint;
  if (n > 0) {
    hint = `${plural(n, "watched listing")} ${n === 1 ? "has" : "have"} research that is missing, too old or behind `
      + "a price move. This queues one research run each, without the per-tick time limit.";
  } else if (stale.waiting.length) {
    const w = stale.waiting.length;
    hint = toActivity(`${w === 1 ? "The stale listing is" : `All ${w} stale listings are`} already queued: see the `);
  } else {
    hint = "Nothing to research: every watched listing's research is up to date.";
  }
  return el("div", { class: "row small" }, button, el("span", { class: "dim" }, hint));
}

// Beside an AI decision that needs research: queues it for that one listing. Pressing again
// while it waits joins the same job.
function researchButton(symbol, done) {
  const button = busyButton("Research", async () => {
    try {
      const job = await api(`/api/jobs/research?${new URLSearchParams({ symbol })}`, { method: "POST" });
      done([toActivity(`Research on ${symbol} is ${job.state}: follow it in the `)]);
    } catch (err) {
      done([`Could not queue research on ${symbol}: ${err.message}`]);
    }
  });
  button.className = "small-btn";
  return button;
}

export { queuedLines, researchButton, staleButton };
