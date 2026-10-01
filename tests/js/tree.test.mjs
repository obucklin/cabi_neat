// Run with: node --test tests/js/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  setShelfHoles,
  shelfHolesOf,
  setDrawerShelfWidth,
  coveredBy,
  dividersOf,
  doorOf,
  getNode,
  isDrawer,
  isSplit,
  movePanels,
  panelGroups,
  panelJointOf,
  setPanelJoint,
  setPanelJointParam,
  setOption,
  setField,
  hasStackedDrawers,
  drawersOf,
  setDrawers,
  setDrawerShelves,
  typeOf,
  setType,
  setDrawerOption,
  firstDrawer,
  PlannerError,
  setDividers,
  setDoor,
  setDrawer,
  setShelves,
  setSize,
  shelvesOf,
} from "../../src/cabi_neat/planner_frontend/tree.js";

const cell = (extra = {}) => ({ kind: "open", size: null, door_style: "none", hinge_side: "left", ...extra });
const door = (extra = {}) => cell({ door_style: "single", ...extra });

test("shelves split a compartment evenly and keep its door on the section", () => {
  const root = setShelves(door(), [], 2);
  assert.equal(root.direction, "rows");
  assert.equal(root.children.length, 3);
  assert.ok(root.one_door && root.door_style === "single");
  assert.ok(root.children.every((c) => c.size === null && c.door_style === "none"));
  assert.equal(shelvesOf(root), 2);
});

test("removing all shelves collapses back to one compartment", () => {
  const root = setShelves(setShelves(door(), [], 3), [], 0);
  assert.ok(!isSplit(root));
  assert.equal(root.door_style, "single");
});

test("changing the shelf count keeps existing compartments and re-spaces evenly", () => {
  let root = setShelves(cell(), [], 1);
  root.children[0] = { ...root.children[0], size: 300, door_style: "single" };
  root = setShelves(root, [], 3);
  assert.equal(root.children.length, 4);
  assert.equal(root.children[0].door_style, "single");
  assert.ok(root.children.every((c) => c.size === null));
  root = setShelves(root, [], 1);
  assert.equal(root.children.length, 2);
});

test("dividers run full height and every column gets the shelves", () => {
  let root = setShelves(door(), [], 2);
  root = setDividers(root, [], 2);
  assert.equal(root.direction, "columns");
  assert.equal(dividersOf(root), 2);
  assert.ok(root.one_door);
  assert.ok(root.children.every((c) => c.direction === "rows" && c.children.length === 3 && !c.one_door));
  assert.equal(shelvesOf(root), 2);
  root = setShelves(root, [], 4);
  assert.ok(root.children.every((c) => c.children.length === 5));
  root = setDividers(root, [], 0);
  assert.equal(root.direction, "rows");
  assert.equal(root.door_style, "single");
});

test("different shelf counts per column show as mixed", () => {
  let root = setDividers(cell(), [], 1);
  root = setShelves(root, [0], 2);
  assert.equal(shelvesOf(root), null);
  assert.equal(shelvesOf(getNode(root, [0])), 2);
});

test("a drawer divided gives stacked or side-by-side drawers", () => {
  const stacked = setShelves(cell({ kind: "drawer" }), [], 2);
  assert.ok(stacked.children.every(isDrawer));
  assert.equal(stacked.door_style, "none");
  const sideBySide = setDividers(cell({ kind: "drawer" }), [], 1);
  assert.ok(sideBySide.children.every(isDrawer));
});

test("panel groups align shelves across columns", () => {
  const root = setShelves(setDividers(cell(), [], 2), [], 2);
  const groups = panelGroups(root, []);
  assert.equal(groups.filter((g) => g.direction === "columns").length, 2);
  const shelves = groups.filter((g) => g.direction === "rows");
  assert.equal(shelves.length, 2);
  assert.equal(shelves[0].members.length, 3);
  const mixed = setShelves(root, [1], 3);
  assert.equal(panelGroups(mixed, []).filter((g) => g.direction === "rows").length, 2 + 3 + 2);
});

// a section with one shelf: [0] 400 high at z 18, [1] 582 high at z 436
const twoRows = () => ({ direction: "rows", children: [cell({ size: 400 }), cell()], size: null, door_style: "none", hinge_side: "left", one_door: false });
const regionsTwo = { "": [18, 18, 1000, 1000], "0": [18, 18, 1000, 400], "1": [18, 436, 1000, 582] };

test("moving a panel resizes its neighbours", () => {
  const root = movePanels(twoRows(), regionsTwo, [{ split: [], gap: 0 }], 50);
  assert.equal(root.children[0].size, 450);
  assert.equal(root.children[1].size, null);
  assert.throws(() => movePanels(twoRows(), regionsTwo, [{ split: [], gap: 0 }], 570), PlannerError);
});

test("moving aligned shelves moves them in every column", () => {
  const root = setShelves(setDividers(cell(), [], 1), [], 1);
  const regions = { "": [18, 18, 1000, 1000], "0": [18, 18, 491, 1000], "1": [527, 18, 491, 1000], "0.0": [18, 18, 491, 491], "0.1": [18, 527, 491, 491], "1.0": [527, 18, 491, 491], "1.1": [527, 527, 491, 491] };
  const [shelf] = panelGroups(root, []).filter((g) => g.direction === "rows");
  const moved = movePanels(root, regions, shelf.members, -91);
  assert.equal(moved.children[0].children[0].size, 400);
  assert.equal(moved.children[1].children[0].size, 400);
});

test("a door covers everything inside and clears inner fronts", () => {
  let root = setShelves(cell(), [], 2);
  root = setDrawer(root, [0], true);
  root = setDoor(root, [1], "right");
  root = setDoor(root, [], "double");
  assert.ok(root.one_door);
  assert.equal(doorOf(root), "double");
  assert.ok(root.children.every((c) => c.door_style === "none" && c.kind === "open"));
  assert.deepEqual(coveredBy(root, [1]), []);
  assert.throws(() => setDoor(root, [1], "left"), PlannerError);
  assert.throws(() => setDrawer(root, [1], true), PlannerError);
  root = setDoor(root, [], "none");
  assert.ok(!root.one_door);
  assert.equal(coveredBy(root, [1]), null);
});

test("only undivided sections can be drawers", () => {
  assert.throws(() => setDrawer(setShelves(cell(), [], 1), [], true), PlannerError);
  const drawer = setDrawer(door(), [], true);
  assert.ok(isDrawer(drawer));
  assert.equal(drawer.door_style, "none");
  assert.ok(!isDrawer(setDrawer(drawer, [], false)));
  assert.equal(doorOf(setDoor(drawer, [], "left")), "left");
});

test("setSize keeps one flexible sibling", () => {
  const root = setSize(twoRows(), regionsTwo, [1], 300);
  assert.equal(root.children[1].size, 300);
  assert.equal(root.children[0].size, null);
});

test("moving a panel only fixes the compartment before it; automatic siblings keep sharing", () => {
  // three evenly shared compartments: 300 each
  const root = setShelves(cell(), [], 2);
  const regions = { "": [18, 18, 1000, 936], "0": [18, 18, 1000, 300], "1": [18, 336, 1000, 300], "2": [18, 654, 1000, 300] };
  const moved = movePanels(root, regions, [{ split: [], gap: 1 }], 50);
  assert.deepEqual(moved.children.map((c) => c.size), [null, 350, null]);
  // with only two compartments one of them stays flexible
  const two = setShelves(cell(), [], 1);
  const regionsTwo = { "": [18, 18, 1000, 1000], "0": [18, 18, 1000, 491], "1": [18, 527, 1000, 491] };
  assert.deepEqual(movePanels(two, regionsTwo, [{ split: [], gap: 0 }], 9).children.map((c) => c.size), [500, null]);
});

test("setting one size leaves automatic siblings automatic", () => {
  const root = setSize(setShelves(cell(), [], 2), {}, [0], 300);
  assert.deepEqual(root.children.map((c) => c.size), [300, null, null]);
  assert.deepEqual(setSize(root, {}, [0], null).children.map((c) => c.size), [null, null, null]);
});

test("counts are validated", () => {
  assert.throws(() => setShelves(cell(), [], -1), PlannerError);
  assert.throws(() => setDividers(cell(), [], 1.5), PlannerError);
});

test("panel joints are set per group and follow count changes", () => {
  let root = setShelves(setDividers(cell(), [], 1), [], 2);
  const shelves = panelGroups(root, []).filter((g) => g.direction === "rows");
  root = setPanelJoint(root, shelves[1].members, "clamex_p14", "mortise_tenon");
  assert.deepEqual(root.children[0].panel_joints, [null, "clamex_p14"]);
  assert.deepEqual(root.children[1].panel_joints, [null, "clamex_p14"]);
  const groups = panelGroups(root, []).filter((g) => g.direction === "rows");
  assert.deepEqual(panelJointOf(root, groups[0], "mortise_tenon"), { type: "mortise_tenon", params: {} });
  assert.deepEqual(panelJointOf(root, groups[1], "mortise_tenon"), { type: "clamex_p14", params: {} });
  // adding a shelf extends the list, removing trims it
  root = setShelves(root, [], 3);
  assert.deepEqual(root.children[0].panel_joints, [null, "clamex_p14", "clamex_p14"]);
  root = setShelves(root, [], 1);
  assert.deepEqual(root.children[0].panel_joints, [null]);
  assert.throws(() => setPanelJoint(root, groups[0].members, "glue", "mortise_tenon"), PlannerError);
});

test("joint parameters are set per group, reset with null, and dropped when the type changes", () => {
  let root = setShelves(setDividers(cell(), [], 1), [], 1);
  const [shelf] = panelGroups(root, []).filter((g) => g.direction === "rows");
  root = setPanelJointParam(root, shelf.members, "tenon_count", 3, "mortise_tenon");
  assert.deepEqual(root.children[0].panel_joints, [{ type: "mortise_tenon", tenon_count: 3 }]);
  assert.deepEqual(root.children[1].panel_joints, [{ type: "mortise_tenon", tenon_count: 3 }]);
  assert.deepEqual(panelJointOf(root, shelf, "x"), { type: "mortise_tenon", params: { tenon_count: 3 } });
  root = setPanelJoint(root, shelf.members, "mortise_tenon", "mortise_tenon");
  assert.deepEqual(root.children[0].panel_joints, [{ type: "mortise_tenon", tenon_count: 3 }]);
  root = setPanelJointParam(root, shelf.members, "tenon_count", null, "mortise_tenon");
  assert.deepEqual(root.children[0].panel_joints, ["mortise_tenon"]);
  root = setPanelJointParam(root, shelf.members, "distance", 80, "mortise_tenon");
  root = setPanelJoint(root, shelf.members, "clamex_p14", "mortise_tenon");
  assert.deepEqual(root.children[0].panel_joints, ["clamex_p14"]);
  // copies made when adding shelves are independent
  root = setPanelJointParam(root, shelf.members, "distance", 70, "mortise_tenon");
  root = setShelves(root, [], 2);
  root.children[0].panel_joints[1].distance = 1;
  assert.equal(root.children[0].panel_joints[0].distance, 70);
});

test("per-section options: set, reset, and hinges stay with the door when dividing", () => {
  let root = setOption(door(), [], "hinges", "hinge_count", 2);
  assert.deepEqual(root.hinges, { hinge_count: 2 });
  root = setShelves(root, [], 2);
  assert.deepEqual(root.hinges, { hinge_count: 2 });
  assert.ok(root.children.every((c) => !c.hinges));
  root = setShelves(root, [], 0);
  assert.deepEqual(root.hinges, { hinge_count: 2 });
  root = setOption(root, [], "hinges", "hinge_count", null);
  assert.equal(root.hinges, null);
  let drawers = setShelves(cell({ kind: "drawer", drawer: { bottom_offset: 12 } }), [], 1);
  assert.ok(hasStackedDrawers(drawers));
  assert.ok(drawers.children.every((c) => c.drawer.bottom_offset === 12));
  drawers = setField(drawers, [], "drawer_shelves", true);
  assert.equal(drawers.drawer_shelves, true);
  assert.ok(!hasStackedDrawers(setShelves(cell(), [], 1)));
});

test("drawers fill a section, replace its shelves, and apply to every column", () => {
  let root = setDrawers(cell(), [], 3);
  assert.equal(root.direction, "rows");
  assert.ok(root.children.every(isDrawer));
  assert.equal(drawersOf(root), 3);
  assert.equal(shelvesOf(root), 2);
  root = setDrawers(root, [], 1);
  assert.ok(isDrawer(root));
  root = setDrawers(root, [], 0);
  assert.ok(!isDrawer(root) && !isSplit(root));
  // replacing shelves
  root = setDrawers(setShelves(cell(), [], 2), [], 2);
  assert.equal(drawersOf(root), 2);
  // per column
  root = setDrawers(setDividers(cell(), [], 1), [], 2);
  assert.equal(root.direction, "columns");
  assert.ok(root.children.every((c) => drawersOf(c) === 2));
  assert.equal(drawersOf(root), 2);
  root = setDrawerShelves(root, [], true);
  assert.ok(root.children.every((c) => c.drawer_shelves === true));
  root = setDrawerShelfWidth(root, [], 80);
  assert.ok(root.children.every((c) => c.drawer_shelf_width === 80));
  // the rails stay when the drawer count changes
  assert.equal(setDrawers(root, [], 3).children[0].drawer_shelf_width, 80);
  root = setDrawerShelfWidth(root, [], null);
  assert.ok(root.children.every((c) => c.drawer_shelf_width === null));
  // drawer settings carry over when the count changes
  let one = setDrawers(cell(), [], 1);
  one.drawer = { bottom_offset: 12 };
  assert.ok(setDrawers(one, [], 3).children.every((c) => c.drawer.bottom_offset === 12));
  // not behind a door
  const covered = setShelves(door(), [], 1);
  assert.throws(() => setDrawers(covered, [0], 2), PlannerError);
  assert.equal(drawersOf(setShelves(cell(), [], 1)), 0);
});

test("section types switch between open, door and drawers", () => {
  let root = setShelves(cell(), [], 2);
  assert.equal(typeOf(root), "open");
  root = setType(root, [], "door");
  assert.equal(typeOf(root), "door");
  assert.equal(doorOf(root), "left");
  assert.equal(shelvesOf(root), 2); // the shelves stay, behind the door
  root = setType(root, [], "drawers");
  assert.equal(typeOf(root), "drawers");
  assert.ok(isDrawer(root) && !hasDoorLike(root));
  root = setDrawers(root, [], 3);
  root = setType(root, [], "drawers"); // no change
  assert.equal(drawersOf(root), 3);
  root = setType(root, [], "open");
  assert.equal(typeOf(root), "open");
  assert.throws(() => setType(root, [], "window"), PlannerError);
  const covered = setShelves(door(), [], 1);
  assert.throws(() => setType(covered, [0], "door"), PlannerError);
  assert.throws(() => setType(covered, [0], "drawers"), PlannerError);
});

function hasDoorLike(node) {
  return node.door_style && node.door_style !== "none";
}

test("drawer settings apply to every drawer of a section", () => {
  let root = setDrawers(setDividers(cell(), [], 1), [], 2);
  root = setDrawerOption(root, [], "bottom_offset", 15);
  assert.ok(root.children.every((c) => c.children.every((d) => d.drawer.bottom_offset === 15)));
  assert.equal(firstDrawer(root).drawer.bottom_offset, 15);
  root = setDrawerOption(root, [], "bottom_offset", null);
  assert.equal(firstDrawer(root).drawer, null);
  assert.equal(firstDrawer(setShelves(cell(), [], 1)), null);
});

test("shelf holes are inherited and switched per section", () => {
  let root = setDividers(cell(), [], 1);
  assert.equal(shelfHolesOf(root, [0]), false);
  root = setShelfHoles(root, [], true);
  assert.equal(shelfHolesOf(root, [0]), true);
  root = setShelfHoles(root, [1], false);
  assert.equal(shelfHolesOf(root, [0]), true);
  assert.equal(shelfHolesOf(root, [1]), false);
  // switching a section clears what's set inside it
  root = setShelfHoles(root, [], true);
  assert.equal(root.children[1].shelf_holes, null);
  assert.equal(shelfHolesOf(root, [1]), true);
});
