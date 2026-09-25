"""Build a :class:`compas_timber.model.TimberModel` from a cabinet layout."""

import math
from collections import defaultdict

from compas.geometry import Frame
from compas.geometry import Point
from compas.geometry import Rotation
from compas.geometry import Translation
from compas.geometry import Vector
from compas_timber.connections import JointTopology
from compas_timber.connections import PlateConnectionSolver
from compas_timber.connections import PlateLButtJoint
from compas_timber.connections import PlateTButtJoint
from compas_timber.elements import Plate
from compas_timber.model import TimberModel

from .joints import ClamexP14Joint
from .joints import CupHingeJoint
from .joints import PlateGrooveJoint
from .joints import PlateMortiseTenon
from .joints import PlateRabbetJoint
from .joints import PlateStoppedDadoJoint
from .layout import resolve_layout

X = Vector(1, 0, 0)
Y = Vector(0, 1, 0)
Z = Vector(0, 0, 1)

# (frame x-axis, frame y-axis) per thickness axis, chosen so the frame normal points along +axis
_FRAME_AXES = {"x": (Y, Z), "y": (Z, X), "z": (X, Y)}
# indices into part.size giving (length, width, thickness)
_DIMS = {"x": (1, 2, 0), "y": (2, 0, 1), "z": (0, 1, 2)}


def plate_from_part(part):
    """Create a :class:`Plate` occupying exactly the box of ``part``.

    The part's name, category and attributes are copied onto the plate.
    """
    xaxis, yaxis = _FRAME_AXES[part.thickness_axis]
    length, width, thickness = (part.size[i] for i in _DIMS[part.thickness_axis])
    plate = Plate(Frame(Point(*part.origin), xaxis, yaxis), length, width, thickness)
    plate.name = part.name
    plate.attributes.update(part.attributes)
    plate.attributes["category"] = part.category
    return plate


# (kind, topology) -> joint type used instead of the plain L/T butt joint
SPECIAL_JOINTS = {
    ("rabbet", JointTopology.TOPO_EDGE_EDGE): PlateRabbetJoint,
    ("mortise_tenon", JointTopology.TOPO_EDGE_FACE): PlateMortiseTenon,
    ("stopped_dado", JointTopology.TOPO_EDGE_FACE): PlateStoppedDadoJoint,
    ("groove", JointTopology.TOPO_EDGE_FACE): PlateGrooveJoint,
    ("clamex_p14", JointTopology.TOPO_EDGE_EDGE): ClamexP14Joint,
    ("clamex_p14", JointTopology.TOPO_EDGE_FACE): ClamexP14Joint,
}


# joints between plates that don't touch, created as they are
UNCONNECTED_JOINTS = {"cup_hinge": CupHingeJoint}


def join_plates(model, main, cross, kind=None, **parameters):
    """Join two plates, choosing an L- or T-butt joint from their actual topology.

    ``main`` is the plate that butts against ``cross``. For an L topology the order is kept; for a T topology the solver
    determines which plate's edge meets the other's face. With ``kind="rabbet"``, an L topology gets a
    :class:`PlateRabbetJoint` (``parameters`` are passed to it) with the rabbet in ``cross``. With
    ``kind="mortise_tenon"``, a T topology gets a :class:`PlateMortiseTenon` with the tenons on the edge plate.

    Returns
    -------
    :class:`compas_timber.connections.PlateJoint` | None
        ``None`` if the plates don't touch.
    """
    if kind in UNCONNECTED_JOINTS:
        return UNCONNECTED_JOINTS[kind].create(model, main, cross, **parameters)
    result = PlateConnectionSolver().find_topology(main, cross)
    special = SPECIAL_JOINTS.get((kind, result.topology))
    if special:
        joint_type = special
    elif result.topology == JointTopology.TOPO_EDGE_EDGE:
        joint_type, parameters = PlateLButtJoint, {}
    elif result.topology == JointTopology.TOPO_EDGE_FACE:
        joint_type, parameters = PlateTButtJoint, {}
    else:
        return None
    segments = {"topology": result.topology, "a_segment_index": result.a_segment_index, "b_segment_index": result.b_segment_index}
    joint = joint_type(result.plate_a, result.plate_b, **segments, **parameters)
    model.add_joint(joint)
    return joint


def open_transformation(part, door_angle=100.0):
    """Transformation showing ``part`` opened: doors swing about their hinge, drawer parts slide out.

    Returns
    -------
    :class:`compas.geometry.Transformation` | None
        ``None`` for parts that don't move.
    """
    if part.category == "door":
        # a left-hinged door extends in +x from its hinge, so it swings towards -y with a negative rotation about +z
        angle = math.radians(door_angle) * (-1 if part.attributes["hinge_side"] == "left" else 1)
        return Rotation.from_axis_and_angle(part.attributes["hinge_axis"], angle, point=part.attributes["hinge_point"])
    if "drawer" in part.attributes:
        return Translation.from_vector([0, -part.attributes["travel"], 0])
    return None


class Cabinet:
    """A cabinet generated from a :class:`CabinetConfig`.

    Parameters
    ----------
    config : :class:`CabinetConfig`

    Attributes
    ----------
    layout : :class:`cabi_neat.layout.Layout`
        The resolved part boxes and joint pairs.
    model : :class:`compas_timber.model.TimberModel`
        The timber model with one plate per part and processed joinery.
    plates : dict
        Plates by part name.
    errors : list
        Joinery errors, plus messages for joint pairs whose plates don't touch.
    """

    def __init__(self, config):
        self.config = config
        self.layout = resolve_layout(config)
        self.model = TimberModel()
        self.plates = {}
        self.errors = []
        self._build()

    def _build(self):
        for part in self.layout.parts:
            plate = plate_from_part(part)
            self.model.add_element(plate)
            self.plates[part.name] = plate
        for main, cross in self.layout.joints:
            kind, parameters = self.layout.joint_kinds.get((main, cross), (None, {}))
            if join_plates(self.model, self.plates[main], self.plates[cross], kind, **parameters) is None:
                self.errors.append("No contact between {} and {}.".format(main, cross))
        self.errors.extend(self.model.process_joinery())

    def parts_by_category(self):
        """Plates grouped by category."""
        groups = defaultdict(list)
        for part in self.layout.parts:
            groups[part.category].append(self.plates[part.name])
        return dict(groups)

    def cut_list(self):
        """Rows of (category, name, length, width, thickness), sorted by category.

        Sizes are measured on the processed plates, so they include what joints add (e.g. a back extended into rabbets).
        """
        rows = []
        for part in self.layout.parts:
            box = self.plates[part.name].elementgeometry.aabb
            length, width = sorted((box.xmax - box.xmin, box.ymax - box.ymin), reverse=True)
            rows.append((part.category, part.name, length, width, box.zmax - box.zmin))
        return sorted(rows)

    def format_cut_list(self):
        lines = ["{:<18} {:<24} {:>8} {:>8} {:>6}".format("category", "name", "length", "width", "thick")]
        for category, name, length, width, thickness in self.cut_list():
            lines.append("{:<18} {:<24} {:>8.1f} {:>8.1f} {:>6.1f}".format(category, name, length, width, thickness))
        return "\n".join(lines)
