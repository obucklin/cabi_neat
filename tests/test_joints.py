"""The cabinet joints on their own: plates placed by hand, joined, and processed."""

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
