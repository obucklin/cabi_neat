"""Cabinet joints on top of compas_timber's plate joints."""

import math

from compas.geometry import Cylinder
from compas.geometry import Frame
from compas.geometry import Line
from compas.geometry import Plane
from compas.geometry import Point
from compas.geometry import Polyline
from compas.geometry import Vector
from compas.geometry import angle_vectors_signed
from compas.geometry import cross_vectors
from compas.geometry import distance_point_plane
from compas.geometry import dot_vectors
from compas.geometry import intersection_plane_plane
from compas_brep import Brep
from compas_model.elements import Feature
from compas_timber.connections import Joint
from compas_timber.connections import JointTopology
from compas_timber.connections import PlateButtJoint
from compas_timber.errors import BeamJoiningError
from compas_timber.fabrication import Drilling
from compas_timber.fabrication import FreeContour
from compas_timber.geometry import brep_difference_first

from .config import HINGE_DEFAULTS
from .config import hinge_positions


def _refresh_blanks(*plates):
    """Make sure the plates' reference sides include this round of edge extensions.

    FreeContour stores its contour relative to a reference side, which is derived from the plate's blank. In
    compas_timber the blank is computed from outlines that only take on the extension planes once the plate's shape is
    computed, and it's cached without being reset by ``Plate.set_extension_plane``. Contours created before that would
    be placed relative to the unextended plate but applied relative to the extended one, missing their target.
    So apply the extensions to the outlines now, then reset the cache (``add_features`` does so as a side effect).
    """
    for plate in plates:
        plate.apply_edge_extensions()
        plate.add_features([])


class PlateRabbetJoint(PlateButtJoint):
    """An L joint where the main plate's edge sits in a rabbet cut into the cross plate, e.g. a cabinet back in the sides.

    Like :class:`compas_timber.connections.PlateLButtJoint`, the cross plate is extended to the main plate's outer
    face. The main plate is extended past the cross plate's inner face by ``rabbet_depth``, into a rabbet that is cut
    into the cross plate with a :class:`compas_timber.fabrication.FreeContour`.

    Parameters
    ----------
    plate_a : :class:`compas_timber.elements.Plate`
        The main plate, whose edge sits in the rabbet.
    plate_b : :class:`compas_timber.elements.Plate`
        The cross plate, which gets the rabbet.
    rabbet_depth : float, optional
        How deep the rabbet goes into the cross plate. Defaults to half the cross plate's thickness.
    **kwargs
        Passed to :class:`compas_timber.connections.PlateJoint` (``topology``, ``a_segment_index``, ``b_segment_index``, ...).
    """

    SUPPORTED_TOPOLOGY = JointTopology.TOPO_EDGE_EDGE

    @property
    def __data__(self):
        data = super(PlateRabbetJoint, self).__data__
        data["rabbet_depth"] = self.rabbet_depth
        return data

    def __init__(self, plate_a=None, plate_b=None, rabbet_depth=None, **kwargs):
        super(PlateRabbetJoint, self).__init__(plate_a, plate_b, **kwargs)
        self.rabbet_depth = rabbet_depth

    def __repr__(self):
        return "PlateRabbetJoint({0}, {1}, {2})".format(self.main_plate, self.cross_plate, JointTopology.get_name(self.topology))

    @property
    def depth(self):
        """The rabbet depth in use: ``rabbet_depth``, or half the cross plate's thickness."""
        return self.rabbet_depth if self.rabbet_depth is not None else self.cross_plate.thickness / 2.0

    def _into_cross_plate(self):
        """Unit vector normal to the cross plate, pointing from its inner face into the plate."""
        inner, outer = self._cross_planes
        normal = Vector(*inner.normal).unitized()
        if dot_vectors(Vector.from_start_end(inner.point, outer.point), normal) < 0:
            normal = -normal
        return normal

    def _set_edge_planes(self):
        if not 0 < self.depth < self.cross_plate.thickness:
            raise BeamJoiningError(
                beams=[self.main_plate, self.cross_plate],
                joint=self,
                debug_info="Rabbet depth {} must be between 0 and the cross plate's thickness {}.".format(self.depth, self.cross_plate.thickness),
            )
        inner = self._cross_planes[0]
        rabbet_floor = Plane(inner.point + self._into_cross_plate() * self.depth, inner.normal)
        self.main_plate.set_extension_plane(self.main_segment_index, rabbet_floor)
        self.cross_plate.set_extension_plane(self.cross_segment_index, self._main_planes[1])

    def add_features(self):
        """Cut the rabbet into the cross plate: a pocket on its inner face, as wide as the main plate is thick."""
        _refresh_blanks(self.main_plate, self.cross_plate)
        inner = self._cross_planes[0]
        near, far = self._main_planes
        edge = self.b_outlines[0].lines[self.cross_segment_index]
        # overshoot so the pocket cuts cleanly through the plate's ends and its edge
        margin = self.cross_plate.thickness + self.main_plate.thickness
        along = edge.direction.unitized()
        start = edge.start - along * margin
        end = edge.end + along * margin

        def on_plane(plane, offset=0.0):
            """Where ``plane`` (shifted by ``offset`` along its normal, away from the near face) meets the inner face."""
            normal = Vector(*plane.normal).unitized()
            if dot_vectors(Vector.from_start_end(near.point, far.point), normal) < 0:
                normal = -normal
            line = Line(*intersection_plane_plane(Plane(plane.point + normal * offset, plane.normal), inner))
            return line.closest_point(start), line.closest_point(end)

        a0, a1 = on_plane(near)
        b0, b1 = on_plane(far, margin)
        contour = Polyline([a0, a1, b1, b0, a0])
        feature = FreeContour.from_polyline_and_element(contour, self.cross_plate, depth=self.depth, interior=True)
        self.cross_plate.add_features(feature)
        self.features.append(feature)


class _PlateHousingJoint(PlateButtJoint):
    """Base for T joints where the main plate's edge sits in pockets cut into the cross plate's face.

    The main plate's edge is extended ``depth`` into the cross plate. Subclasses then decide which parts of that
    extension to keep (:meth:`_trim` cuts it away through the main plate) and where to cut pockets into the cross plate
    (:meth:`_pocket`). Positions along the joining edge are distances from the start of the main plate's edge.
    """

    SUPPORTED_TOPOLOGY = JointTopology.TOPO_EDGE_FACE
    DEPTH_FRACTION = 0.25  # default depth, as a fraction of the cross plate's thickness

    def __init__(self, plate_a=None, plate_b=None, depth=None, **kwargs):
        super(_PlateHousingJoint, self).__init__(plate_a, plate_b, **kwargs)
        self.depth = depth

    @property
    def pocket_depth(self):
        """The depth in use: ``depth``, or ``DEPTH_FRACTION`` of the cross plate's thickness."""
        return self.depth if self.depth is not None else self.cross_plate.thickness * self.DEPTH_FRACTION

    def _error(self, message):
        return BeamJoiningError(beams=[self.main_plate, self.cross_plate], joint=self, debug_info=message)

    def _into_cross_plate(self):
        inner, outer = self._cross_planes
        normal = Vector(*inner.normal).unitized()
        if dot_vectors(Vector.from_start_end(inner.point, outer.point), normal) < 0:
            normal = -normal
        return normal

    def _floor(self):
        """The pocket floor: the cross plate's inner face, moved ``pocket_depth`` into the cross plate."""
        inner = self._cross_planes[0]
        return Plane(inner.point + self._into_cross_plate() * self.pocket_depth, inner.normal)

    def _set_edge_planes(self):
        if not 0 < self.pocket_depth < self.cross_plate.thickness:
            raise self._error("Depth {} must be between 0 and the cross plate's thickness {}.".format(self.pocket_depth, self.cross_plate.thickness))
        self.main_plate.set_extension_plane(self.main_segment_index, self._floor())

    @property
    def _margin(self):
        """Overshoot for pockets that run out of a plate, so they cut cleanly."""
        return self.cross_plate.thickness + self.main_plate.thickness

    def _edge(self):
        """The main plate's joining edge, its unit direction and its length."""
        edge = self.a_outlines[0].lines[self.main_segment_index]
        return edge, edge.direction.unitized(), edge.length

    def _cross_span(self):
        """(min, max) positions of the cross plate along the joining edge."""
        edge, along, _ = self._edge()
        positions = [dot_vectors(Vector.from_start_end(edge.start, p), along) for p in self.cross_plate.outline_a.points]
        return min(positions), max(positions)

    def _add(self, plate, feature):
        plate.add_features(feature)
        self.features.append(feature)

    def _rectangle(self, line_a, line_b, start, end):
        edge, along, _ = self._edge()

        def point(line, t):
            return line.closest_point(edge.start + along * t)

        return Polyline([point(line_a, start), point(line_a, end), point(line_b, end), point(line_b, start), point(line_a, start)])

    def _pocket(self, start, end):
        """Cut a pocket into the cross plate's face, as wide as the main plate is thick, from ``start`` to ``end``."""
        inner = self._cross_planes[0]
        near, far = self._main_planes
        contour = self._rectangle(Line(*intersection_plane_plane(near, inner)), Line(*intersection_plane_plane(far, inner)), start, end)
        self._add(self.cross_plate, FreeContour.from_polyline_and_element(contour, self.cross_plate, depth=self.pocket_depth, interior=True))

    def _trim(self, start, end):
        """Cut away the main plate's extension into the cross plate from ``start`` to ``end``."""
        inner = self._cross_planes[0]
        near = self._main_planes[0]
        floor = self._floor()
        beyond = Plane(floor.point + self._into_cross_plate() * self._margin, floor.normal)
        contour = self._rectangle(Line(*intersection_plane_plane(inner, near)), Line(*intersection_plane_plane(beyond, near)), start, end)
        self._add(self.main_plate, FreeContour.from_polyline_and_element(contour, self.main_plate, depth=self.main_plate.thickness, interior=True))


class PlateMortiseTenon(_PlateHousingJoint):
    """A T joint where tenons on the main plate's edge sit in blind mortises in the cross plate's face, e.g. a shelf in a side.

    The main plate's edge is extended ``depth`` into the cross plate. :class:`compas_timber.fabrication.FreeContour`
    pockets then cut away that extension between the tenons (through the main plate) and cut matching mortises into
    the cross plate (``depth`` deep). Tenons are as thick as the main plate.

    Parameters
    ----------
    plate_a : :class:`compas_timber.elements.Plate`
        The main plate, whose edge carries the tenons.
    plate_b : :class:`compas_timber.elements.Plate`
        The cross plate, which gets the mortises.
    depth : float, optional
        How far the tenons go into the cross plate. Defaults to a quarter of the cross plate's thickness.
    tenon_count : int, optional
        Number of tenons along the edge. Default is 2.
    tenon_length : float, optional
        Length of each tenon along the edge. By default the edge is divided into ``2 * tenon_count + 1`` equal parts,
        with tenons in every other part, so both ends of the edge are free.
    **kwargs
        Passed to :class:`compas_timber.connections.PlateJoint` (``topology``, ``a_segment_index``, ``b_segment_index``, ...).
    """

    @property
    def __data__(self):
        data = super(PlateMortiseTenon, self).__data__
        data["depth"] = self.depth
        data["tenon_count"] = self.tenon_count
        data["tenon_length"] = self.tenon_length
        return data

    def __init__(self, plate_a=None, plate_b=None, depth=None, tenon_count=2, tenon_length=None, **kwargs):
        super(PlateMortiseTenon, self).__init__(plate_a, plate_b, depth=depth, **kwargs)
        self.tenon_count = tenon_count
        self.tenon_length = tenon_length

    def __repr__(self):
        return "PlateMortiseTenon({0}, {1}, {2})".format(self.main_plate, self.cross_plate, JointTopology.get_name(self.topology))

    @property
    def tenon_depth(self):
        """The depth in use: ``depth``, or a quarter of the cross plate's thickness."""
        return self.pocket_depth

    def _set_edge_planes(self):
        if self.tenon_count < 1:
            raise self._error("There must be at least one tenon.")
        super(PlateMortiseTenon, self)._set_edge_planes()

    def _intervals(self, length):
        """(start, end) of each tenon along an edge of ``length``, from its start."""
        n = self.tenon_count
        if self.tenon_length is None:
            part = length / (2 * n + 1)
            return [((2 * i + 1) * part, (2 * i + 2) * part) for i in range(n)]
        gap = (length - n * self.tenon_length) / (n + 1)
        if gap <= 0:
            raise self._error("{} tenons of {} don't fit on an edge of {}.".format(n, self.tenon_length, length))
        return [(gap + i * (self.tenon_length + gap), gap + i * (self.tenon_length + gap) + self.tenon_length) for i in range(n)]

    def add_features(self):
        """Cut the mortises into the cross plate, and cut away the extension between the tenons."""
        _refresh_blanks(self.main_plate, self.cross_plate)
        _, _, length = self._edge()
        intervals = self._intervals(length)
        for start, end in intervals:
            self._pocket(start, end)
        bounds = [-self._margin] + [t for interval in intervals for t in interval] + [length + self._margin]
        for start, end in zip(bounds[::2], bounds[1::2]):
            self._trim(start, end)


class PlateStoppedDadoJoint(_PlateHousingJoint):
    """A T joint where the main plate sits in a stopped dado (housing): a groove in the cross plate's face that runs out
    through one of its edges and stops short at the other end, e.g. a shelf slid into a side from the back.

    The main plate's edge is extended ``depth`` into the groove, except for its first ``stop_distance`` at the closed
    end, which is notched so the joint doesn't show there. The groove runs from the stop out through the cross plate's
    edge in ``open_direction``. Both cuts are :class:`compas_timber.fabrication.FreeContour` s.

    Parameters
    ----------
    plate_a : :class:`compas_timber.elements.Plate`
        The main plate, which slides into the groove.
    plate_b : :class:`compas_timber.elements.Plate`
        The cross plate, which gets the groove.
    depth : float, optional
        Depth of the groove. Defaults to a third of the cross plate's thickness.
    stop_distance : float, optional
        How far from the main plate's closed end the groove stops. Default is 20.
    open_direction : :class:`compas.geometry.Vector`
        Direction, roughly along the joining edge, in which the groove runs out of the cross plate, e.g. towards the
        back of a cabinet.
    **kwargs
        Passed to :class:`compas_timber.connections.PlateJoint` (``topology``, ``a_segment_index``, ``b_segment_index``, ...).
    """

    DEPTH_FRACTION = 1.0 / 3.0

    @property
    def __data__(self):
        data = super(PlateStoppedDadoJoint, self).__data__
        data["depth"] = self.depth
        data["stop_distance"] = self.stop_distance
        data["open_direction"] = self.open_direction
        return data

    def __init__(self, plate_a=None, plate_b=None, depth=None, stop_distance=20.0, open_direction=None, **kwargs):
        super(PlateStoppedDadoJoint, self).__init__(plate_a, plate_b, depth=depth, **kwargs)
        self.stop_distance = stop_distance
        self.open_direction = Vector(*open_direction) if open_direction is not None else None

    def __repr__(self):
        return "PlateStoppedDadoJoint({0}, {1}, {2})".format(self.main_plate, self.cross_plate, JointTopology.get_name(self.topology))

    def _check_direction(self):
        if self.open_direction is None:
            raise self._error("A stopped dado needs an open_direction: which end of the groove runs out of the plate.")

    def _set_edge_planes(self):
        self._check_direction()
        _, along, length = self._edge()
        if abs(dot_vectors(self.open_direction.unitized(), along)) < 1e-6:
            raise self._error("open_direction must point along the joining edge.")
        if not 0 <= self.stop_distance < length:
            raise self._error("Stop distance {} must be between 0 and the edge length {}.".format(self.stop_distance, length))
        super(PlateStoppedDadoJoint, self)._set_edge_planes()

    def groove_interval(self):
        """(start, end) of the groove along the joining edge, from the start of the main plate's edge."""
        _, along, length = self._edge()
        cross_min, cross_max = self._cross_span()
        if dot_vectors(self.open_direction, along) > 0:
            return self.stop_distance, cross_max + self._margin
        return cross_min - self._margin, length - self.stop_distance

    def add_features(self):
        """Cut the groove into the cross plate, and notch the main plate's extension at the closed end."""
        self._check_direction()
        _refresh_blanks(self.main_plate, self.cross_plate)
        _, along, length = self._edge()
        start, end = self.groove_interval()
        self._pocket(start, end)
        if dot_vectors(self.open_direction, along) > 0:
            self._trim(-self._margin, start)
        else:
            self._trim(end, length + self._margin)


class PlateGrooveJoint(_PlateHousingJoint):
    """A T joint where the main plate's whole edge sits in a groove in the cross plate's face, e.g. a drawer bottom.

    The main plate's edge is extended ``depth`` into the groove, a :class:`compas_timber.fabrication.FreeContour` in
    the cross plate. The groove either runs ``through`` the cross plate, out at both ends, or stops ``overhang`` past
    both ends of the main plate's edge, where plates joined to the neighbouring edges reach into their own grooves.

    Parameters
    ----------
    plate_a : :class:`compas_timber.elements.Plate`
        The main plate, whose edge sits in the groove.
    plate_b : :class:`compas_timber.elements.Plate`
        The cross plate, which gets the groove.
    depth : float, optional
        Depth of the groove. Defaults to half the cross plate's thickness.
    through : bool, optional
        Run the groove out of the cross plate at both ends. Default is False.
    overhang : float, optional
        For a stopped groove: how far it runs past both ends of the main plate's edge, as it was before any joint
        extended it. By default the groove is exactly as long as the extended edge, e.g. including the corners of a
        panel that also sits in grooves at its ends: as short as it can be without cutting the panel. Whatever of the
        main plate reaches past the groove's ends (e.g. those corners, with ``overhang=0``) is cut away.
    **kwargs
        Passed to :class:`compas_timber.connections.PlateJoint` (``topology``, ``a_segment_index``, ``b_segment_index``, ...).
    """

    DEPTH_FRACTION = 0.5

    @property
    def __data__(self):
        data = super(PlateGrooveJoint, self).__data__
        data["depth"] = self.depth
        data["through"] = self.through
        data["overhang"] = self.overhang
        return data

    def __init__(self, plate_a=None, plate_b=None, depth=None, through=False, overhang=None, **kwargs):
        super(PlateGrooveJoint, self).__init__(plate_a, plate_b, depth=depth, **kwargs)
        self.through = through
        self.overhang = overhang

    def __repr__(self):
        return "PlateGrooveJoint({0}, {1}, {2})".format(self.main_plate, self.cross_plate, JointTopology.get_name(self.topology))

    def _set_edge_planes(self):
        # the edge as it is before any joint extends it (e.g. into grooves at its ends), which is what a stopped groove
        # has to house; extensions only reach the outlines once the plate's shape is computed, after this
        edge = self.a_outlines[0].lines[self.main_segment_index]
        self._original_edge = (Point(*edge.start), Point(*edge.end))
        super(PlateGrooveJoint, self)._set_edge_planes()

    def groove_interval(self):
        """(start, end) of the groove along the joining edge, from the start of the main plate's edge."""
        if self.through:
            cross_min, cross_max = self._cross_span()
            return cross_min - self._margin, cross_max + self._margin
        edge, along, length = self._edge()
        if self.overhang is None:
            # the edge as extended by all joints (e.g. into grooves at its ends): as short as possible, nothing cut away
            return 0.0, length
        original = getattr(self, "_original_edge", None) or (edge.start, edge.end)
        ends = sorted(dot_vectors(Vector.from_start_end(edge.start, p), along) for p in original)
        return ends[0] - self.overhang, ends[1] + self.overhang

    def add_features(self):
        """Cut the groove into the cross plate, and anything of the main plate reaching past its ends."""
        _refresh_blanks(self.main_plate, self.cross_plate)
        start, end = self.groove_interval()
        self._pocket(start, end)
        if not self.through:
            _, _, length = self._edge()
            self._trim(min(start, 0.0) - self._margin, start)
            self._trim(end, max(end, length) + self._margin)


# ==========================================================================
# Lamello Clamex P-14
# ==========================================================================


class _P14Groove(Feature):
    """A Lamello P-System groove: the segment of a blade disc, cut into an element.

    Subclasses define where the groove is, as a frame at the center of the connector on the element's surface:
    x along the groove, y across its width, z into the element.
    """

    BLADE_RADIUS = 50.2  # Zeta P2 blade, 100.4 mm diameter
    WIDTH = 7.0
    DEPTH = 14.7

    def __init__(self, name=None, is_joinery=True):
        super(_P14Groove, self).__init__(name=name)
        self._is_joinery = is_joinery

    @property
    def is_joinery(self):
        """True if a joint created this feature, so it's regenerated by the joint rather than serialized with the element."""
        return self._is_joinery

    def groove_frame(self, element):
        raise NotImplementedError

    def groove(self, element):
        """The groove's volume in model coordinates."""
        frame = self.groove_frame(element)
        # the blade's axis runs across the groove; its center sits so that the blade reaches DEPTH into the element
        center = frame.point + frame.zaxis * (self.DEPTH - self.BLADE_RADIUS)
        blade = Frame(center, frame.zaxis, frame.xaxis)  # normal = zaxis x xaxis = yaxis
        return Brep.from_cylinder(Cylinder(self.BLADE_RADIUS, self.WIDTH, blade))

    def apply(self, geometry, element):
        groove = self.groove(element)
        groove.transform(element.modeltransformation.inverse())
        return brep_difference_first(geometry, groove)


class P14Edge(_P14Groove):
    """The half of a Clamex P-14 connector that sits in an element's edge.

    Parameters
    ----------
    ref_side : int
        Index of the element's reference side (see ``element.ref_sides``) that is the edge face.
    location : float
        Position of the connector's center along the reference side's x axis. It's centered across the face's width.
    """

    @property
    def __data__(self):
        return {"ref_side": self.ref_side, "location": self.location, "is_joinery": self.is_joinery}

    def __init__(self, ref_side, location, name=None, is_joinery=True):
        super(P14Edge, self).__init__(name=name, is_joinery=is_joinery)
        self.ref_side = ref_side
        self.location = location

    def __repr__(self):
        return "P14Edge(ref_side={}, location={:.1f})".format(self.ref_side, self.location)

    def groove_frame(self, element):
        side = element.ref_sides[self.ref_side]
        # the middle of the element across this face (its thickness, for a plate's edge)
        across = [dot_vectors(Vector.from_start_end(side.point, p), side.yaxis) for outline in element.outlines for p in outline.points]
        point = side.point + side.xaxis * self.location + side.yaxis * ((min(across) + max(across)) / 2.0)
        return Frame(point, side.xaxis, cross_vectors(-side.normal, side.xaxis))


class P14Face(_P14Groove):
    """The half of a Clamex P-14 connector that sits in an element's face.

    Parameters
    ----------
    ref_side : int
        Index of the element's reference side (see ``element.ref_sides``) with the groove.
    x, y : float
        Position of the connector's center in the reference side's frame.
    orientation : float
        Angle in degrees of the connector's axis, from the reference side's x axis, around its normal.
    """

    @property
    def __data__(self):
        return {"ref_side": self.ref_side, "x": self.x, "y": self.y, "orientation": self.orientation, "is_joinery": self.is_joinery}

    def __init__(self, ref_side, x, y, orientation, name=None, is_joinery=True):
        super(P14Face, self).__init__(name=name, is_joinery=is_joinery)
        self.ref_side = ref_side
        self.x = x
        self.y = y
        self.orientation = orientation

    def __repr__(self):
        return "P14Face(ref_side={}, x={:.1f}, y={:.1f}, orientation={:.1f})".format(self.ref_side, self.x, self.y, self.orientation)

    def groove_frame(self, element):
        side = element.ref_sides[self.ref_side]
        angle = math.radians(self.orientation)
        along = side.xaxis * math.cos(angle) + side.yaxis * math.sin(angle)
        point = side.point + side.xaxis * self.x + side.yaxis * self.y
        return Frame(point, along, cross_vectors(-side.normal, along))


class ClamexP14Joint(PlateButtJoint):
    """A butt joint held by Lamello Clamex P-14 connectors, e.g. a top on a side.

    The plates are trimmed like :class:`compas_timber.connections.PlateLButtJoint` (L topology) or
    :class:`compas_timber.connections.PlateTButtJoint` (T topology). Connectors sit at the middle of the main plate's
    thickness, ``distance`` from both ends of the joining edge, plus one in the middle if ``center_connector`` is set.
    Each connector adds a :class:`P14Edge` to the main plate and a :class:`P14Face` to the cross plate.

    Parameters
    ----------
    plate_a : :class:`compas_timber.elements.Plate`
        The main plate, whose edge meets the cross plate's face.
    plate_b : :class:`compas_timber.elements.Plate`
        The cross plate.
    distance : float, optional
        Distance from each end of the joining edge to the nearest connector's center. Default is 60.
    center_connector : bool, optional
        Add a connector in the middle of the edge. Default is False.
    connector_count : int, optional
        Number of connectors, overriding ``distance``/``center_connector``: 1 puts a single connector in the middle of
        the edge, more are spread evenly from ``distance`` to ``distance`` from the ends.
    dowels : bool, optional
        Add two alignment dowels, ``dowel_distance`` from the ends of the edge. Default is False.
    dowel_distance : float, optional
        Distance from each end of the joining edge to a dowel's center. Default is 30.
    dowel_diameter : float, optional
        Default is 8.
    dowel_edge_depth, dowel_face_depth : float, optional
        Depth of the dowel holes in the main plate's edge and in the cross plate's face. Defaults are 20 and 10.
    **kwargs
        Passed to :class:`compas_timber.connections.PlateJoint` (``topology``, ``a_segment_index``, ``b_segment_index``, ...).

    Notes
    -----
    If the edge is too short for two connectors ``distance`` from its ends, a single connector is placed in its middle.
    """

    SUPPORTED_TOPOLOGY = JointTopology.TOPO_EDGE_FACE

    @property
    def __data__(self):
        data = super(ClamexP14Joint, self).__data__
        data["distance"] = self.distance
        data["center_connector"] = self.center_connector
        data["connector_count"] = self.connector_count
        data["dowels"] = self.dowels
        data["dowel_distance"] = self.dowel_distance
        data["dowel_diameter"] = self.dowel_diameter
        data["dowel_edge_depth"] = self.dowel_edge_depth
        data["dowel_face_depth"] = self.dowel_face_depth
        return data

    def __init__(
        self,
        plate_a=None,
        plate_b=None,
        distance=60.0,
        center_connector=False,
        connector_count=None,
        dowels=False,
        dowel_distance=30.0,
        dowel_diameter=8.0,
        dowel_edge_depth=20.0,
        dowel_face_depth=10.0,
        **kwargs,
    ):
        super(ClamexP14Joint, self).__init__(plate_a, plate_b, **kwargs)
        self.distance = distance
        self.center_connector = center_connector
        self.connector_count = connector_count
        self.dowels = dowels
        self.dowel_distance = dowel_distance
        self.dowel_diameter = dowel_diameter
        self.dowel_edge_depth = dowel_edge_depth
        self.dowel_face_depth = dowel_face_depth

    def __repr__(self):
        return "ClamexP14Joint({0}, {1}, {2})".format(self.main_plate, self.cross_plate, JointTopology.get_name(self.topology))

    def _set_edge_planes(self):
        self.main_plate.set_extension_plane(self.main_segment_index, self._cross_planes[0])
        if self.topology == JointTopology.TOPO_EDGE_EDGE:
            self.cross_plate.set_extension_plane(self.cross_segment_index, self._main_planes[1])

    def _into_cross_plate(self):
        inner, outer = self._cross_planes
        normal = Vector(*inner.normal).unitized()
        if dot_vectors(Vector.from_start_end(inner.point, outer.point), normal) < 0:
            normal = -normal
        return normal

    def _span(self):
        """The joining edge, its unit direction, and the (start, end) of the part of it that overlaps the cross plate."""
        edge = self.a_outlines[0].lines[self.main_segment_index]
        along = edge.direction.unitized()
        span = [dot_vectors(Vector.from_start_end(edge.start, p), along) for p in self.cross_plate.outline_a.points]
        return edge, along, max(0.0, min(span)), min(edge.length, max(span))

    def _frames_at(self, positions):
        """Frames on the cross plate's face at the middle of the main plate's thickness, at ``positions`` along the edge.

        x runs along the joining edge, y is the main plate's normal and z points into the main plate.
        """
        inner = self._cross_planes[0]
        near, far = self._main_planes
        edge, along, _, _ = self._span()
        thickness = Vector.from_start_end(near.point, far.point)
        normal = Vector(*near.normal).unitized()
        half = normal * (dot_vectors(thickness, normal) / 2.0)  # from the near face to the middle of the main plate
        yaxis = Vector(*cross_vectors(-self._into_cross_plate(), along))
        return [Frame(Point(*inner.closest_point(edge.start + along * t + half)), along, yaxis) for t in positions]

    def connector_positions(self):
        """Positions of the connectors along the joining edge."""
        _, _, start, end = self._span()
        count = self.connector_count
        if count == 1 or (count is None and end - start < 2 * self.distance):
            return [(start + end) / 2.0]
        if count is not None:
            return [start + self.distance + i * (end - start - 2 * self.distance) / (count - 1) for i in range(count)]
        positions = [start + self.distance, end - self.distance]
        if self.center_connector:
            positions.insert(1, (start + end) / 2.0)
        return positions

    def _check_dowels(self):
        """Raise if a dowel hole would cut into a connector's groove (the edge is too short for both)."""
        if not self.dowels:
            return
        r, d = _P14Groove.BLADE_RADIUS, _P14Groove.DEPTH
        half_groove = math.sqrt(r**2 - (r - d) ** 2)  # half the groove's length at the surface
        _, _, start, end = self._span()
        for dowel in (start + self.dowel_distance, end - self.dowel_distance):
            for connector in self.connector_positions():
                if abs(dowel - connector) < half_groove + self.dowel_diameter / 2.0:
                    raise BeamJoiningError(
                        beams=[self.main_plate, self.cross_plate],
                        joint=self,
                        debug_info="The {:.0f} long edge is too short for connectors and dowels {:.0f} from its ends: they overlap.".format(end - start, self.dowel_distance),
                    )

    def connector_frames(self):
        """Frames at the connectors' centers, on the cross plate's face at the middle of the main plate's thickness.

        x runs along the joining edge, y is the main plate's normal and z points into the main plate.
        """
        return self._frames_at(self.connector_positions())

    def dowel_frames(self):
        """Frames at the alignment dowels' centers (none unless ``dowels``), like :meth:`connector_frames`."""
        if not self.dowels:
            return []
        _, _, start, end = self._span()
        return self._frames_at([start + self.dowel_distance, end - self.dowel_distance])

    def _ref_side(self, plate, point, normal):
        """Index of ``plate``'s reference side through ``point`` whose normal is ``normal``."""
        for i, side in enumerate(plate.ref_sides):
            if abs(distance_point_plane(point, Plane.from_frame(side))) < 1e-3 and dot_vectors(side.normal, normal) > 1 - 1e-6:
                return i
        raise BeamJoiningError(beams=[self.main_plate, self.cross_plate], joint=self, debug_info="No reference side of {} at the connector.".format(plate))

    def add_features(self):
        _refresh_blanks(self.main_plate, self.cross_plate)
        into_cross = self._into_cross_plate()
        for frame in self.connector_frames():
            edge_side = self._ref_side(self.main_plate, frame.point, into_cross)
            side = self.main_plate.ref_sides[edge_side]
            edge = P14Edge(edge_side, dot_vectors(Vector.from_start_end(side.point, frame.point), side.xaxis))

            face_side = self._ref_side(self.cross_plate, frame.point, -into_cross)
            side = self.cross_plate.ref_sides[face_side]
            offset = Vector.from_start_end(side.point, frame.point)
            orientation = math.degrees(angle_vectors_signed(side.xaxis, frame.xaxis, side.normal))
            face = P14Face(face_side, dot_vectors(offset, side.xaxis), dot_vectors(offset, side.yaxis), orientation)

            self.main_plate.add_features(edge)
            self.cross_plate.add_features(face)
            self.features.extend([edge, face])
        self._check_dowels()
        for frame in self.dowel_frames():
            into_main = frame.zaxis
            point = frame.point
            main_hole = Drilling.from_line_and_element(Line(point - into_main * 5.0, point + into_main * self.dowel_edge_depth), self.main_plate, self.dowel_diameter)
            face_hole = Drilling.from_line_and_element(Line(point + into_main * 5.0, point - into_main * self.dowel_face_depth), self.cross_plate, self.dowel_diameter)
            self.main_plate.add_features(main_hole)
            self.cross_plate.add_features(face_hole)
            self.features.extend([main_hole, face_hole])


# ==========================================================================
# Concealed cup hinges (Topfband)
# ==========================================================================


class CupHingeJoint(Joint):
    """European concealed cup hinges ("Topfband") between a door and the panel it hangs on.

    For each hinge, the door gets a cup, a :class:`compas_timber.fabrication.FreeContour`, in its back face, plus two
    screw holes for the hinge, and the panel gets the two screw holes of the mounting plate on its face towards the door.
    The holes are :class:`compas_timber.fabrication.Drilling` s.

    The mounting plate's holes are measured from the door's back face, so they follow the door: for an ``"overlay"``
    door that's the panel's front edge, for an ``"inset"`` door it's further back by the door's thickness (and setback).
    ``mount`` records which hinge type the door needs.

    Parameters
    ----------
    door : :class:`compas_timber.elements.Plate`
        The door.
    panel : :class:`compas_timber.elements.Plate`
        The side or divider the door hangs on.
    mount : str, optional
        ``"overlay"`` (default) or ``"inset"``.
    hinge_count : int, optional
        Number of hinges. Default is 4. ``None`` picks it from the door's height (2 up to 900, 3 up to 1600, 4 up to
        2000, else 5).
    end_distance : float, optional
        Distance from each end of the door's hinge edge to the nearest cup's center. Default is 100.
    cup_diameter, cup_depth : float, optional
        The cup's bore. Defaults are 35 and 13.
    cup_edge_distance : float, optional
        Distance from the door's edge to the edge of the cup ("K"). Default is 5.
    door_hole_spacing : float, optional
        Distance between the door's two screw holes, along the hinge edge. Default is 48.
    door_hole_offset : float, optional
        How much further from the door's edge the screw holes are than the cup's center. Default is 6.
    door_hole_diameter, door_hole_depth : float, optional
        The door's screw holes. Defaults are 3 and 11.
    plate_offset : float, optional
        Distance from the door's back face to the mounting plate's holes, along the panel. Default is 37.
    plate_hole_spacing : float, optional
        Distance between the mounting plate's two holes, centered on the cup's height. Default is 32.
    plate_hole_diameter, plate_hole_depth : float, optional
        The mounting plate's screw holes. Defaults are 5 and 12.
    """

    MIN_ELEMENT_COUNT = 2
    MAX_ELEMENT_COUNT = 2
    PARAMETERS = dict({"mount": "overlay"}, **HINGE_DEFAULTS)
    # the processing dimensions, i.e. everything but the hinge type
    DIMENSIONS = tuple(key for key in PARAMETERS if key != "mount")

    @property
    def __data__(self):
        data = super(CupHingeJoint, self).__data__
        data.update({key: getattr(self, key) for key in self.PARAMETERS})
        return data

    def __init__(self, door=None, panel=None, **kwargs):
        parameters = {key: kwargs.pop(key, default) for key, default in self.PARAMETERS.items()}
        super(CupHingeJoint, self).__init__(elements=(door, panel), **kwargs)
        for key, value in parameters.items():
            setattr(self, key, value)
        if self.mount not in ("overlay", "inset"):
            raise ValueError("mount must be 'overlay' or 'inset', got {!r}.".format(self.mount))

    def __repr__(self):
        return "CupHingeJoint({0}, {1}, {2})".format(self.door, self.panel, self.mount)

    @property
    def door(self):
        return self.element_a

    @property
    def panel(self):
        return self.element_b

    def _error(self, message):
        return BeamJoiningError(beams=[self.door, self.panel], joint=self, debug_info=message)

    # geometry -------------------------------------------------------------

    @staticmethod
    def _center(plate):
        points = plate.outline_a.points[:-1] + plate.outline_b.points[:-1]
        return Point(*[sum(p[i] for p in points) / len(points) for i in range(3)])

    def _hinge_edge(self):
        """The door's edge along the panel: the edge parallel to the panel's faces that is closest to it."""
        panel_plane = self.panel.planes[0]
        normal = Vector(*panel_plane.normal).unitized()
        panel_center = self._center(self.panel)
        best = None
        for edge in self.door.outline_a.lines:
            if abs(dot_vectors(edge.direction.unitized(), normal)) > 1e-6:
                continue
            distance = abs(dot_vectors(Vector.from_start_end(panel_center, edge.point_at(0.5)), normal))
            if best is None or distance < best[0]:
                best = (distance, edge)
        if best is None:
            raise self._error("The door has no edge parallel to the panel.")
        return best[1]

    def _near_face(self, plate, other):
        """The face plane of ``plate`` closer to ``other``, and a unit normal pointing from it into ``plate``."""
        other_center = self._center(other)
        a, b = plate.planes
        if abs(distance_point_plane(other_center, a)) > abs(distance_point_plane(other_center, b)):
            a, b = b, a
        into = Vector(*a.normal).unitized()
        if dot_vectors(Vector.from_start_end(a.point, b.point), into) < 0:
            into = -into
        return a, into

    def hinge_frames(self):
        """Frames at the cup centers, on the door's back face: x along the hinge edge, y from the edge into the door,
        z into the door."""
        edge = self._hinge_edge()
        back, into_door = self._near_face(self.door, self.panel)
        along = edge.direction.unitized()
        # from the hinge edge towards the middle of the door, in the back face
        across = Vector.from_start_end(edge.point_at(0.5), self._center(self.door))
        across = across - along * dot_vectors(across, along) - into_door * dot_vectors(across, into_door)
        across = across.unitized()

        positions = hinge_positions(edge.length, self.hinge_count, self.end_distance)
        offset = self.cup_diameter / 2.0 + self.cup_edge_distance
        frames = []
        for t in positions:
            point = Point(*back.closest_point(edge.start + along * t)) + across * offset
            frames.append(Frame(point, along, across))
        return frames

    def plate_points(self, frame):
        """The mounting plate's hole centers on the panel's face, for the hinge at ``frame``."""
        face, _ = self._near_face(self.panel, self.door)
        _, into_door = self._near_face(self.door, self.panel)
        # frame.point is on the door's back face; from there, into the carcass along the panel's face
        point = Point(*face.closest_point(frame.point)) - into_door * self.plate_offset
        half = frame.xaxis * (self.plate_hole_spacing / 2.0)
        return [point - half, point + half]

    # joint ----------------------------------------------------------------

    def add_extensions(self):
        """Hinges don't change the plates' outlines."""

    def clear_extensions(self):
        """Nothing to clear. (The base class removes beam-style extensions keyed by joint, which plates don't have.)"""

    def _drilling(self, element, point, into, depth, diameter):
        line = Line(point - into * 5.0, point + into * depth)  # start outside the element so the drilling starts on its face
        return Drilling.from_line_and_element(line, element, diameter)

    def _cup(self, frame):
        points = [
            frame.point + frame.xaxis * (math.cos(a) * self.cup_diameter / 2.0) + frame.yaxis * (math.sin(a) * self.cup_diameter / 2.0)
            for a in (2 * math.pi * i / 36 for i in range(36))
        ]
        return FreeContour.from_polyline_and_element(Polyline(points + [points[0]]), self.door, depth=self.cup_depth, interior=True)

    def add_features(self):
        _refresh_blanks(self.door, self.panel)
        if self.hinge_count is not None and self.hinge_count < 1:
            raise self._error("There must be at least one hinge.")
        _, into_door = self._near_face(self.door, self.panel)
        _, into_panel = self._near_face(self.panel, self.door)
        for frame in self.hinge_frames():
            door_features = [self._cup(frame)]
            hole_center = frame.point + frame.yaxis * self.door_hole_offset
            for sign in (-1, 1):
                point = hole_center + frame.xaxis * (sign * self.door_hole_spacing / 2.0)
                door_features.append(self._drilling(self.door, point, into_door, self.door_hole_depth, self.door_hole_diameter))
            panel_features = [self._drilling(self.panel, p, into_panel, self.plate_hole_depth, self.plate_hole_diameter) for p in self.plate_points(frame)]
            self.door.add_features(door_features)
            self.panel.add_features(panel_features)
            self.features.extend(door_features + panel_features)
