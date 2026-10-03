// The boring plan: "your plan" on top, then the cards it is read from. Later panels (guardrails,
// wrappers) append to SECTIONS.
import { el } from "../lib/dom.js";
import { fundsSection } from "../lib/funds.js";
import { guardrailsSection } from "../lib/guardrails.js";
import { planMoneySection } from "../lib/plan_money.js";
import { planSummarySection } from "../lib/plan_summary.js";
import { trendSection } from "../lib/trend.js";

const SECTIONS = [fundsSection, planMoneySection, trendSection, guardrailsSection];

function render(target) {
  const cards = [];
  // A save anywhere can leave a change pending, so cards that show the plan's state refresh too.
  const refresh = () => cards.forEach((s) => s.refresh?.());
  const summary = planSummarySection({ changed: refresh });
  const changed = () => { summary.reload(); refresh(); };
  cards.push(...SECTIONS.map((make) => make({ changed })));
  const sections = [summary, ...cards];
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "plan", label: "Plan", render };
