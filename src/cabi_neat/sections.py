"""Convert an interior tree into *section form*, the form the 2D planner edits.

In section form every node is a section with an explicit front:

- ``door_style`` is set on every node: ``"none"`` for no front of its own, otherwise the section has its own door(s)
  (a :class:`Split` then also has ``one_door=True``) and everything inside it is behind that door;
- drawers are drawer :class:`Cell` s, never inside a section with a door;
- nothing inherits a door style, so compartments are never merged under a shared door implicitly.

Splits that only wrap a single child are collapsed into it. Sizes are kept: flexible ones (``None``) stay flexible, so
they redistribute when their parent changes. The resulting geometry is identical to the original.
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
    return config.copy(interior=_collapse(interior).to_data())


def _collapse(node):
    """Replace splits that only wrap a single child (and have no door of their own) by that child.

    E.g. a cabinet described by ``columns`` with a single column: the column covers the whole interior, so it's not a
    section of its own.
    """
    if not isinstance(node, Split):
        return node
    node.children = [_collapse(c) for c in node.children]
    if len(node.children) == 1 and not node.one_door:
        child = node.children[0]
        child.size = node.size
        return child
    return node


def _extent(regions, path, direction):
    x, z, w, h = regions[path_key(path)]
    return h if direction == SplitDirection.ROWS else w


def _section_cell(cell, style, hinge, covered):
    style = cell.door_style or style
    hinge = cell.hinge_side or hinge
    if cell.kind == ZoneKind.DRAWER:
        return Cell(ZoneKind.DRAWER, door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT, drawer=cell.drawer)
    if covered:
        return Cell(door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT)
    return Cell(door_style=style, hinge_side=hinge, hinges=cell.hinges)


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
            if any(node.children[i].size is not None for i in indices):
                # keep the members where they are; the last one takes whatever the group's size leaves
                for k, i in enumerate(indices[:-1]):
                    cells[k].size = _extent(regions, path + [i], node.direction)
            s, h = resolved(first)
            children.append(Split(node.direction, cells, door_style=s, hinge_side=h, one_door=True, panel_joints=joints(indices[:-1]), hinges=first.hinges))
        elif isinstance(first, Split):
            children.append(_section_split(first, path + [indices[0]], regions, style, hinge, t, covered or own_door))
        else:
            children.append(_section_cell(first, style, hinge, covered or own_door))

    # sizes: every child keeps its own (None = shares the space evenly, and redistributes when the parent changes).
    # A merged group needs its current extent (its members' extents plus the panels between them), unless it holds all
    # of the split's flexible space, in which case it can stay flexible too.
    others_flexible = any(len(g["indices"]) == 1 and node.children[g["indices"][0]].size is None for g in groups)
    for group, child in zip(groups, children):
        indices = group["indices"]
        if len(indices) == 1:
            child.size = node.children[indices[0]].size
        elif others_flexible or any(node.children[i].size is not None for i in indices):
            child.size = sum(_extent(regions, path + [i], node.direction) for i in indices) + t * (len(indices) - 1)
        else:
            child.size = None

    # the panel after each group is the one after its last member
    panel_joints = joints([group["indices"][-1] for group in groups[:-1]])
    if own_door:
        return Split(
            node.direction, children, door_style=style, hinge_side=hinge, one_door=True, panel_joints=panel_joints, hinges=node.hinges, drawer_shelves=node.drawer_shelves
        )
    return Split(node.direction, children, door_style=DoorStyle.NONE, hinge_side=HingeSide.LEFT, panel_joints=panel_joints, drawer_shelves=node.drawer_shelves)
