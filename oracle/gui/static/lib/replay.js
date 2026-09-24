// The Ledgers tab's Replay study card: research replayed at past dates over a fixed spread of
// listings, one replay at a time in the PC's free time. It never starts by itself.
import { api } from "./api.js";
import { compareLines, lineInfos, table } from "./compare.js";
import { cutoffSection } from "./cutoff_probe.js";
import { el } from "./dom.js";
import { money, pct } from "./fmt.js";
import { repaint } from "./repaint.js";
import { busyButton, followJob, runJob, sendJson } from "./ui.js";

const POLL_MS = 15000;
const minutes = (s) => `${Math.max(0, Math.round(s / 60))} min`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const share = (v) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const rate = (v, ci) => share(v) + (ci ? ` (95% ${share(ci[0])} to ${share(ci[1])})` : "");
const HORIZON_NAMES = { "1m": "1 month", "3m": "3 months" };
const clock = (iso) => String(iso).slice(0, 16).replace("T", " ");
const SIZE_MIXES = { mix: "Large and mid/small", large: "Large only", "mid-small": "Mid/small only" };

function replaySection() {
  let started = false;
  let timer = null;
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const body = el("div");
  let report = null; // the last score fetched, and the status score it was fetched for
  let reportKey = null;
  let chart = null;

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

  // `r`: the status's cutoff, the first day after it, and why no study can start, if none can.
  function createForm(r, label) {
    const first = el("input", { type: "date", "aria-label": "First replay day", value: r.replay_from ?? "" });
    if (r.replay_from) first.setAttribute("min", r.replay_from);
    const symbols = el("input", { size: "36", placeholder: "blank: a spread picked for you",
      "aria-label": "Replay listings" });
    const bands = el("select", { "aria-label": "Study size mix" },
      ...Object.entries(SIZE_MIXES).map(([value, text]) => el("option", { value }, text)));
    const create = job(label, "/api/replay", () => ({ first: first.value || null,
      symbols: symbols.value.split(",").map((s) => s.trim()).filter(Boolean), bands: bands.value }),
    (r) => "Created, paused: press Start when you want it to run."
      + (r.left_out?.length ? ` Left out, nothing fits: ${r.left_out.join("; ")}.` : ""));
    const where = r.cutoff_note ? `Creating is refused for now: ${r.cutoff_note}`
      : `The measured cutoff is ${r.cutoff}: the first replay day must be after it, from ${r.replay_from}.`;
    return el("div", null,
      el("div", { class: "row" }, "First replay day ", first, " listings ", symbols, " ", bands, " ", create),
      el("p", { class: "dim small" }, "One replay per listing on its first trading day of each month, up to a month "
        + "ago so each has an outcome. About 20 listings per size spread over regions and sectors, each on its home "
        + "market with prices from the first day to now, picked once and kept. Creating fetches prices and takes a "
        + "few minutes: a mix checks about twice as many listings as one size. Listings you name stand in for the "
        + "large half, and a mix still picks a mid/small half beside them. " + where));
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
    const stages = Object.entries(s.stages ?? {}).map(([stage, profile]) => `${stage} on ${profile}`).join(", ");
    return [
      el("p", null, `${s.done} done, ${s.pending} queued, ${s.failed.length} failed of ${s.total} `
        + `(${s.first_day} to ${s.last_day}); ${mean}.`),
      ...(stages ? [el("p", { class: "dim small" }, `Stage models, fixed when the study was created: ${stages}.`
        + (s.cutoff ? ` Measured cutoff: ${s.cutoff}.` : ""))] : []),
      el("p", { class: s.state === "paused" && s.paused_reason ? "warn-text small" : "small" }, current),
      el("p", { class: "dim small" }, polled),
    ];
  }

  function listings(s) {
    const text = s.listings.map((p) => `${p.symbol} (${[p.region, p.sector, p.market_cap].filter(Boolean).join(", ")})`)
      .join(", ");
    if (!s.untouched) return [el("p", { class: "small" }, `Listings: ${text}.`)];
    return [
      el("p", { class: "dim small" }, "Before the first replay, any listing can be swapped for another: it is held to "
        + "the same checks."),
      table([["Symbol"], ["Region"], ["Sector"], ["Size"], ["Replace with"]], s.listings.map((p) => {
        const other = el("input", { size: "10", placeholder: "symbol", "aria-label": `Replace ${p.symbol} with` });
        return el("tr", null, el("td", { class: "mono" }, p.symbol), el("td", null, p.region),
          el("td", null, p.sector), el("td", null, p.market_cap ?? ""), el("td", null, other, " ",
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
      ...(s.failed.length ? [
        el("div", { class: "row" }, press(`Retry failed (${s.failed.length})`, "/api/replay/retry", {})),
        table([["Symbol"], ["As of"], ["Error"], [""]],
          s.failed.map((c) => el("tr", null, el("td", { class: "mono" }, c.symbol), el("td", null, c.as_of),
            el("td", { class: "small" }, c.error ?? ""),
            el("td", null, press("Retry", "/api/replay/retry", { cell_id: c.id })))), ""),
      ] : []),
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
        + `${rate(h.hit_rate_vs_basket, h.hit_vs_basket_ci)} against the basket. No skill: of the called windows `
        + `${share(h.base_rate_up)} rose and ${share(h.base_rate_beat_basket)} beat the basket, so this mix of calls `
        + `would hit ${share(h.expected_hit_rate)} (${share(h.expected_hit_rate_vs_basket)} against the basket) `
        + `by chance.`),
      table([["Verdict or band"], ["Count", null, true], ["Hit", null, true], ["Hit vs basket", null, true],
        ["Mean return", null, true], ["Mean vs basket", null, true], ["Confidence", null, true]],
      [...h.by_verdict, ...h.by_band].map(group), ""),
    ];
  }

  // The study's headline numbers beside each size's: the comparison the size mix is for.
  function sizes(r) {
    if (!r.by_size?.length) return [];
    const rows = [{ label: "All", horizons: r.horizons, account: r.account }, ...r.by_size];
    const calls = (h) => String(h.by_band.reduce((n, g) => n + g.n, 0));
    const num = (text) => el("td", { class: "num" }, text);
    const account = (row) => Object.fromEntries((row.account?.lines ?? []).map((line) => [line.name, line.return]));
    return [
      el("h3", null, "By size"),
      el("p", { class: "dim small" }, `${r.by_size.map((z) => `${z.label}: ${z.listings.join(", ")}.`).join(" ")} `
        + "Each size is scored against the whole study's basket, and traded as an account of its own."),
      ...r.horizons.map((h, i) => table([[HORIZON_NAMES[h.horizon] ?? h.horizon], ["Calls", null, true],
        ["Hit", null, true], ["No skill", null, true], ["Hit vs basket", null, true],
        ["No skill vs basket", null, true], ["Mean as called", null, true]], rows.map((row) => {
        const x = row.horizons[i];
        return el("tr", null, el("td", null, row.label), num(calls(x)), num(share(x.hit_rate)),
          num(share(x.expected_hit_rate)), num(share(x.hit_rate_vs_basket)),
          num(share(x.expected_hit_rate_vs_basket)), num(pct(x.mean_as_called)));
      }), "")),
      ...(r.account ? [table([["Account return"], ...Object.values(lineInfos(r.account.lines))
        .map((info) => [info.label, null, true])], rows.map((row) => el("tr", null, el("td", null, row.label),
        ...r.account.lines.map((line) => num(pct(account(row)[line.name]))))), "")] : []),
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

  function score(r, accountNode) {
    if (!r) return [];
    const m = r.mix;
    const missing = r.refetch_listings.length
      ? `A window closed since ${r.refetch_listings.join(", ")} last had prices fetched: press Update prices.`
      : "Every closed window has its prices fetched.";
    const failures = r.price_failures.length ? ` Failed then: ${r.price_failures.join(", ")}.` : "";
    const updated = r.prices_checked_at
      ? `Study prices last checked ${clock(r.prices_checked_at)} UTC.${failures}`
      : "Study prices not checked yet.";
    return [
      el("h3", null, "Score"),
      el("p", null, `${r.verdicts} verdicts, ${r.no_verdict} without a verdict, ${r.failed} failed, `
        + `${r.to_run} still to run, of ${r.total} replays. Verdict mix: ${m.buy} buy, ${m.hold} hold, ${m.sell} sell `
        + `(${share(m.buy_share)} buys), mean confidence ${m.mean_confidence == null ? "n/a"
          : m.mean_confidence.toFixed(2)}.`),
      ...(r.excluded ? [el("p", { class: "warn-text small" }, `${plural(r.excluded, "replay")} excluded: on or `
        + "before the current cutoff.")] : []),
      ...r.horizons.flatMap(horizon),
      el("p", { class: "dim small" }, "Hits are buys that rose and sells that fell; holds call no direction. The "
        + "bands hold buys and sells only, their returns as called (a sell's sign flipped). Returns are in each "
        + "listing's own currency, so \"vs basket\" compares against a basket that mixes currencies."),
      ...sizes(r),
      el("div", { class: "row" }, updatePrices, el("span", { class: "dim small" }, `${missing} ${updated}`)),
      accountNode,
      el("h3", null, "Limits"),
      el("ul", { class: "small" }, ...r.limits.map((line) => el("li", null, line))),
    ];
  }

  // The chart is drawn once the card is on screen, as it sizes to its box.
  function account(r) {
    const a = r?.account;
    if (!a) return { node: el("div"), draw: () => null };
    const lines = compareLines(a.lines, a.currency, "Too few days to draw a chart yet.");
    const node = el("div", null,
      el("h3", null, "Account"),
      el("p", { class: "dim small" }, `The verdicts traded as the AI account would, in memory, ${a.start} to `
        + `${a.end}, over ${plural(a.listings.length, "listing")}, from ${money(a.capital)} ${a.currency}.`),
      lines.node,
      a.left_out.length ? el("p", { class: "warn-text small" }, `Left out: ${a.left_out.join("; ")}.`) : null,
      el("ul", { class: "dim small" }, ...a.notes.map((text) => el("li", null, text))));
    return { node, draw: lines.draw };
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
    const shown = account(s ? report : null);
    repaint(body, s ? [...study(s), ...score(report, shown.node), el("details", null,
      el("summary", null, "New study"),
      el("p", { class: "dim small" }, "The current study is kept; the new one takes its place here, paused."),
      createForm(r, "Create new study"))] : [createForm(r, "Create study")], () => {
      chart?.remove();
      chart = shown.draw();
    });
  }

  // A finished probe moves the cutoff, so the create form reloads with it.
  const cutoff = cutoffSection(load);

  async function start() {
    if (started) return;
    started = true;
    await Promise.all([load(), cutoff.start()]);
    timer = setInterval(() => { if (node.isConnected) load(); else clearInterval(timer); }, POLL_MS);
  }

  const node = el("div", { class: "card" },
    el("h2", null, "Replay study"),
    el("p", { class: "dim small" }, "Research replayed at past dates, seeing only what existed then, to measure "
      + "whether its verdicts beat what prices did next. Each replay is a normal research job in the Activity tab."),
    cutoff.node, body, note);
  return { node, start };
}

export { replaySection };
