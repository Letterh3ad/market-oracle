// "Your plan" (boring-plan 05): the fund, wrapper, platform, monthly amount and trend overlay, each
// with its reason, what is still missing, and the two settings only this card sets (fund, monthly).
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { sendJson } from "./ui.js";

function planSummarySection() {
  const note = el("div", { class: "form-note" });
  const body = el("div");
  const fund = el("select", { name: "fund", "aria-label": "Fund" });
  const monthly = el("input", { name: "monthly", type: "number", min: "0", step: "any", size: 8,
    "aria-label": "Monthly (GBP)" });
  let started = false;
  let loads = 0;

  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  function fill(s) {
    const label = Object.fromEntries(s.funds);
    fund.replaceChildren(el("option", { value: "", selected: s.picked ? null : "" },
      `Default (${label[s.default_fund] ?? s.default_fund})`),
    ...s.funds.map(([key, name]) => el("option", { value: key, selected: s.picked && s.fund === key ? "" : null }, name)));
    fund.value = s.picked ? s.fund : "";
    monthly.value = s.monthly ? String(s.monthly) : "";
    const rows = s.lines.map((l) => el("tr", { "data-line": l.name },
      el("td", null, l.label), el("td", { class: l.known ? null : "warn-text" }, el("strong", null, l.value)),
      el("td", { class: "small" }, l.reason)));
    body.replaceChildren(table([["Part"], ["Plan"], ["Why"]], rows, "Nothing yet."),
      ...(s.missing.length ? [el("h3", null, "Missing"),
        el("ul", { class: "small", "data-missing": "" }, ...s.missing.map((m) => el("li", null, m.text)))] : []));
  }

  async function load() {
    const mine = ++loads;
    try {
      const s = await api("/api/plan/summary");
      if (mine !== loads) return;
      fill(s);
    } catch (err) {
      if (mine === loads) say(`Could not load your plan: ${err.message}`);
    }
  }

  const save = el("button", { type: "button", onclick: async () => {
    save.disabled = true;
    try {
      await sendJson("/api/plan/choice", { fund: fund.value || "default", monthly: Number(monthly.value || 0) }, "PUT");
      say("Saved.", false);
      await load();
    } catch (err) {
      say(err.message);
    } finally {
      save.disabled = false;
    }
  } }, "Save");

  const node = el("div", { class: "card", "data-plan-summary": "" },
    el("h2", null, "Your plan"),
    el("p", { class: "dim small" }, "What to do with real money each month, and why, from the cards below and your "
      + "settings. The fund defaults to the numbers' pick; choose another to keep it. Nothing here places a trade."),
    body,
    el("div", { class: "row wrap" }, el("label", null, "Fund ", fund), el("label", null, "Monthly (GBP) ", monthly), save),
    note);

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return load();
  }

  return { node, start, reload: load };
}

export { planSummarySection };
