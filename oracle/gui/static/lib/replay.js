// The Ledgers tab's Replay study card: research replayed at past dates over a fixed spread of
// listings, one replay at a time in the PC's free time. It never starts by itself.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { pct } from "./fmt.js";
import { repaint } from "./repaint.js";
import { busyButton, runJob, sendJson } from "./ui.js";

const POLL_MS = 15000;
const minutes = (s) => `${Math.max(0, Math.round(s / 60))} min`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const share = (v) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const rate = (v, ci) => share(v) + (ci ? ` (95% ${share(ci[0])} to ${share(ci[1])})` : "");
const HORIZON_NAMES = { "1m": "1 month", "3m": "3 months" };

function replaySection() {
  let started = false;
  let timer = null;
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const body = el("div");
  let report = null; // the last score fetched, and the status score it was fetched for
  let reportKey = null;

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

  function createForm(cutoff, label) {
    const first = el("input", { type: "date", "aria-label": "First replay day", value: cutoff ?? "" });
    if (cutoff) first.setAttribute("min", cutoff);
    const create = busyButton(label, async () => {
      try {
        await sendJson("/api/replay", { first: first.value || null });
        say("Created, paused: press Start when you want it to run.", false);
      } catch (err) {
        say(err.message);
      }
      await load();
    });
    const where = cutoff ? `the model's measured cutoff is ${cutoff}; no replay may start before it`
      : "no model cutoff is measured yet, so pick the first day yourself: after what the model can know";
    return el("div", null,
      el("div", { class: "row" }, "First replay day ", first, " ", create),
      el("p", { class: "dim small" }, `One replay per listing on the first trading day of each month, up to a month `
        + `ago so each has an outcome. About 20 listings spread over regions and sectors, picked once and kept. `
        + `Its ${where}.`));
  }

  function progress(s) {
    const current = s.running.length
      ? s.running.map((c) => `Replaying ${c.symbol} as of ${c.as_of}, ${minutes(c.age_s)} so far.`).join(" ")
      : s.state === "paused" ? `Paused${s.paused_reason ? `: ${s.paused_reason}` : "."}`
        : s.complete ? "Complete: every replay has run."
          : s.waiting ? `Waiting: ${s.waiting}.` : "Waiting for the PC to be free.";
    const mean = s.mean_replay_s == null ? "no replay finished yet" : `${minutes(s.mean_replay_s)} per replay on average`;
    return [
      el("p", null, `${s.done} done, ${s.pending} queued, ${s.failed.length} failed of ${s.total} `
        + `(${s.first_day} to ${s.last_day}); ${mean}.`),
      el("p", { class: s.state === "paused" && s.paused_reason ? "warn-text small" : "small" }, current),
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
    const listings = s.listings.map((p) => `${p.symbol} (${p.region}, ${p.sector})`).join(", ");
    const toggle = s.state === "running" ? press("Pause", "/api/replay/pause")
      : press("Start", "/api/replay/start");
    return [
      el("div", { class: "row" }, toggle, el("span", { class: "dim small" }, s.state === "running"
        ? "Replays run one at a time when nothing else is queued, no tick is due and the PC is idle."
        : "Nothing runs until you press Start. Pause lets a running replay finish.")),
      ...progress(s),
      el("p", { class: "small" }, `Listings: ${listings}.`),
      ...failures(s),
    ];
  }

  function group(g) {
    return el("tr", null, el("td", null, g.name), el("td", { class: "num" }, String(g.n)),
      el("td", { class: "num" }, share(g.hit_rate)), el("td", { class: "num" }, share(g.hit_rate_vs_basket)),
      el("td", { class: "num" }, pct(g.mean_return)), el("td", { class: "num" }, pct(g.mean_vs_basket)),
      el("td", { class: "num" }, g.mean_confidence == null ? "n/a" : g.mean_confidence.toFixed(2)));
  }

  function horizon(h) {
    return [
      el("p", null, `${HORIZON_NAMES[h.horizon] ?? h.horizon}: ${h.scored} scored, ${h.pending} pending, `
        + `${h.unpriced} without prices; hit rate ${rate(h.hit_rate, h.hit_ci)}, `
        + `${rate(h.hit_rate_vs_basket, h.hit_vs_basket_ci)} against the basket. Base rates: `
        + `${share(h.base_rate_up)} of all the study's windows rose, ${share(h.base_rate_beat_basket)} beat the basket.`),
      table([["Verdict or band"], ["Count", null, true], ["Hit", null, true], ["Hit vs basket", null, true],
        ["Mean return", null, true], ["Mean vs basket", null, true], ["Confidence", null, true]],
      [...h.by_verdict, ...h.by_band].map(group), ""),
    ];
  }

  const updatePrices = busyButton("Update prices", async (onState) => {
    try {
      const r = await runJob("/api/replay/prices", onState);
      const failed = r.listings.filter((o) => o.error);
      say(r.listings.length ? `Prices fetched for ${r.listings.length - failed.length} of `
        + `${plural(r.listings.length, "listing")}${failed.length ? `; failed: ${failed.map((o) => `${o.symbol} `
        + `(${o.error})`).join(", ")}` : ""}.` : "No listing was missing prices.", failed.length > 0);
    } catch (err) {
      say(err.message);
    }
    reportKey = null;
    await load();
  });

  function score(r) {
    if (!r) return [];
    const m = r.mix;
    const missing = r.unpriced_listings.length
      ? `No stored prices cover ${r.unpriced_listings.join(", ")} for a window that has closed: press Update prices.`
      : "Every closed window has its prices.";
    const updated = r.prices_updated_at
      ? `Study prices last updated ${r.prices_updated_at.slice(0, 16).replace("T", " ")} UTC.`
      : "Study prices not updated yet.";
    return [
      el("h3", null, "Score"),
      el("p", null, `${r.verdicts} verdicts, ${r.no_verdict} without a verdict, ${r.failed} failed, `
        + `${r.to_run} still to run, of ${r.total} replays. Verdict mix: ${m.buy} buy, ${m.hold} hold, ${m.sell} sell `
        + `(${share(m.buy_share)} buys), mean confidence ${m.mean_confidence == null ? "n/a"
          : m.mean_confidence.toFixed(2)}.`),
      ...r.horizons.flatMap(horizon),
      el("p", { class: "dim small" }, "Hits are buys that rose and sells that fell; holds call no direction. The "
        + "bands hold buys and sells only, their returns as called (a sell's sign flipped). Returns are in each "
        + "listing's own currency, so \"vs basket\" compares against a basket that mixes currencies."),
      el("div", { class: "row" }, updatePrices, el("span", { class: "dim small" }, `${missing} ${updated}`)),
      el("h3", null, "Limits"),
      el("ul", { class: "small" }, ...r.limits.map((line) => el("li", null, line))),
    ];
  }

  // The status poll is cheap; the full report is fetched only when the status's score moved.
  async function loadReport(s) {
    const key = `${s.id}:${JSON.stringify(s.score)}`;
    if (key === reportKey) return;
    try {
      report = (await api("/api/replay/report")).report;
      reportKey = key;
    } catch (err) {
      say(`Could not load the replay score: ${err.message}`);
    }
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
    if (s) await loadReport(s);
    repaint(body, s ? [...study(s), ...score(report), el("details", null, el("summary", null, "New study"),
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
