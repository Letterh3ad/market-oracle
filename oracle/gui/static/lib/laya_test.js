// The Ledgers tab's Laya predictive test card: score the backfilled history with Laya, run the
// pre-registered test (and its sensitivity run), and show the kept tables with the verdict.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { repaint } from "./repaint.js";
import { busyButton, runJob } from "./ui.js";

const num = (v, digits = 4) => (v == null ? "n/a" : (v > 0 ? "+" : "") + v.toFixed(digits));
const BEATS = { true: "beats", false: "does not beat", null: "cannot be set against",
  undefined: "cannot be set against" };
const tone = (label) => (label === "PASS" ? "up" : label === "FAIL" ? "down" : "dim");

function horizon(h) {
  const segments = table([["Segment"], ["IC", null, 1], ["t", null, 1], ["Days", null, 1], ["Counted"],
    ["Weights (fitted on trailing data)"]],
  h.segments.map((s) => el("tr", null, el("td", null, `${s.start} to ${s.end}`),
    el("td", { class: "num" }, num(s.ic)), el("td", { class: "num" }, num(s.t, 2)),
    el("td", { class: "num" }, String(s.days)), el("td", { class: s.counted ? "" : "dim" }, s.counted ? "yes" : `no: ${s.why}`),
    el("td", { class: "small mono" }, Object.entries(s.weights).filter(([, w]) => w)
      .map(([f, w]) => `${f} ${num(w, 2)}`).join(", ")))), "No segments.");
  const features = table([["Feature (as is, counted days)"], ["IC", null, 1], ["t", null, 1], ["Days", null, 1],
    ["Segments positive", null, 1]],
  h.features.map((f) => el("tr", { class: f.in_score ? "" : "dim" },
    el("td", { class: "mono" }, f.name + (f.in_score ? "" : " (secondary)")),
    el("td", { class: "num" }, num(f.ic)), el("td", { class: "num" }, num(f.t, 2)),
    el("td", { class: "num" }, String(f.days)), el("td", { class: "num" }, `${f.positive} of ${h.counted}`))),
  "No features.");
  return [
    el("h4", null, `${h.horizon}-day excess return, features over ${h.lookback} days: `,
      el("span", { class: tone(h.verdict) }, h.verdict)),
    el("p", { class: "small" }, `IC ${num(h.ic)}, t ${num(h.t, 2)}, ${h.days} IC days, positive in ${h.positive} of `
      + `${h.counted} counted segments.`),
    el("p", { class: "small" }, `News-attention baseline (own_attention, sign fitted): IC ${num(h.baseline_ic)}, t `
      + `${num(h.baseline_t, 2)}; Laya ${BEATS[String(h.beats_baseline)]} it. Momentum baseline (own_momentum, sign `
      + `fitted): IC ${num(h.momentum_ic)}, t ${num(h.momentum_t, 2)}; Laya ${BEATS[String(h.beats_momentum)]} it. `
      + "Laya counts as useful only if it beats both baselines; the pass bar is separate."),
    segments, features,
  ];
}

function result(t) {
  const pairs = Object.entries(t.articles).map(([r, n]) => `${r} ${n}`).join(", ");
  return el("div", null,
    el("h3", null, `${t.label}: `, el("span", { class: tone(t.verdict) }, t.verdict)),
    el("p", { class: "dim small" }, `Model ${t.model}; ${t.start} to ${t.end}; ${t.companies} companies; pairs counted `
      + `${pairs || "none"}. Run ${String(t.run_at).slice(0, 16)}. t-stat: ${t.t_method}.`),
    ...t.horizons.flatMap(horizon),
    el("p", { class: "dim small" }, t.caveat));
}

function layaTestSection() {
  const body = el("div", null, el("p", { class: "dim" }, "Loading..."));
  const note = el("p", { class: "small" });
  const plan = el("p", { class: "dim small" });
  let started = false;
  const say = (text) => note.replaceChildren(text);

  async function load() {
    let got;
    try {
      got = await api("/api/laya/tests");
    } catch (err) {
      say(`Could not load the Laya test: ${err.message}`);
      return;
    }
    const last = got.last_scoring ? `Last scoring run: ${got.last_scoring.summary}.` : "History not scored yet.";
    repaint(body, [el("p", { class: "small" }, last),
      ...(got.tests.length ? got.tests.map(result) : [el("p", { class: "dim" }, "No test run yet.")])]);
  }

  const cpu = el("input", { type: "checkbox", "aria-label": "Allow the CPU" });
  const score = busyButton("Score history with Laya", async (onState) => {
    try {
      const r = await runJob(`/api/jobs/laya-history${cpu.checked ? "?allow_cpu=true" : ""}`, onState);
      say(r.summary);
    } catch (err) {
      say(err.message);
    }
    await load();
  });
  const test = (sensitivity) => busyButton(sensitivity ? "Run sensitivity (links as known today)" : "Run the test",
    async (onState) => {
      try {
        const t = await runJob(`/api/jobs/laya-test${sensitivity ? "?sensitivity=true" : ""}`, onState);
        say(`${t.label}: ${t.verdict}`);
      } catch (err) {
        say(err.message);
      }
      await load();
    });
  const estimate = busyButton("Count pairs and estimate", async () => {
    try {
      const p = await api("/api/laya/plan");
      plan.replaceChildren(`${p.pairs} pairs for ${p.targets} companies, ${p.unscored} not scored: about `
        + `${(p.estimate_s / 3600).toFixed(1)} h at ${p.ms_per_pair} ms a pair (${p.basis}).`);
    } catch (err) {
      plan.replaceChildren(err.message);
    }
  });

  async function start() {
    if (started) return;
    started = true;
    await load();
  }

  const node = el("div", { class: "card" },
    el("h2", null, "Laya predictive test"),
    el("p", { class: "dim small" }, "Does Laya's reading of the news predict a company's next 5 and 20 trading days "
      + "over its industry? Scored per (article, company) pair over the backfilled history, then judged walk-forward "
      + "in six-month segments from 2022-09-21 against a bar fixed before any result: rank IC above 0 with t above 2 "
      + "overall and positive in most counted segments, at both horizons. Laya's probabilities are uncalibrated."),
    el("div", { class: "row" }, score, " ", estimate, " ", test(false), " ", test(true)),
    el("div", { class: "row small" }, el("label", null, cpu, " Allow the CPU when the GPU has no room (about 40 times "
      + "slower: days, not hours); otherwise scoring waits for GPU room")),
    plan, note, body);
  return { node, start };
}

export { layaTestSection };
