// The research lab (research-lab spec 17): the scoreboard first, the strategy tests, then the literature index; later panels
// (results table, papers) append to SECTIONS.
import { el } from "../lib/dom.js";
import { scoreboardSection } from "../lib/lab.js";
import { literatureSection } from "../lib/literature.js";
import { papersSection } from "../lib/papers.js";
import { strategySection } from "../lib/strategy.js";

const SECTIONS = [scoreboardSection, strategySection, literatureSection, papersSection];

function render(target) {
  const sections = SECTIONS.map((make) => make());
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "research", label: "Research", render };
