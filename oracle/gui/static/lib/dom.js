// --- error banner ---------------------------------------------------------
function showError(message) {
  document.getElementById("error-msg").textContent = message;
  document.getElementById("error-banner").hidden = false;
}
function clearError() {
  document.getElementById("error-banner").hidden = true;
  document.getElementById("error-msg").textContent = "";
}

// --- DOM builder --------------------------------------------------------------
// el(tag, attrs, ...children). attrs: class/className -> class attr, dataset ->
// data-* attrs, on* -> event listeners, anything else -> setAttribute.
// Children: strings become text nodes, nodes appended as-is, nullish skipped.
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null) continue;
      if (key === "class" || key === "className") {
        node.className = value;
      } else if (key === "dataset") {
        for (const [dk, dv] of Object.entries(value)) node.dataset[dk] = dv;
      } else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else {
        node.setAttribute(key, value);
      }
    }
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export { el, showError, clearError };
