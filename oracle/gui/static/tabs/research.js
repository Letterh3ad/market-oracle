// The research lab (research-lab spec 17): the scoreboard first; later panels (literature index,
// results table, papers) append to SECTIONS.
import { el } from "../lib/dom.js";
import { scoreboardSection } from "../lib/lab.js";

const SECTIONS = [scoreboardSection];

function render(target) {
  const sections = SECTIONS.map((make) => make());
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "research", label: "Research", render };
