// The research lab (research-lab spec 17): the scoreboard, the results table, the strategy tests, the funnel stages,
// the literature index, the papers card, then the weekly page card. The results table links to a method's notes
// (literature) and a test's registration (scoreboard); a funnel run reloads it.
import { el } from "../lib/dom.js";
import { scoreboardSection } from "../lib/lab.js";
import { literatureSection } from "../lib/literature.js";
import { papersSection } from "../lib/papers.js";
import { resultsSection } from "../lib/results.js";
import { strategySection } from "../lib/strategy.js";
import { funnelSection } from "../lib/triage.js";
import { weeklySection } from "../lib/weekly.js";

const reveal = (section) => section.node.scrollIntoView?.({ behavior: "smooth" });

function render(target) {
  const board = scoreboardSection();
  const literature = literatureSection();
  const holder = {};
  const funnel = funnelSection({ changed: () => holder.results.start() });
  holder.results = resultsSection({
    openMethod: async (ref) => { await literature.open(ref); reveal(literature); },
    openTest: async (key) => { await board.show(key); reveal(board); },
    pick: (ref) => { funnel.pick(ref); reveal(funnel); },
  });
  const sections = [board, holder.results, strategySection(), funnel, literature, papersSection(), weeklySection()];
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "research", label: "Research", render };
