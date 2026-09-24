// Repaints content already on screen after an action, so the page does not jump.
// The caller fetches first and calls this with the finished nodes: the old content stays up
// until then. The box keeps its old height until `after` (drawing charts, loading parts that
// fill in later) settles, the scroll goes back where it was, and focus returns to the control
// with the same id or label.
function repaint(box, nodes, after = () => {}) {
  const { scrollX, scrollY } = window;
  const focused = focusKey(box);
  box.style.minHeight = `${box.offsetHeight}px`;
  box.replaceChildren(...nodes);
  const settled = after();
  window.scrollTo(scrollX, scrollY);
  if (focused) refocus(box, focused);
  Promise.resolve(settled).catch(() => {}).finally(() => { box.style.minHeight = ""; });
}

function focusKey(box) {
  const node = document.activeElement;
  if (!node || node === box || !box.contains(node)) return null;
  const key = { tag: node.tagName, id: node.id || null, label: node.getAttribute("aria-label") };
  return key.id || key.label ? key : null;
}

function refocus(box, key) {
  const match = [...box.querySelectorAll("[id], [aria-label]")].find((n) => n.tagName === key.tag
    && (key.id ? n.id === key.id : n.getAttribute("aria-label") === key.label));
  match?.focus({ preventScroll: true });
}

export { repaint };
