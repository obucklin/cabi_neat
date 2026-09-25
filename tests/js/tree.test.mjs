// Run with: node --test tests/js/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  coveredBy,
  dividersOf,
  doorOf,
  freeze,
  getNode,
  isDrawer,
  isSplit,
  movePanels,
  panelGroups,
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

test("freeze keeps the last child flexible", () => {
  assert.deepEqual(freeze(twoRows(), regionsTwo).children.map((c) => c.size), [400, null]);
});

test("counts are validated", () => {
  assert.throws(() => setShelves(cell(), [], -1), PlannerError);
  assert.throws(() => setDividers(cell(), [], 1.5), PlannerError);
});
