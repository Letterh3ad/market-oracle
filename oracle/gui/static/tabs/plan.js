// The boring plan: "your plan" on top, then the cards it is read from. Later panels (guardrails,
// wrappers) append to SECTIONS.
import { el } from "../lib/dom.js";
import { fundsSection } from "../lib/funds.js";
import { planMoneySection } from "../lib/plan_money.js";
import { planSummarySection } from "../lib/plan_summary.js";
import { trendSection } from "../lib/trend.js";

const SECTIONS = [fundsSection, planMoneySection, trendSection];

function render(target) {
  const summary = planSummarySection();
  const changed = () => summary.reload();
  const sections = [summary, ...SECTIONS.map((make) => make({ changed }))];
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "plan", label: "Plan", render };
