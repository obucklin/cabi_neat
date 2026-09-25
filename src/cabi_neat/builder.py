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


def join_plates(model, main, cross):
    """Join two plates, choosing an L- or T-butt joint from their actual topology.

    ``main`` is the plate that butts against ``cross``. For an L topology the order is kept; for a T topology the solver
    determines which plate's edge meets the other's face.

    Returns
    -------
    :class:`compas_timber.connections.PlateJoint` | None
        ``None`` if the plates don't touch.
    """
    result = PlateConnectionSolver().find_topology(main, cross)
    if result.topology == JointTopology.TOPO_EDGE_EDGE:
        joint_type = PlateLButtJoint
    elif result.topology == JointTopology.TOPO_EDGE_FACE:
        joint_type = PlateTButtJoint
    else:
        return None
    joint = joint_type(result.plate_a, result.plate_b, topology=result.topology, a_segment_index=result.a_segment_index, b_segment_index=result.b_segment_index)
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
            if join_plates(self.model, self.plates[main], self.plates[cross]) is None:
                self.errors.append("No contact between {} and {}.".format(main, cross))
        self.errors.extend(self.model.process_joinery())

    def parts_by_category(self):
        """Plates grouped by category."""
        groups = defaultdict(list)
        for part in self.layout.parts:
            groups[part.category].append(self.plates[part.name])
        return dict(groups)

    def cut_list(self):
        """Rows of (category, name, length, width, thickness), sorted by category."""
        rows = [(p.category, p.name) + p.dimensions for p in self.layout.parts]
        return sorted(rows)

    def format_cut_list(self):
        lines = ["{:<18} {:<24} {:>8} {:>8} {:>6}".format("category", "name", "length", "width", "thick")]
        for category, name, length, width, thickness in self.cut_list():
            lines.append("{:<18} {:<24} {:>8.1f} {:>8.1f} {:>6.1f}".format(category, name, length, width, thickness))
        return "\n".join(lines)
