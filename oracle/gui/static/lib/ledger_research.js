import { api } from "./api.js";
import { el } from "./dom.js";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const toActivity = (text) => el("span", null, text, el("a", { href: "#activity" }, "Activity tab"), ".");
const ACTIVE = ["queued", "running"];

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

// Disables the button while `action` runs; `action` repaints it afterwards.
function pressable(label, action) {
  const button = el("button", { type: "button", onclick: async () => {
    button.disabled = true;
    button.textContent = "Queueing...";
    await action();
  } }, label);
  return button;
}

// The Ledgers tab's research buttons, kept in step with the research jobs in place: a light
// poll of /api/activity repaints only these buttons, never the page. One per tab render.
function researchLive() {
  let active = new Map(); // subject -> its queued or running research job
  let due = [];
  let clock = 0; // orders presses against polls, so a poll sent before a press cannot undo it
  const pressed = new Map(); // subject -> clock at the press
  const researched = new Set();
  const lines = new Set();
  const staleRows = new Set();

  const mark = (job) => {
    active.set(job.subject, job);
    pressed.set(job.subject, ++clock);
    researched.delete(job.subject);
  };

  function paintLine(line) {
    const job = active.get(line.symbol);
    if (!job && researched.has(line.symbol)) {
      line.node.replaceChildren(el("span", { class: "dim" }, "Researched"));
      return;
    }
    line.button.disabled = Boolean(job);
    line.button.textContent = !job ? "Research" : job.state === "running" ? "Researching..." : "Queued";
    line.note.textContent = job?.state === "running" && job.progress ? ` ${job.progress}` : "";
  }

  function paintStale(row) {
    const w = due.filter((s) => active.has(s)).length;
    const n = due.length - w;
    row.button.textContent = `Research all stale (${n})`;
    row.button.disabled = n === 0;
    let hint;
    if (n > 0) {
      hint = `${plural(n, "watched listing")} ${n === 1 ? "has" : "have"} research that is missing, too old or behind `
        + "a price move. This queues one research run each, without the per-tick time limit.";
    } else if (w) {
      hint = toActivity(`${w === 1 ? "The stale listing is" : `All ${w} stale listings are`} already queued: see the `);
    } else {
      hint = "Nothing to research: every watched listing's research is up to date.";
    }
    row.hint.replaceChildren(hint);
  }

  function paintAll() {
    for (const line of lines) if (line.node.isConnected) paintLine(line); else lines.delete(line);
    for (const row of staleRows) if (row.node.isConnected) paintStale(row); else staleRows.delete(row);
  }

  // Queues research on every watched listing a tick would research now (missing, too old or
  // behind a price move). `stale` is /api/ledgers/ai/stale; `done` gets the lines to show.
  function staleButton(stale, done) {
    due = stale.due;
    for (const s of due) researched.delete(s);
    for (const s of stale.waiting) if (!active.has(s)) active.set(s, { subject: s, state: "queued" });
    const button = pressable("Research all stale", async () => {
      try {
        const r = await api("/api/ledgers/ai/research-stale", { method: "POST" });
        r.queued.forEach(mark);
        done(queuedLines(r));
      } catch (err) {
        done([`Could not queue research: ${err.message}`]);
      }
      paintAll();
    });
    const row = { button, hint: el("span", { class: "dim" }) };
    row.node = el("div", { class: "row small" }, button, row.hint);
    staleRows.add(row);
    paintStale(row);
    return row.node;
  }

  // Beside an AI decision that needs research: queues it for that one listing.
  function researchButton(symbol, done) {
    const button = pressable("Research", async () => {
      try {
        const job = await api(`/api/jobs/research?${new URLSearchParams({ symbol })}`, { method: "POST" });
        mark(job);
        done([toActivity(`Research on ${symbol} is ${job.state}: follow it in the `)]);
      } catch (err) {
        done([`Could not queue research on ${symbol}: ${err.message}`]);
      }
      paintAll();
    });
    button.className = "small-btn";
    const line = { symbol, button, note: el("span", { class: "dim small" }) };
    line.node = el("span", null, button, line.note);
    lines.add(line);
    paintLine(line);
    return line.node;
  }

  // A listing whose job has ended is looked up again: researched, it is no longer due.
  async function poll() {
    const asked = ++clock;
    let jobs;
    try {
      ({ jobs } = await api("/api/activity"));
    } catch {
      return; // the next poll tries again
    }
    const now = new Map(jobs.filter((j) => j.kind === "research" && ACTIVE.includes(j.state)).map((j) => [j.subject, j]));
    for (const [s, job] of active) if (!now.has(s) && (pressed.get(s) ?? 0) > asked) now.set(s, job);
    const ended = [...active.keys()].filter((s) => !now.has(s));
    active = now;
    if (ended.length) {
      try {
        due = (await api("/api/ledgers/ai/stale")).due;
        for (const s of ended) if (!due.includes(s)) researched.add(s);
      } catch {
        // Unknown for now: the buttons offer research again, and the server refuses a double.
      }
    }
    paintAll();
  }

  // Polls every `ms` while `node` is on screen.
  function start(node, ms = 3000) {
    poll();
    const timer = setInterval(() => (node.isConnected ? poll() : clearInterval(timer)), ms);
    return timer;
  }

  return { staleButton, researchButton, poll, start };
}

export { queuedLines, researchLive };
