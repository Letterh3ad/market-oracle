// The research lab: each section is a card; research-lab tickets append theirs to SECTIONS.
import { el } from "../lib/dom.js";
import { literatureSection } from "../lib/literature.js";

const SECTIONS = [literatureSection];

function render(target) {
  const sections = SECTIONS.map((make) => make());
  target.replaceChildren(el("div", null, ...sections.map((s) => s.node)));
  return Promise.all(sections.map((s) => s.start()));
}

export default { id: "research", label: "Research", render };
