// The Ledgers tab's Replay study card: research replayed at past dates over a fixed spread of
// listings, one replay at a time in the PC's free time. It never starts by itself.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { repaint } from "./repaint.js";
import { busyButton, followJob, sendJson } from "./ui.js";

const POLL_MS = 15000;
const minutes = (s) => `${Math.max(0, Math.round(s / 60))} min`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const clock = (iso) => String(iso).slice(0, 16).replace("T", " ");

function replaySection() {
  let started = false;
  let timer = null;
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const body = el("div");

  // Every press reloads the card, so it always shows what the store holds.
  const press = (label, path, payload) => busyButton(label, async () => {
    try {
      await sendJson(path, payload);
      say("", false);
    } catch (err) {
      say(err.message);
    }
    await load();
  });

  // Creating and replacing check prices first, so they run as jobs; their progress shows here.
  const job = (label, path, payload, done) => busyButton(label, async () => {
    try {
      const started = await sendJson(path, payload());
      const result = await followJob(started, (j) => say(j.progress ?? `${label}: ${j.state}...`, false));
      say(done(result ?? {}), false);
    } catch (err) {
      say(err.message);
    }
    await load();
  });

  function createForm(cutoff, label) {
    const first = el("input", { type: "date", "aria-label": "First replay day", value: cutoff ?? "" });
    if (cutoff) first.setAttribute("min", cutoff);
    const symbols = el("input", { size: "36", placeholder: "blank: a spread picked for you",
      "aria-label": "Replay listings" });
    const create = job(label, "/api/replay", () => ({ first: first.value || null,
      symbols: symbols.value.split(",").map((s) => s.trim()).filter(Boolean) }),
    (r) => "Created, paused: press Start when you want it to run."
      + (r.left_out?.length ? ` Left out, nothing fits: ${r.left_out.join("; ")}.` : ""));
    const where = cutoff ? `the model's measured cutoff is ${cutoff}; no replay may start before it`
      : "no model cutoff is measured yet, so pick the first day yourself: after what the model can know";
    return el("div", null,
      el("div", { class: "row" }, "First replay day ", first, " listings ", symbols, " ", create),
      el("p", { class: "dim small" }, "One replay per listing on its first trading day of each month, up to a month "
        + "ago so each has an outcome. About 20 listings spread over regions and sectors, each on its home market "
        + "with prices from the first day to now (fetched while creating, a minute or two), picked once and kept. "
        + `Its ${where}.`));
  }

  function progress(s) {
    const current = s.running.length
      ? s.running.map((c) => `Replaying ${c.symbol} as of ${c.as_of}, ${minutes(c.age_s)} so far.`).join(" ")
      : s.state === "paused" ? `Paused${s.paused_reason ? `: ${s.paused_reason}` : "."}`
        : s.complete ? "Complete: every replay is done."
          : s.finished ? "Finished, with failed replays: retry them below."
            : s.waiting ? `Waiting: ${s.waiting}.` : "Waiting for the PC to be free.";
    const mean = s.mean_replay_s == null ? "no replay finished yet" : `${minutes(s.mean_replay_s)} per replay on average`;
    const polled = s.polled_at ? `Last checked ${clock(s.polled_at)} UTC.`
      : "Not checked yet: replays are paced by the running app.";
    return [
      el("p", null, `${s.done} done, ${s.pending} queued, ${s.failed.length} failed of ${s.total} `
        + `(${s.first_day} to ${s.last_day}); ${mean}.`),
      el("p", { class: s.state === "paused" && s.paused_reason ? "warn-text small" : "small" }, current),
      el("p", { class: "dim small" }, polled),
    ];
  }

  function listings(s) {
    const text = s.listings.map((p) => `${p.symbol} (${p.region}, ${p.sector})`).join(", ");
    if (!s.untouched) return [el("p", { class: "small" }, `Listings: ${text}.`)];
    return [
      el("p", { class: "dim small" }, "Before the first replay, any listing can be swapped for another: it is held to "
        + "the same checks."),
      table([["Symbol"], ["Region"], ["Sector"], ["Replace with"]], s.listings.map((p) => {
        const other = el("input", { size: "10", placeholder: "symbol", "aria-label": `Replace ${p.symbol} with` });
        return el("tr", null, el("td", { class: "mono" }, p.symbol), el("td", null, p.region),
          el("td", null, p.sector), el("td", null, other, " ",
            job("Replace", "/api/replay/replace", () => ({ old: p.symbol, new: other.value.trim() }),
              () => `Replaced ${p.symbol}.`)));
      }), ""),
    ];
  }

  function failures(s) {
    if (!s.failed.length && !s.top_failures.length) return [];
    const retrying = s.retrying ? ` ${plural(s.retrying, "cell")} waiting for its one automatic retry.` : "";
    return [
      el("h3", null, "Failures"),
      el("ul", { class: "small" }, ...s.top_failures.map(([reason, n]) => el("li", null, `${n} x ${reason}`))),
      el("p", { class: "dim small" }, `A failed replay is retried once automatically, later in the queue; after that `
        + `it stays failed until you retry it.${retrying}`),
      s.failed.length ? el("div", { class: "row" }, press(`Retry failed (${s.failed.length})`, "/api/replay/retry", {}))
        : null,
      s.failed.length ? table([["Symbol"], ["As of"], ["Error"], [""]],
        s.failed.map((c) => el("tr", null, el("td", { class: "mono" }, c.symbol), el("td", null, c.as_of),
          el("td", { class: "small" }, c.error ?? ""),
          el("td", null, press("Retry", "/api/replay/retry", { cell_id: c.id })))), "") : null,
    ];
  }

  function study(s) {
    const toggle = s.state === "running" ? press("Pause", "/api/replay/pause")
      : press("Start", "/api/replay/start");
    return [
      el("div", { class: "row" }, toggle, el("span", { class: "dim small" }, s.state === "running"
        ? "Replays run one at a time when nothing else is queued, no tick is due and the PC is idle; a tick or your "
          + "own research stops a running replay, which goes back in the queue."
        : "Nothing runs until you press Start. Pause lets a running replay finish.")),
      ...progress(s),
      ...listings(s),
      ...failures(s),
    ];
  }

  async function load() {
    let r;
    try {
      r = await api("/api/replay");
    } catch (err) {
      body.replaceChildren(el("p", { class: "down" }, `Could not load the replay study: ${err.message}`));
      return;
    }
    const s = r.study;
    repaint(body, s ? [...study(s), el("details", null, el("summary", null, "New study"),
      el("p", { class: "dim small" }, "The current study is kept; the new one takes its place here, paused."),
      createForm(r.cutoff, "Create new study"))] : [createForm(r.cutoff, "Create study")]);
  }

  async function start() {
    if (started) return;
    started = true;
    await load();
    timer = setInterval(() => { if (node.isConnected) load(); else clearInterval(timer); }, POLL_MS);
  }

  const node = el("div", { class: "card" },
    el("h2", null, "Replay study"),
    el("p", { class: "dim small" }, "Research replayed at past dates, seeing only what existed then, to measure "
      + "whether its verdicts beat what prices did next. Each replay is a normal research job in the Activity tab."),
    body, note);
  return { node, start };
}

export { replaySection };
