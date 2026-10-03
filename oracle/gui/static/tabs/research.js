// The research lab (research-lab spec 17): the scoreboard first, then the literature index; later panels
// (results table, papers) append to SECTIONS.
import { el } from "../lib/dom.js";
import { scoreboardSection } from "../lib/lab.js";
import { literatureSection } from "../lib/literature.js";

const SECTIONS = [scoreboardSection, literatureSection];

function render(target) {
  const sections = SECTIONS.map((make) => make());
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "research", label: "Research", render };
