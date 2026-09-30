// Dated headlines behind a link or a company's news; only a web address becomes a link.
import { el, safeUrl } from "./dom.js";

function headlines(items) {
  if (!items.length) return null;
  return el("ul", { class: "small" }, ...items.map((e) => {
    const link = safeUrl(e.link);
    const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
    return el("li", null, `${(e.published_at ?? "undated").slice(0, 10)} `, title,
      e.outlet ? el("span", { class: "dim" }, ` (${e.outlet})`) : null);
  }));
}

export { headlines };
