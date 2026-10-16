"""HOPS programs for the cabinet's plates, written like the workshop's own programs and with its macros.

One program per plate, in the style of ``Hops/Hop/OB/flur``: the header, ``DX``/``DY``/``DZ`` as ``VARS``, ``Fertigteil``
and ``Park_V7``, then the machining:

* format: the saw (``WZS``) and ``_Sformat_V5``, before anything else, so the rest is placed on the finished part;
* pockets (rabbets, grooves, mortises, housings): ``_RechteckTasche_V5`` with the largest router that fits,
  ``_ExecutePocket_V5`` for other shapes;
* the notches beside tenons (stopped dados, mortise and tenon): contours in the plane of the plate's end, with the
  tenons' corners rounded like the ends of the dados and mortises the router leaves;
* hinge pots: ``_Kreisbahn_V5`` with a router (403 Ø20);
* drillings: ``Bohrung`` into faces and ``HorzB`` into edges, with a spindle drill of the hole's diameter (``WZB``).
  Shelf holes use the drill head instead (``WZB`` 501, as the workshop's macros call it) if it has a bit of their
  diameter that's long enough;
* Clamex P-14: in faces ``Clamex_MS_FI`` (finger mill), in edges ``Clamex1_Stumpf`` (ends) and ``Clamex1_Stumpf_X``
  (long edges) with the disc, including the access holes.

Which tools there are is in :class:`HopsTools`; :meth:`HopsTools.from_too` reads it from the machine's tool table.
:class:`HopsSettings` adds which tool does which operation (instead of the automatic choice), fit tolerances per
operation and how parts are cut to size (saw or router), and is saved as a file of its own. What each feature is (a
groove, a mortise, ...) comes from ``feature.machining``, set by the joints that make them, with the joint's own tool and
tolerances for it, which take precedence.

HOPS coordinates: X along the plate's length, Y along its width, Z = 0 on the face on top and negative into the part.
A plate with machining on both faces is done in two passes: the other face first, then a stop to turn it over about its
long edge (X), then the face on top.
"""

import json
import math
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from typing import Dict
from typing import Optional
from typing import Tuple

# ==========================================================================
# Tools
# ==========================================================================


@dataclass(frozen=True)
class Router:
    """A router in the tool changer (``WZF``)."""

    number: int
    diameter: float
    usable_length: float
    name: str = ""


@dataclass(frozen=True)
class Drill:
    """A drill in the tool changer, used by the spindle (``WZB``)."""

    number: int
    diameter: float
    usable_length: float
    name: str = ""


# the ETH machine, from its tool table 092026tools.too (loaded tools), plus the D3 and D8 drills that will be added
ETH_ROUTERS = (
    Router(403, 20.0, 95.0, "Turbex Ø20"),
    Router(402, 13.4, 35.0, "Dia Ø14"),
    Router(405, 12.0, 45.0, "Turbex Ø12"),
    Router(404, 11.5, 45.0, "SRSL Ø12"),
    Router(410, 6.0, 22.0, "ER16 Ø6"),
)
ETH_DRILLS = (
    Drill(609, 5.0, 30.0, "Drill Ø5 short"),
    Drill(603, 8.0, 80.0, "Drill Ø8"),  # in the table, not loaded yet
    Drill(602, 3.0, 30.0, "Drill Ø3"),  # not in the table yet: number and length are placeholders
)
# the drill head (Bohrkopf), as the workshop's macros call it (_Drill_Standard_ID), and its vertical bits
DRILL_HEAD = 501
ETH_DRILL_HEAD_BITS = (Drill(DRILL_HEAD, 5.0, 30.0, "Drill head Ø5"),)  # assumed: check against the machine's drill head

# routers in the table that aren't for pockets
NOT_FOR_POCKETS = (401,)  # Turbex lock-case cutter


def _close(a, b, tol=0.05):
    return abs(a - b) <= tol


@dataclass
class HopsTools:
    """The machine's tools and how the HOPS export uses them.

    Parameters
    ----------
    routers, drills
        What's in the tool changer. Default: the ETH machine (tool table 092026tools).
    drill_head, drill_head_bits
        The drill head's tool number (``WZB``) and its vertical bits, as :class:`Drill` s with that number. Shelf holes
        use it when it has a bit of their diameter that's long enough; ``()`` never uses it.
    saw
        Saw blade for the format (``WZS``).
    clamex_disc, clamex_finger, clamex_drill
        Clamex tools: the disc for edges (108), the finger mill for faces (136), the drill for access holes (135).
    clamex_hole_diameter, clamex_hole_position
        Diameter of the Clamex access holes and their distance from the edge (``B_DM`` and ``B_POS`` of
        ``Clamex1_Stumpf``).
    clamex_access_holes
        Drill the access holes next to the edge grooves (``Art 0``). The plate is then machined with the face the holes
        go into on top: the underside of shelves and of the carcass top, the inside of everything else.
    format_parts
        Cut every part to size with ``_Sformat_V5`` first.
    format_depth, format_lead
        ``TIEFE`` and ``EINLAUF`` of ``_Sformat_V5``.
    through_overcut
        How much deeper than the plate through pockets and holes go.
    """

    routers: Tuple[Router, ...] = ETH_ROUTERS
    drills: Tuple[Drill, ...] = ETH_DRILLS
    drill_head: int = DRILL_HEAD
    drill_head_bits: Tuple[Drill, ...] = ETH_DRILL_HEAD_BITS
    saw: int = 201
    clamex_disc: int = 108
    clamex_finger: int = 136
    clamex_drill: int = 135
    clamex_hole_diameter: float = 6.0
    clamex_hole_position: float = 7.5
    clamex_access_holes: bool = True
    format_parts: bool = True
    format_depth: float = -2.0
    format_lead: float = 100.0
    through_overcut: float = 0.5

    @classmethod
    def from_too(cls, path, **kwargs):
        """Tools from a HOPS tool table (``.too``, UTF-16). Only loaded tools (a tool number other than -1) are used.

        Routers are the group ``Schaft`` (except those in ``NOT_FOR_POCKETS``), drills ``Drill``, the drill head's bits
        ``Bohrkopf`` and the saw the first of ``Saw blades``; Clamex tools from ``Special`` by name (``CLAMEX_Nut...``:
        disc, ``..._Schaft...``: finger mill, ``..._Drill``: drill, and the access holes' diameter). Other settings are
        passed on as ``kwargs``.
        """
        with open(path, "rb") as f:
            raw = f.read()
        return cls.from_too_text(decode_too(raw), **kwargs)

    @classmethod
    def from_too_text(cls, text, **kwargs):
        """Tools from the text of a HOPS tool table, see :meth:`from_too`."""
        import configparser

        table = configparser.ConfigParser(strict=False, interpolation=None)
        table.optionxform = str
        table.read_string(text)
        routers, drills, saws, head = [], [], [], []
        head_number = kwargs.get("drill_head", DRILL_HEAD)
        for i in range(int(table["ToolsCount"]["Tools"])):
            tool = table["ToolData{}".format(i)]
            edge = table["ToolData{}CuttingEdge0".format(i)]
            group, number = tool.get("GroupName", ""), int(edge.get("ID", "-1"))
            diameter = 2 * float(edge.get("Radius", "0") or 0)
            usable = float(edge.get("UseableLength", "0") or 0)
            name = tool.get("Name", "")
            if int(tool.get("ToolNo", "-1")) == -1:
                continue  # not in the tool changer
            elif group == "Schaft" and number not in NOT_FOR_POCKETS:
                routers.append(Router(number, diameter, usable, name))
            elif group == "Drill":
                drills.append(Drill(number, diameter, usable, name))
            elif group == "Bohrkopf":
                head.append(Drill(head_number, diameter, usable, name))
            elif group == "Saw blades":
                saws.append(number)
            elif group == "Special" and "clamex" in name.lower():
                role = {"nut": "clamex_disc", "schaft": "clamex_finger", "drill": "clamex_drill"}
                for word, key in role.items():
                    if word in name.lower():
                        kwargs.setdefault(key, number)
                        if key == "clamex_drill":
                            kwargs.setdefault("clamex_hole_diameter", diameter)
        kwargs.setdefault("routers", tuple(sorted(routers, key=lambda r: -r.diameter)))
        kwargs.setdefault("drills", tuple(drills))
        kwargs.setdefault("drill_head_bits", tuple(head))
        if saws:
            kwargs.setdefault("saw", saws[0])
        return cls(**kwargs)

    def to_data(self):
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ("routers", "drills", "drill_head_bits"):
                value = [{"number": t.number, "diameter": t.diameter, "usable_length": t.usable_length, "name": t.name} for t in value]
            data[f.name] = value
        return data

    @classmethod
    def from_data(cls, data):
        data = dict(data)
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown tool settings: {}.".format(", ".join(sorted(unknown))))
        for key, kind in (("routers", Router), ("drills", Drill), ("drill_head_bits", Drill)):
            if key in data:
                data[key] = tuple(kind(int(t["number"]), float(t["diameter"]), float(t["usable_length"]), t.get("name", "")) for t in data[key])
        return cls(**data)

    def tool(self, number):
        """The router, drill or drill head bit with ``number``, or None."""
        for tool in self.routers + self.drills + self.drill_head_bits:
            if tool.number == number:
                return tool
        return None

    def router_for(self, width, depth, length=None):
        """The largest router at most ``width`` (and ``length``) wide that reaches ``depth``, or None."""
        fitting = [r for r in self.routers if r.diameter <= width + 1e-6 and (length is None or r.diameter <= length + 1e-6) and r.usable_length >= depth]
        return max(fitting, key=lambda r: r.diameter, default=None)

    def drill_for(self, diameter, depth):
        """The shortest spindle drill of ``diameter`` that reaches ``depth``, or None."""
        drills = [d for d in self.drills if _close(d.diameter, diameter) and d.usable_length >= depth]
        return min(drills, key=lambda d: d.usable_length, default=None)

    def head_drill_for(self, diameter, depth):
        """A bit of the drill head for a hole into a face if one fits, else :meth:`drill_for`."""
        bits = [d for d in self.drill_head_bits if _close(d.diameter, diameter) and d.usable_length >= depth]
        if bits:
            return bits[0]
        return self.drill_for(diameter, depth)

    def closest_drills(self, diameter):
        """Diameters that could drill instead, for messages."""
        return sorted({d.diameter for d in self.drills}, key=lambda d: abs(d - diameter))[:2]


def decode_too(raw):
    """The text of a tool table file's bytes: UTF-16 with a byte order mark, else Latin-1."""
    return raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("latin-1")


# ==========================================================================
# Settings
# ==========================================================================

# the kinds of machining whose tool and fit tolerances can be set
OPERATIONS = ("groove", "rabbet", "mortise", "dado", "pocket", "tenon_notch", "pot", "drill", "shelf_hole", "horizontal_drill")
# operations done with routers, the others with drills
ROUTER_OPERATIONS = ("groove", "rabbet", "mortise", "dado", "pocket", "tenon_notch", "pot")
# for lists and messages, including the Clamex machinings, which the macros do with their own tools
OPERATION_LABELS = {
    "groove": "Grooves",
    "rabbet": "Rabbets",
    "mortise": "Mortises",
    "dado": "Stopped dados",
    "pocket": "Other pockets",
    "tenon_notch": "Tenon notches",
    "pot": "Hinge pots",
    "drill": "Drillings",
    "shelf_hole": "Shelf holes",
    "horizontal_drill": "Horizontal drillings",
    "clamex_face": "Clamex P-14 (face)",
    "clamex_edge": "Clamex P-14 (edge)",
}
# what a joint's machining settings can set, per operation: see check_machining
MACHINING_KEYS = ("width", "length", "depth", "tool")
# how parts are cut to size: with the saw (_Sformat_V5) or a router along their outline
CONTOURS = ("saw", "router")


@dataclass
class Tolerance:
    """How much bigger than nominal a machining is cut, in mm, e.g. for assembly clearance. Negative is smaller.

    ``width`` is added across a pocket (the mating plate's thickness) or to a pot's or hole's diameter, ``length``
    along a pocket (its closed ends move by half each; open ends run out anyway) or to a tenon notch, and ``depth`` to
    the depth.
    """

    width: float = 0.0
    length: float = 0.0
    depth: float = 0.0

    def to_data(self):
        return {"width": self.width, "length": self.length, "depth": self.depth}

    @classmethod
    def from_data(cls, data):
        if isinstance(data, Tolerance):
            return data
        unknown = set(data) - {"width", "length", "depth"}
        if unknown:
            raise ValueError("Unknown tolerance dimensions: {}.".format(", ".join(sorted(unknown))))
        return cls(**{k: float(v or 0.0) for k, v in data.items()})


@dataclass
class HopsSettings:
    """Everything the HOPS export can be told: the machine's tools, which tool does what, and fit tolerances.

    These are the defaults for every cabinet; a joint can set its own tool and tolerances for the cuts it makes (its
    ``machining``, see :func:`check_machining`), which take precedence.

    Parameters
    ----------
    tools : :class:`HopsTools`
        The machine: its tools and general settings (format, overcut, Clamex).
    tolerances : dict
        ``{operation: Tolerance}`` (see ``OPERATIONS``); operations not listed are cut at nominal size.
    tool_for : dict
        ``{operation: tool number}``: the tool to use for an operation instead of the automatic choice (the largest
        router or the shortest drill that fits). A tool that doesn't fit is reported and the automatic one used.
    groove_overrun : float, optional
        How far grooves run past the part's edge at open ends. ``None``: one tool diameter.
    contour : str
        How parts are cut to size (if ``tools.format_parts``): ``"saw"`` (``_Sformat_V5``) or ``"router"``, along the
        part's outline (``_Format_V5`` for rectangles, a contour otherwise).
    contour_tool : int, optional
        The router for ``contour="router"``. ``None``: the largest that reaches through the part.
    """

    tools: HopsTools = field(default_factory=HopsTools)
    tolerances: Dict[str, Tolerance] = field(default_factory=dict)
    tool_for: Dict[str, Optional[int]] = field(default_factory=dict)
    groove_overrun: Optional[float] = None
    contour: str = "saw"
    contour_tool: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.tools, dict):
            self.tools = HopsTools.from_data(self.tools)
        for what, keys in (("tolerances", self.tolerances), ("tool_for", self.tool_for)):
            unknown = set(keys) - set(OPERATIONS)
            if unknown:
                raise ValueError("Unknown operations in {}: {}.".format(what, ", ".join(sorted(unknown))))
        self.tolerances = {op: Tolerance.from_data(t) for op, t in self.tolerances.items()}
        self.tool_for = {op: int(n) for op, n in self.tool_for.items() if n is not None}
        if self.contour not in CONTOURS:
            raise ValueError("contour must be one of {}, got {!r}.".format(", ".join(CONTOURS), self.contour))
        if self.contour_tool is not None:
            self.contour_tool = int(self.contour_tool)

    def tolerance(self, operation, own=None):
        """The :class:`Tolerance` of an operation, with a joint's own settings ``own`` (``{"width", ...}``) for it."""
        tolerance = self.tolerances.get(operation) or Tolerance()
        own = own or {}
        return Tolerance(*(own[k] if own.get(k) is not None else getattr(tolerance, k) for k in ("width", "length", "depth")))

    def tool(self, operation, own=None):
        """The tool number chosen for an operation (or by a joint's own settings ``own``), or None for the automatic one."""
        if own and own.get("tool") is not None:
            return own["tool"]
        return self.tool_for.get(operation)

    def to_data(self):
        return {
            "tools": self.tools.to_data(),
            "tolerances": {op: t.to_data() for op, t in self.tolerances.items()},
            "tool_for": dict(self.tool_for),
            "groove_overrun": self.groove_overrun,
            "contour": self.contour,
            "contour_tool": self.contour_tool,
        }

    @classmethod
    def from_data(cls, data):
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown HOPS settings: {}.".format(", ".join(sorted(unknown))))
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


def check_machining(machining):
    """Validate a joint's machining settings, ``{operation: {"width", "length", "depth", "tool"}}``: its own tool and
    fit tolerances for the cuts it makes, instead of :class:`HopsSettings`' for those operations. Returns a copy with
    numbers, without empty values, or None if nothing is set."""
    if not machining:
        return None
    unknown = set(machining) - set(OPERATIONS)
    if unknown:
        raise ValueError("Unknown operations in machining settings: {}.".format(", ".join(sorted(unknown))))
    checked = {}
    for operation, values in machining.items():
        unknown = set(values or {}) - set(MACHINING_KEYS)
        if unknown:
            raise ValueError("Unknown machining settings for {}: {}.".format(operation, ", ".join(sorted(unknown))))
        values = {k: (int(v) if k == "tool" else float(v)) for k, v in (values or {}).items() if v is not None and v != ""}
        if values:
            checked[operation] = values
    return checked or None


def merge_machining(*machinings):
    """Machining settings (see :func:`check_machining`) on top of each other, later ones winning per value."""
    merged = {}
    for machining in machinings:
        for operation, values in (machining or {}).items():
            merged[operation] = dict(merged.get(operation, {}), **values)
    return merged or None


def as_settings(settings):
    """``settings`` as :class:`HopsSettings`: ``None`` gives the defaults, a :class:`HopsTools` the defaults with those tools."""
    if settings is None:
        return HopsSettings()
    if isinstance(settings, HopsTools):
        return HopsSettings(tools=settings)
    return settings


# ==========================================================================
# Program text
# ==========================================================================


def _n(value):
    """A number as HOPS text: at most 3 decimals, no trailing zeros."""
    text = "{:.3f}".format(value).rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _call(macro, **params):
    return "CALL {} ( VAL {})".format(macro, ",".join("{}:={}".format(k.upper(), v) for k, v in params.items()))


def _tool(kind, number):
    return "{} ({},_VE,_V,_VA,_SD,_ANF,'1')".format(kind, number)


HEADER = """;MAKROTYP=0
;INSTVERSION=
;EXEVERSION=7.10.0.108[Hops.exe]
;BILD={bild}.WMF
;INFO=
;WZGV=7235C_219
;WZGVCONFIG=
;MASCHINE=HOLZHER
;NCNAME={name}
;KOMMENTAR={comment}
;DX=0.000
;DY=0.000
;DZ=0
;DIALOGDLL=Dialoge.Dll
;DIALOGPROC=StandardFormAnzeigen
;DIALOGKIND=0
;AUTOSCRIPTSTART=1
;BUTTONBILD=
;DIMENSION_UNIT=0"""


def _ascii(text):
    return text.encode("ascii", "replace").decode("ascii")


# ==========================================================================
# Plate geometry in HOPS coordinates
# ==========================================================================


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


class _PlateSpace:
    """The plate's blank as a box [0, L] x [0, W] x [0, T], and HOPS coordinates with one of its faces on top.

    ``up`` is +1 when the blank's +z face is on top, -1 when it's turned over (about X: y -> W - y). The part can also
    lie turned on the table by ``turns`` quarter turns (anticlockwise, seen from above), see :meth:`orient`.

    ``size`` is the part's size in HOPS coordinates (DX, DY, DZ), ``local_size`` the blank's.
    """

    def __init__(self, plate):
        blank = plate.blank
        self.frame = blank.frame
        self.local_size = (blank.xsize, blank.ysize, blank.zsize)
        self.turns = 0
        self.size = self.local_size
        self.axes = (tuple(self.frame.xaxis), tuple(self.frame.yaxis), tuple(self.frame.zaxis))

    def orient(self, up, down=None):
        """Turn the part on the table so its long edge is along X.

        Of the two ways that does, the one that takes the world direction ``down`` (if given, and in the part's plane)
        to -Y, else to -X: e.g. a drawer part's bottom edge, with the bottom's groove, at Y = 0 (at X = 0 if it's the
        short edge). Without ``down``, the one with the fewest turns.
        """
        length, width, thickness = self.local_size
        turns = [k for k in range(4) if (width if k % 2 else length) >= (length if k % 2 else width) - 1e-6]
        if down is not None:
            dx, dy, _ = self.hops_vector(self.local_vector(down), up, turns=0)
            if math.hypot(dx, dy) > 0.5:

                def along(k):
                    x, y = dx, dy
                    for _ in range(k):
                        x, y = -y, x
                    return (-y, -x)  # how much it points to -Y, then to -X

                turns.sort(key=along, reverse=True)
        self.turns = turns[0]
        self.size = (width, length, thickness) if self.turns % 2 else self.local_size

    def local_point(self, point):
        d = _sub(tuple(point), tuple(self.frame.point))
        return tuple(_dot(d, a) + s / 2.0 for a, s in zip(self.axes, self.local_size))

    def local_vector(self, vector):
        return tuple(_dot(tuple(vector), a) for a in self.axes)

    def hops_point(self, local, up):
        x, y, z = local
        length, width, thickness = self.local_size
        x, y, z = (x, y, z - thickness) if up > 0 else (x, width - y, -z)
        for _ in range(self.turns):
            x, y = width - y, x
            length, width = width, length
        return (x, y, z)

    def hops_vector(self, local, up, turns=None):
        x, y, z = local
        x, y, z = (x, y, z) if up > 0 else (x, -y, -z)
        for _ in range(self.turns if turns is None else turns):
            x, y = -y, x
        return (x, y, z)

    def face_normal(self, up):
        """The world normal of the face on top for ``up``."""
        return tuple(up * c for c in self.axes[2])

    def side_of(self, ref_side):
        """+1/-1 for the blank's +z/-z face, 0 for an edge."""
        nz = self.local_vector(ref_side.normal)[2]
        return 0 if abs(nz) < 0.5 else (1 if nz > 0 else -1)


def _ref_point(ref_side, x, y):
    return tuple(ref_side.point + ref_side.xaxis * x + ref_side.yaxis * y)


# ==========================================================================
# Operations
# ==========================================================================

# order of the operations within a pass, so tools are changed as rarely as possible
ORDER = ("pocket", "side_contour", "pot", "drill", "clamex_face", "horizontal", "clamex_edge")


@dataclass
class _Op:
    kind: str  # one of ORDER
    tool: Optional[int]  # WZF/WZB number to call first, None for none
    lines: list
    comment: str = ""
    face: int = 0  # +1/-1: the blank face that must be on top, 0: an edge (any pass)
    sort: tuple = field(default_factory=tuple)


class PlateProgram:
    """The HOPS program of one plate.

    Parameters
    ----------
    plate : :class:`compas_timber.elements.Plate`
        What each of its features is comes from ``feature.machining`` (``{"kind": ..., "settings": ...}``, set by the
        joints, with their own tool and tolerances for it): grooves (e.g. for drawer bottoms) are milled as lines along
        their walls, except in drawer fronts, and the notches beside the tenons of stopped dados and mortise and tenon
        joints (``"tenon_notch"``) from the plate's end.
    tools : :class:`HopsSettings` | :class:`HopsTools`, optional
        The tools and how to use them. A :class:`HopsTools` uses them with the default settings.
    access_direction : vector, optional
        Where the Clamex access holes should face (world). The face closest to it goes on top if there are any.
    down : vector, optional
        Parts lie with their long edge along X. Of the two ways, the one that takes this world direction to -Y (else
        -X), e.g. down for drawer parts: their bottom edge, with the bottom's groove, then lies along X at Y = 0.

    Attributes
    ----------
    skipped : list(str)
        What was left out or done differently than asked, for the report.
    """

    def __init__(self, plate, tools=None, access_direction=None, comment="", down=None):
        self.plate = plate
        self.settings = as_settings(tools)
        self.tools = self.settings.tools
        self.access_direction = access_direction
        self.comment = comment
        self.space = _PlateSpace(plate)
        self.down = down
        self.skipped = []
        self._contours = 0
        self._entry = None  # the machining being converted: {"operation", "own"}
        self._outline_points = None  # the outline, if it isn't a rectangle: points in the blank's coordinates

    # -- building ---------------------------------------------------------

    def _skip(self, message):
        self.skipped.append("{}: {}".format(self.plate.name, message))

    def _note(self, message):
        """Like :meth:`_skip`, for something that was still machined, just not as asked. Said once per part."""
        message = "{}: {}".format(self.plate.name, message)
        if message not in self.skipped:
            self.skipped.append(message)

    def _tolerance(self):
        """The tolerance of the machining being converted."""
        entry = self._entry
        if entry is None:
            return Tolerance()
        return self.settings.tolerance(entry["operation"], entry["own"])

    def _choose(self, auto, candidates, fits, what):
        """The tool for the machining being converted: the one set for it if it ``fits``, else ``auto``."""
        entry = self._entry
        number = self.settings.tool(entry["operation"], entry["own"]) if entry else None
        if number is None:
            return auto
        numbered = [t for t in candidates if t.number == number]
        tool = next((t for t in numbered if fits(t)), None)
        if tool is not None:
            return tool
        tool = numbered[0] if numbered else None
        reason = "isn't in the tool table" if tool is None else "doesn't fit"
        self._note("tool {} {} for {} - {}".format(number, reason, what, "used {}".format(auto.number) if auto else "no other tool fits"))
        return auto

    def text(self):
        self._contours = 0
        space = self.space
        features = list(self.plate.features)
        edge_clamex = [f for f in features if type(f).__name__ == "P14Edge"]

        # which face goes on top (the last pass): the Clamex access holes' face, else the face with the most work
        faces = {1: 0, -1: 0}
        for feature in features[1:]:
            side = self._face_of(feature)
            if side:
                faces[side] += 1
        if edge_clamex and self.tools.clamex_access_holes and self.access_direction is not None:
            top = 1 if _dot(space.face_normal(1), tuple(self.access_direction)) >= 0 else -1
        else:
            top = 1 if faces[1] >= faces[-1] else -1
        first = -top if faces[-top] else None
        space.orient(top, self.down)
        length, width, thickness = space.size

        ops = {top: [], -top: [], 0: []}
        for index, feature in enumerate(features):
            name = type(feature).__name__
            if index == 0 and name == "FreeContour" and not feature.counter_sink:
                self._outline_points = self._outline(feature)
                continue
            for op in self._convert(feature, top):
                ops[op.face].append(op)
        ops[0].extend(self.edge_clamex(top))

        lines = [HEADER.format(bild=_ascii(self.plate.name).upper(), name=_ascii(self.plate.name), comment=_ascii(self.comment))]
        lines.append("VARS")
        lines += ["   D{} := {};*VAR*Dimension {}".format(axis, _n(value), axis) for axis, value in zip("XYZ", (length, width, thickness))]
        lines += ["START", "Fertigteil (DX,DY,DZ,0,0,0,0,0,'',0,0,0)", "CALL Park_V7 ( VAL MODE:=3,POSX:=0,POSY:=0)"]
        formatted = not self.tools.format_parts
        if first is not None:
            lines.append(";===== Pass 1: {} face up =====".format("underside" if first == -top else "top"))
            if not formatted:
                lines += self._format(first)
                formatted = True
            lines += self._render(ops[first])
            lines.append(_call("MachineStop_V7", mode=0, parkmode=10, parkposx=0, parkposy=0, typ=0, r6=0, str="'Turn the panel over about its long edge (X)'", r7=0))
            lines.append(";===== Pass 2 =====")
        if not formatted:
            lines += self._format(top)
        lines += self._render(ops[top] + ops[0])
        return "\n".join(lines) + "\n"

    def _render(self, ops):
        lines, current = [], None
        for op in sorted(ops, key=lambda o: (ORDER.index(o.kind), o.tool or 0, o.sort)):
            if op.comment:
                lines.append("; " + op.comment)
            if op.tool is not None and op.tool != current:
                lines.append(_tool("WZB" if op.kind in ("drill", "horizontal") else "WZF", op.tool))
                current = op.tool
            elif op.kind in ("clamex_face", "clamex_edge"):
                current = None  # the macros call their own tools
            lines.extend(op.lines)
        return lines

    def _format(self, face):
        """Cutting the part to size, with ``face`` on top: with the saw, or with a router along its outline."""
        t = self.tools
        if self.settings.contour == "saw":
            if self._outline_points:
                self._skip("non-rectangular outline - the saw cuts its bounding rectangle, cut it to shape separately")
            return [
                "; Format",
                _tool("WZS", t.saw),
                _call("_Sformat_V5", tiefe=_n(t.format_depth), richtung=0, einlauf=_n(t.format_lead), l=1, v=1, r=1, h=1, esmd=1),
            ]
        thickness = self.space.size[2]
        reach = thickness - min(t.format_depth, 0.0)

        def fits(r):
            return r.usable_length >= reach - 1e-6

        router = max((r for r in t.routers if fits(r)), key=lambda r: r.diameter, default=None)
        number = self.settings.contour_tool
        if number is not None:
            pinned = next((r for r in t.routers if r.number == number), None)
            if pinned is not None and fits(pinned):
                router = pinned
            else:
                reason = "isn't in the tool table" if pinned is None else "doesn't reach through the part"
                self._note("contour tool {} {} - {}".format(number, reason, "used {}".format(router.number) if router else "no router does"))
        if router is None:
            self._skip("outline - no router reaches through {}".format(_n(thickness)))
            return []
        lines = ["; Format: router along the outline", _tool("WZF", router.number)]
        if not self._outline_points:
            lines.append(_call("_Format_V5", eckenverrundung=0, tiefe=_n(t.format_depth), aufmass=0, anf="_ANF", esmd=1, ggl=0, laser=0, depth=0))
            return lines
        # any other outline: a contour around it, the tool outside (on the right of an anticlockwise path)
        points = [self.space.hops_point(p, face)[:2] for p in self._outline_points]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
        if area < 0:
            points.reverse()
        lines.append("SP ({},{},{},2,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)".format(_n(points[0][0]), _n(points[0][1]), _n(t.format_depth)))
        lines += ["G01 ({},{},0,0,1,2)".format(_n(x), _n(y)) for x, y in points[1:] + points[:1]]
        lines.append("EP (1,_ANF*2,0)")
        return lines

    def _notch(self, feature):
        """The :class:`TenonNotch` of a notch beside a tenon, or None."""
        machining = getattr(feature, "machining", None) or {}
        if machining.get("kind") != "tenon_notch":
            return None
        return TenonNotch(machining["depth"], machining["dado_width"], machining["dado_depth"], machining.get("label", TenonNotch.kind), machining.get("mating"))

    def _operation(self, feature):
        """What a feature is, one of ``OPERATIONS`` (or ``"clamex_face"``), from ``feature.machining``."""
        kind = (getattr(feature, "machining", None) or {}).get("kind")
        name = type(feature).__name__
        if name == "Drilling":
            if kind == "shelf_hole" or getattr(feature, "shelf_hole", False):
                return "shelf_hole"
            return "drill" if self._face_of(feature) else "horizontal_drill"
        if name == "P14Face":
            return "clamex_face"
        if kind in ("rabbet", "groove", "mortise", "dado", "pot", "tenon_notch"):
            return kind
        return "pocket"

    def _face_of(self, feature):
        name = type(feature).__name__
        if self._notch(feature):
            return 0  # milled from the end
        if name in ("FreeContour", "Drilling"):
            return self.space.side_of(self.plate.ref_sides[feature.ref_side_index])
        if name == "P14Face":
            return self.space.side_of(self.plate.ref_sides[feature.ref_side])
        return 0

    def _outline(self, feature):
        """The outline's points in the blank's coordinates if it isn't the blank's rectangle, else None."""
        contour = feature.contour_param_object
        points = [self.space.local_point(_ref_point(self.plate.ref_sides[feature.ref_side_index], p[0], p[1])) for p in contour.polyline]
        if len(points) > 1 and math.dist(points[0], points[-1]) < 1e-6:
            points = points[:-1]
        length, width, _ = self.space.local_size
        if any(min(abs(p[0]), abs(p[0] - length)) > 0.01 or min(abs(p[1]), abs(p[1] - width)) > 0.01 for p in points):
            return points
        return None

    def _convert(self, feature, top):
        name = type(feature).__name__
        if name == "P14Edge":
            return []  # all edges' connectors together, below
        if name not in ("FreeContour", "Drilling", "P14Face"):
            self._skip("{} - not supported".format(name))
            return []
        own = (getattr(feature, "machining", None) or {}).get("settings")
        self._entry = {"operation": self._operation(feature), "own": own}
        try:
            notch = self._notch(feature)
            if notch:
                return self._tenon_notch(feature, notch, top)
            if name == "FreeContour" and feature.counter_sink:
                return self._pocket(feature)
            if name == "Drilling":
                return self._drilling(feature, top)
            if name == "P14Face":
                return self._clamex_face(feature)
            self._skip("{} - not supported".format(name))
            return []
        finally:
            self._entry = None

    # -- pockets -------------------------------------------------------------

    def _pocket(self, feature):
        ref_side = self.plate.ref_sides[feature.ref_side_index]
        face = self.space.side_of(ref_side)
        contour = feature.contour_param_object
        if not face:
            self._skip("pocket in an edge - not supported")
            return []
        if any(abs(i) > 1e-6 for i in getattr(contour, "inclination", [0.0])):
            self._skip("pocket with inclined sides - not supported")
            return []
        points = [self.space.hops_point(self.space.local_point(_ref_point(ref_side, p[0], p[1])), face)[:2] for p in contour.polyline]
        if len(points) > 1 and math.dist(points[0], points[-1]) < 1e-6:
            points = points[:-1]
        length, width, thickness = self.space.size
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        if max(xs) <= 1e-3 or min(xs) >= length - 1e-3 or max(ys) <= 1e-3 or min(ys) >= width - 1e-3:
            return []  # entirely outside the part, e.g. a notch where the part wasn't extended: nothing to cut
        depth = contour.depth
        if depth >= thickness - 1e-6:
            depth = thickness + self.tools.through_overcut
        else:
            depth += self._tolerance().depth
        circle = _circle(points)
        if circle:
            return self._pot(circle, depth, face)
        rectangle = _rectangle(points)
        # grooves (for drawer bottoms) as lines along their walls, except in a visible drawer front: a pocket there
        is_groove = self._operation(feature) == "groove" and self.plate.attributes.get("category") != "drawer_front"
        if rectangle and is_groove:
            groove = self._groove(rectangle, depth, face)
            if groove:
                return groove
        if rectangle:
            return self._rectangle_pocket(rectangle, depth, face)
        return self._free_pocket(points, depth, face)

    def _pot(self, circle, depth, face):
        (cx, cy), radius = circle
        t = self.tools
        radius += self._tolerance().width / 2.0
        comment = "Pot D{} {} deep".format(_n(2 * radius), _n(depth))

        # a router that clears the whole pot on one circle around the center
        def fits(r):
            return radius - 1e-6 <= r.diameter <= 2 * radius + 1e-6 and r.usable_length >= depth

        router = max((r for r in t.routers if fits(r)), key=lambda r: r.diameter, default=None)
        router = self._choose(router, t.routers, fits, "pot D{}".format(_n(2 * radius)))
        if router is None:
            self._skip("pot D{} - no router between D{} and D{}".format(_n(2 * radius), _n(radius), _n(2 * radius)))
            return []
        call = _call(
            "_Kreisbahn_V5",
            x_mitte=_n(cx),
            y_mitte=_n(cy),
            tiefe=_n(depth),
            zutiefe="_AT_MAXDEPTH",
            radius=_n(radius),
            radiuskorrektur=1,
            ab=1,
            aufmass=0,
            bearb_umkehren=0,
            winkel=360,
            anf="_ANF",
            abf="_ANF",
            rampe=1,
            interpol=1,
            esxy=1,
            esmd=1,
            laser=0,
        )
        return [_Op("pot", router.number, [call], comment, face, (cx, cy))]

    def _rectangle_pocket(self, rectangle, depth, face):
        (cx, cy), (a, b), angle = rectangle
        length, width, _ = self.space.size
        if abs(math.sin(math.radians(angle))) < 1e-6 or abs(math.cos(math.radians(angle))) < 1e-6:
            # axis aligned: sides on or beyond the plate's edges are open, the pocket can grow past them for a bigger tool
            if abs(math.sin(math.radians(angle))) > 0.5:
                a, b = b, a
            x0, x1, y0, y1 = cx - a / 2, cx + a / 2, cy - b / 2, cy + b / 2
            open_x = (x0 <= 1e-3, x1 >= length - 1e-3)
            open_y = (y0 <= 1e-3, y1 >= width - 1e-3)
            rabbet = self._rabbet(x0, x1, y0, y1, open_x, open_y, depth, face)
            if rabbet:
                return rabbet
            # what's in the part, before and after the tolerances: across the narrow side and along the long one
            inside = (min(x1, length) - max(x0, 0.0), min(y1, width) - max(y0, 0.0))
            tolerance = self._tolerance()
            narrow_x = (x1 - x0) <= (y1 - y0)
            dx, dy = (tolerance.width, tolerance.length) if narrow_x else (tolerance.length, tolerance.width)
            x0, x1, y0, y1 = x0 - dx / 2, x1 + dx / 2, y0 - dy / 2, y1 + dy / 2
            biggest = max((r.diameter for r in self.tools.routers if r.usable_length >= depth), default=0)
            x0, x1 = _grow(x0, x1, biggest, open_x)
            y0, y1 = _grow(y0, y1, biggest, open_y)
            cx, cy, a, b, angle = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, 0.0
        else:
            tolerance = self._tolerance()
            if a <= b:
                a, b = a + tolerance.width, b + tolerance.length
            else:
                a, b = a + tolerance.length, b + tolerance.width
        router = self.tools.router_for(min(a, b), depth, max(a, b))
        router = self._choose(
            router,
            self.tools.routers,
            lambda r: r.diameter <= min(a, b) + 1e-6 and r.usable_length >= depth,
            "pocket {} x {}".format(_n(a), _n(b)),
        )
        if router is None:
            self._skip("pocket {} x {}, {} deep - no router fits".format(_n(a), _n(b), _n(depth)))
            return []
        call = _call(
            "_RechteckTasche_V5",
            x_mitte=_n(cx),
            y_mitte=_n(cy),
            taschenlaenge=_n(a),
            taschenbreite=_n(b),
            radius=_n(router.diameter / 2.0),  # the corners the router leaves anyway
            winkel=_n(angle),
            tiefe=_n(-depth),
            zustellung="_AT_MAXDEPTH",
            ab=0,  # no lead-out
            abf="_ANF",
            interpol=1,
            umkehren=0,
            esxy=1,
            esmd=0,
            laser=0,
        )
        return [_Op("pocket", router.number, [call], "Pocket {} x {}, {} deep".format(_n(a), _n(b), _n(depth)), face, (cy, cx))]

    def _groove(self, rectangle, depth, face):
        """A groove as lines along its walls, with a linear approach (``SP`` 1) and no lead-out (``EP`` 0).

        The largest router that fits the width, one line with radius correction along each wall (tool inside the
        groove, on the left of the path) or one down the middle if the router is as wide as the groove, and more
        down the middle if the two don't clear it. Open ends run out to the part's edge; closed ends stop a tool radius
        short, so the cut ends at the groove's end; open ones run a tool diameter past the part's edge. ``SP``'s Z is the groove's floor above the underside.
        Returns None for grooves that aren't along X or Y (milled as pockets instead).
        """
        (cx, cy), (a, b), angle = rectangle
        if abs(math.sin(math.radians(angle))) > 1e-6 and abs(math.cos(math.radians(angle))) > 1e-6:
            return None
        if abs(math.sin(math.radians(angle))) > 0.5:
            a, b = b, a
        length, width, thickness = self.space.size
        along_x = a >= b
        if along_x:
            lo, hi, start, end, span, name = cy - b / 2, cy + b / 2, cx - a / 2, cx + a / 2, length, "DX"
        else:
            lo, hi, start, end, span, name = cx - a / 2, cx + a / 2, cy - b / 2, cy + b / 2, width, "DY"
        open_start, open_end = start <= 1e-3, end >= span - 1e-3
        # tolerances: wider on both walls, longer at closed ends
        tolerance = self._tolerance()
        lo, hi = lo - tolerance.width / 2.0, hi + tolerance.width / 2.0
        if not open_start:
            start -= tolerance.length / 2.0
        if not open_end:
            end += tolerance.length / 2.0
        groove_width = hi - lo
        router = self.tools.router_for(groove_width, depth, end - start)
        router = self._choose(
            router,
            self.tools.routers,
            lambda r: r.diameter <= groove_width + 1e-6 and r.usable_length >= depth,
            "groove {} wide".format(_n(groove_width)),
        )
        if router is None:
            self._skip("groove {} wide, {} deep - no router fits".format(_n(groove_width), _n(depth)))
            return []
        radius = router.diameter / 2.0
        # where the tool's center starts and ends along the groove: the part's edge if open, a radius short if closed
        # open ends run past the part's edge, by default a tool diameter
        overrun = router.diameter if self.settings.groove_overrun is None else self.settings.groove_overrun
        first = _n(-overrun) if open_start else _n(start + radius)
        last = "{}+{}".format(name, _n(overrun)) if open_end else _n(end - radius)
        # (position across, radius correction, forwards): the walls with the tool on the left, else the middle
        if groove_width - router.diameter < 0.01:
            paths = [(_n((lo + hi) / 2.0), 0, True)]
        else:
            paths = [(_n(lo), 1, along_x), (_n(hi), 1, not along_x)]
            inner = groove_width - 2 * router.diameter
            count = math.ceil(inner / (0.8 * router.diameter)) if inner > 0 else 0
            paths += [(_n(lo + router.diameter + (i + 0.5) * inner / count), 0, True) for i in range(count)]
        lines = []
        for across, correction, forwards in paths:
            ends = (first, last) if forwards else (last, first)
            points = [(e, across) if along_x else (across, e) for e in ends]
            lines += [
                "SP ({},{},{},{},1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)".format(points[0][0], points[0][1], _n(thickness - depth), correction),
                "G01 ({},{},0,0,1,2)".format(points[1][0], points[1][1]),
                "EP (0,_ANF*2,0)",  # no lead-out
            ]
        comment = "Groove {} wide, {} deep".format(_n(groove_width), _n(depth))
        return [_Op("pocket", router.number, lines, comment, face, (lo, start))]

    def _rabbet(self, x0, x1, y0, y1, open_x, open_y, depth, face):
        """A rabbet (a pocket open along one side and at both ends) as a single line along its inner edge.

        As in the earlier programs: ``SP`` at the part's edge, one relative ``G01`` over the part's length or width,
        ``EP``; radius correction 1, the tool on the left of the path, travelling so that's the rabbet's side. ``SP``'s Z
        is the rabbet's floor above the underside. Returns None if it isn't a rabbet or no router is wide enough.
        """
        length, width, thickness = self.space.size
        # the contour usually runs past the part's edges: only what's in the part counts
        x0, x1, y0, y1 = max(x0, 0.0), min(x1, length), max(y0, 0.0), min(y1, width)
        # the width tolerance moves the rabbet's inner edge further into the part
        extra = self._tolerance().width
        if all(open_x) and sum(open_y) == 1:  # along X
            if open_y[0]:  # at the front: travel -X along its rear edge
                y1 += extra
                start, move = (length, y1), "-DX,0"
            else:  # at the rear: travel +X along its front edge
                y0 -= extra
                start, move = (0.0, y0), "DX,0"
            rabbet_width = y1 - y0
        elif all(open_y) and sum(open_x) == 1:  # along Y
            if open_x[0]:  # at the left end: travel +Y along its right edge
                x1 += extra
                start, move = (x1, 0.0), "0,DY"
            else:  # at the right end: travel -Y along its left edge
                x0 -= extra
                start, move = (x0, width), "0,-DY"
            rabbet_width = x1 - x0
        else:
            return None

        def fits(r):
            return r.diameter >= rabbet_width - 1e-6 and r.usable_length >= depth

        router = max((r for r in self.tools.routers if fits(r)), key=lambda r: r.diameter, default=None)
        if router is None:
            return None
        router = self._choose(router, self.tools.routers, fits, "rabbet {} wide".format(_n(rabbet_width)))
        lines = [
            "SP ({},{},{},1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)".format(_n(start[0]), _n(start[1]), _n(thickness - depth)),
            "G01 ({},0,0,10,2)".format(move),
            "EP (1,_ANF*2,0)",
        ]
        comment = "Rabbet {} wide, {} deep".format(_n(rabbet_width), _n(depth))
        return [_Op("pocket", router.number, lines, comment, face, (start[1], start[0]))]

    def _free_pocket(self, points, depth, face):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        tolerance = self._tolerance()
        if tolerance.width or tolerance.length:
            self._note("pocket that isn't a rectangle: only its depth tolerance is used")
        span = min(max(xs) - min(xs), max(ys) - min(ys)) / 2.0
        router = self.tools.router_for(span, depth)
        router = self._choose(router, self.tools.routers, lambda r: r.diameter <= span + 1e-6 and r.usable_length >= depth, "pocket")
        if router is None:
            self._skip("pocket, {} deep - no router fits".format(_n(depth)))
            return []
        self._contours += 1
        name = "P{}".format(self._contours)
        lines = ["KB ('{}','',{},{},0,'',1,0)".format(name, _n(points[0][0]), _n(points[0][1]))]
        lines += ["KG01 ('',{},{},0,'',1,2)".format(_n(p[0]), _n(p[1])) for p in points[1:]]
        lines.append("KG01ZuKB()")
        lines.append(
            _call(
                "_ExecutePocket_V5",
                namen="'{}'".format(name),
                aa="-_WZR",
                ueberlappung=10,
                mode=2,
                angle=0,
                tiefe=_n(-depth),
                esmd=0,
                anzahl=0,
                maxz="_AT_MAXDEPTH",
                rd=1,
                fliegendeintauchen=0,
                maxeintauchlaenge=20,
            )
        )
        return [_Op("pocket", router.number, lines, "Pocket, {} deep".format(_n(depth)), face, (min(ys), min(xs)))]

    # -- tenon notches ----------------------------------------------------------

    def _tenon_notch(self, feature, notch, top):
        """A notch beside the tenons of a stopped dado or a mortise and tenon, milled from the plate's end with the tool
        axis along it.

        A router milling the dado or mortise from above leaves its closed ends rounded with the router's radius, across
        its width. So the tenons' ends get the same corners: in the plane of the end face (``Ebene1``-``Ebene4``: X along
        the face as seen from outside, Y from 0 at the underside to ``DZ``, Z into the part), a contour up each tenon end
        beside the notch with an arc of that radius at either face, ``notch.depth`` deep. It's run with decreasing
        allowances (``SP``'s ``Aufmass``), the last one 0, so the passes clear the notch: out to the edge at a corner of
        the plate, from both sides to the middle between two tenons.
        """
        length, width, thickness = self.space.size
        ref_side = self.plate.ref_sides[feature.ref_side_index]
        points = [self.space.hops_point(self.space.local_point(_ref_point(ref_side, p[0], p[1])), top)[:2] for p in feature.contour_param_object.polyline]
        x0, x1 = max(min(p[0] for p in points), 0.0), min(max(p[0] for p in points), length)
        y0, y1 = max(min(p[1] for p in points), 0.0), min(max(p[1] for p in points), width)
        # the end face's plane, its length and the notch's [lo, hi] along the plane's X
        if abs(x1 - x0 - notch.depth) < 0.01 and x0 < 1e-3:
            plane, span, size, lo, hi = "Ebene2", "DY", width, width - y1, width - y0  # left end, X runs towards -Y
        elif abs(x1 - x0 - notch.depth) < 0.01 and x1 > length - 1e-3:
            plane, span, size, lo, hi = "Ebene4", "DY", width, y0, y1  # right end, X runs towards +Y
        elif abs(y1 - y0 - notch.depth) < 0.01 and y0 < 1e-3:
            plane, span, size, lo, hi = "Ebene1", "DX", length, x0, x1  # front, X runs towards +X
        elif abs(y1 - y0 - notch.depth) < 0.01 and y1 > width - 1e-3:
            plane, span, size, lo, hi = "Ebene3", "DX", length, length - x1, length - x0  # rear, X runs towards -X
        else:
            self._skip("{} notch that isn't at the plate's end - milled as a pocket instead".format(notch.kind.lower()))
            return self._pocket(feature)
        at_start, at_end = lo < 1e-3, hi > size - 1e-3
        if at_start and at_end:
            self._skip("{} notch along the whole end - milled as a pocket instead".format(notch.kind.lower()))
            return self._pocket(feature)
        # tolerances: longer beside the tenons (shorter tenons), deeper into the part
        tolerance = self._tolerance()
        if not at_start:
            lo -= tolerance.length / 2.0
        if not at_end:
            hi += tolerance.length / 2.0
        notch_depth = notch.depth + tolerance.depth

        # the corners follow the router that cuts the dado or mortise: the one set for those, else the automatic one
        def cuts_dado(r):
            return r.diameter <= notch.dado_width + 1e-6 and r.usable_length >= notch.dado_depth

        router = self.tools.router_for(notch.dado_width, notch.dado_depth)
        mating = self.tools.tool(self.settings.tool("mortise" if notch.kind.startswith("Mortise") else "dado", notch.mating))
        if mating is not None and mating in self.tools.routers and cuts_dado(mating):
            router = mating
        router = self._choose(router, self.tools.routers, lambda r: cuts_dado(r) and r.usable_length >= notch_depth, "{} notch".format(notch.kind.lower()))
        if router is None or router.usable_length < notch_depth:
            self._skip("{} notch - no router for it".format(notch.kind.lower()))
            return []
        if not (at_start or at_end) and hi - lo < router.diameter - 1e-6:
            self._skip("{} notch {} wide between tenons - narrower than the router (D{})".format(notch.kind.lower(), _n(hi - lo), _n(router.diameter)))
            return []
        radius = min(router.diameter / 2.0, thickness / 2.0)
        r = _n(radius)
        # one contour per tenon end beside the notch: (X of the end, X of the arcs' ends on the faces, radius correction,
        # arc, how wide it clears, the largest allowance that keeps the tool off a tenon on the other side)
        contours = []
        if not at_end:  # a tenon beyond X = hi: up its end with the tool on the left, arcs clockwise
            clear, cap = (hi, None) if at_start else ((hi - lo) / 2.0, hi - lo - router.diameter)
            contours.append((_n(hi), "{}+{}".format(_n(hi), r), 1, "G02R", clear, cap))
        if not at_start:  # a tenon before X = lo: tool on the right, arcs anticlockwise
            if at_end:
                s = _n(size - lo)
                contours.append(("{}-{}".format(span, s), "{}-{}-{}".format(span, s, r), 2, "G03R", size - lo, None))
            else:
                contours.append((_n(lo), "{}-{}".format(_n(lo), r), 2, "G03R", (hi - lo) / 2.0, hi - lo - router.diameter))
        # allowances: steps of 3/4 of the tool, until the first pass reaches the open edge (or the middle)
        step = max(math.floor(0.75 * router.diameter), 1)
        lines = ["{}()".format(plane)]
        for end, corner, correction, arc, clear, cap in contours:
            passes = 0
            while clear - passes * step - router.diameter > 0:
                passes += 1
            allowances = []
            for k in range(passes, -1, -1):
                allowance = k * step if cap is None else min(k * step, cap)
                if not allowances or allowance < allowances[-1] - 1e-6:
                    allowances.append(allowance)
            for allowance in allowances:
                lines.append("SP ({},0,{},{},2,_ANF*2,{},0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)".format(corner, _n(-notch_depth), correction, _n(allowance)))
                lines.append("{} ({},{},0,{},0,1,2)".format(arc, end, r, r))
                if thickness - 2 * radius > 1e-6:
                    lines.append("G01 ({},DZ-{},0,0,1,2)".format(end, r))
                lines.append("{} ({},DZ,0,{},0,1,2)".format(arc, corner, r))
                lines.append("EP (2,_ANF*2,0)")
        lines.append("Ebene0()")
        comment = "{}: {} x {} notch milled from the end, corners R{}".format(notch.kind, _n(hi - lo), _n(notch_depth), r)
        return [_Op("side_contour", router.number, lines, comment, 0, (plane, lo))]

    # -- drillings -----------------------------------------------------------

    def _drilling(self, drilling, top):
        ref_side = self.plate.ref_sides[drilling.ref_side_index]
        cylinder = drilling.cylinder_from_params_and_element(self.plate)
        entry_world = _ref_point(ref_side, drilling.start_x, drilling.start_y)
        direction_world = tuple(cylinder.frame.zaxis)
        if _dot(_sub(tuple(cylinder.frame.point), entry_world), direction_world) < 0:
            direction_world = tuple(-c for c in direction_world)
        thickness = self.space.size[2]
        local_entry = self.space.local_point(entry_world)
        local_direction = self.space.local_vector(direction_world)
        through = not drilling.depth_limited
        depth = drilling.depth if drilling.depth_limited else 0.0
        diameter = drilling.diameter
        face = self.space.side_of(ref_side)
        shelf_hole = self._entry is not None and self._entry["operation"] == "shelf_hole"
        # tolerances: deeper, and a bigger diameter if there's a drill for it
        tolerance = self._tolerance()
        if not through:
            depth += tolerance.depth
        if tolerance.width:
            wider = diameter + tolerance.width
            if self.tools.drill_for(wider, depth) or (shelf_hole and self.tools.head_drill_for(wider, depth)):
                diameter = wider
            else:
                self._note("no drill D{} for a D{} hole with its tolerance - drilled D{}".format(_n(wider), _n(diameter), _n(diameter)))
        candidates = self.tools.drills + (self.tools.drill_head_bits if shelf_hole else ())

        def choose(auto, depth):
            drill = self._choose(auto, candidates, lambda d: _close(d.diameter, diameter) and d.usable_length >= depth, "a D{} hole".format(_n(diameter)))
            return drill

        if face:  # into a face, straight down with that face on top
            if abs(abs(local_direction[2]) - 1) > 1e-3:
                self._skip("inclined drilling - not supported")
                return []
            if through or depth >= thickness - 1e-6:
                depth = thickness + self.tools.through_overcut
            x, y, _ = self.space.hops_point(local_entry, face)
            # shelf holes (see cabi_neat.builder) with the drill head if possible, everything else with a spindle drill
            drill = choose(self.tools.head_drill_for(diameter, depth) if shelf_hole else self.tools.drill_for(diameter, depth), depth)
            comment = "Drilling D{}, {} deep".format(_n(diameter), _n(depth))
            if drill is not None and drill.number == self.tools.drill_head:
                comment += " (drill head)"
            if drill is not None:
                return [_Op("drill", drill.number, [_bohrung(x, y, diameter, depth)], comment, face, (y, x))]
            self._skip("drilling D{} {} deep - no drill for it (closest: {})".format(_n(diameter), _n(depth), ", ".join("D" + _n(d) for d in self.tools.closest_drills(diameter))))
            return []

        # into an edge: horizontally, in the last pass
        x, y, z = self.space.hops_point(local_entry, top)
        dx, dy, dz = self.space.hops_vector(local_direction, top)
        if abs(dz) > 1e-3:
            self._skip("inclined drilling into an edge - not supported")
            return []
        if through:
            self._skip("drilling through along the plate - not supported")
            return []
        drill = choose(self.tools.drill_for(diameter, depth), depth)
        if drill is None:
            self._skip("horizontal drilling D{} {} deep - no drill for it".format(_n(diameter), _n(depth)))
            return []
        angle = math.degrees(math.atan2(dy, dx)) % 360.0
        line = "HorzB ({},{},{},{},{},0,0,{},1,0,0,0)".format(_n(x), _n(y), _n(z + thickness), _n(diameter), _n(-depth), _n(angle))
        return [_Op("horizontal", drill.number, [line], "Horizontal drilling D{}, {} deep".format(_n(diameter), _n(depth)), 0, (angle, x, y))]

    # -- Clamex ----------------------------------------------------------------

    def _clamex_face(self, feature):
        ref_side = self.plate.ref_sides[feature.ref_side]
        face = self.space.side_of(ref_side)
        if not face:
            self._skip("Clamex P-14 groove (face) on an edge - not supported")
            return []
        frame = feature.groove_frame(self.plate)
        x, y, _ = self.space.hops_point(self.space.local_point(frame.point), face)
        dx, dy, _ = self.space.hops_vector(self.space.local_vector(frame.xaxis), face)
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        call = _call("Clamex_MS_FI", cl_wkz=self.tools.clamex_finger, cl_typ=1, posx=_n(x), posy=_n(y), dw=_n(angle), esxy=1, vers_b="0.00", wkz_typ=-1)
        return [_Op("clamex_face", None, [call], "Clamex P-14 (face)", face, (x, y))]

    def edge_clamex(self, top):
        """The edge halves of Clamex connectors: ``Clamex1_Stumpf`` for the ends, ``Clamex1_Stumpf_X`` for the long edges.

        Each call has three places per edge: near its start, in the middle and near its end, each measured from its own
        corner. Connectors that don't fit into one call go into another.
        """
        length, width, thickness = self.space.size
        edges = {}  # edge -> [(position along the edge, depth below the top)]
        for feature in self.plate.features:
            if type(feature).__name__ != "P14Edge":
                continue
            point = feature.groove_frame(self.plate).point
            x, y, z = self.space.hops_point(self.space.local_point(point), top)
            if abs(x) < 1e-3:
                edges.setdefault("left", []).append((y, -z))
            elif abs(x - length) < 1e-3:
                edges.setdefault("right", []).append((y, -z))
            elif abs(y) < 1e-3:
                edges.setdefault("front", []).append((x, -z))
            elif abs(y - width) < 1e-3:
                edges.setdefault("rear", []).append((x, -z))
            else:
                self._skip("Clamex P-14 groove (edge) not on the plate's outline - not supported")
        ops = []
        for edge in ("left", "right", "rear", "front"):
            connectors = edges.get(edge)
            if not connectors:
                continue
            span = width if edge in ("left", "right") else length
            for group in _slot_groups(connectors, span):
                ops.append(_Op("clamex_edge", None, [self._stumpf(edge, group)], "Clamex P-14 ({} edge)".format(edge), 0, (edge,)))
        return ops

    def _stumpf(self, edge, slots):
        """One call of ``Clamex1_Stumpf`` or ``_X`` with the connectors ``slots``: {slot (0 start, 1 middle, 2 end): (distance, depth)}.

        In ``Clamex1_Stumpf`` (ends) C1-C3 are the left end and C4-C6 the right, from its rear corner, middle and front
        corner; in ``Clamex1_Stumpf_X`` (long edges) C1-C3 are the rear edge and C4-C6 the front, from its left corner,
        middle and right corner. The distances are along the edge from that corner (from the middle for the middle one).
        """
        t = self.tools
        ends = edge in ("left", "right")
        first = 0 if edge in ("left", "rear") else 3
        params = dict(clamex_id=t.clamex_finger, clamex_id_h=t.clamex_disc, clamex_b_id=t.clamex_drill, art=0 if t.clamex_access_holes else 1)
        params.update(b_pos=_n(t.clamex_hole_position), b_dm=_n(t.clamex_hole_diameter), c_tiefe=14)
        values = {}
        for i in range(6):
            values["c{}".format(i + 1)] = 0
            along, depth = "0", "_RZ/2"
            values[("ey" if ends else "ex") + str(i + 1)] = along
            values[("ex" if ends else "ey") + str(i + 1)] = depth
        # the ends' three places run rear to front, the long edges' left to right
        order = (2, 1, 0) if ends else (0, 1, 2)
        for slot, (distance, depth) in slots.items():
            index = first + order[slot] + 1
            values["c{}".format(index)] = -1
            values[("ey" if ends else "ex") + str(index)] = _n(distance)
            values[("ex" if ends else "ey") + str(index)] = _n(depth)
        for i in range(6):
            params["ex{}".format(i + 1)] = values["ex{}".format(i + 1)]
            params["ey{}".format(i + 1)] = values["ey{}".format(i + 1)]
        for i in range(6):
            params["c{}".format(i + 1)] = values["c{}".format(i + 1)]
        params.update(ril=0, c_typ=1, tenso=0, absenken=0)
        return _call("Clamex1_Stumpf" if ends else "Clamex1_Stumpf_X", **params)


@dataclass(frozen=True)
class TenonNotch:
    """What the HOPS export needs to know about a notch beside a tenon: at the closed end of a stopped dado, or between
    and beside the tenons of a mortise and tenon."""

    depth: float  # how far the tenon goes into the dado or mortise, the notch's depth from the end
    dado_width: float  # the dado's or mortise's width: the tenon plate's thickness
    dado_depth: float
    kind: str = "Stopped dado tenon"  # for comments and messages
    mating: Optional[dict] = None  # the joint's own settings for its dado or mortise (its tool sets the corners')


def _slot_groups(connectors, span):
    """Connectors along an edge (position from its start, depth) in calls of three places: start, middle, end.

    Returns a list of {slot: (distance, depth)}, the distance measured as the macros do: from the edge's start for slot
    0, from the middle for 1, from the end for 2.
    """
    groups = []
    for position, depth in sorted(connectors):
        if abs(position - span / 2.0) < 0.5:
            slot, distance = 1, position - span / 2.0
        elif position < span / 2.0:
            slot, distance = 0, position
        else:
            slot, distance = 2, span - position
        group = next((g for g in groups if slot not in g), None)
        if group is None:
            group = {}
            groups.append(group)
        group[slot] = (distance, depth)
    return groups


def _bohrung(x, y, diameter, depth):
    return "Bohrung ({},{},{},{},10,0,0,1,0,0,0,0)".format(_n(x), _n(y), _n(diameter), _n(-depth))


def _grow(lo, hi, size, open_sides):
    """Widen [lo, hi] to at least ``size`` past its open sides (away from the plate), if it's narrower."""
    missing = size - (hi - lo)
    if missing <= 0:
        return lo, hi
    if open_sides[0] and open_sides[1]:
        return lo - missing / 2.0, hi + missing / 2.0
    if open_sides[0]:
        return lo - missing, hi
    if open_sides[1]:
        return lo, hi + missing
    return lo, hi


def _circle(points, tol=0.05):
    """((cx, cy), radius) if the points lie on a circle (a polygon of at least 8 corners), else None."""
    if len(points) < 8:
        return None
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    radii = [math.dist((cx, cy), p) for p in points]
    radius = max(radii)
    if radius > 0 and max(radii) - min(radii) <= tol * radius:
        return (cx, cy), radius
    return None


def _rectangle(points, tol=1e-3):
    """((cx, cy), (a, b), angle) if the 4 points are a rectangle: side lengths a (at ``angle`` degrees from X) and b."""
    if len(points) != 4:
        return None
    sides = [(points[(i + 1) % 4][0] - points[i][0], points[(i + 1) % 4][1] - points[i][1]) for i in range(4)]
    for i in range(4):
        u, v = sides[i], sides[(i + 1) % 4]
        if abs(u[0] * v[0] + u[1] * v[1]) > tol * max(1.0, math.hypot(*u) * math.hypot(*v)):
            return None
    cx = sum(p[0] for p in points) / 4.0
    cy = sum(p[1] for p in points) / 4.0
    angle = math.degrees(math.atan2(sides[0][1], sides[0][0])) % 180.0
    if angle > 90.0 + 1e-9:
        angle -= 180.0
    return (cx, cy), (math.hypot(*sides[0]), math.hypot(*sides[1])), angle
