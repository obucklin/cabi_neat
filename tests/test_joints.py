"""The cabinet joints on their own: plates placed by hand, joined, and processed."""

import math

import pytest
from compas.data import json_dumps
from compas.data import json_loads
from compas.geometry import Frame
from compas.geometry import Point
from compas.geometry import Vector

pytest.importorskip("OCP", reason="requires the OCC backend (compas_brep[occ])")

from compas_timber.elements import Plate  # noqa: E402
from compas_timber.model import TimberModel  # noqa: E402

from cabi_neat.joints import ClamexP14Joint  # noqa: E402
from cabi_neat.joints import CupHingeJoint  # noqa: E402
from cabi_neat.joints import P14Edge  # noqa: E402
from cabi_neat.joints import P14Face  # noqa: E402
from cabi_neat.joints import PlateStoppedDadoJoint  # noqa: E402

pytestmark = pytest.mark.requires_occ

X, Y, Z = Vector(1, 0, 0), Vector(0, 1, 0), Vector(0, 0, 1)
T = 18.0


def groove_volume(radius=P14Edge.BLADE_RADIUS, depth=P14Edge.DEPTH, width=P14Edge.WIDTH):
    """Volume of a disc segment of the given depth, times the groove width."""
    import math

    d = radius - depth
    return (radius**2 * math.acos(d / radius) - d * math.sqrt(radius**2 - d**2)) * width


@pytest.fixture
def plates():
    side = Plate(Frame(Point(0, 0, 0), Y, Z), 500, 1200, T)  # x 0..18
    top = Plate(Frame(Point(T, 0, 1200 - T), X, Y), 764, 500, T)  # on top of the side's inner face: L
    shelf = Plate(Frame(Point(T, 0, 600), X, Y), 764, 492, T)  # against the side's inner face: T
    model = TimberModel()
    for plate in (side, top, shelf):
        model.add_element(plate)
    return model, side, top, shelf


def test_connector_frames(plates):
    model, side, top, shelf = plates
    corner = ClamexP14Joint.create(model, top, side)
    middle = ClamexP14Joint.create(model, shelf, side, distance=80, center_connector=True)
    assert model.process_joinery() == []
    # 60 from both ends of the 500 edge, in the middle of the top's thickness, on the side's inner face
    assert [tuple(f.point) for f in corner.connector_frames()] == [pytest.approx((T, 440, 1191)), pytest.approx((T, 60, 1191))]
    assert sorted(f.point.y for f in middle.connector_frames()) == pytest.approx([80, 246, 412])
    frame = corner.connector_frames()[0]
    assert frame.zaxis.dot(X) == pytest.approx(1)  # into the main plate
    assert abs(frame.xaxis.dot(Y)) == pytest.approx(1)  # along the edge


def test_features_and_grooves(plates):
    model, side, top, shelf = plates
    ClamexP14Joint.create(model, top, side)
    ClamexP14Joint.create(model, shelf, side, center_connector=True)
    model.process_joinery()
    assert [type(f) for f in top._features] == [P14Edge, P14Edge]
    assert sorted(f.location for f in top._features) == pytest.approx([60, 440])
    faces = [f for f in side._features if isinstance(f, P14Face)]
    assert len(faces) == 5
    assert all(abs(f.orientation) in (pytest.approx(0), pytest.approx(180)) for f in faces)
    g = groove_volume()
    assert 764 * 500 * T - top.modelgeometry.volume == pytest.approx(2 * g, rel=1e-4)
    assert 764 * 492 * T - shelf.modelgeometry.volume == pytest.approx(3 * g, rel=1e-4)
    assert 500 * 1200 * T - side.modelgeometry.volume == pytest.approx(5 * g, rel=1e-4)


def test_edge_groove_is_centered_in_the_thickness(plates):
    model, side, top, shelf = plates
    ClamexP14Joint.create(model, top, side)
    model.process_joinery()
    box = top._features[0].groove(top).aabb
    assert (box.zmin + box.zmax) / 2 == pytest.approx(1200 - T / 2)
    assert box.zmax - box.zmin == pytest.approx(P14Edge.WIDTH)
    assert box.xmax == pytest.approx(T + P14Edge.DEPTH)  # 14.7 into the top, from the side's face


def test_short_edge_gets_one_connector(plates):
    model, side, top, shelf = plates
    short = Plate(Frame(Point(T, 0, 300), X, Y), 300, 100, T)
    model.add_element(short)
    joint = ClamexP14Joint.create(model, short, side)
    model.process_joinery()
    assert [f.point.y for f in joint.connector_frames()] == pytest.approx([50])


def test_serialization(plates):
    model, side, top, shelf = plates
    ClamexP14Joint.create(model, shelf, side, distance=70, center_connector=True)
    model.process_joinery()
    restored = json_loads(json_dumps(model))
    joint = next(j for j in restored.joints if isinstance(j, ClamexP14Joint))
    assert joint.distance == 70 and joint.center_connector is True
    face = json_loads(json_dumps(P14Face(2, 10.0, 20.0, 90.0)))
    assert (face.ref_side, face.x, face.y, face.orientation) == (2, 10.0, 20.0, 90.0)
    edge = json_loads(json_dumps(P14Edge(4, 60.0)))
    assert (edge.ref_side, edge.location) == (4, 60.0)


@pytest.mark.parametrize("open_direction, stop_at", [(Y, 20), (-Y, 472)])
def test_stopped_dado(plates, open_direction, stop_at):
    model, side, top, shelf = plates  # the shelf spans y 0..492, the side y 0..500
    joint = PlateStoppedDadoJoint.create(model, shelf, side, open_direction=open_direction)
    assert model.process_joinery() == []
    assert joint.pocket_depth == pytest.approx(6)
    shelf_volume = 764 * 492 * T + 6 * T * (492 - 20)
    assert shelf.modelgeometry.volume == pytest.approx(shelf_volume, rel=1e-5)
    # the groove runs from the stop out through the side's edge in the open direction
    groove_length = 500 - stop_at if open_direction == Y else stop_at
    assert 500 * 1200 * T - side.modelgeometry.volume == pytest.approx(6 * T * groove_length, rel=1e-3)


def test_stopped_dado_needs_direction(plates):
    model, side, top, shelf = plates
    PlateStoppedDadoJoint.create(model, shelf, side)
    errors = model.process_joinery()
    assert errors and all("open_direction" in e.debug_info for e in errors)


def test_stopped_dado_serialization(plates):
    model, side, top, shelf = plates
    PlateStoppedDadoJoint.create(model, shelf, side, depth=5, stop_distance=30, open_direction=Y)
    model.process_joinery()
    joint = next(j for j in json_loads(json_dumps(model)).joints if isinstance(j, PlateStoppedDadoJoint))
    assert (joint.depth, joint.stop_distance) == (5, 30)
    assert joint.open_direction.dot(Y) == pytest.approx(1)


@pytest.mark.parametrize(
    "mount, door_frame, height, cup_x, back_y, plate_y",
    [
        ("overlay", Frame(Point(1, -T, 1), Z, X), 1198, 1 + 22.5, 0, 37),  # in front of the side
        ("inset", Frame(Point(T + 3, 0, T + 3), Z, X), 1158, T + 3 + 22.5, T, T + 37),  # inside the opening: 37 from its back face
    ],
)
def test_cup_hinges(mount, door_frame, height, cup_x, back_y, plate_y):
    side = Plate(Frame(Point(0, 0, 0), Y, Z), 500, 1200, T)
    door = Plate(door_frame, height, 400, T)
    model = TimberModel()
    model.add_element(side)
    model.add_element(door)
    joint = CupHingeJoint.create(model, door, side, mount=mount)
    assert model.process_joinery() == []
    frames = joint.hinge_frames()
    bottom = door_frame.point.z
    step = (height - 200) / 3
    assert [f.point.z for f in frames] == pytest.approx([bottom + 100 + i * step for i in range(4)])  # 4 hinges, 100 from the ends
    for frame in frames:
        assert (frame.point.x, frame.point.y) == pytest.approx((cup_x, back_y))  # 22.5 from the hinge edge, on the back face
        holes = joint.plate_points(frame)
        assert [(p.x, p.y) for p in holes] == [pytest.approx((T, plate_y))] * 2
        assert [p.z for p in holes] == pytest.approx([frame.point.z - 16, frame.point.z + 16])
    assert door.debug_info == [] and side.debug_info == []
    # four 35 x 13 cups plus two 3 mm screw holes each; four pairs of 5 mm plate holes (Drilling cuts slightly past its depth)
    cups = 4 * (math.pi * 17.5**2 * 13 + 2 * math.pi * 1.5**2 * 11)
    assert height * 400 * T - door.modelgeometry.volume == pytest.approx(cups, rel=0.02)
    plate_holes = 4 * 2 * math.pi * 2.5**2 * 12
    assert 500 * 1200 * T - side.modelgeometry.volume == pytest.approx(plate_holes, rel=0.12)


def test_door_screw_holes():
    side = Plate(Frame(Point(0, 0, 0), Y, Z), 500, 1200, T)
    door = Plate(Frame(Point(1, -T, 1), Z, X), 1198, 400, T)
    model = TimberModel()
    model.add_element(side)
    model.add_element(door)
    joint = CupHingeJoint.create(model, door, side, hinge_count=2)
    model.process_joinery()
    drillings = [f for f in door.features if type(f).__name__ == "Drilling"]
    assert len(drillings) == 4 and all(d.diameter == 3.5 for d in drillings)
    frame = joint.hinge_frames()[0]
    # the holes' centers on the back face: 48 apart along the edge, 6 further from the edge than the cup
    centers = sorted((d.cylinder_from_params_and_element(door).frame.point for d in drillings), key=lambda p: p.z)[:2]
    assert [p.x for p in centers] == pytest.approx([frame.point.x + 6] * 2, abs=1e-3)
    assert centers[1].z - centers[0].z == pytest.approx(48)


def test_cup_hinge_count_and_serialization():
    side = Plate(Frame(Point(0, 0, 0), Y, Z), 500, 2200, T)
    door = Plate(Frame(Point(1, -T, 1), Z, X), 2198, 400, T)
    model = TimberModel()
    model.add_element(side)
    model.add_element(door)
    joint = CupHingeJoint.create(model, door, side, end_distance=120, hinge_count=None)
    model.process_joinery()
    assert len(joint.hinge_frames()) == 5  # from the door's height
    restored = next(j for j in json_loads(json_dumps(model)).joints if isinstance(j, CupHingeJoint))
    assert (restored.mount, restored.end_distance, restored.hinge_count, restored.plate_offset) == ("overlay", 120, None, 37.0)
    with pytest.raises(ValueError, match="mount"):
        CupHingeJoint(door, side, mount="half")


def test_hinge_dimension_names_match():
    from cabi_neat.config import HINGE_DIMENSIONS

    assert tuple(HINGE_DIMENSIONS) == CupHingeJoint.DIMENSIONS


def test_single_connector_with_dowels(plates):
    model, side, top, shelf = plates
    joint = ClamexP14Joint.create(model, shelf, side, connector_count=1, dowels=True, dowel_distance=40)
    assert model.process_joinery() == []
    assert [f.point.y for f in joint.connector_frames()] == pytest.approx([246])
    assert sorted(f.point.y for f in joint.dowel_frames()) == pytest.approx([40, 452])
    assert sum(isinstance(f, P14Edge) for f in shelf.features) == 1
    holes = [f for f in shelf.features if type(f).__name__ == "Drilling"]
    assert len(holes) == 2 and all(h.diameter == 8 for h in holes)
    assert sum(type(f).__name__ == "Drilling" for f in side.features) == 2
    # a dowel is 20 deep in the shelf's edge and 10 deep in the side's face
    dowel = 3.14159265 * 4**2
    removed_side = 500 * 1200 * T - side.modelgeometry.volume
    assert removed_side == pytest.approx(groove_volume() + 2 * dowel * 10, rel=0.05)
    restored = next(j for j in json_loads(json_dumps(model)).joints if isinstance(j, ClamexP14Joint))
    assert (restored.connector_count, restored.dowels, restored.dowel_distance) == (1, True, 40)


def test_stopped_groove_trims_the_main_plate_past_its_ends(plates):
    from cabi_neat.joints import PlateGrooveJoint

    model, side, top, shelf = plates
    # a 300 long panel in the middle of the side's face, extended into a groove only as long as its edge
    short = Plate(Frame(Point(T, 100, 300), X, Y), 300, 300, T)
    model.add_element(short)
    joint = PlateGrooveJoint.create(model, short, side, overhang=0)
    assert model.process_joinery() == []
    assert joint.groove_interval() == pytest.approx((0, 300))
    assert short.modelgeometry.volume == pytest.approx(300 * 300 * T + 9 * T * 300, rel=1e-6)


def test_dowels_overlapping_a_connector_are_reported(plates):
    model, side, top, shelf = plates
    short = Plate(Frame(Point(T, 100, 300), X, Y), 300, 110, T)  # a 110 long edge: one connector, dowels 30 from the ends
    model.add_element(short)
    ClamexP14Joint.create(model, short, side, connector_count=1, dowels=True)
    errors = model.process_joinery()
    assert len(errors) == 1 and "too short" in errors[0].debug_info
