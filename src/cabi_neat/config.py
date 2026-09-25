"""Parameters describing a cabinet.

All dimensions are in millimetres.
"""

import json
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from dataclasses import replace
from enum import Enum
from typing import List
from typing import Optional
from typing import Union


class DoorMount(str, Enum):
    """How fronts (doors and drawer fronts) sit relative to the carcass."""

    OVERLAY = "overlay"  # in front of the carcass, covering its front edges
    INSET = "inset"  # inside the opening, flush with the carcass front


class DoorStyle(str, Enum):
    """Number of door leaves covering an opening."""

    NONE = "none"
    SINGLE = "single"
    DOUBLE = "double"


class HingeSide(str, Enum):
    """Side on which a single door is hinged."""

    LEFT = "left"
    RIGHT = "right"


class SplitDirection(str, Enum):
    """How a :class:`Split` arranges its children."""

    ROWS = "rows"  # bottom to top, separated by shelves
    COLUMNS = "columns"  # left to right, separated by vertical dividers


class ZoneKind(str, Enum):
    """What a zone of a column contains."""

    OPEN = "open"  # a shelf compartment, covered by a door if the column has one
    DRAWER = "drawer"


@dataclass
class Zone:
    """A compartment in a column, bounded by shelves (or the carcass top/bottom).

    Parameters
    ----------
    kind
        What the zone contains.
    height
        Clear height of the zone. ``None`` shares the remaining height equally with other unsized zones.
    """

    kind: ZoneKind = ZoneKind.OPEN
    height: Optional[float] = None

    def __post_init__(self):
        self.kind = ZoneKind(self.kind)

    def to_data(self):
        return {"kind": self.kind.value, "height": self.height}

    @classmethod
    def from_data(cls, data):
        return cls(**data)


@dataclass
class Column:
    """A vertical section of the cabinet, separated from its neighbours by dividers.

    Parameters
    ----------
    zones
        Zones from bottom to top. Consecutive zones are separated by a shelf.
    width
        Clear width of the column. ``None`` shares the remaining width equally with other unsized columns.
    door_style
        Overrides the cabinet's door style for this column.
    hinge_side
        Overrides the cabinet's hinge side for single doors in this column.
    """

    zones: List[Zone] = field(default_factory=lambda: [Zone()])
    width: Optional[float] = None
    door_style: Optional[DoorStyle] = None
    hinge_side: Optional[HingeSide] = None

    def __post_init__(self):
        self.zones = [z if isinstance(z, Zone) else Zone.from_data(z) for z in self.zones]
        if self.door_style is not None:
            self.door_style = DoorStyle(self.door_style)
        if self.hinge_side is not None:
            self.hinge_side = HingeSide(self.hinge_side)

    def to_data(self):
        return {
            "zones": [z.to_data() for z in self.zones],
            "width": self.width,
            "door_style": self.door_style.value if self.door_style else None,
            "hinge_side": self.hinge_side.value if self.hinge_side else None,
        }

    @classmethod
    def from_data(cls, data):
        return cls(**data)


def _enum_value(value):
    return value.value if value is not None else None


@dataclass
class Cell:
    """A compartment of the interior tree, see :class:`Split`.

    Parameters
    ----------
    kind
        What the compartment contains.
    size
        Clear height (in a rows split) or clear width (in a columns split). ``None`` shares the remaining space.
    door_style, hinge_side
        Override the inherited door style / hinge side for this compartment's front.
    """

    kind: ZoneKind = ZoneKind.OPEN
    size: Optional[float] = None
    door_style: Optional[DoorStyle] = None
    hinge_side: Optional[HingeSide] = None

    def __post_init__(self):
        self.kind = ZoneKind(self.kind)
        self.door_style = DoorStyle(self.door_style) if self.door_style is not None else None
        self.hinge_side = HingeSide(self.hinge_side) if self.hinge_side is not None else None

    def to_data(self):
        return {"kind": self.kind.value, "size": self.size, "door_style": _enum_value(self.door_style), "hinge_side": _enum_value(self.hinge_side)}


@dataclass
class Split:
    """A region of the cabinet interior divided into rows (by shelves) or columns (by vertical dividers).

    Splits nest, so any arrangement of shelves and dividers can be described, e.g. a full-width shelf high up with a
    vertical divider below it::

        rows(
            columns(rows("open", "open", "open"), "open"),  # below the high shelf: shelves left, one cavity right
            Cell(size=300),  # above the high shelf: 300 mm clear height
        )

    Parameters
    ----------
    direction
        ``"rows"`` (children bottom to top, separated by shelves) or ``"columns"`` (children left to right, separated by dividers).
    children
        :class:`Split` or :class:`Cell` nodes. Strings ``"open"`` / ``"drawer"`` are shorthand for cells.
        Each child's ``size`` is its clear dimension along the split direction, which fixes the position of the panels between children.
    size
        Clear size of this split within its parent. ``None`` shares the remaining space.
    door_style, hinge_side
        Defaults for fronts in this subtree.
    one_door
        Cover the whole split with a single door (or pair of doors). Panels behind it are set back for inset doors.
        Otherwise, consecutive open cells of a rows split share one door, and open cells of a columns split get one door each.
    """

    direction: SplitDirection = SplitDirection.ROWS
    children: List[Union["Split", Cell]] = field(default_factory=lambda: [Cell()])
    size: Optional[float] = None
    door_style: Optional[DoorStyle] = None
    hinge_side: Optional[HingeSide] = None
    one_door: bool = False

    def __post_init__(self):
        self.direction = SplitDirection(self.direction)
        self.children = [node_from_data(c) for c in self.children]
        self.door_style = DoorStyle(self.door_style) if self.door_style is not None else None
        self.hinge_side = HingeSide(self.hinge_side) if self.hinge_side is not None else None

    def to_data(self):
        return {
            "direction": self.direction.value,
            "children": [c.to_data() for c in self.children],
            "size": self.size,
            "door_style": _enum_value(self.door_style),
            "hinge_side": _enum_value(self.hinge_side),
            "one_door": self.one_door,
        }


def node_from_data(data):
    """Create a :class:`Split` or :class:`Cell` from its data, an existing node, or a kind string."""
    if isinstance(data, (Split, Cell)):
        return data
    if isinstance(data, (str, ZoneKind)):
        return Cell(data)
    if "children" in data:
        return Split(**data)
    return Cell(**data)


def rows(*children, **kwargs):
    """A :class:`Split` stacking ``children`` bottom to top, separated by shelves."""
    return Split(SplitDirection.ROWS, list(children), **kwargs)


def columns(*children, **kwargs):
    """A :class:`Split` placing ``children`` left to right, separated by vertical dividers."""
    return Split(SplitDirection.COLUMNS, list(children), **kwargs)


@dataclass
class CabinetConfig:
    """All parameters of a cabinet.

    Parameters
    ----------
    width, height, depth
        Outer dimensions of the carcass (fronts excluded).
    thickness
        Thickness of carcass panels, shelves and dividers.
    back_thickness
        Thickness of the back panel, which sits between sides, top and bottom, flush with the rear.
    front_thickness
        Thickness of doors and drawer fronts.
    columns
        Columns from left to right. A simple layout, ignored when ``interior`` is set.
    interior
        A tree of :class:`Split` and :class:`Cell` describing any arrangement of shelves and dividers.
    door_mount
        Whether fronts are overlay or inset.
    door_style
        Default door style for columns that don't set their own.
    hinge_side
        Default hinge side for single doors.
    front_gap
        Reveal between neighbouring fronts, and between inset fronts and the carcass.
    inset_setback
        How far inset fronts are set back from the carcass front.
    drawer_side_thickness
        Thickness of drawer box sides, front and back.
    drawer_bottom_thickness
        Thickness of drawer box bottoms.
    drawer_clearance
        Gap between a drawer box side and the carcass (space for runners).
    drawer_top_clearance
        Gap between the top of a drawer box and the zone above.
    drawer_height
        Default clear height of drawer zones that don't set their own. ``None`` shares the height like other zones.
    shelves_between_drawers
        Put a shelf between stacked drawers. Without it, stacked drawers are only separated by the front gap.
    drawer_front_one_piece
        Use the visible drawer front as the front of the drawer box, instead of a separate box front behind it.
    drawer_depth
        Depth of drawer boxes. ``None`` uses the interior depth minus ``drawer_rear_clearance``.
    drawer_rear_clearance
        Gap between an auto-sized drawer box and the back panel.
    """

    width: float = 800.0
    height: float = 1200.0
    depth: float = 500.0
    thickness: float = 18.0
    back_thickness: float = 8.0
    front_thickness: float = 18.0
    columns: List[Column] = field(default_factory=lambda: [Column()])
    interior: Optional[Union[Split, Cell]] = None
    door_mount: DoorMount = DoorMount.OVERLAY
    door_style: DoorStyle = DoorStyle.SINGLE
    hinge_side: HingeSide = HingeSide.LEFT
    front_gap: float = 3.0
    inset_setback: float = 0.0
    drawer_side_thickness: float = 12.0
    drawer_bottom_thickness: float = 8.0
    drawer_clearance: float = 13.0
    drawer_top_clearance: float = 20.0
    drawer_height: Optional[float] = None
    shelves_between_drawers: bool = False
    drawer_front_one_piece: bool = False
    drawer_depth: Optional[float] = None
    drawer_rear_clearance: float = 20.0

    def __post_init__(self):
        self.columns = [c if isinstance(c, Column) else Column.from_data(c) for c in self.columns]
        if self.interior is not None:
            self.interior = node_from_data(self.interior)
        self.door_mount = DoorMount(self.door_mount)
        self.door_style = DoorStyle(self.door_style)
        self.hinge_side = HingeSide(self.hinge_side)

    # ==========================================================================
    # presets
    # ==========================================================================

    @classmethod
    def stack(cls, zones, **kwargs):
        """Single column with an arbitrary stack of zones (bottom to top).

        Parameters
        ----------
        zones : list of :class:`Zone` | str
            Zones, or zone kinds as strings (``"open"``, ``"drawer"``).
        """
        zones = [Zone(z) if isinstance(z, (str, ZoneKind)) else z for z in zones]
        return cls(columns=[Column(zones=zones)], **kwargs)

    @classmethod
    def grid(cls, columns, rows, **kwargs):
        """``columns`` equal columns, each with ``rows`` equal open compartments."""
        return cls(columns=[Column(zones=[Zone() for _ in range(rows)]) for _ in range(columns)], **kwargs)

    @classmethod
    def shelves_and_drawers(cls, shelves, drawers, columns=1, **kwargs):
        """Each column gets ``drawers`` drawers at the bottom and ``shelves`` evenly spaced shelves above.

        Every drawer and every shelf compartment gets an equal share of the height. With drawers, one additional shelf
        separates the drawer section from the shelf section.
        """
        zones = [Zone(ZoneKind.DRAWER) for _ in range(drawers)] + [Zone() for _ in range(shelves + 1)]
        return cls(columns=[Column(zones=list(zones)) for _ in range(columns)], **kwargs)

    # ==========================================================================
    # helpers
    # ==========================================================================

    def copy(self, **changes):
        """Return a deep copy, optionally with some top-level parameters changed."""
        return replace(CabinetConfig.from_data(self.to_data()), **changes)

    def interior_tree(self):
        """The interior as a :class:`Split` tree: ``interior`` if set, otherwise built from ``columns``."""
        if self.interior is not None:
            return self.interior
        return columns(
            *[
                Split(SplitDirection.ROWS, [Cell(z.kind, z.height) for z in col.zones], size=col.width, door_style=col.door_style, hinge_side=col.hinge_side)
                for col in self.columns
            ]
        )

    def clear_door_overrides(self):
        """Remove door style and hinge side overrides from columns and interior nodes."""
        nodes = list(self.columns) + ([self.interior] if self.interior is not None else [])
        while nodes:
            node = nodes.pop()
            node.door_style = None
            node.hinge_side = None
            nodes.extend(getattr(node, "children", []))

    # ==========================================================================
    # serialization
    # ==========================================================================

    def to_data(self):
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "columns":
                value = [c.to_data() for c in value]
            elif f.name == "interior":
                value = value.to_data() if value is not None else None
            elif isinstance(value, Enum):
                value = value.value
            data[f.name] = value
        return data

    @classmethod
    def from_data(cls, data):
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError("Unknown cabinet parameters: {}".format(", ".join(sorted(unknown))))
        return cls(**data)

    def to_json(self, filepath=None, indent=2):
        """Serialize to a JSON string, and write it to ``filepath`` if given."""
        text = json.dumps(self.to_data(), indent=indent)
        if filepath:
            with open(filepath, "w") as f:
                f.write(text)
        return text

    @classmethod
    def from_json(cls, text_or_filepath):
        """Load from a JSON string or file path."""
        text = text_or_filepath
        if not text.lstrip().startswith("{"):
            with open(text_or_filepath) as f:
                text = f.read()
        return cls.from_data(json.loads(text))
