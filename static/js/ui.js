// Small DOM helpers. Text from the API is always inserted with textContent, never as HTML.

/** Create an element: el("button", {class: "link", onclick: fn}, "Text", childNode, ...) */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/** Show a loading / empty / error line in a state element. kind: "loading" | "empty" | "error" | null (hide). */
export function setState(node, kind, text, action) {
  clear(node);
  node.hidden = !kind;
  if (!kind) return;
  node.className = `state${kind === "error" ? " error" : ""}`;
  if (kind === "loading") node.append(el("span", { class: "spinner", "aria-hidden": "true" }));
  node.append(text);
  if (action) node.append(" ", el("button", { type: "button", class: "link", onclick: action.run }, action.label));
}

const number = new Intl.NumberFormat("en");
const oneDecimal = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const fmt = {
  int: (value) => number.format(Math.round(value)),
  km: (value) => `${value >= 100 ? number.format(Math.round(value)) : oneDecimal.format(value)} km`,
  metres: (value) => (value >= 1000 ? `${oneDecimal.format(value / 1000)} km` : `${number.format(Math.round(value))} m`),
  score: (value) => Number(value).toFixed(2),
  percent: (value) => `${oneDecimal.format(value)} %`,
  date: (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }),
};

/** "motorway_link" -> "Motorway link" */
export function titleCase(osmValue) {
  const text = String(osmValue).replaceAll("_", " ").replaceAll(":", ": ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
