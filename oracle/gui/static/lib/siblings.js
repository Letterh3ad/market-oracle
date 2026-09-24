// A dead listing's note: since when, and the company's live listings, each with Swap when
// the dead one is watched. `onSwapped` runs after a swap with the server's answer.
import { el, showError, clearError, companyLink } from "./dom.js";
import { sendJson } from "./ui.js";

function swapSummary(r) {
  const held = r.held_by.length ? ` Paper positions stay on ${r.old} (${r.held_by.join(", ")}).` : "";
  return `Watching ${r.new} instead of ${r.old} on ${r.watchlists.join(", ")}.${held}`;
}

function deadNote(symbol, deadSince, siblings, watched, onSwapped, more = "") {
  if (!deadSince) return null;
  const box = el("div", { class: "warn-text small" });
  const swap = (s) => el("button", { type: "button", "aria-label": `Watch ${s.symbol} instead of ${symbol}`, onclick: async () => {
    try {
      clearError();
      const r = await sendJson("/api/watchlists/swap", { old: symbol, new: s.symbol });
      box.replaceChildren(swapSummary(r));
      onSwapped(r);
    } catch (err) { showError(err.message); }
  } }, "Swap");
  box.append(`No price source has had data for this listing since ${deadSince}.${more}`);
  if (!siblings.length) box.append(" The company has no other live listing.");
  else box.append(" Live listings, best first:", ...siblings.map((s) => el("div", null,
    companyLink(s.symbol), ` ${[s.exchange, s.currency].filter(Boolean).join(", ")} `,
    watched ? swap(s) : null)));
  if (siblings.length && watched) box.append(el("div", { class: "dim" }, "Swap changes every watchlist; paper positions stay on the old symbol."));
  return box;
}

export { deadNote, swapSummary };
