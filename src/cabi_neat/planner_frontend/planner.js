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
  highlight: new Set(), // names of the highlighted parts (e.g. the shelf being edited), in the plan and in 3D
  pickCount: null, // how many 3D picks the server has seen; null until the first check
  hover: null, // pointer position in mm
  drag: null, // {group, origin, delta, moved}
  busy: false,
  history: [],
  future: [],
  showFronts: true,
  showCutList: false,
  cutList: [],
  snap: 10,
  name: "cabinet", // the default name of saved and written files
  fileHandle: null, // the JSON file the cabinet was loaded from or last saved to, where the browser can write it (Chrome/Edge)
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
  if (data.shelf_hole_defaults) state.shelfHoleDefaults = data.shelf_hole_defaults;
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

/** Names of the parts (shelves, dividers) of a panel group. */
function groupParts(group) {
  const keys = new Set(group.members.map((m) => `${T.key(m.split)}/${m.gap}`));
  return state.layout.parts.filter((p) => keys.has(`${p.attributes.split}/${p.attributes.gap}`)).map((p) => p.name);
}

/** A part was clicked in the 3D view: go to its section and highlight it. */
function showPicked(name) {
  const part = state.layout.parts.find((p) => p.name === name);
  if (!part) return;
  let path = null;
  if (part.attributes.split !== undefined) path = pathFromKey(part.attributes.split);
  else if (part.attributes.drawer !== undefined) path = part.attributes.drawer.split("_").map(Number);
  else if (part.attributes.node !== undefined) path = part.attributes.node === "" ? [] : part.attributes.node.split("_").map(Number);
  if (path) {
    // a shelf of a column in a divided section is edited from the divided section
    const parent = path.length ? T.getNode(root(), path.slice(0, -1)) : null;
    if (part.attributes.split !== undefined && parent && parent.direction === T.COLUMNS && T.isSplit(T.getNode(root(), path))) path = path.slice(0, -1);
    if (T.key(path) in regions()) select(path);
  }
  highlight([name]);
}

/** Highlight parts in the plan, the sidebar and the 3D view. */
function highlight(names) {
  const next = new Set(names);
  if (next.size === state.highlight.size && [...next].every((n) => state.highlight.has(n))) return;
  state.highlight = next;
  render();
  props.querySelectorAll(".panel-row").forEach((row) => {
    const parts = row.dataset.parts ? row.dataset.parts.split(",") : [];
    row.classList.toggle("highlight", parts.some((n) => next.has(n)));
  });
  fetch("api/highlight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ parts: [...next] }) }).catch(() => {});
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
  const hasCountertop = state.config.countertop;
  const tt = hasCountertop ? state.config.countertop_thickness ?? t : t;
  const [left, right] = hasCountertop ? [state.config.countertop_overhang_left, state.config.countertop_overhang_right] : [0, 0];
  const m = Math.max(W, H) * 0.08;
  const fs = Math.max(W, H) / 55;
  svg.setAttribute("viewBox", `${-m - left} ${-m} ${W + left + right + 2 * m} ${H + 2 * m}`);

  const out = [];
  const rect = (x, z, w, h, cls, extra = "") => `<rect x="${x}" y="${H - z - h}" width="${w}" height="${h}" class="${cls}" vector-effect="non-scaling-stroke" ${extra}/>`;
  const text = (x, z, label, cls, size = fs) => `<text x="${x}" y="${H - z}" class="${cls}" font-size="${size}">${esc(label)}</text>`;
  const line = (points, cls, extra = "") =>
    `<polyline points="${points.map(([x, z]) => `${x},${H - z}`).join(" ")}" class="${cls}" vector-effect="non-scaling-stroke" ${extra}/>`;

  out.push(rect(t, t, W - 2 * t, H - t - tt, "cavity"));
  for (const part of state.layout.parts) {
    if (["side", "top", "countertop", "bottom"].includes(part.category)) out.push(rect(part.x, part.z, part.w, part.h, "carcass"));
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
    if (dragged.has(id) || state.highlight.has(part.name)) cls += " selected-panel";
    else if (hoveredKeys.has(id)) cls += " hover-panel own";
    else if (own.has(id)) cls += " own";
    else if (inside) cls += " locked";
    out.push(rect(x, z, part.w, part.h, cls, 'stroke-width="1.5"'));
  }
  // shelf holes, seen from the front: a short mark into the panel at each height
  for (const [x, z, dx, depth] of state.layout.holes || []) {
    out.push(line([[x, z], [x + dx * depth, z]], "shelf-hole", 'stroke-width="2"'));
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
        // the hinges' pots, on the back of the door
        const r = (part.attributes.cup_diameter || 35) / 2;
        for (const [hx, hz] of part.attributes.hinges || []) {
          out.push(`<circle cx="${hx}" cy="${H - hz}" r="${r}" class="hinge-pot" vector-effect="non-scaling-stroke"/>`);
          out.push(`<line x1="${hinge}" y1="${H - hz}" x2="${hx}" y2="${H - hz}" class="hinge-arm" vector-effect="non-scaling-stroke"/>`);
        }
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

// ids of the collapsed groups, kept across re-renders (groups start open)
const closedGroups = new Set();

/**
 * A group that collapses by clicking its title. `check` is an option checkbox shown on the title line: while it's
 * off the group has nothing to show, so it's just the title and the checkbox.
 */
function foldGroup(id, title, body, check = "", major = false) {
  const head = `<h2>${title}</h2>${check}`;
  const cls = `group fold${major ? " major" : ""}`;
  if (!body) return `<div class="${cls}"><div class="fold-head">${head}</div></div>`;
  return `<details class="${cls}" data-fold="${id}" ${closedGroups.has(id) ? "" : "open"}><summary class="fold-head">${head}</summary><div class="fold-body">${body}</div></details>`;
}

/** One of the carcass level's main groups: Cabinet, Carcass joints, Countertop, Interior. */
function majorGroup(id, title, body, check = "") {
  return foldGroup(id, title, body, check, true);
}

/** The checkbox on a group's title line; `attrs` say what it switches (data-param or data-action). */
function headCheck(on, attrs, title) {
  return `<input type="checkbox" class="head-check" ${attrs} ${on ? "checked" : ""} title="${esc(title)}" aria-label="${esc(title)}" />`;
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
      majorGroup(
        "cabinet",
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
      majorGroup(
        "carcass-joints",
        "Carcass joints",
        `<div class="subhead">Back in the sides: rabbet</div>
        ${numberRow("Depth", c.rabbet_depth, `data-param="rabbet_depth" data-nullable min="0" placeholder="${round(c.thickness / 2)}" title="Empty: half the side thickness"`)}
        <div class="subhead">${c.countertop ? "Bottom on the sides" : "Top and bottom on the sides"}: Clamex P-14</div>
        ${numberRow("Connector distance", c.clamex_distance, 'data-param="clamex_distance" min="0"')}
        <label class="row">Center connector <input type="checkbox" data-param="clamex_center" ${c.clamex_center ? "checked" : ""} /></label>`,
      ),
    );
    let countertop = "";
    if (c.countertop) {
      countertop = `${numberRow("Thickness", c.countertop_thickness, `data-param="countertop_thickness" data-nullable min="1" placeholder="${round(c.thickness)}" title="Empty: the panel thickness"`)}
        <div class="subhead">Overhang</div>
        ${numberRow("Front", c.countertop_overhang_front, 'data-param="countertop_overhang_front" min="0"')}
        ${numberRow("Back", c.countertop_overhang_back, 'data-param="countertop_overhang_back" min="0"')}
        ${numberRow("Left", c.countertop_overhang_left, 'data-param="countertop_overhang_left" min="0"')}
        ${numberRow("Right", c.countertop_overhang_right, 'data-param="countertop_overhang_right" min="0"')}
        <label class="row">On the sides <select data-param="countertop_joint">
          <option value="clamex_p14" ${c.countertop_joint === "clamex_p14" ? "selected" : ""}>Clamex P-14</option>
          <option value="butt" ${c.countertop_joint === "butt" ? "selected" : ""}>butt</option>
        </select></label>`;
      if (c.countertop_joint === "clamex_p14") {
        const center = c.countertop_clamex_center ?? c.clamex_center;
        countertop += `
        ${numberRow("Connector distance", c.countertop_clamex_distance, `data-param="countertop_clamex_distance" data-nullable min="0" placeholder="${round(c.clamex_distance)}" title="Empty: the carcass's connector distance"`)}
        <label class="row">Center connector <input type="checkbox" data-param="countertop_clamex_center" ${center ? "checked" : ""} /></label>`;
      }
      countertop += `<div class="hint">Height includes the countertop.</div>`;
    }
    html.push(majorGroup("countertop", "Countertop", countertop, headCheck(c.countertop, 'data-param="countertop"', "Countertop instead of top")));
    // the interior's type, front, shelves and dividers, all in one group
    html.push(majorGroup("interior", "Interior", sectionGroups(path).join("")));
  } else {
    const summary = sectionSummary(T.getNode(root(), path));
    const covered = T.coveredBy(root(), path);
    const behind = covered
      ? `<div class="hint">Behind the door of <button class="linklike" data-action="select" data-path="${T.key(covered)}">${esc(sectionLabel(covered))}</button>.</div>`
      : "";
    html.push(`<h3>${sectionLabel(path)}</h3>${summary ? `<div class="hint">${esc(summary)}</div>` : ""}${behind}`);
    html.push(...sectionGroups(path));
  }

  if (state.showCutList) {
    const rows = state.cutList.map((r) => `<tr><td>${esc(r[1])}</td><td>${round(r[2])}</td><td>${round(r[3])}</td><td>${round(r[4])}</td></tr>`);
    html.push(majorGroup("cutlist", `Cut list · ${state.cutList.length} parts`, `<table class="cutlist"><thead><tr><th>Part</th><th>L</th><th>W</th><th>T</th></tr></thead><tbody>${rows.join("")}</tbody></table>`));
  }

  const scroll = props.scrollTop;
  props.innerHTML = html.join("");
  props.scrollTop = scroll;
}

/** The parameter groups of the section at `path`: dimensions, front (with its hinges or drawer), shelves and dividers. */
const TYPE_LABELS = { open: "Open", door: "Door", drawers: "Drawers" };
// depth of a front rail between drawers when it's switched on
const DEFAULT_RAIL_WIDTH = 80;
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
  // the interior's size is the cabinet's, so at the top it's just a note
  groups.push(path.length ? group("Dimensions", dims) : dims);

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
    let onShelf = false;
    if (drawers === null || drawers > 1) {
      const stack = T.isSplit(node) && node.direction === T.COLUMNS ? node.children.find(T.isSplit) : node;
      onShelf = Boolean((stack && stack.drawer_shelves) ?? c.shelves_between_drawers);
      // the shelves between the drawers, their kind and their positions, together
      let shelves = `<label class="row">Shelves between drawers <input type="checkbox" data-action="drawer-shelves" ${onShelf ? "checked" : ""} /></label>`;
      if (onShelf) {
        const width = (stack && stack.drawer_shelf_width) ?? c.drawer_shelf_width;
        shelves += `<label class="row" title="Only a strip at the front of the cabinet, not reaching the back">Front rail only <input type="checkbox" data-action="drawer-rail" ${width !== null ? "checked" : ""} /></label>`;
        if (width !== null) shelves += numberRow("Rail width", width, 'data-action="drawer-rail-width" min="1" title="How far the rail reaches back from the front"');
      }
      body += `<div class="subgroup">${shelves}${panelRows(path)}</div>`;
    } else body += panelRows(path);
    body += collapsible("drawer-settings", "Drawer settings" + changed(Object.keys(T.firstDrawer(node).drawer || {}).length), drawerFields(T.firstDrawer(node), onShelf), 1);
    body += drawerWarnings(path);
    groups.push(group("Drawers", body));
  } else {
    groups.push(group(type === "door" ? "Shelves & dividers behind the door" : "Shelves & dividers", counter("Shelves", T.shelvesOf(node), "shelves") + counter("Dividers", T.dividersOf(node), "dividers") + panelRows(path)));
    const on = T.shelfHolesOf(root(), path);
    const check = headCheck(on, 'data-action="shelf-holes"', "Rows of shelf holes on a 32 mm spacing in the panels left and right of the compartments");
    groups.push(foldGroup("shelf-holes", "Shelf holes (32 mm)", on ? shelfHoleRows() : "", check));
  }
  return groups;
}

/** The layout's warnings about the drawers in the section at `path` (keys like "0_1", "" for the whole interior). */
function drawerWarnings(path) {
  const key = path.join("_");
  const warnings = (state.layout.warnings || []).filter((w) => !key || w.node === key || w.node.startsWith(key + "_"));
  return warnings.map((w) => `<div class="warning">⚠ ${esc(w.message)}</div>`).join("");
}

/** The shelf hole dimensions, shared by the whole cabinet. */
function shelfHoleRows() {
  const c = state.config;
  const holes = { ...state.shelfHoleDefaults, ...(c.shelf_hole_options || {}) };
  const holeRow = (key, label, title, placeholder = "") =>
    `<label class="row" title="${esc(title)}">${label} <input type="number" min="0" step="1" data-cabinet-option="shelf_hole_options" data-key="${key}"
      value="${c.shelf_hole_options && c.shelf_hole_options[key] !== undefined ? round(c.shelf_hole_options[key]) : ""}" placeholder="${placeholder || round(holes[key])}" /></label>`;
  return `<div class="hint">For all shelf holes in the cabinet. The drill head drills them if it has a bit for them.</div>
    ${holeRow("diameter", "Diameter", "Hole diameter")}
    ${holeRow("depth", "Depth", "Empty: half the panel thickness minus 1, so holes from both faces of a divider don't meet", round(c.thickness / 2 - 1))}
    ${holeRow("spacing", "Spacing", "Distance between holes in a row")}
    ${holeRow("front_distance", "From the front", "From the panel's front edge to the front row")}
    ${holeRow("back_distance", "From the back", "From the back panel to the rear row")}
    ${holeRow("end_distance", "From shelves", "Least distance from a compartment's bottom and top to a hole")}`;
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
    const names = groupParts(g);
    const lit = names.some((n) => state.highlight.has(n)) ? " highlight" : "";
    html += `<div class="panel-row${lit}" data-parts="${names.join(",")}" data-group="${i}"><span>${label}</span>
      <input type="number" value="${round(groupPosition(g, path))}" min="0" step="1" data-action="position" data-group="${i}" aria-label="${label} position" />
      ${hasPanel ? `<select data-action="joint" data-group="${i}" aria-label="${label} joint">${options.join("")}</select>` : "<span></span>"}
    </div>`;
    if (hasPanel) html += jointFields(joint, i, label);
  });
  return html;
}

/** Drawer settings, shown from `drawer` and applied to every drawer of the section; empty fields use the cabinet's. */
/** A drawer's settings. On shelves (`onShelf`) the bottom is placed by its clearance above the shelf, not its height in the box. */
function drawerFields(drawer, onShelf = false) {
  const c = state.config;
  const own = drawer.drawer || {};
  const onePiece = own.front_one_piece ?? c.drawer_front_one_piece;
  const number = (key, label, fallback, title) =>
    `<label class="row" title="${esc(title)}">${label} <input type="number" min="0" step="1" data-action="drawer-option" data-key="${key}"
      value="${own[key] === undefined ? "" : round(own[key])}" placeholder="${fallback}" /></label>`;
  const bottom = onShelf
    ? number("bottom_clearance", "Bottom clearance", round(c.drawer_bottom_clearance ?? own.bottom_offset ?? c.drawer_bottom_offset), "Underside of the bottom above the shelf. At least 5, or the groove for the bottom comes too close to the sides' lower edge")
    : number("bottom_offset", "Bottom height", round(c.drawer_bottom_offset), "Underside of the bottom above the box's lower edge");
  return `<label class="row" title="The visible front is also the drawer box's front">One-piece front <input type="checkbox" data-action="drawer-option" data-key="front_one_piece" ${onePiece ? "checked" : ""} /></label>
    ${number("side_clearance", "Side clearance", round(c.drawer_clearance), "Gap between the box's sides and the carcass on each side, for the runners")}
    ${number("side_height", "Side height", c.drawer_side_height === null ? "front − 20" : round(c.drawer_side_height), "Height of the drawer box. Empty: 20 lower than the front, as far as it fits")}
    ${bottom}
    ${number("bottom_thickness", "Bottom thickness", round(c.drawer_bottom_thickness ?? c.back_thickness), "Empty: the carcass back's thickness")}
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

// remember which menus are open and which groups collapsed; `toggle` doesn't bubble, so listen while capturing
props.addEventListener(
  "toggle",
  (evt) => {
    const { menu, fold } = evt.target.dataset || {};
    if (menu) {
      if (evt.target.open) openMenus.add(menu);
      else openMenus.delete(menu);
    } else if (fold) {
      if (evt.target.open) closedGroups.delete(fold);
      else closedGroups.add(fold);
    }
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
  highlight([]);
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

// clicking into a panel's row (its label, position or joint) highlights that panel
props.addEventListener("focusin", (evt) => {
  const row = evt.target.closest(".panel-row");
  if (row && row.dataset.parts) highlight(row.dataset.parts.split(","));
});

props.addEventListener("click", async (evt) => {
  const row = evt.target.closest(".panel-row");
  if (row && row.dataset.parts) highlight(row.dataset.parts.split(","));
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
  if (el.dataset.cabinetOption) {
    const field = el.dataset.cabinetOption;
    const options = { ...(state.config[field] || {}) };
    if (el.value === "") delete options[el.dataset.key];
    else options[el.dataset.key] = Number(el.value);
    if (!(await post({ ...state.config, [field]: Object.keys(options).length ? options : null }))) renderProps();
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
  else if (action === "shelf-holes") await edit(() => T.setShelfHoles(root(), path, el.checked));
  else if (action === "drawer-rail") await edit(() => T.setDrawerShelfWidth(root(), path, el.checked ? DEFAULT_RAIL_WIDTH : null));
  else if (action === "drawer-rail-width" && el.value !== "") await edit(() => T.setDrawerShelfWidth(root(), path, Number(el.value)));
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
    highlight(groupParts(group));
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
  const cutList = document.getElementById("cutlist-toggle");
  cutList.textContent = state.showCutList ? "Hide cut list" : "Show cut list";
  cutList.title = state.showCutList ? "Hide the cut list in the sidebar" : "Show the cut list in the sidebar";
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
  if ((evt.metaKey || evt.ctrlKey) && evt.key.toLowerCase() === "s") {
    evt.preventDefault();
    // let an edited field apply first
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    if (evt.shiftKey) saveAs();
    else save();
    return;
  }
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

/** Replace the cabinet with a fresh, empty one. It's an ordinary edit, so Undo brings the previous cabinet back. */
async function newCabinet() {
  if (state.busy) return;
  try {
    const res = await fetch("api/new");
    const config = await res.json();
    if (await post(config)) {
      select([]);
      setName("cabinet", null);
      setMessage("New cabinet. Undo brings the previous one back.");
    }
  } catch (e) {
    setMessage(`Could not start a new cabinet: ${e.message}`, "error");
  }
}

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
/**
 * Where to save a file: a file handle from a save dialog where the browser has one (Chrome/Edge), else a download name.
 * Must be called straight from a click, before anything slow: the browser only shows the dialog for a user gesture.
 * Returns null if the dialog was cancelled.
 */
async function pickSaveTarget(suggestedName, description, accept) {
  if (!window.showSaveFilePicker) return { download: suggestedName };
  try {
    return { handle: await window.showSaveFilePicker({ suggestedName, types: [{ description, accept }] }) };
  } catch (e) {
    if (e.name === "AbortError") return null;
    throw e;
  }
}

/** Write `blob` to a target from `pickSaveTarget`. Returns the file's name. */
async function writeSaveTarget(target, blob) {
  if (target.handle) {
    const writable = await target.handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return target.handle.name;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = target.download;
  a.click();
  URL.revokeObjectURL(a.href);
  return target.download;
}

// ============================================================================
// files
// ============================================================================

const JSON_TYPE = { "application/json": [".json"] };

/** Name the cabinet (the default name of its files) and remember the JSON file it now belongs to, if any. */
function setName(name, handle) {
  state.name = name.trim() || "cabinet";
  state.fileHandle = handle;
  updateName();
}

function updateName() {
  document.getElementById("cabinet-name").value = state.name;
  document.title = `${state.name} · Cabinet planner`;
  const save = document.getElementById("save");
  save.title = state.fileHandle ? `Save to ${state.fileHandle.name} (Ctrl+S)` : "Save the configuration as JSON (Ctrl+S)";
}

/** "kitchen" for "kitchen.json". */
function baseName(filename) {
  return filename.replace(/\.json$/i, "");
}

function configBlob() {
  return new Blob([JSON.stringify(state.config, null, 2)], { type: "application/json" });
}

/** Open a configuration JSON: with an open dialog that lets Save write back to it where the browser has one. */
async function loadFile() {
  if (!window.showOpenFilePicker) {
    document.getElementById("upload").click();
    return;
  }
  let handle;
  try {
    [handle] = await window.showOpenFilePicker({ types: [{ description: "Cabinet configuration", accept: JSON_TYPE }] });
  } catch (e) {
    if (e.name !== "AbortError") setMessage(`Could not open: ${e.message}`, "error");
    return;
  }
  await loadConfig(await handle.getFile(), handle);
}

async function loadConfig(file, handle) {
  try {
    if (await post(JSON.parse(await file.text()))) {
      select([]);
      setName(baseName(file.name), handle);
      setMessage(`Loaded ${file.name}.`);
    }
  } catch (e) {
    setMessage(`Could not read ${file.name}: ${e.message}`, "error");
  }
}

/** Save to the JSON file the cabinet belongs to, or ask where to save it if there's none yet. */
async function save() {
  const handle = state.fileHandle;
  if (!handle) return saveAs();
  try {
    // a file that was opened needs the user's permission to be written
    if ((await handle.queryPermission({ mode: "readwrite" })) !== "granted" && (await handle.requestPermission({ mode: "readwrite" })) !== "granted") {
      setMessage(`Not allowed to write ${handle.name}: use Save as.`, "error");
      return;
    }
    await writeSaveTarget({ handle }, configBlob());
    setMessage(`Saved ${handle.name}.`);
  } catch (e) {
    setMessage(`Could not save ${handle.name}: ${e.message}`, "error");
  }
}

async function saveAs() {
  try {
    const target = await pickSaveTarget(`${state.name}.json`, "Cabinet configuration", JSON_TYPE);
    if (!target) return;
    const name = await writeSaveTarget(target, configBlob());
    setName(baseName(name), target.handle || null);
    setMessage(`Saved ${name}.`);
  } catch (e) {
    setMessage(`Could not save: ${e.message}`, "error");
  }
}

/** The cut list as CSV, one row per part. */
async function cutListBlob() {
  const res = await fetch("api/cutlist");
  if (!res.ok) throw new Error((await res.text()) || `the server answered ${res.status}`);
  const { rows } = await res.json();
  const cell = (v) => (typeof v === "number" ? String(round(v)) : `"${String(v).replace(/"/g, '""')}"`);
  const lines = [["Category", "Part", "Length", "Width", "Thickness"].map(cell), ...rows.map((r) => r.map(cell))];
  return { blob: new Blob([lines.map((l) => l.join(",")).join("\r\n") + "\r\n"], { type: "text/csv" }), summary: `${rows.length} parts.` };
}

/** One of the backend's exports (it generates the model first if needed). */
async function exportBlob(kind) {
  const res = await fetch(`api/export/${kind}`);
  if (!res.ok) throw new Error((await res.text()) || `the server answered ${res.status}`);
  return { blob: await res.blob(), summary: res.headers.get("X-Export-Summary") || "" };
}

/** Write one of the files for the workshop, named after the cabinet. */
async function writeFile(kind) {
  if (state.busy) return;
  const formats = {
    cutlist: [`${state.name}_cutlist.csv`, "Cut list (CSV)", { "text/csv": [".csv"] }, cutListBlob],
    btlx: [`${state.name}.btlx`, "BTLx", { "application/xml": [".btlx"] }, () => exportBlob("btlx")],
    hops: [`${state.name}_hops.zip`, "HOPS programs (zip)", { "application/zip": [".zip"] }, () => exportBlob("hops")],
    dxf: [`${state.name}_outlines.dxf`, "Part outlines (DXF)", { "application/dxf": [".dxf"] }, () => exportBlob("dxf")],
  };
  const [name, description, accept, make] = formats[kind];
  // ask where to save first: after the (slow) export the click no longer counts as a user gesture
  let target;
  try {
    target = await pickSaveTarget(name, description, accept);
  } catch (e) {
    setMessage(`Could not write ${description}: ${e.message}`, "error");
    return;
  }
  if (!target || state.busy) return;
  state.busy = true;
  updateToolbar();
  setMessage(state.stale ? "Generating 3D and writing…" : "Writing…");
  try {
    const { blob, summary } = await make();
    const saved = await writeSaveTarget(target, blob);
    setMessage(`Wrote ${saved}. ${summary}`);
    if (state.stale) await load();
  } catch (e) {
    setMessage(`Could not write ${description}: ${e.message}`, "error");
  } finally {
    state.busy = false;
    updateToolbar();
  }
}

// the toolbar's drop-down menus: one open at a time, closed by picking an item or clicking elsewhere
const menus = [...document.querySelectorAll("#toolbar details.menu")];
for (const menu of menus) {
  menu.addEventListener("toggle", () => {
    if (menu.open) for (const other of menus) if (other !== menu) other.open = false;
  });
  menu.querySelector(".menu-items").addEventListener("click", (evt) => {
    if (evt.target.closest("button")) menu.open = false;
  });
}
document.addEventListener("click", (evt) => {
  for (const menu of menus) if (menu.open && !menu.contains(evt.target)) menu.open = false;
});

const MENU_ACTIONS = {
  new: newCabinet,
  load: loadFile,
  save,
  "save-as": saveAs,
  "write-cutlist": () => writeFile("cutlist"),
  "write-btlx": () => writeFile("btlx"),
  "write-hops": () => writeFile("hops"),
  "write-dxf": () => writeFile("dxf"),
};
for (const [id, action] of Object.entries(MENU_ACTIONS)) document.getElementById(id).addEventListener("click", action);

document.getElementById("upload").addEventListener("change", async (evt) => {
  const file = evt.target.files[0];
  evt.target.value = "";
  if (file) await loadConfig(file, null);
});
document.getElementById("cabinet-name").addEventListener("change", (evt) => setName(evt.target.value, state.fileHandle));

// ============================================================================
// start
// ============================================================================

document.getElementById("view3d").src = `/?ws_host=${location.hostname}&ws_port=${location.port || 80}`;
new ResizeObserver(() => render()).observe(svg);
updateToolbar();
updateName();
load();

// pick up changes made elsewhere (another planner tab)
setInterval(async () => {
  if (state.busy || state.drag) return;
  try {
    const res = await fetch("api/version");
    const { version, pick } = await res.json();
    if (version !== state.version) await load();
    if (pick && pick.count !== state.pickCount) {
      // on the first check just take note: don't jump to something picked before this page opened
      const first = state.pickCount === null;
      state.pickCount = pick.count;
      if (!first && pick.name) showPicked(pick.name);
    }
  } catch {
    // server stopped; keep the last state on screen
  }
}, 1000);
