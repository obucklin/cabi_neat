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

/** Freeze all sizes from the resolved regions, keeping the last child of each split flexible. */
export function freeze(root, regions) {
  root = clone(root);
  const visit = (node, path) => {
    if (!isSplit(node)) return;
    node.children.forEach((child, i) => {
      child.size = i === node.children.length - 1 ? null : extent(regions, [...path, i], node.direction);
      visit(child, [...path, i]);
    });
  };
  visit(root, []);
  return root;
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
    if (isSplit(result)) result.one_door = true;
  }
  return result;
}

/** `node` divided into `n + 1` equal parts along `direction`. New parts copy the last one (or the node itself). */
function withCount(node, direction, n) {
  if (isSplit(node) && node.direction === direction) {
    if (n === 0) return collapseTo(node, node.children[0]);
    const children = node.children.slice(0, n + 1).map((c) => ({ ...c, size: null }));
    while (children.length < n + 1) children.push({ ...clone(children[children.length - 1]), size: null });
    return { ...node, children };
  }
  if (n === 0) return node;
  const inner = withoutFront(clone(node));
  inner.size = null;
  return {
    direction,
    children: Array.from({ length: n + 1 }, () => clone(inner)),
    size: node.size,
    door_style: hasDoor(node) ? node.door_style : "none",
    hinge_side: node.hinge_side ?? "left",
    one_door: hasDoor(node),
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

/** Move panels (each the boundary between children `gap` and `gap + 1` of the split at `split`) by `delta` mm. */
export function movePanels(root, regions, members, delta, minSize = 30) {
  root = freeze(root, regions);
  for (const { split, gap } of members) {
    const parent = getNode(root, split);
    const a = extent(regions, [...split, gap], parent.direction) + delta;
    const b = extent(regions, [...split, gap + 1], parent.direction) - delta;
    if (a < minSize || b < minSize) throw new PlannerError(`Compartments must stay at least ${minSize} mm.`);
    parent.children[gap].size = a;
    if (parent.children[gap + 1].size !== null) parent.children[gap + 1].size = b;
  }
  return root;
}

/** Set the size of the node at `path` along its parent's direction (null = flexible). */
export function setSize(root, regions, path, size) {
  if (path.length === 0) throw new PlannerError("The whole interior's size follows the cabinet dimensions.");
  root = freeze(root, regions);
  const parent = getNode(root, path.slice(0, -1));
  const index = path[path.length - 1];
  parent.children[index].size = size;
  // keep one flexible sibling so the total still adds up
  if (parent.children.every((c) => c.size !== null)) {
    const flex = parent.children.length - 1 === index ? index - 1 : parent.children.length - 1;
    if (flex < 0) throw new PlannerError("A single compartment always fills its region.");
    parent.children[flex].size = null;
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
