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
  if (data.joint_defaults) state.jointDefaults = data.joint_defaults;
  if ("stale" in data) state.stale = data.stale;
  if (data.hinge_defaults) state.hingeDefaults = data.hinge_defaults;
  validateSelection();
  render();
  renderProps();
  updateToolbar();
  if (state.showCutList) loadCutList();
}

/** Apply a config on the backend. Returns true on success. */
async function post(config, { record = true } = {}) {
  if (state.busy) return false;
  state.busy = true;
  setMessage("Updating…");
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
  const wasStale = state.stale;
  if (wasStale) setMessage("Generating 3D for the cut list…");
  const res = await fetch("api/cutlist");
  state.cutList = (await res.json()).rows;
  if (wasStale) {
    setMessage(null);
    await load();
  } else renderProps();
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

/** Panel groups of the section at `path`, each with the gap rectangles of its members. */
function groupsFor(path) {
  return T.panelGroups(root(), path).map((group) => ({
    ...group,
    rects: group.members.map(({ split, gap }) => {
      const a = regionOf([...split, gap]);
      const b = regionOf([...split, gap + 1]);
      return group.direction === T.ROWS ? [a[0], a[1] + a[3], a[2], b[1] - (a[1] + a[3])] : [a[0] + a[2], a[1], b[0] - (a[0] + a[2]), a[3]];
    }),
  }));
}

/** Editable panel groups of the selected section. */
function ownGroups() {
  return groupsFor(state.selection);
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

/** Distance from the start of the section at `path` to the near face of a group's panel. */
function groupPosition(group, path = state.selection) {
  const s = regionOf(path);
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

function counter(label, value, action, attrs = "") {
  return `<div class="row">${label}<span class="counter">
    <button data-action="${action}" data-step="-1" ${attrs} aria-label="Fewer ${label.toLowerCase()}">−</button>
    <input type="number" min="0" max="30" step="1" data-action="${action}" ${attrs} value="${value === null ? "" : value}" placeholder="mixed" />
    <button data-action="${action}" data-step="1" ${attrs} aria-label="More ${label.toLowerCase()}">+</button>
  </span></div>`;
}

function group(title, body) {
  return `<div class="group"><h2>${title}</h2>${body}</div>`;
}

/** "Section 1.2" for path [0, 1]. */
function sectionLabel(path) {
  return path.length ? `Section ${path.map((i) => i + 1).join(".")}` : "Carcass";
}

/** A short description of a section: its front and how it's divided. */
function sectionSummary(node) {
  const parts = [];
  if (T.isDrawer(node)) parts.push("drawer");
  else if (T.hasDoor(node)) parts.push({ left: "door L", right: "door R", double: "double door" }[T.doorOf(node)]);
  const shelves = T.shelvesOf(node);
  const dividers = T.dividersOf(node);
  if (dividers) parts.push(`${dividers} divider${dividers > 1 ? "s" : ""}`);
  if (shelves) parts.push(`${shelves} shel${shelves > 1 ? "ves" : "f"}`);
  return parts.join(", ");
}

/** The hierarchy bar: buttons for every level down to the current compartment. */
function renderCrumbs() {
  const crumbs = document.getElementById("crumbs");
  const path = state.selection;
  const buttons = [`<button data-crumb="up" ${path.length ? "" : "disabled"} title="Go to the parent (Esc)">↑ Up</button>`];
  for (let n = 0; n <= path.length; n++) {
    const p = path.slice(0, n);
    const summary = sectionSummary(T.getNode(root(), p));
    if (n) buttons.push('<span class="sep">›</span>');
    buttons.push(`<button data-crumb="${T.key(p)}" class="${n === path.length ? "current" : ""}" title="${esc(summary)}">${sectionLabel(p)}</button>`);
  }
  const node = T.getNode(root(), path);
  const hint = T.isSplit(node) ? "Click a compartment in the plan to open it." : "";
  crumbs.innerHTML = buttons.join("") + `<span class="hint">${hint}</span>`;
}

function renderProps() {
  if (!state.config) return;
  renderCrumbs();
  const c = state.config;
  const path = state.selection;
  const html = [];

  if (path.length === 0) {
    html.push(
      group(
        "Cabinet",
        `${numberRow("Width", c.width, 'data-param="width" min="100"')}
        ${numberRow("Height", c.height, 'data-param="height" min="100"')}
        ${numberRow("Depth", c.depth, 'data-param="depth" min="100"')}
        ${numberRow("Panel thickness", c.thickness, 'data-param="thickness" min="5"')}
        ${numberRow("Back thickness", c.back_thickness, 'data-param="back_thickness" min="1"')}
        ${numberRow("Front thickness", c.front_thickness, 'data-param="front_thickness" min="5"')}
        ${numberRow("Front gap", c.front_gap, 'data-param="front_gap" min="0"')}
        <label class="row">Fronts <select data-param="door_mount">
          <option value="overlay" ${c.door_mount === "overlay" ? "selected" : ""}>overlay</option>
          <option value="inset" ${c.door_mount === "inset" ? "selected" : ""}>inset</option>
        </select></label>`,
      ),
    );
    html.push(
      group(
        "Carcass joints",
        `<div class="subhead">Back in the sides: rabbet</div>
        ${numberRow("Depth", c.rabbet_depth, `data-param="rabbet_depth" data-nullable min="0" placeholder="${round(c.thickness / 2)}" title="Empty: half the side thickness"`)}
        <div class="subhead">Top and bottom on the sides: Clamex P-14</div>
        ${numberRow("Connector distance", c.clamex_distance, 'data-param="clamex_distance" min="0"')}
        <label class="row">Center connector <input type="checkbox" data-param="clamex_center" ${c.clamex_center ? "checked" : ""} /></label>`,
      ),
    );
  } else {
    const summary = sectionSummary(T.getNode(root(), path));
    const covered = T.coveredBy(root(), path);
    const behind = covered
      ? `<div class="hint">Behind the door of <button class="linklike" data-action="select" data-path="${T.key(covered)}">${esc(sectionLabel(covered))}</button>.</div>`
      : "";
    html.push(`<h3>${sectionLabel(path)}</h3>${summary ? `<div class="hint">${esc(summary)}</div>` : ""}${behind}`);
  }
  html.push(...sectionGroups(path));

  if (state.showCutList) {
    const rows = state.cutList.map((r) => `<tr><td>${esc(r[1])}</td><td>${round(r[2])}</td><td>${round(r[3])}</td><td>${round(r[4])}</td></tr>`);
    html.push(group(`Cut list · ${state.cutList.length} parts`, `<table class="cutlist"><thead><tr><th>Part</th><th>L</th><th>W</th><th>T</th></tr></thead><tbody>${rows.join("")}</tbody></table>`));
  }

  const scroll = props.scrollTop;
  props.innerHTML = html.join("");
  props.scrollTop = scroll;
}

/** The parameter groups of the section at `path`: dimensions, front (with its hinges or drawer), shelves and dividers. */
const TYPE_LABELS = { open: "Open", door: "Door", drawers: "Drawers" };
const DOOR_STYLES = { left: "Left", right: "Right", double: "Double" };

/** The parameter groups of the section at `path`: dimensions, type, then the controls of that type. */
function sectionGroups(path) {
  const c = state.config;
  const node = T.getNode(root(), path);
  const r = regionOf(path);
  const covered = T.coveredBy(root(), path);
  const groups = [];

  let dims = `<div class="hint">${round(r[2])} × ${round(r[3])} mm clear</div>`;
  if (path.length) {
    const parent = T.getNode(root(), path.slice(0, -1));
    const rows = parent.direction === T.ROWS;
    dims += `${numberRow(rows ? "Height" : "Width", rows ? r[3] : r[2], 'min="1" data-action="size"')}
      <label class="row">Automatic (shares the space evenly) <input type="checkbox" data-action="flex" ${node.size === null ? "checked" : ""} /></label>`;
  }
  groups.push(group(path.length ? "Dimensions" : "Interior", dims));

  // behind a door a section is always open; elsewhere choose what it is
  const type = T.typeOf(node);
  if (!covered) {
    const buttons = T.TYPES.map((t) => `<button data-action="type" data-type="${t}" class="${t === type ? "active" : ""}">${TYPE_LABELS[t]}</button>`);
    groups.push(group("Type", `<div class="segmented">${buttons.join("")}</div>`));
  }

  if (type === "door") {
    const door = T.doorOf(node);
    const styles = Object.entries(DOOR_STYLES).map(([d, label]) => `<button data-action="door" data-door="${d}" class="${d === door ? "active" : ""}">${label}</button>`);
    groups.push(group("Door", `<div class="segmented">${styles.join("")}</div>${hingeMenu(node)}`));
  }

  if (type === "drawers") {
    const drawers = T.drawersOf(node);
    let body = counter("Drawers", drawers, "drawers") + counter("Dividers", T.dividersOf(node), "dividers");
    if (drawers === null || drawers > 1) {
      const stack = T.isSplit(node) && node.direction === T.COLUMNS ? node.children.find(T.isSplit) : node;
      const on = (stack && stack.drawer_shelves) ?? c.shelves_between_drawers;
      body += `<label class="row">Shelves between drawers <input type="checkbox" data-action="drawer-shelves" ${on ? "checked" : ""} /></label>`;
    }
    body += collapsible("drawer-settings", "Drawer settings" + changed(Object.keys(T.firstDrawer(node).drawer || {}).length), drawerFields(T.firstDrawer(node)), 1);
    body += panelRows(path);
    groups.push(group("Drawers", body));
  } else {
    groups.push(group(type === "door" ? "Shelves & dividers behind the door" : "Shelves & dividers", counter("Shelves", T.shelvesOf(node), "shelves") + counter("Dividers", T.dividersOf(node), "dividers") + panelRows(path)));
  }
  return groups;
}

/** Positions (and joints, where there's a panel) of the section's shelves, dividers and drawer boundaries. */
function panelRows(path) {
  const c = state.config;
  const panelGroups = groupsFor(path);
  if (!panelGroups.length) return "";
  const counts = { [T.ROWS]: 0, [T.COLUMNS]: 0 };
  let html = `<div class="hint">Position from the bottom (shelves) or left (dividers) of this section, and joint.</div>`;
  panelGroups.forEach((g, i) => {
    const n = ++counts[g.direction];
    // stacked drawers have no panel between them, so no joint either
    const hasPanel = g.rects.some(([, , w, h]) => Math.min(w, h) > 0.5);
    const label = g.direction === T.ROWS ? (hasPanel ? `Shelf ${n}` : `Drawer ${n} top`) : `Divider ${n}`;
    const joint = T.panelJointOf(root(), g, c.interior_joint);
    const options = Object.entries(T.JOINTS).map(([value, text]) => `<option value="${value}" ${value === joint.type ? "selected" : ""}>${text}</option>`);
    html += `<div class="panel-row"><span>${label}</span>
      <input type="number" value="${round(groupPosition(g, path))}" min="0" step="1" data-action="position" data-group="${i}" aria-label="${label} position" />
      ${hasPanel ? `<select data-action="joint" data-group="${i}" aria-label="${label} joint">${options.join("")}</select>` : "<span></span>"}
    </div>`;
    if (hasPanel) html += jointFields(joint, i, label);
  });
  return html;
}

/** Drawer settings, shown from `drawer` and applied to every drawer of the section; empty fields use the cabinet's. */
function drawerFields(drawer) {
  const c = state.config;
  const own = drawer.drawer || {};
  const onePiece = own.front_one_piece ?? c.drawer_front_one_piece;
  const number = (key, label, fallback, title) =>
    `<label class="row" title="${esc(title)}">${label} <input type="number" min="0" step="1" data-action="drawer-option" data-key="${key}"
      value="${own[key] === undefined ? "" : round(own[key])}" placeholder="${fallback}" /></label>`;
  return `<label class="row" title="The visible front is also the drawer box's front">One-piece front <input type="checkbox" data-action="drawer-option" data-key="front_one_piece" ${onePiece ? "checked" : ""} /></label>
    ${number("bottom_offset", "Bottom height", round(c.drawer_bottom_offset), "Underside of the bottom above the box's lower edge")}
    ${number("groove_depth", "Bottom groove depth", c.drawer_groove_depth ?? round((c.drawer_side_thickness ?? c.thickness) / 2), "Empty: half the grooved panel's thickness")}`;
}

// the door hinges' processing dimensions, in collapsible groups; empty fields use the cabinet's defaults
const HINGE_FIELDS = [
  ["general", "General", [["hinge_count", "Hinges per door", 1], ["end_distance", "End distance"]]],
  [
    "door",
    "Door",
    [
      ["cup_diameter", "Pot diameter"],
      ["cup_depth", "Pot depth"],
      ["cup_edge_distance", "Pot offset (to door edge)"],
      ["door_hole_diameter", "Screw diameter"],
      ["door_hole_depth", "Screw depth"],
      ["door_hole_spacing", "Screw spacing"],
      ["door_hole_offset", "Screw offset from pot center"],
    ],
  ],
  ["side", "Side", [["plate_offset", "Offset from door back"], ["plate_hole_spacing", "Screw spacing"], ["plate_hole_diameter", "Screw diameter"], ["plate_hole_depth", "Screw depth"]]],
];

// ids of the open collapsible menus, kept across re-renders
const openMenus = new Set();

function collapsible(id, title, body, level) {
  return `<details class="collapsible level-${level}" data-menu="${id}" ${openMenus.has(id) ? "open" : ""}><summary>${title}</summary><div class="collapsible-body">${body}</div></details>`;
}

function changed(count) {
  return count ? ` <span class="changed">· ${count} changed</span>` : "";
}

/** A door's hinges: its own dimensions in General / Door / Side menus; empty fields use the cabinet's. */
function hingeMenu(node) {
  const own = node.hinges || {};
  const defaults = { ...(state.hingeDefaults || {}), ...(state.config.hinges || {}) };
  const menus = HINGE_FIELDS.map(([id, title, fields]) => {
    const inputs = fields.map(([key, label, min]) => {
      const value = own[key];
      return `<label class="row">${esc(label)} <input type="number" min="${min ?? 0}" step="any" data-option="hinges" data-key="${key}"
        value="${value === undefined || value === null ? "" : round(value)}" placeholder="${defaults[key] ?? ""}" /></label>`;
    });
    const count = fields.filter(([key]) => own[key] !== undefined && own[key] !== null).length;
    return collapsible(`hinges/${id}`, title + changed(count), inputs.join(""), 2);
  });
  return collapsible("hinges", `Hinges · ${state.config.door_mount}` + changed(Object.keys(own).length), menus.join(""), 1);
}

// remember which menus are open; `toggle` doesn't bubble, so listen while capturing
props.addEventListener(
  "toggle",
  (evt) => {
    const id = evt.target.dataset && evt.target.dataset.menu;
    if (!id) return;
    if (evt.target.open) openMenus.add(id);
    else openMenus.delete(id);
  },
  true,
);

// the parameters shown for each interior joint type; empty fields use the cabinet's defaults
const JOINT_FIELDS = {
  mortise_tenon: [
    { key: "depth", label: "Depth", auto: (c) => `${round(c.thickness / 4)}`, hint: "Empty: a quarter of the receiving panel's thickness" },
    { key: "tenon_count", label: "Tenons", min: 1 },
  ],
  clamex_p14: [
    { key: "distance", label: "From ends" },
    { key: "center_connector", label: "Center", checkbox: true },
  ],
  stopped_dado: [
    { key: "depth", label: "Depth", auto: (c) => `${round(c.thickness / 3)}`, hint: "Empty: a third of the grooved panel's thickness" },
    { key: "stop_distance", label: "Stop", hint: "How far behind the front edge the groove stops" },
  ],
};

function jointFields(joint, group, label, at = "") {
  const c = state.config;
  const defaults = (state.jointDefaults && state.jointDefaults[joint.type]) || {};
  const fields = (JOINT_FIELDS[joint.type] || []).map((f) => {
    const attrs = `data-action="joint-param" data-group="${group}" data-key="${f.key}" ${at} aria-label="${label} ${f.label.toLowerCase()}"`;
    if (f.checkbox) {
      const checked = joint.params[f.key] ?? defaults[f.key];
      return `<label class="param">${f.label} <input type="checkbox" ${checked ? "checked" : ""} ${attrs} /></label>`;
    }
    const value = joint.params[f.key];
    const fallback = defaults[f.key] ?? (f.auto ? f.auto(c) : "");
    return `<label class="param" title="${esc(f.hint || "Empty: the cabinet default")}">${f.label}
      <input type="number" min="${f.min ?? 0}" step="1" value="${value === undefined || value === null ? "" : round(value)}" placeholder="${esc(fallback)}" ${attrs} /></label>`;
  });
  return fields.length ? `<div class="joint-params">${fields.join("")}</div>` : "";
}

function select(path) {
  state.selection = path;
  render();
  renderProps();
  props.scrollTop = 0;
}

// the counters in "Shelves & dividers": [read the count, set the count]
const COUNTERS = {
  shelves: [T.shelvesOf, T.setShelves],
  dividers: [T.dividersOf, T.setDividers],
  drawers: [T.drawersOf, T.setDrawers],
};

/** The section the sidebar shows: the current one. */
function sectionOf() {
  return state.selection;
}

document.getElementById("crumbs").addEventListener("click", (evt) => {
  const button = evt.target.closest("button[data-crumb]");
  if (!button) return;
  const crumb = button.dataset.crumb;
  select(crumb === "up" ? state.selection.slice(0, -1) : pathFromKey(crumb));
});

props.addEventListener("click", async (evt) => {
  const button = evt.target.closest("button[data-action]");
  if (!button || state.busy) return;
  const { action } = button.dataset;
  const path = sectionOf();
  if (action === "select") select(pathFromKey(button.dataset.path));
  else if (action === "type") await edit(() => T.setType(root(), path, button.dataset.type));
  else if (action === "door") await edit(() => T.setDoor(root(), path, button.dataset.door));
  else if (action in COUNTERS) {
    const [count, set] = COUNTERS[action];
    const n = Math.max(0, (count(T.getNode(root(), path)) ?? 0) + Number(button.dataset.step));
    await edit(() => set(root(), path, n));
  }
});

props.addEventListener("change", async (evt) => {
  const el = evt.target;
  if (state.busy) return;
  if (el.dataset.option) {
    // a per-section option: a door's hinges or a drawer's settings
    const value = el.type === "checkbox" ? el.checked : el.value === "" ? null : Number(el.value);
    await edit(() => T.setOption(root(), sectionOf(), el.dataset.option, el.dataset.key, value));
    return;
  }
  if (el.dataset.field) {
    await edit(() => T.setField(root(), sectionOf(), el.dataset.field, el.checked));
    return;
  }
  if (el.dataset.param) {
    let value = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : Number(el.value);
    if ("nullable" in el.dataset && el.value === "") value = null;
    if (!(await post({ ...state.config, [el.dataset.param]: value }))) renderProps();
    return;
  }
  const path = sectionOf();
  const { action } = el.dataset;
  const group = () => groupsFor(path)[Number(el.dataset.group)];
  if (action in COUNTERS) await edit(() => COUNTERS[action][1](root(), path, Number(el.value)));
  else if (action === "drawer-shelves") await edit(() => T.setDrawerShelves(root(), path, el.checked));
  else if (action === "drawer-option") {
    const value = el.type === "checkbox" ? el.checked : el.value === "" ? null : Number(el.value);
    await edit(() => T.setDrawerOption(root(), path, el.dataset.key, value));
  }
  else if (action === "joint") await edit(() => T.setPanelJoint(root(), group().members, el.value, state.config.interior_joint));
  else if (action === "joint-param") {
    const value = el.type === "checkbox" ? el.checked : el.value === "" ? null : Number(el.value);
    await edit(() => T.setPanelJointParam(root(), group().members, el.dataset.key, value, state.config.interior_joint));
  }
  else if (action === "position") {
    const g = group();
    const delta = Number(el.value) - groupPosition(g, path);
    await edit(() => T.movePanels(root(), regions(), g.members, delta));
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
  const generate = document.getElementById("generate");
  generate.classList.toggle("stale", Boolean(state.stale));
  generate.textContent = state.stale ? "Generate 3D •" : "Generate 3D";
  generate.title = state.stale ? "The 3D view doesn't show the latest changes yet: build the timber model and update it" : "Build the timber model and update the 3D view";
  generate.disabled = state.busy;
  document.getElementById("view-plan-fronts").checked = state.showFronts;
  document.getElementById("view-3d-fronts").checked = state.display.show_fronts;
  document.getElementById("view-3d-open").checked = state.display.open_fronts;
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
async function generate() {
  if (state.busy) return;
  state.busy = true;
  updateToolbar();
  setMessage("Generating 3D…");
  try {
    const res = await fetch("api/generate", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setMessage(data.error || "The model could not be built.", "error");
      return;
    }
    applyState(data);
    setMessage(data.errors.length ? `Joinery: ${data.errors.join(" ")}` : null, data.errors.length ? "error" : "info");
  } catch (e) {
    setMessage(`Could not reach the server: ${e.message}`, "error");
  } finally {
    state.busy = false;
    updateToolbar();
  }
}

document.getElementById("generate").addEventListener("click", generate);
document.getElementById("view-plan-fronts").addEventListener("change", (evt) => {
  state.showFronts = evt.target.checked;
  render();
});
document.getElementById("view-3d-fronts").addEventListener("change", (evt) => setDisplay({ show_fronts: evt.target.checked }));
document.getElementById("view-3d-open").addEventListener("change", (evt) => setDisplay({ open_fronts: evt.target.checked }));
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
document.getElementById("download").addEventListener("click", async () => {
  const text = JSON.stringify(state.config, null, 2);
  if (window.showSaveFilePicker) {
    // Chrome/Edge: a real save dialog
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: "cabinet.json",
        types: [{ description: "Cabinet configuration", accept: { "application/json": [".json"] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      setMessage(`Saved ${handle.name}.`);
    } catch (e) {
      if (e.name !== "AbortError") setMessage(`Could not save: ${e.message}`, "error");
    }
    return;
  }
  // other browsers: download, which asks where to save if the browser is set to
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
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
