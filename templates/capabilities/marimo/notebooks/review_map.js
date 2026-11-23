// The pipeline map on the review notebook's overview. It only draws: what the nodes are, where
// they sit and what color they get all come from `review_map` in astack's runtime.
const SVG = "http://www.w3.org/2000/svg";
const [W, H, GAP_X, GAP_Y, PAD, PILL_W] = [112, 46, 28, 14, 12, 168];
const [TITLE_SIZE, SUB_SIZE, MIN_TITLE_PX] = [13, 11, 11];
const FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif";

// Fit the width, but never shrink a title below a readable size. Past that, the map scrolls.
export function fitScale(available, width) {
  return Math.min(1, Math.max(MIN_TITLE_PX / TITLE_SIZE, available / width));
}

// Shorten by measured width, so wide letters cannot spill out of a box.
function fitText(text, room, font) {
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return text.length > 16 ? `${text.slice(0, 15)}…` : text;
  context.font = font;
  if (context.measureText(text).width <= room) return text;
  let kept = text.length;
  while (kept > 1 && context.measureText(`${text.slice(0, kept)}…`).width > room) kept -= 1;
  return `${text.slice(0, kept)}…`;
}

function reach(id, next) {
  const seen = new Set();
  const stack = [id];
  while (stack.length) {
    for (const item of next[stack.pop()] || []) {
      if (!seen.has(item)) {
        seen.add(item);
        stack.push(item);
      }
    }
  }
  return seen;
}

function make(tag, attributes, parent) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function render({ model, el }) {
  const nodes = model.get("nodes");
  const edges = model.get("edges");
  const colors = model.get("colors");
  if (!nodes.length) return;
  // Shared code wraps within the width of the steps, so it never makes the map wider.
  const columns = Math.max(1, ...nodes.filter((n) => n.y >= 0).map((n) => n.x + 1));
  const stepsWidth = columns * W + (columns - 1) * GAP_X;
  const perRow = Math.max(1, Math.floor((stepsWidth + GAP_X) / (PILL_W + GAP_X)));
  const pills = nodes.filter((n) => n.y < 0).length;
  const top = pills ? Math.ceil(pills / perRow) * (H + GAP_Y) + 6 : 0;
  const pos = Object.fromEntries(
    nodes.map((n) => [
      n.id,
      n.y < 0
        ? {
            x: PAD + (n.x % perRow) * (PILL_W + GAP_X),
            y: PAD + Math.floor(n.x / perRow) * (H + GAP_Y),
            w: PILL_W,
          }
        : { x: PAD + n.x * (W + GAP_X), y: PAD + top + n.y * (H + GAP_Y), w: W },
    ]),
  );
  const width = Math.max(...nodes.map((n) => pos[n.id].x + pos[n.id].w)) + PAD;
  const height = Math.max(...nodes.map((n) => pos[n.id].y)) + H + PAD;
  const svg = make("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "group",
    "font-family": FONT,
  });
  svg.style.display = "block";
  const defs = make("defs", {}, svg);
  const marker = make(
    "marker",
    { id: "astack-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto" },
    defs,
  );
  make("path", { d: "M0,0L10,5L0,10z", fill: "#999" }, marker);

  // No prototype, so a step named `constructor` or `__proto__` is just a name.
  const ups = Object.create(null);
  const downs = Object.create(null);
  for (const e of edges) {
    (downs[e.from] ||= []).push(e.to);
    (ups[e.to] ||= []).push(e.from);
  }
  const lines = edges.map((e) => {
    const [a, b] = [pos[e.from], pos[e.to]];
    const [x1, y1, x2, y2] = [a.x + a.w, a.y + H / 2, b.x, b.y + H / 2];
    const mid = (x1 + x2) / 2;
    const path = make(
      "path",
      { d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`, fill: "none", stroke: "#999", "marker-end": "url(#astack-arrow)" },
      svg,
    );
    return { path, e };
  });

  const boxes = Object.create(null);
  function highlight(id) {
    const up = id ? reach(id, ups) : new Set();
    const down = id ? reach(id, downs) : new Set();
    const chain = new Set([id, ...up, ...down]);
    for (const { path, e } of lines) {
      const direct = e.from === id || e.to === id;
      const near =
        ((up.has(e.from) || e.from === id) && (up.has(e.to) || e.to === id)) ||
        ((down.has(e.from) || e.from === id) && (down.has(e.to) || e.to === id));
      path.style.opacity = !id || direct ? 1 : near ? 0.8 : 0.12;
      path.setAttribute("stroke", id && direct ? "currentColor" : "#999");
      path.setAttribute("stroke-width", id && direct ? 2.5 : id && near ? 1.5 : 1);
    }
    for (const [key, { group, rect }] of Object.entries(boxes)) {
      group.style.opacity = !id || chain.has(key) || key.startsWith("shared:") ? 1 : 0.35;
      if (key === id) rect.setAttribute("stroke", "currentColor");
      else rect.removeAttribute("stroke");
    }
  }

  for (const n of nodes) {
    const { x, y, w } = pos[n.id];
    const group = make("g", { tabindex: 0, role: "button", "aria-label": `${n.label}: ${n.status}` }, svg);
    group.style.cursor = "pointer";
    const rect = make("rect", { x, y, width: w, height: H, rx: n.shape === "pill" ? H / 2 : 6, fill: colors[n.status] || "#555" }, group);
    const title = make("text", { x: x + w / 2, y: y + 19, "text-anchor": "middle", fill: "#fff", "font-size": TITLE_SIZE, "font-weight": 600 }, group);
    title.textContent = fitText(n.label, w - 10, `600 ${TITLE_SIZE}px ${FONT}`);
    const sub = make("text", { x: x + w / 2, y: y + 35, "text-anchor": "middle", fill: "#fff", "font-size": SUB_SIZE }, group);
    sub.textContent = fitText(n.sub ? `${n.status} · ${n.sub}` : n.status, w - 10, `${SUB_SIZE}px ${FONT}`);
    make("title", {}, group).textContent = n.label;
    const pick = () => {
      model.set("picked", n.id);
      model.save_changes();
    };
    group.addEventListener("mouseenter", () => highlight(n.id));
    group.addEventListener("mouseleave", () => highlight(null));
    group.addEventListener("focus", () => highlight(n.id));
    group.addEventListener("blur", () => highlight(null));
    group.addEventListener("click", pick);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        pick();
      }
    });
    boxes[n.id] = { group, rect };
  }
  el.style.display = "block";
  el.style.width = "100%";
  el.style.maxHeight = "min(45vh, 28rem)";
  el.style.overflow = "auto";
  el.tabIndex = 0;
  el.setAttribute("role", "region");
  el.setAttribute("aria-label", "Pipeline map");
  el.appendChild(svg);
  const fit = () => {
    const scale = fitScale(el.clientWidth || width, width);
    svg.setAttribute("width", Math.round(width * scale));
    svg.setAttribute("height", Math.round(height * scale));
  };
  fit();
  const observer = new ResizeObserver(fit);
  observer.observe(el);
  return () => observer.disconnect();
}

export default { render };
