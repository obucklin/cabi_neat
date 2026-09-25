"""Resolve a :class:`CabinetConfig` into axis-aligned part boxes.

This module is pure Python (no compas_timber / OCC), so the layout logic can be tested and reasoned about on its own.

Coordinate system: x = width (left to right), y = depth (front = 0, back = +depth), z = height.
The origin is the front-bottom-left corner of the carcass. Overlay fronts sit at negative y.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .config import CabinetConfig
from .config import Cell
from .config import DoorMount
from .config import DoorStyle
from .config import HingeSide
from .config import Split
from .config import SplitDirection
from .config import ZoneKind

AXES = ("x", "y", "z")

CARCASS = ("side", "top", "bottom", "back", "divider", "shelf")
FRONTS = ("door", "drawer_front")
DRAWER_BOX = ("drawer_side", "drawer_box_front", "drawer_back", "drawer_bottom")


@dataclass
class Part:
    """A rectangular panel.

    Parameters
    ----------
    name
        Unique name within the cabinet.
    category
        Part category, see ``CARCASS``, ``FRONTS``, ``DRAWER_BOX``.
    origin
        Minimum corner of the box.
    size
        Extent of the box along x, y, z.
    thickness_axis
        The axis (``"x"``, ``"y"`` or ``"z"``) along which the panel's thickness runs.
    attributes
        Extra information, e.g. ``node`` (path of the interior node that created the part), ``drawer``, ``hinge_side``, ``hinge_point``.
    """

    name: str
    category: str
    origin: Tuple[float, float, float]
    size: Tuple[float, float, float]
    thickness_axis: str
    attributes: Dict = field(default_factory=dict)

    @property
    def thickness(self):
        return self.size[AXES.index(self.thickness_axis)]

    @property
    def max(self):
        return tuple(o + s for o, s in zip(self.origin, self.size))

    @property
    def dimensions(self):
        """(length, width, thickness), with length >= width."""
        i = AXES.index(self.thickness_axis)
        a, b = [s for j, s in enumerate(self.size) if j != i]
        return max(a, b), min(a, b), self.size[i]


@dataclass
class Opening:
    """A rectangle in the front (xz) plane, with what bounds it on each side.

    Bounds are ``"outer"`` (a carcass side/top/bottom), ``"inner"`` (a divider or shelf) or ``"none"`` (drawers stacked without a shelf).
    """

    x: float
    z: float
    width: float
    height: float
    bounds: Dict[str, str]  # keys: left, right, bottom, top


@dataclass
class Layout:
    """Result of resolving a config: parts plus the joints to create between them."""

    config: CabinetConfig
    parts: List[Part] = field(default_factory=list)
    joints: List[Tuple[str, str]] = field(default_factory=list)  # (main, cross) part names
    joint_kinds: Dict[Tuple[str, str], Tuple[str, Dict]] = field(default_factory=dict)  # (main, cross) -> (kind, parameters), default: butt
    regions: Dict[str, Tuple[float, float, float, float]] = field(default_factory=dict)  # node path key -> (x, z, width, height)

    def add(self, part):
        if any(p.name == part.name for p in self.parts):
            raise RuntimeError("Duplicate part name {!r}.".format(part.name))
        self.parts.append(part)
        return part

    def join(self, main, cross, kind=None, **parameters):
        """Record a joint between two parts. ``kind`` selects a special joint (e.g. ``"rabbet"``), default is a butt joint."""
        self.joints.append((main.name, cross.name))
        if kind:
            self.joint_kinds[(main.name, cross.name)] = (kind, parameters)

    def part(self, name):
        for p in self.parts:
            if p.name == name:
                return p
        raise KeyError(name)

    def by_category(self, *categories):
        return [p for p in self.parts if p.category in categories]


def distribute(total, sizes, what="size"):
    """Resolve ``None`` entries in ``sizes`` so that all entries sum to ``total``.

    Raises
    ------
    ValueError
        If the sizes can't be satisfied with positive values.
    """
    if not sizes:
        raise ValueError("At least one {} is required.".format(what))
    for s in sizes:
        if s is not None and s <= 0:
            raise ValueError("{} must be positive, got {}.".format(what.capitalize(), s))
    fixed = sum(s for s in sizes if s is not None)
    free = [i for i, s in enumerate(sizes) if s is None]
    if not free:
        if abs(fixed - total) > 1e-6:
            raise ValueError("Fixed {}s sum to {:.1f} but {:.1f} is available.".format(what, fixed, total))
        return list(sizes)
    share = (total - fixed) / len(free)
    if share <= 0:
        raise ValueError("Not enough space: {:.1f} available for {}s, {:.1f} already used by fixed ones.".format(total, what, fixed))
    return [share if s is None else s for s in sizes]


def resolve_layout(config):
    """Compute all parts and joints of a cabinet.

    Parameters
    ----------
    config : :class:`CabinetConfig`

    Returns
    -------
    :class:`Layout`

    Raises
    ------
    ValueError
        If the configuration is geometrically impossible.
    """
    c = config
    W, H, D, t, bt = c.width, c.height, c.depth, c.thickness, c.back_thickness
    if min(W, H, D, t, bt, c.front_thickness) <= 0:
        raise ValueError("Dimensions and thicknesses must be positive.")
    if W <= 2 * t or H <= 2 * t or D <= bt:
        raise ValueError("Cabinet is too small for its panel thickness.")

    layout = Layout(config=c)
    inner_depth = D - bt
    inset = c.door_mount == DoorMount.INSET
    # inset fronts sit inside the carcass, so shelves and drawer boxes start behind them
    front_depth = c.inset_setback + c.front_thickness if inset else 0.0
    if front_depth >= inner_depth:
        raise ValueError("Cabinet is too shallow for inset fronts.")

    # carcass: sides run full height; the back sits between them, full height and flush with the rear;
    # top and bottom sit between the sides and in front of the back
    left = layout.add(Part("side_left", "side", (0, 0, 0), (t, D, H), "x"))
    right = layout.add(Part("side_right", "side", (W - t, 0, 0), (t, D, H), "x"))
    bottom = layout.add(Part("bottom", "bottom", (t, 0, 0), (W - 2 * t, D - bt, t), "z"))
    top = layout.add(Part("top", "top", (t, 0, H - t), (W - 2 * t, D - bt, t), "z"))
    back = layout.add(Part("back", "back", (t, D - bt, 0), (W - 2 * t, bt, H), "y"))
    for panel in (bottom, top):
        for side in (left, right):
            if c.carcass_joint == "clamex_p14":
                layout.join(panel, side, "clamex_p14", distance=c.clamex_distance, center_connector=c.clamex_center)
            else:
                layout.join(panel, side)
    for side in (left, right):
        if c.back_joint == "rabbet":
            layout.join(back, side, "rabbet", rabbet_depth=c.rabbet_depth)
        else:
            layout.join(back, side)
    for panel in (bottom, top):
        layout.join(panel, back)

    root = c.interior_tree()
    root_is_cell = isinstance(root, Cell)
    if root_is_cell:
        root = Split(SplitDirection.ROWS, [root])
    panels = {"left": left, "right": right, "bottom": bottom, "top": top}
    bounds = {"left": "outer", "right": "outer", "bottom": "outer", "top": "outer"}
    _resolve_split(layout, root, [], (t, t, W - 2 * t, H - 2 * t), panels, bounds, back, front_depth, c.door_style, c.hinge_side, covered=False)
    if root_is_cell:
        # the wrapper split is internal: report the cell as the root region
        layout.regions = {"": layout.regions["0"]}
    return layout


def _name(prefix, *indices):
    return "_".join([prefix] + [str(i) for i in indices])


def path_key(path):
    """Key of an interior node in :attr:`Layout.regions`: child indices joined by dots, ``""`` for the root."""
    return ".".join(str(i) for i in path)


def _resolve_split(layout, node, path, rect, panels, bounds, back, front_depth, style, hinge, covered):
    """Place the panels, drawers and fronts of ``node`` within ``rect`` = (x, z, width, height).

    ``panels`` and ``bounds`` describe what surrounds ``rect`` on each side. ``covered`` is True when a single door
    already covers ``rect``, so panels inside are set back behind it and no further fronts are added.
    """
    c = layout.config
    t = c.thickness
    inner_depth = c.depth - c.back_thickness
    x, z, w, h = rect
    style = node.door_style or style
    hinge = node.hinge_side or hinge
    children = node.children
    if not children:
        raise ValueError("Split {} has no children.".format(_name("at", *path) if path else "at the root"))
    is_rows = node.direction == SplitDirection.ROWS
    layout.regions[path_key(path)] = tuple(rect)

    if node.one_door and not covered and style != DoorStyle.NONE:
        _add_door(
            layout, _name("door", *path) if path else "door", Opening(x, z, w, h, dict(bounds)), style, hinge, {"node": "_".join(map(str, path))}, panels, c.hinge_options(node)
        )
        covered = True

    def child_style(child):
        return child.door_style or style, child.hinge_side or hinge

    def is_drawer(child):
        return isinstance(child, Cell) and child.kind == ZoneKind.DRAWER

    # runs of consecutive open cells sharing one door (rows only), as lists of child indices
    runs = []
    if not covered:
        for i, child in enumerate(children):
            if not (isinstance(child, Cell) and child.kind == ZoneKind.OPEN and child_style(child)[0] != DoorStyle.NONE):
                continue
            # a compartment with its own door style gets its own door; those inheriting theirs share one when stacked
            inherits = child.door_style is None and children[i - 1].door_style is None if i > 0 else False
            if is_rows and runs and runs[-1][-1] == i - 1 and inherits and child_style(children[i - 1]) == child_style(child):
                runs[-1].append(i)
            else:
                runs.append([i])
    run_of = {i: ri for ri, run in enumerate(runs) for i in run}

    # a panel separates consecutive children, except between two drawers stacked in a rows split
    drawer_shelves = c.shelves_between_drawers if node.drawer_shelves is None else node.drawer_shelves
    has_panel = [not (is_rows and is_drawer(a) and is_drawer(b)) or drawer_shelves for a, b in zip(children, children[1:])]
    available = (h if is_rows else w) - sum(has_panel) * t
    sizes = [ch.size if ch.size is not None else (c.drawer_height if is_rows and is_drawer(ch) else None) for ch in children]
    sizes = distribute(available, sizes, "row height" if is_rows else "column width")

    before, after = ("bottom", "top") if is_rows else ("left", "right")
    child_panel_sets = []  # the panels around each child
    prev_panel, prev_bound = panels[before], bounds[before]
    openings = []
    pos = z if is_rows else x
    for i, (child, size) in enumerate(zip(children, sizes)):
        child_rect = (x, pos, w, size) if is_rows else (pos, z, size, h)
        child_panels = dict(panels, **{before: prev_panel})
        child_bounds = dict(bounds, **{before: prev_bound})
        pos += size
        if i < len(children) - 1:
            if has_panel[i]:
                # with inset fronts, a panel hidden behind a single door is set back behind it; panels between fronts come forward flush
                set_back = covered or (i in run_of and run_of.get(i + 1) == run_of[i])
                y0 = front_depth if set_back else 0.0
                attrs = {"node": "_".join(map(str, path)), "split": path_key(path), "gap": i}
                if is_rows:
                    panel = layout.add(Part(_name("shelf", *path, i), "shelf", (x, y0, pos), (w, inner_depth - y0, t), "z", attrs))
                    ends = (panels["left"], panels["right"])
                else:
                    panel = layout.add(Part(_name("divider", *path, i), "divider", (pos, y0, z), (t, inner_depth - y0, h), "x", attrs))
                    ends = (panels["bottom"], panels["top"])
                joint, parameters = node.panel_joint(i, c.interior_joint)
                parameters = dict(c.joint_defaults()[joint], **parameters)
                panel.attributes["joint"] = joint
                panel.attributes["joint_parameters"] = parameters
                for end in ends:
                    _join_interior(layout, panel, end, joint, parameters)
                layout.join(panel, back)
                prev_panel, prev_bound = panel, "inner"
                pos += t
            else:
                prev_panel, prev_bound = None, "none"
            child_panels[after], child_bounds[after] = prev_panel, prev_bound
        else:
            child_panels[after], child_bounds[after] = panels[after], bounds[after]
        child_panel_sets.append(dict(child_panels))

        child_path = path + [i]
        if isinstance(child, Split):
            cs, ch = child_style(child)
            _resolve_split(layout, child, child_path, child_rect, child_panels, child_bounds, back, front_depth, cs, ch, covered)
            openings.append(None)
            continue
        opening = Opening(*child_rect, child_bounds)
        openings.append(opening)
        layout.regions[path_key(child_path)] = tuple(child_rect)
        if child.kind == ZoneKind.DRAWER:
            if covered:
                raise ValueError("Drawer {} is behind a door.".format("_".join(map(str, child_path))))
            _add_drawer(layout, "_".join(map(str, child_path)), opening, front_depth, c.drawer_options(child))

    for ri, run in enumerate(runs):
        first, last = openings[run[0]], openings[run[-1]]
        if is_rows:
            merged = Opening(first.x, first.z, first.width, last.z + last.height - first.z, dict(first.bounds, top=last.bounds["top"]))
        else:
            merged = first
        cs, ch = child_style(children[run[0]])
        # named after the first compartment it covers, so it can't clash with a door covering a whole split
        sides = {"left": child_panel_sets[run[0]]["left"], "right": child_panel_sets[run[-1]]["right"]}
        _add_door(layout, _name("door", *path, run[0]), merged, cs, ch, {"node": "_".join(map(str, path))}, sides, c.hinge_options(children[run[0]]))


def _join_interior(layout, panel, other, joint, parameters):
    """Join a shelf or divider to the panel at one of its ends with ``joint`` (see ``INTERIOR_JOINTS``)."""
    if joint == "butt":
        layout.join(panel, other)
    elif joint == "stopped_dado":
        # open at the back (+y) so the panel slides in from behind; stopped at the front
        layout.join(panel, other, joint, open_direction=(0.0, 1.0, 0.0), **parameters)
    else:
        layout.join(panel, other, joint, **parameters)


def _add_door(layout, name, opening, style, hinge, attrs, panels, hinges=None):
    """Add a single door or a pair of doors covering ``opening``, hung with cup hinges on the ``panels`` (left/right) around it."""
    c = layout.config
    fx, fz, fw, fh = _front_rect(c, opening)
    fy = _front_y(c)
    if style == DoorStyle.SINGLE:
        leaves = [(name, fx, fw, hinge)]
    else:
        lw = (fw - c.front_gap) / 2.0
        if lw <= 0:
            raise ValueError("Opening for {} is too narrow for double doors.".format(name))
        leaves = [(name + "_l", fx, lw, HingeSide.LEFT), (name + "_r", fx + lw + c.front_gap, lw, HingeSide.RIGHT)]
    for leaf_name, lx, lw, side in leaves:
        hinge_x = lx if side == HingeSide.LEFT else lx + lw
        hinge_panel = panels[side.value]
        leaf_attrs = dict(attrs, hinge_side=side.value, hinge_point=(hinge_x, fy, fz), hinge_axis=(0.0, 0.0, 1.0), hinge_panel=hinge_panel.name)
        door = layout.add(Part(leaf_name, "door", (lx, fy, fz), (lw, c.front_thickness, fh), "y", leaf_attrs))
        layout.join(door, hinge_panel, "cup_hinge", mount=c.door_mount.value, **(hinges or {}))


def _front_y(c):
    """y of the front face of doors and drawer fronts."""
    return c.inset_setback if c.door_mount == DoorMount.INSET else -c.front_thickness


def _front_rect(c, opening):
    """(x, z, width, height) of the front covering ``opening``."""
    t, g = c.thickness, c.front_gap
    if c.door_mount == DoorMount.INSET:
        x, z, w, h = opening.x + g, opening.z + g, opening.width - 2 * g, opening.height - 2 * g
        # stacked drawers without a shelf share the gap between them
        if opening.bounds["bottom"] == "none":
            z, h = z - g / 2.0, h + g / 2.0
        if opening.bounds["top"] == "none":
            h += g / 2.0
    else:
        grow = {"outer": t, "inner": t / 2.0, "none": 0.0}
        b = opening.bounds
        # overlay fronts cover the surrounding panels: fully at the carcass edge, half on shared panels
        x0 = opening.x - grow[b["left"]] + g / 2.0
        x1 = opening.x + opening.width + grow[b["right"]] - g / 2.0
        z0 = opening.z - grow[b["bottom"]] + g / 2.0
        z1 = opening.z + opening.height + grow[b["top"]] - g / 2.0
        x, z, w, h = x0, z0, x1 - x0, z1 - z0
    if w <= 0 or h <= 0:
        raise ValueError("Opening is too small for a front.")
    return x, z, w, h


def _add_drawer(layout, key, opening, front_depth, options):
    """Add a drawer box and front in ``opening``, with its settings ``options`` (see :meth:`CabinetConfig.drawer_options`)."""
    c = layout.config
    st, bt, clr = c.drawer_panel_thickness, c.drawer_bottom_thickness, c.drawer_clearance
    inner_depth = c.depth - c.back_thickness

    fx, fz, fw, fh = _front_rect(c, opening)
    bw = opening.width - 2 * clr
    # the box starts at the bottom of the opening, but never below its front (inset fronts, stacked drawers), and
    # stops the top clearance below the opening's top
    bz = max(opening.z, fz)
    bh = opening.z + opening.height - c.drawer_top_clearance - bz
    by = front_depth
    bd = c.drawer_depth if c.drawer_depth is not None else inner_depth - by - c.drawer_rear_clearance
    if bw <= 2 * st or bh <= options["bottom_offset"] + bt or bd <= 2 * st:
        raise ValueError("Drawer {} is too small ({:.0f} x {:.0f} x {:.0f}).".format(key, bw, bh, bd))
    if by + bd > inner_depth:
        raise ValueError("Drawer depth {:.0f} exceeds the interior depth.".format(bd))
    bx = opening.x + clr

    attrs = {"node": key, "drawer": key, "travel": 0.75 * bd}
    front = layout.add(Part("drawer_{}_front".format(key), "drawer_front", (fx, _front_y(c), fz), (fw, c.front_thickness, fh), "y", dict(attrs)))
    side_l = layout.add(Part("drawer_{}_side_l".format(key), "drawer_side", (bx, by, bz), (st, bd, bh), "x", dict(attrs)))
    side_r = layout.add(Part("drawer_{}_side_r".format(key), "drawer_side", (bx + bw - st, by, bz), (st, bd, bh), "x", dict(attrs)))
    box_back = layout.add(Part("drawer_{}_back".format(key), "drawer_back", (bx + st, by + bd - st, bz), (bw - 2 * st, st, bh), "y", dict(attrs)))
    if options["front_one_piece"]:
        # the visible front is also the box front: the sides are fixed to its back face with Clamex connectors
        box_front = front
        bottom_y = by
        for panel in (side_l, side_r):
            layout.join(panel, front, "clamex_p14", connector_count=1, dowels=True)
    else:
        # a box front, rabbeted into the sides like the back; the visible front is fixed to it
        box_front = layout.add(Part("drawer_{}_box_front".format(key), "drawer_box_front", (bx + st, by, bz), (bw - 2 * st, st, bh), "y", dict(attrs)))
        bottom_y = by + st
        for side in (side_l, side_r):
            layout.join(box_front, side, "rabbet", rabbet_depth=None)
    for side in (side_l, side_r):
        layout.join(box_back, side, "rabbet", rabbet_depth=None)
    # the bottom sits in grooves, raised off the box's lower edge; they run through the sides and stop in the front and back
    bottom_z = bz + options["bottom_offset"]
    box_bottom = layout.add(Part("drawer_{}_bottom".format(key), "drawer_bottom", (bx + st, bottom_y, bottom_z), (bw - 2 * st, by + bd - st - bottom_y, bt), "z", dict(attrs)))
    for panel in (side_l, side_r):
        layout.join(box_bottom, panel, "groove", depth=options["groove_depth"], through=True)
    # stopped grooves in the front and back, exactly as long as the bottom's edge including its corners in the side grooves
    for panel in (box_front, box_back):
        layout.join(box_bottom, panel, "groove", depth=options["groove_depth"], through=False)


def part_category_group(category) -> Optional[str]:
    """``"carcass"``, ``"front"`` or ``"drawer_box"``."""
    if category in CARCASS:
        return "carcass"
    if category in FRONTS:
        return "front"
    if category in DRAWER_BOX:
        return "drawer_box"
    return None
