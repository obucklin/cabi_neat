// Pure operations on the interior tree (the JSON form of cabi_neat's Split / Cell) in section form:
//
// - every node is a section: a Cell (a compartment or a drawer) or a Split (shelves: "rows", dividers: "columns");
// - every node has an explicit door_style: "none", or its own door(s) ("single" + hinge_side, or "double");
//   a Split with a door also has one_door = true, and everything inside it is behind that door;
// - drawers are Cells with kind "drawer", never behind a door.
//
// Operations take the current tree (plus the resolved `regions` {"0.1": [x, z, w, h], ...} where sizes matter) and
// return a new tree, or throw a PlannerError with a message for the user.

export class PlannerError extends Error {}

export const ROWS = "rows";
export const COLUMNS = "columns";

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function isSplit(node) {
  return Boolean(node) && Array.isArray(node.children);
}

export function isDrawer(node) {
  return Boolean(node) && !isSplit(node) && node.kind === "drawer";
}

export function hasDoor(node) {
  return Boolean(node) && Boolean(node.door_style) && node.door_style !== "none";
}

export function key(path) {
  return path.join(".");
}

export function getNode(root, path) {
  let node = root;
  for (const i of path) node = node.children[i];
  return node;
}

/** Path of the nearest ancestor (not the node itself) that has a door, or null. */
export function coveredBy(root, path) {
  for (let n = path.length - 1; n >= 0; n--) {
    if (hasDoor(getNode(root, path.slice(0, n)))) return path.slice(0, n);
  }
  return null;
}

/** Whether the section at `path` gets shelf holes: its own setting, else the nearest ancestor's (default: no). */
export function shelfHolesOf(root, path) {
  for (let n = path.length; n >= 0; n--) {
    const own = getNode(root, path.slice(0, n)).shelf_holes;
    if (own !== null && own !== undefined) return own;
  }
  return false;
}

/** Switch shelf holes on or off for the section at `path` and everything in it. */
export function setShelfHoles(root, path, on) {
  root = clone(root);
  const node = getNode(root, path);
  const clear = (n) => {
    if (!isSplit(n)) return;
    for (const child of n.children) {
      child.shelf_holes = null;
      clear(child);
    }
  };
  clear(node);
  node.shelf_holes = on;
  return root;
}

function replace(root, path, node) {
  if (path.length === 0) return node;
  getNode(root, path.slice(0, -1)).children[path[path.length - 1]] = node;
  return root;
}

function extent(regions, path, direction) {
  const r = regions[key(path)];
  if (!r) throw new PlannerError("Layout is out of date, try again.");
  return direction === ROWS ? r[3] : r[2];
}

/**
 * Make sure a split keeps a flexible child (size null), so its children still add up when its own size changes.
 * Picks the last child that isn't one of `keep` (the ones the user just set), or failing that the last of them.
 */
function keepFlexible(split, keep) {
  if (split.children.some((c) => c.size === null)) return;
  const candidates = split.children.map((_, i) => i).filter((i) => !keep.includes(i));
  const flex = candidates.length ? candidates[candidates.length - 1] : keep[keep.length - 1];
  split.children[flex].size = null;
}

// ============================================================================
// shelves and dividers
// ============================================================================

/** Number of shelves of a section. For a section with dividers, the shelves of its columns (null if they differ). */
export function shelvesOf(node) {
  if (!isSplit(node)) return 0;
  if (node.direction === ROWS) return node.children.length - 1;
  const counts = new Set(node.children.map((c) => (isSplit(c) && c.direction === ROWS ? c.children.length - 1 : 0)));
  return counts.size === 1 ? [...counts][0] : null;
}

export function dividersOf(node) {
  return isSplit(node) && node.direction === COLUMNS ? node.children.length - 1 : 0;
}

function withoutFront(node) {
  if (hasDoor(node)) {
    node.door_style = "none";
    if (isSplit(node)) node.one_door = false;
  }
  return node;
}

/** Collapse a split into its first child, which takes over the split's size and door. */
function collapseTo(node, first) {
  const result = { ...clone(first), size: node.size };
  if (hasDoor(node)) {
    result.door_style = node.door_style;
    result.hinge_side = node.hinge_side;
    result.hinges = node.hinges ?? null;
    if (isSplit(result)) result.one_door = true;
  }
  result.shelf_holes = node.shelf_holes ?? first.shelf_holes ?? null;
  if (isSplit(result) && isSplit(node)) {
    result.drawer_shelves = node.drawer_shelves ?? null;
    result.drawer_shelf_width = node.drawer_shelf_width ?? null;
  }
  return result;
}

/** `node` divided into `n + 1` equal parts along `direction`. New parts copy the last one (or the node itself). */
function withCount(node, direction, n) {
  if (isSplit(node) && node.direction === direction) {
    if (n === 0) return collapseTo(node, node.children[0]);
    const children = node.children.slice(0, n + 1).map((c) => ({ ...c, size: null }));
    while (children.length < n + 1) children.push({ ...clone(children[children.length - 1]), size: null });
    let panelJoints = node.panel_joints ? node.panel_joints.slice(0, n) : null;
    if (panelJoints) while (panelJoints.length < n) panelJoints.push(clone(panelJoints[panelJoints.length - 1] ?? null));
    return { ...node, children, panel_joints: panelJoints };
  }
  if (n === 0) return node;
  const inner = withoutFront(clone(node));
  inner.size = null;
  // the door, and its hinges, stay on the section being divided
  if (hasDoor(node)) inner.hinges = null;
  return {
    direction,
    children: Array.from({ length: n + 1 }, () => clone(inner)),
    size: node.size,
    door_style: hasDoor(node) ? node.door_style : "none",
    hinge_side: node.hinge_side ?? "left",
    one_door: hasDoor(node),
    panel_joints: null,
    hinges: hasDoor(node) ? (node.hinges ?? null) : null,
  };
}

function checkCount(n) {
  if (!Number.isInteger(n) || n < 0) throw new PlannerError("Enter a whole number of 0 or more.");
  if (n > 30) throw new PlannerError("That's a lot of panels - 30 at most.");
}

/** Set the number of shelves of the section at `path`, evenly spaced. With dividers, every column gets them. */
export function setShelves(root, path, n) {
  checkCount(n);
  root = clone(root);
  const node = getNode(root, path);
  if (isSplit(node) && node.direction === COLUMNS) {
    node.children = node.children.map((c) => withCount(c, ROWS, n));
    return root;
  }
  return replace(root, path, withCount(node, ROWS, n));
}

/** Set the number of dividers of the section at `path`, evenly spaced and full height. */
export function setDividers(root, path, n) {
  checkCount(n);
  root = clone(root);
  return replace(root, path, withCount(getNode(root, path), COLUMNS, n));
}

/**
 * The panels of the section at `path` that are edited when it's selected, grouped so that shelves at the same
 * position in every column move together: [{direction, members: [{split, gap}]}].
 */
export function panelGroups(root, path) {
  const node = getNode(root, path);
  const groups = [];
  if (!isSplit(node)) return groups;
  for (let g = 0; g < node.children.length - 1; g++) groups.push({ direction: node.direction, members: [{ split: path, gap: g }] });
  if (node.direction === COLUMNS) {
    const columns = node.children.map((c, i) => [c, i]).filter(([c]) => isSplit(c) && c.direction === ROWS);
    const aligned = columns.length === node.children.length && new Set(columns.map(([c]) => c.children.length)).size === 1;
    if (aligned) {
      for (let g = 0; g < columns[0][0].children.length - 1; g++) {
        groups.push({ direction: ROWS, members: columns.map(([, i]) => ({ split: [...path, i], gap: g })) });
      }
    } else {
      for (const [c, i] of columns) {
        for (let g = 0; g < c.children.length - 1; g++) groups.push({ direction: ROWS, members: [{ split: [...path, i], gap: g }] });
      }
    }
  }
  return groups;
}

/**
 * Move panels (each the boundary between children `gap` and `gap + 1` of the split at `split`) by `delta` mm.
 * The compartment before each panel gets a fixed size; the others keep theirs (automatic ones still share).
 */
export function movePanels(root, regions, members, delta, minSize = 30) {
  root = clone(root);
  for (const { split, gap } of members) {
    const parent = getNode(root, split);
    const a = extent(regions, [...split, gap], parent.direction) + delta;
    const b = extent(regions, [...split, gap + 1], parent.direction) - delta;
    if (a < minSize || b < minSize) throw new PlannerError(`Compartments must stay at least ${minSize} mm.`);
    parent.children[gap].size = a;
    // the compartment after the panel keeps its own setting: if it's fixed it gives up what the first one gains, so
    // the next panel stays where it is; if it's automatic it keeps sharing with the other automatic ones
    if (parent.children[gap + 1].size !== null) parent.children[gap + 1].size = b;
    keepFlexible(parent, [gap]);
  }
  return root;
}

/** Set the size of the node at `path` along its parent's direction (null = flexible: shares the space evenly). */
export function setSize(root, regions, path, size) {
  if (path.length === 0) throw new PlannerError("The whole interior's size follows the cabinet dimensions.");
  root = clone(root);
  const parent = getNode(root, path.slice(0, -1));
  const index = path[path.length - 1];
  if (size !== null && parent.children.length === 1) throw new PlannerError("A single compartment always fills its region.");
  parent.children[index].size = size;
  if (size !== null && parent.children.every((c) => c.size !== null)) {
    // keep a sibling flexible so the total still adds up
    const others = parent.children.map((_, i) => i).filter((i) => i !== index);
    parent.children[others[others.length - 1]].size = null;
  }
  return root;
}

// ============================================================================
// fronts
// ============================================================================

export const DOORS = ["none", "left", "right", "double"];

export function doorOf(node) {
  if (!hasDoor(node)) return "none";
  if (node.door_style === "double") return "double";
  return node.hinge_side === "right" ? "right" : "left";
}

function clearFronts(node) {
  withoutFront(node);
  if (isDrawer(node)) node.kind = "open";
  if (isSplit(node)) node.children.forEach(clearFronts);
}

/** Give the section at `path` a door ("left", "right", "double") or none. A door covers everything inside it. */
export function setDoor(root, path, door) {
  root = clone(root);
  const node = getNode(root, path);
  if (door !== "none" && coveredBy(root, path)) throw new PlannerError("This section is behind another door.");
  if (door === "none") {
    withoutFront(node);
    return root;
  }
  if (isSplit(node)) {
    node.children.forEach(clearFronts);
    node.one_door = true;
  } else {
    node.kind = "open";
  }
  node.door_style = door === "double" ? "double" : "single";
  node.hinge_side = door === "right" ? "right" : "left";
  return root;
}

/** Make the section at `path` a drawer, or a plain compartment again. */
export function setDrawer(root, path, on) {
  root = clone(root);
  const node = getNode(root, path);
  if (!on) {
    if (isDrawer(node)) node.kind = "open";
    return root;
  }
  if (isSplit(node)) throw new PlannerError("Only a section without shelves or dividers can be a drawer.");
  if (coveredBy(root, path)) throw new PlannerError("A drawer can't be behind a door. Remove the door first.");
  node.kind = "drawer";
  withoutFront(node);
  return root;
}

// ============================================================================
// panel joints
// ============================================================================

export const JOINTS = { mortise_tenon: "Mortise & tenon", clamex_p14: "Clamex P-14", stopped_dado: "Stopped dado" };

/** `{type, params}` of an entry of `panel_joints`: a type, `{type, ...params}`, or null for `fallback`. */
function jointEntry(entry, fallback) {
  if (!entry) return { type: fallback, params: {} };
  if (typeof entry === "string") return { type: entry, params: {} };
  const { type, ...params } = entry;
  return { type, params };
}

/** The joint of a panel group (its first member's): `{type, params}`, with only the parameters it sets itself. */
export function panelJointOf(root, group, fallback) {
  const { split, gap } = group.members[0];
  const joints = getNode(root, split).panel_joints;
  return jointEntry(joints && joints[gap], fallback);
}

function updateMembers(root, members, update) {
  root = clone(root);
  for (const { split, gap } of members) {
    const node = getNode(root, split);
    if (!node.panel_joints) node.panel_joints = Array(node.children.length - 1).fill(null);
    node.panel_joints[gap] = update(node.panel_joints[gap]);
  }
  return root;
}

/** Set the joint type of every panel in a group (e.g. aligned shelves across columns), dropping other types' parameters. */
export function setPanelJoint(root, members, type, fallback) {
  if (!(type in JOINTS) && type !== "butt") throw new PlannerError(`Unknown joint ${type}.`);
  return updateMembers(root, members, (entry) => {
    const current = jointEntry(entry, fallback);
    return current.type === type && Object.keys(current.params).length ? { type, ...current.params } : type;
  });
}

/** Set one parameter of the joint of every panel in a group; null goes back to the cabinet's default. */
export function setPanelJointParam(root, members, key, value, fallback) {
  return setPanelJointParams(root, members, { [key]: value }, fallback);
}

/** Set several parameters (`{key: value}`) of the joint of every panel in a group; null goes back to the default. */
export function setPanelJointParams(root, members, changes, fallback) {
  return updateMembers(root, members, (entry) => {
    const { type, params } = jointEntry(entry, fallback);
    const next = { ...params };
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === undefined) delete next[key];
      else next[key] = value;
    }
    return Object.keys(next).length ? { type, ...next } : type;
  });
}

// ============================================================================
// per-section options
// ============================================================================

/** Set `key` of the options dict `field` (e.g. "hinges", "drawer") of the section at `path`; null goes back to the default. */
export function setOption(root, path, field, key, value) {
  return setOptions(root, path, field, { [key]: value });
}

/** Set several keys (`{key: value}`) of the options dict `field` of the section at `path`; null goes back to the default. */
export function setOptions(root, path, field, changes) {
  root = clone(root);
  const node = getNode(root, path);
  node[field] = applyChanges(node[field], changes);
  return root;
}

/** `options` with `changes` (`{key: value}`) applied, null values removed; null if nothing's left. */
function applyChanges(options, changes) {
  const next = { ...(options || {}) };
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined) delete next[key];
    else next[key] = value;
  }
  return Object.keys(next).length ? next : null;
}

/** Set a plain field (e.g. "drawer_shelves") of the section at `path`. */
export function setField(root, path, field, value) {
  root = clone(root);
  getNode(root, path)[field] = value;
  return root;
}

/** True if the section at `path` stacks two drawers directly on top of each other. */
export function hasStackedDrawers(node) {
  return isSplit(node) && node.direction === ROWS && node.children.some((c, i) => i > 0 && isDrawer(c) && isDrawer(node.children[i - 1]));
}

// ============================================================================
// drawers
// ============================================================================

/** Number of drawers filling a section: 1 for a drawer, n for n stacked drawers; per column with dividers (null if they differ). */
export function drawersOf(node) {
  if (isDrawer(node)) return 1;
  if (!isSplit(node)) return 0;
  if (node.direction === ROWS) return node.children.every(isDrawer) ? node.children.length : 0;
  const counts = new Set(node.children.map(drawersOf));
  return counts.size === 1 ? [...counts][0] : null;
}

function drawer(size, options) {
  return { kind: "drawer", size, door_style: "none", hinge_side: "left", drawer: options ? clone(options) : null };
}

/** `node` filled with `n` stacked drawers (0: a plain compartment again). */
function withDrawers(node, n) {
  const existing = drawersOf(node) ? (isDrawer(node) ? node : node.children[0]) : null;
  const options = existing ? existing.drawer : null;
  if (n === 0) return existing ? { kind: "open", size: node.size, door_style: "none", hinge_side: "left" } : node;
  if (n === 1) return drawer(node.size, options);
  return {
    direction: ROWS,
    children: Array.from({ length: n }, () => drawer(null, options)),
    size: node.size,
    door_style: "none",
    hinge_side: "left",
    one_door: false,
    panel_joints: null,
    drawer_shelves: isSplit(node) && existing ? (node.drawer_shelves ?? null) : null,
    drawer_shelf_width: isSplit(node) && existing ? (node.drawer_shelf_width ?? null) : null,
  };
}

/** Fill the section at `path` with `n` equal stacked drawers, replacing its shelves. With dividers, every column gets them. */
export function setDrawers(root, path, n) {
  checkCount(n);
  if (n > 0 && coveredBy(root, path)) throw new PlannerError("Drawers can't be behind a door. Remove the door first.");
  root = clone(root);
  const node = getNode(root, path);
  if (isSplit(node) && node.direction === COLUMNS) {
    node.children = node.children.map((c) => withDrawers(c, n));
    return root;
  }
  return replace(root, path, withDrawers(node, n));
}

/** Set `field` (e.g. "drawer_shelves") of the drawer stacks of the section at `path` (every column's, with dividers). */
export function setDrawerStacks(root, path, field, value) {
  root = clone(root);
  const node = getNode(root, path);
  const stacks = node.direction === COLUMNS ? node.children.filter(isSplit) : [node];
  for (const stack of stacks) stack[field] = value;
  return root;
}

/** Set "shelves between drawers" for the drawer stacks of the section at `path` (every column's, with dividers). */
export function setDrawerShelves(root, path, on) {
  return setDrawerStacks(root, path, "drawer_shelves", on);
}

/** Make the shelves between drawers of the section at `path` front rails `width` deep (null: full-depth shelves). */
export function setDrawerShelfWidth(root, path, width) {
  return setDrawerStacks(root, path, "drawer_shelf_width", width);
}

// ============================================================================
// section type
// ============================================================================

export const TYPES = ["open", "door", "drawers"];

/** "drawers" if a section is filled with drawers, "door" if it has a door, otherwise "open". */
export function typeOf(node) {
  if (drawersOf(node) !== 0) return "drawers";
  return hasDoor(node) ? "door" : "open";
}

/** Make the section at `path` open, a door (hinged left unless it already had one) or drawers (one, unless it had some). */
export function setType(root, path, type) {
  if (!TYPES.includes(type)) throw new PlannerError(`Unknown section type ${type}.`);
  const node = getNode(root, path);
  if (typeOf(node) === type) return root;
  if (type !== "open" && coveredBy(root, path)) throw new PlannerError("This section is behind another door.");
  if (type === "drawers") return setDrawers(setDoor(root, path, "none"), path, 1);
  if (drawersOf(node) !== 0) root = setDrawers(root, path, 0);
  return setDoor(root, path, type === "door" ? "left" : "none");
}

/** Set `key` of the settings of every drawer in the section at `path` (null goes back to the cabinet's default). */
export function setDrawerOption(root, path, key, value) {
  return setDrawerOptions(root, path, { [key]: value });
}

/** Set several settings (`{key: value}`) of every drawer in the section at `path`; null goes back to the cabinet's. */
export function setDrawerOptions(root, path, changes) {
  root = clone(root);
  const visit = (node) => {
    if (isDrawer(node)) node.drawer = applyChanges(node.drawer, changes);
    else if (isSplit(node)) node.children.forEach(visit);
  };
  visit(getNode(root, path));
  return root;
}

/** The first drawer in a section (whose settings the sidebar shows for all of them), or null. */
export function firstDrawer(node) {
  if (isDrawer(node)) return node;
  if (!isSplit(node)) return null;
  for (const child of node.children) {
    const found = firstDrawer(child);
    if (found) return found;
  }
  return null;
}
