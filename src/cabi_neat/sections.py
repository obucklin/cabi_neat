"""Convert an interior tree into *section form*, the form the 2D planner edits.

In section form every node is a section with an explicit front:

- ``door_style`` is set on every node: ``"none"`` for no front of its own, otherwise the section has its own door(s)
  (a :class:`Split` then also has ``one_door=True``) and everything inside it is behind that door;
- drawers are drawer :class:`Cell` s, never inside a section with a door;
- nothing inherits a door style, so compartments are never merged under a shared door implicitly.

Sizes are frozen from the resolved layout: every child gets its current size, except the last child of each split,
which stays flexible (``None``) so the cabinet can still be resized. The resulting geometry is identical to the original.
"""

from .config import Cell
from .config import DoorStyle
from .config import HingeSide
from .config import Split
from .config import SplitDirection
from .config import ZoneKind
from .layout import path_key
from .layout import resolve_layout


def sectioned(config):
    """Return a copy of ``config`` whose ``interior`` is in section form.

    Raises
    ------
    ValueError
        If ``config`` can't be resolved.
    """
    layout = resolve_layout(config)
    root = config.interior_tree()
    regions = dict(layout.regions)
    if isinstance(root, Cell):
        interior = _section_cell(root, config.door_style, config.hinge_side, covered=False)
    else:
        interior = _section_split(root, [], regions, config.door_style, config.hinge_side, config.thickness, covered=False)
    return config.copy(interior=interior.to_data())


def _extent(regions, path, direction):
    x, z, w, h = regions[path_key(path)]
    return h if direction == SplitDirection.ROWS else w


def _section_cell(cell, style, hinge, covered):
    style = cell.door_style or style
    hinge = cell.hinge_side or hinge
    if cell.kind == ZoneKind.DRAWER:
        return Cell(ZoneKind.DRAWER, door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT)
    if covered:
        return Cell(door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT)
    return Cell(door_style=style, hinge_side=hinge)


def _section_split(node, path, regions, style, hinge, t, covered):
    style = node.door_style or style
    hinge = node.hinge_side or hinge
    own_door = node.one_door and style != DoorStyle.NONE and not covered

    def resolved(child):
        return child.door_style or style, child.hinge_side or hinge

    # group children: stacked open compartments that inherit a door share it, as the resolver would do
    groups = []
    for i, child in enumerate(node.children):
        mergeable = (
            not covered
            and not own_door
            and node.direction == SplitDirection.ROWS
            and isinstance(child, Cell)
            and child.kind == ZoneKind.OPEN
            and child.door_style is None
            and resolved(child)[0] != DoorStyle.NONE
        )
        if mergeable and groups and groups[-1]["mergeable"] and resolved(node.children[groups[-1]["indices"][-1]]) == resolved(child):
            groups[-1]["indices"].append(i)
        else:
            groups.append({"indices": [i], "mergeable": mergeable})

    def joints(gaps):
        """The panel joints of ``node`` at the given gap indices (None if the node has none)."""
        return [node.panel_joints[g] for g in gaps] if node.panel_joints is not None else None

    children = []
    for group in groups:
        indices = group["indices"]
        first = node.children[indices[0]]
        if len(indices) > 1:
            cells = [Cell(door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT) for _ in indices]
            for k, i in enumerate(indices[:-1]):
                cells[k].size = _extent(regions, path + [i], node.direction)
            s, h = resolved(first)
            children.append(Split(node.direction, cells, door_style=s, hinge_side=h, one_door=True, panel_joints=joints(indices[:-1])))
        elif isinstance(first, Split):
            children.append(_section_split(first, path + [indices[0]], regions, style, hinge, t, covered or own_door))
        else:
            children.append(_section_cell(first, style, hinge, covered or own_door))

    # freeze sizes: each group's extent is its members' extents plus the panels between them
    for gi, (group, child) in enumerate(zip(groups, children)):
        if gi == len(groups) - 1:
            child.size = None
        else:
            indices = group["indices"]
            child.size = sum(_extent(regions, path + [i], node.direction) for i in indices) + t * (len(indices) - 1)

    # the panel after each group is the one after its last member
    panel_joints = joints([group["indices"][-1] for group in groups[:-1]])
    if own_door:
        return Split(node.direction, children, door_style=style, hinge_side=hinge, one_door=True, panel_joints=panel_joints)
    return Split(node.direction, children, door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT, panel_joints=panel_joints)
