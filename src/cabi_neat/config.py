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


BACK_JOINTS = ("rabbet", "butt")
INTERIOR_JOINTS = ("mortise_tenon", "clamex_p14", "stopped_dado", "butt")
CARCASS_JOINTS = ("butt", "clamex_p14")
# the dimensions of door hinges that can be set in CabinetConfig.hinges (see cabi_neat.joints.CupHingeJoint)
HINGE_DIMENSIONS = (
    "hinge_count",
    "end_distance",
    "cup_diameter",
    "cup_depth",
    "cup_edge_distance",
    "door_hole_spacing",
    "door_hole_offset",
    "door_hole_diameter",
    "door_hole_depth",
    "plate_offset",
    "plate_hole_spacing",
    "plate_hole_diameter",
    "plate_hole_depth",
)
# the parameters each interior joint type takes, as passed to its joint class
JOINT_PARAMETERS = {
    "mortise_tenon": ("depth", "tenon_count"),
    "clamex_p14": ("distance", "center_connector"),
    "stopped_dado": ("depth", "stop_distance"),
    "butt": (),
}


# per-drawer settings (Cell.drawer), each defaulting to the cabinet's field in DRAWER_DEFAULTS
DRAWER_OPTIONS = ("front_one_piece", "bottom_offset", "groove_depth")
DRAWER_DEFAULTS = {"front_one_piece": "drawer_front_one_piece", "bottom_offset": "drawer_bottom_offset", "groove_depth": "drawer_groove_depth"}


def _check_options(options, allowed, what):
    """Validate a dict of per-node options, returning a copy without ``None`` values (or ``None`` if empty)."""
    if not options:
        return None
    unknown = set(options) - set(allowed)
    if unknown:
        raise ValueError("Unknown {}: {}.".format(what, ", ".join(sorted(unknown))))
    options = {k: v for k, v in options.items() if v is not None}
    return options or None


def _check_panel_joint(entry):
    """Validate one entry of :attr:`Split.panel_joints`, returning a copy."""
    if entry is None:
        return None
    joint = entry if isinstance(entry, str) else entry.get("type")
    if joint not in INTERIOR_JOINTS:
        raise ValueError("Panel joints must be one of {}, got {!r}.".format(", ".join(INTERIOR_JOINTS), joint))
    if isinstance(entry, str):
        return entry
    unknown = set(entry) - {"type"} - set(JOINT_PARAMETERS[joint])
    if unknown:
        raise ValueError("Unknown parameters for {}: {}.".format(joint, ", ".join(sorted(unknown))))
    return dict(entry)


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
    hinges
        Hinge dimensions for this compartment's door, over the cabinet's ``hinges`` (see ``HINGE_DIMENSIONS``).
    drawer
        Settings for this drawer, over the cabinet's (see ``DRAWER_OPTIONS``).
    """

    kind: ZoneKind = ZoneKind.OPEN
    size: Optional[float] = None
    door_style: Optional[DoorStyle] = None
    hinge_side: Optional[HingeSide] = None
    hinges: Optional[dict] = None
    drawer: Optional[dict] = None

    def __post_init__(self):
        self.kind = ZoneKind(self.kind)
        self.door_style = DoorStyle(self.door_style) if self.door_style is not None else None
        self.hinge_side = HingeSide(self.hinge_side) if self.hinge_side is not None else None
        self.hinges = _check_options(self.hinges, HINGE_DIMENSIONS, "hinge dimensions")
        self.drawer = _check_options(self.drawer, DRAWER_OPTIONS, "drawer settings")

    def to_data(self):
        return {
            "kind": self.kind.value,
            "size": self.size,
            "door_style": _enum_value(self.door_style),
            "hinge_side": _enum_value(self.hinge_side),
            "hinges": dict(self.hinges) if self.hinges else None,
            "drawer": dict(self.drawer) if self.drawer else None,
        }


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
    panel_joints
        How each panel between consecutive children joins its neighbours, one entry per panel: a joint type (see
        ``INTERIOR_JOINTS``), or a dict with a ``"type"`` and that joint's parameters (see ``JOINT_PARAMETERS``), e.g.
        ``{"type": "clamex_p14", "distance": 80}``. ``None``, or a ``None`` entry, uses the cabinet's ``interior_joint``.
        Parameters that aren't given use the cabinet's defaults (:meth:`CabinetConfig.joint_defaults`).
    hinges
        Hinge dimensions for this split's door (with ``one_door``), over the cabinet's ``hinges``.
    drawer_shelves
        Put shelves between stacked drawers in this split, overriding the cabinet's ``shelves_between_drawers``.
    """

    direction: SplitDirection = SplitDirection.ROWS
    children: List[Union["Split", Cell]] = field(default_factory=lambda: [Cell()])
    size: Optional[float] = None
    door_style: Optional[DoorStyle] = None
    hinge_side: Optional[HingeSide] = None
    one_door: bool = False
    panel_joints: Optional[List[Union[None, str, dict]]] = None
    hinges: Optional[dict] = None
    drawer_shelves: Optional[bool] = None

    def __post_init__(self):
        self.direction = SplitDirection(self.direction)
        self.children = [node_from_data(c) for c in self.children]
        self.door_style = DoorStyle(self.door_style) if self.door_style is not None else None
        self.hinge_side = HingeSide(self.hinge_side) if self.hinge_side is not None else None
        if self.panel_joints is not None:
            if len(self.panel_joints) != len(self.children) - 1:
                raise ValueError("panel_joints needs one entry per panel: {} for {} children.".format(len(self.children) - 1, len(self.children)))
            self.panel_joints = [_check_panel_joint(j) for j in self.panel_joints]
        self.hinges = _check_options(self.hinges, HINGE_DIMENSIONS, "hinge dimensions")

    def panel_joint(self, gap, default):
        """``(type, parameters)`` of the panel after child ``gap``, or ``(default, {})``."""
        entry = self.panel_joints[gap] if self.panel_joints is not None else None
        if entry is None:
            return default, {}
        if isinstance(entry, str):
            return entry, {}
        parameters = dict(entry)
        return parameters.pop("type"), parameters

    def to_data(self):
        return {
            "direction": self.direction.value,
            "children": [c.to_data() for c in self.children],
            "size": self.size,
            "door_style": _enum_value(self.door_style),
            "hinge_side": _enum_value(self.hinge_side),
            "one_door": self.one_door,
            "panel_joints": [dict(j) if isinstance(j, dict) else j for j in self.panel_joints] if self.panel_joints is not None else None,
            "hinges": dict(self.hinges) if self.hinges else None,
            "drawer_shelves": self.drawer_shelves,
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
    back_joint
        How the back joins the sides: ``"rabbet"`` (the back sits in a rabbet cut into each side) or ``"butt"``.
    rabbet_depth
        Depth of the rabbet in the sides. ``None`` uses half the side thickness.
    carcass_joint
        How the top and bottom join the sides: ``"butt"`` or ``"clamex_p14"`` (Lamello Clamex P-14 connectors).
    clamex_distance
        Distance from each end of a joining edge to the nearest Clamex connector.
    clamex_center
        Add a Clamex connector in the middle of each joining edge.
    interior_joint
        Default for how shelves and dividers join the carcass and each other: ``"mortise_tenon"``, ``"clamex_p14"``,
        ``"stopped_dado"`` (a groove open at the back, so the panel slides in from behind) or ``"butt"``. Each panel can override it (see :attr:`Split.panel_joints`).
    tenon_depth
        How far tenons go into the panel they join. ``None`` uses a quarter of that panel's thickness.
    tenon_count
        Number of tenons per shelf or divider edge.
    dado_depth
        Depth of stopped dado grooves. ``None`` uses a third of the grooved panel's thickness.
    dado_stop
        How far behind a shelf's or divider's front edge its stopped dado ends, so the groove doesn't show at the front.
    hinges
        Door hinge dimensions that differ from :class:`cabi_neat.joints.CupHingeJoint`'s defaults, e.g.
        ``{"door_hole_spacing": 52, "hinge_count": 3}``. The hinge type follows ``door_mount``.
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
        Thickness of drawer box sides, front and back. ``None`` uses the carcass ``thickness``.
    drawer_bottom_thickness
        Thickness of drawer box bottoms.
    drawer_bottom_offset
        Height of a drawer bottom's underside above the box's lower edge. The bottom sits in grooves in the sides,
        front and back, so there must be material below it.
    drawer_groove_depth
        Depth of the grooves holding drawer bottoms. ``None`` uses half the grooved panel's thickness.
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
    back_joint: str = "rabbet"
    rabbet_depth: Optional[float] = None
    carcass_joint: str = "clamex_p14"
    clamex_distance: float = 60.0
    clamex_center: bool = True
    interior_joint: str = "mortise_tenon"
    tenon_depth: Optional[float] = None
    tenon_count: int = 2
    dado_depth: Optional[float] = None
    dado_stop: float = 20.0
    hinges: Optional[dict] = None
    front_thickness: float = 18.0
    columns: List[Column] = field(default_factory=lambda: [Column()])
    interior: Optional[Union[Split, Cell]] = None
    door_mount: DoorMount = DoorMount.OVERLAY
    door_style: DoorStyle = DoorStyle.SINGLE
    hinge_side: HingeSide = HingeSide.LEFT
    front_gap: float = 3.0
    inset_setback: float = 0.0
    drawer_side_thickness: Optional[float] = None
    drawer_bottom_thickness: float = 8.0
    drawer_bottom_offset: float = 10.0
    drawer_groove_depth: Optional[float] = None
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
        if self.back_joint not in BACK_JOINTS:
            raise ValueError("back_joint must be one of {}, got {!r}.".format(", ".join(BACK_JOINTS), self.back_joint))
        if self.hinges:
            unknown = set(self.hinges) - set(HINGE_DIMENSIONS)
            if unknown:
                raise ValueError("Unknown hinge dimensions: {}.".format(", ".join(sorted(unknown))))
        if self.carcass_joint not in CARCASS_JOINTS:
            raise ValueError("carcass_joint must be one of {}, got {!r}.".format(", ".join(CARCASS_JOINTS), self.carcass_joint))
        if self.interior_joint not in INTERIOR_JOINTS:
            raise ValueError("interior_joint must be one of {}, got {!r}.".format(", ".join(INTERIOR_JOINTS), self.interior_joint))

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

    @property
    def drawer_panel_thickness(self):
        """Thickness of drawer box sides, front and back: ``drawer_side_thickness``, or the carcass ``thickness``."""
        return self.drawer_side_thickness if self.drawer_side_thickness is not None else self.thickness

    def drawer_options(self, cell):
        """The settings of the drawer ``cell``: its own, falling back to the cabinet's."""
        own = cell.drawer or {}
        return {key: own.get(key, getattr(self, field)) for key, field in DRAWER_DEFAULTS.items()}

    def hinge_options(self, node):
        """The hinge dimensions of ``node``'s door: its own, falling back to the cabinet's ``hinges``."""
        return dict(self.hinges or {}, **(node.hinges or {}))

    def joint_defaults(self):
        """Parameters of each interior joint type, for panels that don't set their own."""
        return {
            "mortise_tenon": {"depth": self.tenon_depth, "tenon_count": self.tenon_count},
            "clamex_p14": {"distance": 60.0, "center_connector": True},
            "stopped_dado": {"depth": self.dado_depth, "stop_distance": self.dado_stop},
            "butt": {},
        }

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
