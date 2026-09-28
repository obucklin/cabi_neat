"""HOPS programs for the cabinet's plates, written like the workshop's own programs and with its macros.

One program per plate, in the style of ``Hops/Hop/OB/flur``: the header, ``DX``/``DY``/``DZ`` as ``VARS``, ``Fertigteil``
and ``Park_V7``, then the machining:

* format: the saw (``WZS``) and ``_Sformat_V5``, before anything else, so the rest is placed on the finished part;
* pockets (rabbets, grooves, mortises, housings): ``_RechteckTasche_V5`` with the largest router that fits,
  ``_ExecutePocket_V5`` for other shapes;
* hinge pots: ``_Kreisbahn_V5`` with a router (403 Ø20);
* drillings: ``Bohrung`` into faces and ``HorzB`` into edges, always with a spindle drill of the hole's diameter
  (``WZB``), never the drill head;
* Clamex P-14: in faces ``Clamex_MS_FI`` (finger mill), in edges ``Clamex1_Stumpf`` (ends) and ``Clamex1_Stumpf_X``
  (long edges) with the disc, including the access holes.

Which tools there are and which one does what is in :class:`HopsTools`; :meth:`HopsTools.from_too` reads it from the
machine's tool table.

HOPS coordinates: X along the plate's length, Y along its width, Z = 0 on the face on top and negative into the part.
A plate with machining on both faces is done in two passes: the other face first, then a stop to turn it over about its
long edge (X), then the face on top.
"""

import math
from dataclasses import dataclass
from dataclasses import field
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


# the ETH machine, from its tool table 260611_ETH.too (loaded tools), plus the D3 and D8 drills that will be added
ETH_ROUTERS = (
    Router(403, 20.0, 95.0, "Turbex Ø20"),
    Router(402, 13.4, 35.0, "Dia Ø14"),
    Router(405, 12.0, 45.0, "Turbex Ø12"),
    Router(404, 11.8, 45.0, "SRSL Ø12"),
    Router(410, 6.0, 22.0, "ER16 Ø6"),
)
ETH_DRILLS = (
    Drill(604, 3.5, 32.0, "Drill Ø3,5"),
    Drill(609, 5.0, 30.0, "Drill Ø5 short"),
    Drill(607, 4.0, 107.0, "Drill Ø4 long"),
    Drill(608, 6.0, 55.0, "Drill Ø6"),
    Drill(603, 8.0, 80.0, "Drill Ø8"),  # in the table, not loaded yet
    Drill(602, 3.0, 30.0, "Drill Ø3"),  # not in the table yet: number and length are placeholders
)
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
        What's in the tool changer. Default: the ETH machine (tool table 260611_ETH). The drill head isn't used.
    saw
        Saw blade for the format (``WZS``).
    clamex_disc, clamex_finger, clamex_drill
        Clamex tools: the disc for edges (108), the finger mill for faces (136), the drill for access holes (135).
    clamex_hole_diameter
        Diameter of the Clamex access holes (``B_DM`` of ``Clamex1_Stumpf``).
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
    saw: int = 201
    clamex_disc: int = 108
    clamex_finger: int = 136
    clamex_drill: int = 135
    clamex_hole_diameter: float = 6.8
    clamex_access_holes: bool = True
    format_parts: bool = True
    format_depth: float = -2.0
    format_lead: float = 100.0
    through_overcut: float = 0.5

    @classmethod
    def from_too(cls, path, **kwargs):
        """Tools from a HOPS tool table (``.too``, UTF-16). Only loaded tools (a tool number other than -1) are used.

        Routers are the group ``Schaft`` (except those in ``NOT_FOR_POCKETS``), drills ``Drill`` and the saw the first
        of ``Saw blades``. The drill head (``Bohrkopf``) isn't used. Other settings are passed on as ``kwargs``.
        """
        import configparser

        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("latin-1")
        table = configparser.ConfigParser(strict=False, interpolation=None)
        table.optionxform = str
        table.read_string(text)
        routers, drills, saws = [], [], []
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
            elif group == "Saw blades":
                saws.append(number)
        kwargs.setdefault("routers", tuple(sorted(routers, key=lambda r: -r.diameter)))
        kwargs.setdefault("drills", tuple(drills))
        if saws:
            kwargs.setdefault("saw", saws[0])
        return cls(**kwargs)

    def router_for(self, width, depth, length=None):
        """The largest router at most ``width`` (and ``length``) wide that reaches ``depth``, or None."""
        fitting = [r for r in self.routers if r.diameter <= width + 1e-6 and (length is None or r.diameter <= length + 1e-6) and r.usable_length >= depth]
        return max(fitting, key=lambda r: r.diameter, default=None)

    def drill_for(self, diameter, depth):
        """The shortest spindle drill of ``diameter`` that reaches ``depth``, or None."""
        drills = [d for d in self.drills if _close(d.diameter, diameter) and d.usable_length >= depth]
        return min(drills, key=lambda d: d.usable_length, default=None)

    def closest_drills(self, diameter):
        """Diameters that could drill instead, for messages."""
        return sorted({d.diameter for d in self.drills}, key=lambda d: abs(d - diameter))[:2]


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

    ``up`` is +1 when the blank's +z face is on top, -1 when it's turned over (about X: y -> W - y).
    """

    def __init__(self, plate):
        blank = plate.blank
        self.frame = blank.frame
        self.size = (blank.xsize, blank.ysize, blank.zsize)
        self.axes = (tuple(self.frame.xaxis), tuple(self.frame.yaxis), tuple(self.frame.zaxis))

    def local_point(self, point):
        d = _sub(tuple(point), tuple(self.frame.point))
        return tuple(_dot(d, a) + s / 2.0 for a, s in zip(self.axes, self.size))

    def local_vector(self, vector):
        return tuple(_dot(tuple(vector), a) for a in self.axes)

    def hops_point(self, local, up):
        x, y, z = local
        length, width, thickness = self.size
        return (x, y, z - thickness) if up > 0 else (x, width - y, -z)

    def hops_vector(self, local, up):
        x, y, z = local
        return (x, y, z) if up > 0 else (x, -y, -z)

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
ORDER = ("pocket", "pot", "drill", "clamex_face", "horizontal", "clamex_edge")


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
    tools : :class:`HopsTools`, optional
    access_direction : vector, optional
        Where the Clamex access holes should face (world). The face closest to it goes on top if there are any.
    """

    def __init__(self, plate, tools=None, access_direction=None, comment=""):
        self.plate = plate
        self.tools = tools or HopsTools()
        self.access_direction = access_direction
        self.comment = comment
        self.space = _PlateSpace(plate)
        self.skipped = []
        self._contours = 0

    # -- building ---------------------------------------------------------

    def _skip(self, message):
        self.skipped.append("{}: {}".format(self.plate.name, message))

    def text(self):
        space = self.space
        length, width, thickness = space.size
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

        ops = {top: [], -top: [], 0: []}
        for index, feature in enumerate(features):
            name = type(feature).__name__
            if index == 0 and name == "FreeContour" and not feature.counter_sink:
                self._outline(feature)
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
                lines += self._format()
                formatted = True
            lines += self._render(ops[first])
            lines.append(_call("MachineStop_V7", mode=0, parkmode=10, parkposx=0, parkposy=0, typ=0, r6=0, str="'Turn the panel over about its long edge (X)'", r7=0))
            lines.append(";===== Pass 2 =====")
        if not formatted:
            lines += self._format()
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

    def _format(self):
        t = self.tools
        return [
            "; Format",
            _tool("WZS", t.saw),
            _call("_Sformat_V5", tiefe=_n(t.format_depth), richtung=0, einlauf=_n(t.format_lead), l=1, v=1, r=1, h=1, esmd=1),
        ]

    def _face_of(self, feature):
        name = type(feature).__name__
        if name in ("FreeContour", "Drilling"):
            return self.space.side_of(self.plate.ref_sides[feature.ref_side_index])
        if name == "P14Face":
            return self.space.side_of(self.plate.ref_sides[feature.ref_side])
        return 0

    def _outline(self, feature):
        contour = feature.contour_param_object
        points = [self.space.local_point(_ref_point(self.plate.ref_sides[feature.ref_side_index], p[0], p[1])) for p in contour.polyline]
        length, width, _ = self.space.size
        if any(min(abs(p[0]), abs(p[0] - length)) > 0.01 or min(abs(p[1]), abs(p[1] - width)) > 0.01 for p in points):
            self._skip("non-rectangular outline - not supported yet, cut it to shape separately")

    def _convert(self, feature, top):
        name = type(feature).__name__
        if name == "FreeContour" and feature.counter_sink:
            return self._pocket(feature)
        if name == "Drilling":
            return self._drilling(feature, top)
        if name == "P14Face":
            return self._clamex_face(feature)
        if name == "P14Edge":
            return []  # all edges' connectors together, below
        self._skip("{} - not supported".format(name))
        return []

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
        circle = _circle(points)
        if circle:
            return self._pot(circle, depth, face)
        rectangle = _rectangle(points)
        if rectangle:
            return self._rectangle_pocket(rectangle, depth, face)
        return self._free_pocket(points, depth, face)

    def _pot(self, circle, depth, face):
        (cx, cy), radius = circle
        t = self.tools
        comment = "Pot D{} {} deep".format(_n(2 * radius), _n(depth))
        # a router that clears the whole pot on one circle around the center
        routers = [r for r in t.routers if radius - 1e-6 <= r.diameter <= 2 * radius + 1e-6 and r.usable_length >= depth]
        router = max(routers, key=lambda r: r.diameter, default=None)
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
            biggest = max((r.diameter for r in self.tools.routers if r.usable_length >= depth), default=0)
            x0, x1 = _grow(x0, x1, biggest, open_x)
            y0, y1 = _grow(y0, y1, biggest, open_y)
            cx, cy, a, b, angle = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, 0.0
        router = self.tools.router_for(min(a, b), depth, max(a, b))
        if router is None:
            self._skip("pocket {} x {}, {} deep - no router fits".format(_n(a), _n(b), _n(depth)))
            return []
        call = _call(
            "_RechteckTasche_V5",
            x_mitte=_n(cx),
            y_mitte=_n(cy),
            taschenlaenge=_n(a),
            taschenbreite=_n(b),
            radius=0,
            winkel=_n(angle),
            tiefe=_n(-depth),
            zustellung="_AT_MAXDEPTH",
            ab=2,
            abf="_ANF",
            interpol=1,
            umkehren=0,
            esxy=1,
            esmd=0,
            laser=0,
        )
        return [_Op("pocket", router.number, [call], "Pocket {} x {}, {} deep".format(_n(a), _n(b), _n(depth)), face, (cy, cx))]

    def _rabbet(self, x0, x1, y0, y1, open_x, open_y, depth, face):
        """A rabbet (a pocket open along one side and at both ends) as a single line along its inner edge.

        As in the earlier programs: ``SP`` at the part's edge, one relative ``G01`` over the part's length or width,
        ``EP``; radius correction 1, the tool on the left of the path, travelling so that's the rabbet's side. ``SP``'s Z
        is the rabbet's floor above the underside. Returns None if it isn't a rabbet or no router is wide enough.
        """
        length, width, thickness = self.space.size
        # the contour usually runs past the part's edges: only what's in the part counts
        x0, x1, y0, y1 = max(x0, 0.0), min(x1, length), max(y0, 0.0), min(y1, width)
        if all(open_x) and sum(open_y) == 1:  # along X
            rabbet_width = y1 - y0
            if open_y[0]:  # at the front: travel -X along its rear edge
                start, move = (length, y1), "-DX,0"
            else:  # at the rear: travel +X along its front edge
                start, move = (0.0, y0), "DX,0"
        elif all(open_y) and sum(open_x) == 1:  # along Y
            rabbet_width = x1 - x0
            if open_x[0]:  # at the left end: travel +Y along its right edge
                start, move = (x1, 0.0), "0,DY"
            else:  # at the right end: travel -Y along its left edge
                start, move = (x0, width), "0,-DY"
        else:
            return None
        routers = [r for r in self.tools.routers if r.diameter >= rabbet_width - 1e-6 and r.usable_length >= depth]
        router = max(routers, key=lambda r: r.diameter, default=None)
        if router is None:
            return None
        lines = [
            "SP ({},{},{},1,1,_ANF*2,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0.5mm)".format(_n(start[0]), _n(start[1]), _n(thickness - depth)),
            "G01 ({},0,0,10,2)".format(move),
            "EP (1,_ANF*2,0)",
        ]
        comment = "Rabbet {} wide, {} deep".format(_n(rabbet_width), _n(depth))
        return [_Op("pocket", router.number, lines, comment, face, (start[1], start[0]))]

    def _free_pocket(self, points, depth, face):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        router = self.tools.router_for(min(max(xs) - min(xs), max(ys) - min(ys)) / 2.0, depth)
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

        if face:  # into a face, straight down with that face on top
            if abs(abs(local_direction[2]) - 1) > 1e-3:
                self._skip("inclined drilling - not supported")
                return []
            if through or depth >= thickness - 1e-6:
                depth = thickness + self.tools.through_overcut
            x, y, _ = self.space.hops_point(local_entry, face)
            drill = self.tools.drill_for(diameter, depth)
            comment = "Drilling D{}, {} deep".format(_n(diameter), _n(depth))
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
        drill = self.tools.drill_for(diameter, depth)
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
            x, y, z = self.space.hops_point(self.space.local_point(feature.groove_frame(self.plate).point), top)
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
        params.update(b_pos="7.5", b_dm=_n(t.clamex_hole_diameter), c_tiefe=14)
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
