// 2D front-elevation planner. The backend owns all geometry: this page edits the interior tree (tree.js),
// posts the config, and draws the layout the backend resolved. Coordinates are in mm, z up.
//
// Workflow: select a section (click), set its shelves / dividers with the counters, then drag its panels or type
// their positions. Clicking inside the selected section selects the child section under the pointer; the selected
// section's own panels are editable, anything nested deeper is greyed out.
import * as T from "./tree.js";

const svg = document.getElementById("plan");
const props = document.getElementById("props");
const messageEl = document.getElementById("message");
const main = document.getElementById("main");

const DOOR_LABELS = { none: "None", left: "Left", right: "Right", double: "Double" };
const FRONT_TEXT = { left: "Door, hinged left", right: "Door, hinged right", double: "Double doors" };

const state = {
  config: null,
  layout: null,
  display: { show_fronts: true, open_fronts: false },
  version: -1,
  selection: [], // path of the selected section, [] = the whole interior
  hover: null, // pointer position in mm
  drag: null, // {group, origin, delta, moved}
  busy: false,
  history: [],
  future: [],
  showFronts: true,
  showCutList: false,
  cutList: [],
  snap: 10,
};

// ============================================================================
// server
// ============================================================================

async function load() {
  const res = await fetch("api/state");
  applyState(await res.json());
}

function applyState(data) {
  state.config = data.config;
  state.layout = data.layout;
  state.version = data.version;
  if (data.display) state.display = data.display;
  validateSelection();
  render();
  renderProps();
  if (state.showCutList) loadCutList();
}

/** Apply a config on the backend. Returns true on success. */
async function post(config, { record = true } = {}) {
  if (state.busy) return false;
  state.busy = true;
  setMessage("Building…");
  try {
    const res = await fetch("api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    const data = await res.json();
    if (!data.ok) {
      setMessage(data.error || "The configuration could not be built.", "error");
      return false;
    }
    if (record) {
      state.history.push(state.config);
      state.future = [];
    }
    setMessage(null);
    applyState(data);
    return true;
  } catch (e) {
    setMessage(`Could not reach the server: ${e.message}`, "error");
    return false;
  } finally {
    state.busy = false;
    updateToolbar();
  }
}

/** Run a tree operation and post the result, reporting PlannerErrors to the user. */
async function edit(operation) {
  let interior;
  try {
    interior = operation();
  } catch (e) {
    if (e instanceof T.PlannerError) {
      setMessage(e.message, "error");
      renderProps();
      return false;
    }
    throw e;
  }
  const ok = await post({ ...state.config, interior });
  if (!ok) renderProps();
  return ok;
}

async function setDisplay(changes) {
  state.display = { ...state.display, ...changes };
  await fetch("api/display", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
}

async function loadCutList() {
  const res = await fetch("api/cutlist");
  state.cutList = (await res.json()).rows;
  renderProps();
}

function setMessage(text, kind = "info") {
  messageEl.textContent = text || "";
  messageEl.className = text ? kind : "";
}

// ============================================================================
// helpers
// ============================================================================

const root = () => state.config.interior;
const regions = () => state.layout.regions;

function pathFromKey(k) {
  return k === "" ? [] : k.split(".").map(Number);
}

function regionOf(path) {
  return regions()[T.key(path)];
}

function isPrefix(prefix, path) {
  return prefix.length <= path.length && prefix.every((v, i) => path[i] === v);
}

function round(value) {
  return Math.round(value * 10) / 10;
}

function esc(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function toModel(evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  return { x: p.x, z: state.config.height - p.y };
}

function pxToMm(px) {
  const m = svg.getScreenCTM();
  return m ? px / m.a : px;
}

/** Deepest tree node whose region contains `p`. */
function regionAt(p) {
  let best = null;
  let depth = -1;
  for (const [k, [x, z, w, h]] of Object.entries(regions())) {
    if (p.x >= x && p.x <= x + w && p.z >= z && p.z <= z + h) {
      const d = k === "" ? 0 : k.split(".").length;
      if (d > depth) {
        depth = d;
        best = pathFromKey(k);
      }
    }
  }
  return best;
}

function validateSelection() {
  let path = state.selection;
  while (path.length && !(T.key(path) in regions())) path = path.slice(0, -1);
  state.selection = path;
}

function sectionName(path) {
  if (path.length === 0) return "Interior";
  const node = T.getNode(root(), path);
  if (T.isDrawer(node)) return "Drawer";
  if (T.isSplit(node)) return node.direction === T.ROWS ? "Shelved section" : "Divided section";
  return "Compartment";
}

/** Clicking selects one level at a time: inside the selection its child, elsewhere the sibling-level section. */
function sectionAt(p) {
  const leaf = regionAt(p);
  if (!leaf) return null;
  const sel = state.selection;
  if (isPrefix(sel, leaf)) return leaf.length > sel.length ? leaf.slice(0, sel.length + 1) : sel;
  let common = 0;
  while (common < sel.length && sel[common] === leaf[common]) common++;
  return leaf.slice(0, Math.min(common + 1, leaf.length));
}

/** Editable panel groups of the selected section, each with the gap rectangles of its members. */
function ownGroups() {
  return T.panelGroups(root(), state.selection).map((group) => ({
    ...group,
    rects: group.members.map(({ split, gap }) => {
      const a = regionOf([...split, gap]);
      const b = regionOf([...split, gap + 1]);
      return group.direction === T.ROWS ? [a[0], a[1] + a[3], a[2], b[1] - (a[1] + a[3])] : [a[0] + a[2], a[1], b[0] - (a[0] + a[2]), a[3]];
    }),
  }));
}

function groupAt(p) {
  const tol = pxToMm(5);
  for (const group of ownGroups()) {
    for (const [x, z, w, h] of group.rects) {
      if (p.x >= x - tol && p.x <= x + w + tol && p.z >= z - tol && p.z <= z + h + tol) return group;
    }
  }
  return null;
}

/** Distance from the start of the selected section to the near face of a group's panel. */
function groupPosition(group) {
  const s = regionOf(state.selection);
  const [x, z] = group.rects[0];
  return group.direction === T.ROWS ? z - s[1] : x - s[0];
}

// ============================================================================
// drawing
// ============================================================================

function render() {
  if (!state.config) return;
  const W = state.config.width;
  const H = state.config.height;
  const t = state.config.thickness;
  const m = Math.max(W, H) * 0.08;
  const fs = Math.max(W, H) / 55;
  svg.setAttribute("viewBox", `${-m} ${-m} ${W + 2 * m} ${H + 2 * m}`);

  const out = [];
  const rect = (x, z, w, h, cls, extra = "") => `<rect x="${x}" y="${H - z - h}" width="${w}" height="${h}" class="${cls}" vector-effect="non-scaling-stroke" ${extra}/>`;
  const text = (x, z, label, cls, size = fs) => `<text x="${x}" y="${H - z}" class="${cls}" font-size="${size}">${esc(label)}</text>`;
  const line = (points, cls, extra = "") =>
    `<polyline points="${points.map(([x, z]) => `${x},${H - z}`).join(" ")}" class="${cls}" vector-effect="non-scaling-stroke" ${extra}/>`;

  out.push(rect(t, t, W - 2 * t, H - 2 * t, "cavity"));
  for (const part of state.layout.parts) {
    if (["side", "top", "bottom"].includes(part.category)) out.push(rect(part.x, part.z, part.w, part.h, "carcass"));
  }

  const sel = state.selection;
  const groups = ownGroups();
  const own = new Set(groups.flatMap((g) => g.members.map((mb) => `${T.key(mb.split)}/${mb.gap}`)));
  const dragged = state.drag ? new Set(state.drag.group.members.map((mb) => `${T.key(mb.split)}/${mb.gap}`)) : new Set();
  const hovered = state.hover && !state.drag ? groupAt(state.hover) : null;
  const hoveredKeys = hovered ? new Set(hovered.members.map((mb) => `${T.key(mb.split)}/${mb.gap}`)) : new Set();

  // child sections of the selection that have their own shelves/dividers are greyed out
  const selNode = T.getNode(root(), sel);
  if (T.isSplit(selNode)) {
    const aligned = groups.some((g) => g.direction === T.ROWS && g.members.length > 1);
    selNode.children.forEach((child, i) => {
      const children = aligned && T.isSplit(child) ? child.children.map((c, j) => [c, [...sel, i, j]]) : [[child, [...sel, i]]];
      for (const [node, path] of children) {
        const r = regionOf(path);
        if (!r) continue;
        out.push(rect(...r, T.isSplit(node) ? "dim-overlay" : "child-outline"));
      }
    });
  }

  // shelves and dividers
  for (const part of state.layout.parts) {
    if (part.category !== "shelf" && part.category !== "divider") continue;
    const id = `${part.attributes.split}/${part.attributes.gap}`;
    let { x, z } = part;
    if (dragged.has(id)) {
      if (part.category === "shelf") z += state.drag.delta;
      else x += state.drag.delta;
    }
    let cls = "panel";
    if (part.y > 0.5) cls += " setback";
    const inside = isPrefix(sel, pathFromKey(part.attributes.split));
    if (dragged.has(id)) cls += " selected-panel";
    else if (hoveredKeys.has(id)) cls += " hover-panel own";
    else if (own.has(id)) cls += " own";
    else if (inside) cls += " locked";
    out.push(rect(x, z, part.w, part.h, cls, 'stroke-width="1.5"'));
  }
  // boundaries between stacked drawers have no panel: show a handle
  for (const group of groups) {
    group.rects.forEach(([x, z, w, h]) => {
      if (Math.min(w, h) > 0.5) return;
      const offset = dragged.size && state.drag.group === group ? state.drag.delta : 0;
      const pts = group.direction === T.ROWS ? [[x, z + offset], [x + w, z + offset]] : [[x + offset, z], [x + offset, z + h]];
      out.push(line(pts, "gap-handle", 'stroke-width="3"'));
    });
  }

  // fronts
  if (state.showFronts) {
    for (const part of state.layout.parts) {
      if (part.category === "door") {
        out.push(rect(part.x, part.z, part.w, part.h, "door"));
        const hinge = part.attributes.hinge_side === "right" ? part.x + part.w : part.x;
        const free = part.attributes.hinge_side === "right" ? part.x : part.x + part.w;
        out.push(line([[free, part.z + part.h], [hinge, part.z + part.h / 2], [free, part.z]], "swing"));
      } else if (part.category === "drawer_front") {
        out.push(rect(part.x, part.z, part.w, part.h, "drawer-front"));
        const cx = part.x + part.w / 2;
        const hz = part.z + part.h - Math.min(part.h * 0.25, 60);
        out.push(line([[cx - Math.min(part.w * 0.15, 80), hz], [cx + Math.min(part.w * 0.15, 80), hz]], "handle", 'stroke-width="3"'));
      }
    }
  }

  // front labels
  const walk = (node, path) => {
    const r = regionOf(path);
    if (r && (T.hasDoor(node) || T.isDrawer(node))) {
      out.push(text(r[0] + r[2] / 2, r[1] + r[3] - fs * 1.1, T.isDrawer(node) ? "Drawer" : FRONT_TEXT[T.doorOf(node)], "label", fs * 0.8));
    }
    if (T.isSplit(node)) node.children.forEach((c, i) => walk(c, [...path, i]));
  };
  walk(root(), []);

  // selection
  const r = regionOf(sel);
  if (r) out.push(rect(...r, "selected", 'stroke-width="3"'));

  // drag labels: the sizes on both sides of the first dragged panel
  if (state.drag && state.drag.moved) {
    const { split, gap } = state.drag.group.members[0];
    const rows = state.drag.group.direction === T.ROWS;
    for (const [i, sign] of [[gap, 1], [gap + 1, -1]]) {
      const c = regionOf([...split, i]);
      const size = (rows ? c[3] : c[2]) + sign * state.drag.delta;
      const cz = rows ? (sign > 0 ? c[1] + size / 2 : c[1] + c[3] - size / 2) : c[1] + c[3] / 2;
      const cx = rows ? c[0] + c[2] / 2 : sign > 0 ? c[0] + size / 2 : c[0] + c[2] - size / 2;
      out.push(text(cx, cz, `${round(size)}`, "preview-label"));
    }
  }

  // overall dimensions
  out.push(`<text x="${W / 2}" y="${H + m * 0.55}" class="dim" font-size="${fs}">${round(W)}</text>`);
  out.push(`<text transform="translate(${-m * 0.45} ${H / 2}) rotate(-90)" class="dim" font-size="${fs}">${round(H)}</text>`);

  svg.innerHTML = out.join("");
  svg.style.cursor = hovered ? (hovered.direction === T.ROWS ? "row-resize" : "col-resize") : state.drag ? "grabbing" : "default";
}

// ============================================================================
// sidebar
// ============================================================================

function numberRow(label, value, attrs) {
  return `<label class="row">${esc(label)} <input type="number" value="${value === null || value === undefined ? "" : round(value)}" step="1" ${attrs} /></label>`;
}

function counter(label, value, action, disabled) {
  const dis = disabled ? "disabled" : "";
  return `<div class="row ${disabled ? "disabled" : ""}">${label}<span class="counter">
    <button data-action="${action}" data-step="-1" ${dis} aria-label="Fewer ${label.toLowerCase()}">−</button>
    <input type="number" min="0" max="30" step="1" data-action="${action}" value="${value === null ? "" : value}" placeholder="mixed" ${dis} />
    <button data-action="${action}" data-step="1" ${dis} aria-label="More ${label.toLowerCase()}">+</button>
  </span></div>`;
}

function renderProps() {
  if (!state.config) return;
  const c = state.config;
  const html = [];

  html.push(`<div class="group"><h2>Cabinet</h2>
    ${numberRow("Width", c.width, 'data-param="width" min="100"')}
    ${numberRow("Height", c.height, 'data-param="height" min="100"')}
    ${numberRow("Depth", c.depth, 'data-param="depth" min="100"')}
    ${numberRow("Panel thickness", c.thickness, 'data-param="thickness" min="5"')}
    ${numberRow("Back thickness", c.back_thickness, 'data-param="back_thickness" min="1"')}
    <label class="row">Back joint <select data-param="back_joint">
      <option value="rabbet" ${c.back_joint === "rabbet" ? "selected" : ""}>rabbet</option>
      <option value="butt" ${c.back_joint === "butt" ? "selected" : ""}>butt</option>
    </select></label>
    ${c.back_joint === "rabbet" ? numberRow("Rabbet depth", c.rabbet_depth, `data-param="rabbet_depth" data-nullable min="0" placeholder="${round(c.thickness / 2)}" title="Empty: half the side thickness"`) : ""}
    ${numberRow("Front thickness", c.front_thickness, 'data-param="front_thickness" min="5"')}
    ${numberRow("Front gap", c.front_gap, 'data-param="front_gap" min="0"')}
    <div class="hint">Top and bottom join the sides with Clamex P-14 connectors, including a center connector.
      Each shelf and divider picks its own joint below.</div>
    ${numberRow("Connector distance", c.clamex_distance, 'data-param="clamex_distance" min="0"')}
    ${numberRow("Tenon depth", c.tenon_depth, `data-param="tenon_depth" data-nullable min="0" placeholder="${round(c.thickness / 4)}" title="Empty: a quarter of the receiving panel's thickness"`)}
    ${numberRow("Tenons per edge", c.tenon_count, 'data-param="tenon_count" min="1" max="10"')}
    ${numberRow("Dado depth", c.dado_depth, `data-param="dado_depth" data-nullable min="0" placeholder="${round(c.thickness / 3)}" title="Empty: a third of the grooved panel's thickness"`)}
    ${numberRow("Dado stop from front", c.dado_stop, 'data-param="dado_stop" min="0"')}
    <label class="row">Fronts <select data-param="door_mount">
      <option value="overlay" ${c.door_mount === "overlay" ? "selected" : ""}>overlay</option>
      <option value="inset" ${c.door_mount === "inset" ? "selected" : ""}>inset</option>
    </select></label>
    <label class="row">Shelves between drawers <input type="checkbox" data-param="shelves_between_drawers" ${c.shelves_between_drawers ? "checked" : ""} /></label>
    <label class="row">One-piece drawer fronts <input type="checkbox" data-param="drawer_front_one_piece" ${c.drawer_front_one_piece ? "checked" : ""} /></label>
  </div>`);

  html.push(sectionProps(state.selection));

  html.push(`<div class="group"><h2>View</h2>
    <label class="row">Fronts in plan <input type="checkbox" data-view="plan-fronts" ${state.showFronts ? "checked" : ""} /></label>
    <label class="row">Fronts in 3D <input type="checkbox" data-view="show_fronts" ${state.display.show_fronts ? "checked" : ""} /></label>
    <label class="row">Open fronts in 3D <input type="checkbox" data-view="open_fronts" ${state.display.open_fronts ? "checked" : ""} /></label>
  </div>`);

  if (state.showCutList) {
    const rows = state.cutList.map((r) => `<tr><td>${esc(r[1])}</td><td>${round(r[2])}</td><td>${round(r[3])}</td><td>${round(r[4])}</td></tr>`);
    html.push(`<div class="group"><h2>Cut list · ${state.cutList.length} parts</h2>
      <table class="cutlist"><thead><tr><th>Part</th><th>L</th><th>W</th><th>T</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`);
  }

  props.innerHTML = html.join("");
}

function sectionProps(path) {
  const c = state.config;
  const node = T.getNode(root(), path);
  const r = regionOf(path);
  const crumbs = [];
  for (let n = 0; n <= path.length; n++) {
    const p = path.slice(0, n);
    crumbs.push(`<button data-action="select" data-path="${T.key(p)}" ${n === path.length ? "disabled" : ""}>${esc(sectionName(p))}</button>`);
  }
  const html = [`<div class="group"><h2>${esc(sectionName(path))}</h2><div class="crumbs">${crumbs.join("›")}</div>
    <div class="hint">${round(r[2])} × ${round(r[3])} mm clear</div>`];

  if (path.length) {
    const parent = T.getNode(root(), path.slice(0, -1));
    const rows = parent.direction === T.ROWS;
    html.push(`${numberRow(rows ? "Height" : "Width", rows ? r[3] : r[2], 'min="1" data-action="size"')}
      <label class="row">Flexible (takes the remaining space) <input type="checkbox" data-action="flex" ${node.size === null ? "checked" : ""} /></label>`);
  }

  html.push(counter("Shelves", T.shelvesOf(node), "shelves", false));
  html.push(counter("Dividers", T.dividersOf(node), "dividers", false));

  const groups = ownGroups();
  if (groups.length) {
    const counts = { [T.ROWS]: 0, [T.COLUMNS]: 0 };
    html.push(`<div class="hint">Panels: position from the bottom (shelves) or left (dividers) of this section, and joint.</div>`);
    groups.forEach((group, i) => {
      const n = ++counts[group.direction];
      const label = group.direction === T.ROWS ? `Shelf ${n}` : `Divider ${n}`;
      // stacked drawers have no panel between them, so no joint either
      const hasPanel = group.rects.some(([, , w, h]) => Math.min(w, h) > 0.5);
      const joint = T.panelJointOf(root(), group, c.interior_joint);
      const options = Object.entries(T.JOINTS).map(([value, text]) => `<option value="${value}" ${value === joint ? "selected" : ""}>${text}</option>`);
      html.push(`<div class="panel-row"><span>${label}</span>
        <input type="number" value="${round(groupPosition(group))}" min="0" step="1" data-action="position" data-group="${i}" aria-label="${label} position" />
        ${hasPanel ? `<select data-action="joint" data-group="${i}" aria-label="${label} joint">${options.join("")}</select>` : "<span></span>"}
      </div>`);
    });
  }

  const covered = T.coveredBy(root(), path);
  const door = T.doorOf(node);
  if (covered) {
    html.push(`<div class="hint">Behind the door of <button class="linklike" data-action="select" data-path="${T.key(covered)}">${esc(sectionName(covered))}</button>.</div>`);
  }
  const doors = T.DOORS.map((d) => `<button data-action="door" data-door="${d}" class="${d === door ? "active" : ""}" ${covered ? "disabled" : ""}>${DOOR_LABELS[d]}</button>`);
  html.push(`<div class="${covered ? "disabled" : ""}">Door</div><div class="segmented">${doors.join("")}</div>`);
  const drawerDisabled = covered || T.isSplit(node);
  html.push(`<label class="row ${drawerDisabled ? "disabled" : ""}">Drawer <input type="checkbox" data-action="drawer" ${T.isDrawer(node) ? "checked" : ""} ${drawerDisabled ? "disabled" : ""} /></label>`);
  if (T.isSplit(node) && !covered) html.push(`<div class="hint">Only a section without shelves or dividers can be a drawer.</div>`);
  html.push("</div>");
  return html.join("");
}

function select(path) {
  state.selection = path;
  render();
  renderProps();
}

props.addEventListener("click", async (evt) => {
  const button = evt.target.closest("button[data-action]");
  if (!button || state.busy) return;
  const { action } = button.dataset;
  const path = state.selection;
  if (action === "select") select(pathFromKey(button.dataset.path));
  else if (action === "door") await edit(() => T.setDoor(root(), path, button.dataset.door));
  else if (action === "shelves" || action === "dividers") {
    const node = T.getNode(root(), path);
    const current = action === "shelves" ? (T.shelvesOf(node) ?? 0) : T.dividersOf(node);
    const n = Math.max(0, current + Number(button.dataset.step));
    await edit(() => (action === "shelves" ? T.setShelves(root(), path, n) : T.setDividers(root(), path, n)));
  }
});

props.addEventListener("change", async (evt) => {
  const el = evt.target;
  if (el.dataset.view) {
    if (el.dataset.view === "plan-fronts") {
      state.showFronts = el.checked;
      render();
    } else await setDisplay({ [el.dataset.view]: el.checked });
    return;
  }
  if (state.busy) return;
  if (el.dataset.param) {
    let value = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : Number(el.value);
    if ("nullable" in el.dataset && el.value === "") value = null;
    if (!(await post({ ...state.config, [el.dataset.param]: value }))) renderProps();
    return;
  }
  const path = state.selection;
  const { action } = el.dataset;
  if (action === "shelves") await edit(() => T.setShelves(root(), path, Number(el.value)));
  else if (action === "dividers") await edit(() => T.setDividers(root(), path, Number(el.value)));
  else if (action === "drawer") await edit(() => T.setDrawer(root(), path, el.checked));
  else if (action === "joint") await edit(() => T.setPanelJoint(root(), ownGroups()[Number(el.dataset.group)].members, el.value));
  else if (action === "position") {
    const group = ownGroups()[Number(el.dataset.group)];
    const delta = Number(el.value) - groupPosition(group);
    await edit(() => T.movePanels(root(), regions(), group.members, delta));
  } else if (action === "size") await edit(() => T.setSize(root(), regions(), path, Number(el.value)));
  else if (action === "flex") {
    const r = regionOf(path);
    const rows = T.getNode(root(), path.slice(0, -1)).direction === T.ROWS;
    await edit(() => T.setSize(root(), regions(), path, el.checked ? null : rows ? r[3] : r[2]));
  }
});

// ============================================================================
// pointer and keyboard
// ============================================================================

let frame = null;
function scheduleRender() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = null;
    render();
  });
}

svg.addEventListener("pointermove", (evt) => {
  if (!state.config) return;
  const p = toModel(evt);
  state.hover = p;
  const drag = state.drag;
  if (drag) {
    const axis = drag.group.direction === T.ROWS ? "z" : "x";
    const raw = p[axis] - drag.origin[axis];
    drag.delta = Math.round(raw / state.snap) * state.snap;
    drag.moved = drag.moved || Math.abs(raw) > pxToMm(3);
  }
  scheduleRender();
});

svg.addEventListener("pointerleave", () => {
  state.hover = null;
  scheduleRender();
});

svg.addEventListener("pointerdown", (evt) => {
  if (!state.config || state.busy || evt.button !== 0) return;
  const p = toModel(evt);
  const group = groupAt(p);
  if (group) {
    svg.setPointerCapture(evt.pointerId);
    state.drag = { group, origin: p, delta: 0, moved: false };
    render();
    return;
  }
  const path = sectionAt(p);
  if (path) select(path);
});

svg.addEventListener("pointerup", async () => {
  const drag = state.drag;
  if (!drag) return;
  state.drag = null;
  if (drag.moved && drag.delta !== 0) await edit(() => T.movePanels(root(), regions(), drag.group.members, drag.delta));
  render();
});

function updateToolbar() {
  document.getElementById("undo").disabled = !state.history.length || state.busy;
  document.getElementById("redo").disabled = !state.future.length || state.busy;
  document.getElementById("cutlist-toggle").classList.toggle("active", state.showCutList);
}

async function undo() {
  if (!state.history.length || state.busy) return;
  const previous = state.history.pop();
  const current = state.config;
  if (await post(previous, { record: false })) state.future.push(current);
  else state.history.push(previous);
  updateToolbar();
}

async function redo() {
  if (!state.future.length || state.busy) return;
  const next = state.future.pop();
  const current = state.config;
  if (await post(next, { record: false })) state.history.push(current);
  else state.future.push(next);
  updateToolbar();
}

document.addEventListener("keydown", (evt) => {
  if (evt.target instanceof Element && evt.target.closest("input, select, textarea")) return;
  const mod = evt.metaKey || evt.ctrlKey;
  if (mod && evt.key.toLowerCase() === "z") {
    evt.preventDefault();
    if (evt.shiftKey) redo();
    else undo();
  } else if (evt.key === "Escape") {
    state.drag = null;
    select(state.selection.slice(0, -1));
  }
});

document.getElementById("undo").addEventListener("click", undo);
document.getElementById("redo").addEventListener("click", redo);
document.getElementById("snap").addEventListener("change", (evt) => (state.snap = Number(evt.target.value)));
document.getElementById("cutlist-toggle").addEventListener("click", () => {
  state.showCutList = !state.showCutList;
  updateToolbar();
  if (state.showCutList) loadCutList();
  else renderProps();
});
document.getElementById("toggle-3d").addEventListener("click", () => {
  main.classList.toggle("no-3d");
  render();
});
document.getElementById("download").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state.config, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "cabinet.json";
  a.click();
  URL.revokeObjectURL(a.href);
});
document.getElementById("upload").addEventListener("change", async (evt) => {
  const file = evt.target.files[0];
  evt.target.value = "";
  if (!file) return;
  try {
    await post(JSON.parse(await file.text()));
  } catch (e) {
    setMessage(`Could not read ${file.name}: ${e.message}`, "error");
  }
});

// ============================================================================
// start
// ============================================================================

document.getElementById("view3d").src = `/?ws_host=${location.hostname}&ws_port=${location.port || 80}`;
new ResizeObserver(() => render()).observe(svg);
updateToolbar();
load();

// pick up changes made elsewhere (another planner tab)
setInterval(async () => {
  if (state.busy || state.drag) return;
  try {
    const res = await fetch("api/version");
    const { version } = await res.json();
    if (version !== state.version) load();
  } catch {
    // server stopped; keep the last state on screen
  }
}, 1500);
