import json

import pytest
from shapely.geometry import Polygon

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig
from cabi_neat.dxf import plate_outline
from cabi_neat.dxf import plate_shape
from cabi_neat.web import WebPlanner


@pytest.fixture(scope="module")
def planner():
    planner = WebPlanner(CabinetConfig.from_json("examples/03_custom_stack.json"))
    answer = planner.request("POST", "api/generate", "{}")
    assert answer["json"]["ok"], answer
    return planner


def test_requests():
    planner = WebPlanner(CabinetConfig.grid(2, 2))
    state = json.loads(planner.request_json("GET", "api/state"))
    assert state["status"] == 200 and state["json"]["stale"]
    # posting a config, with what the page sends: JSON text
    config = dict(state["json"]["config"], width=900)
    answer = json.loads(planner.request_json("POST", "api/config", json.dumps(config)))
    assert answer["json"]["ok"] and planner.config.width == 900
    assert json.loads(planner.request_json("GET", "api/version?x=1"))["json"]["version"] == planner.version
    assert json.loads(planner.request_json("GET", "api/nothing"))["status"] == 404


def test_exports_are_bytes(planner):
    answer = planner.request("GET", "api/export/hops")
    assert answer["status"] == 200 and answer["media_type"] == "application/zip"
    assert answer["body"][:2] == b"PK"
    assert answer["headers"]["x-export-summary"] == "Everything was exported."
    # for JavaScript: Latin-1 text of the bytes
    text = json.loads(planner.request_json("GET", "api/export/hops"))["body"]
    assert text.encode("latin-1") == answer["body"]


def test_scene(planner):
    scene = json.loads(planner.scene())
    names = {p["name"] for p in scene["parts"]}
    assert names == set(planner.cabinet.plates)
    side = next(p for p in scene["parts"] if p["name"] == "side_left")
    assert side["group"] == "carcass" and side["matrix"] is None
    assert abs(side["z1"] - side["z0"]) == pytest.approx(18)
    assert Polygon(side["polygons"][0][0]).area > 0
    # fronts hidden, or shown open
    planner.set_display(show_fronts=False)
    hidden = json.loads(planner.scene())
    assert hidden["revision"] > scene["revision"] and not any(p["group"] == "front" for p in hidden["parts"])
    planner.set_display(show_fronts=True, open_fronts=True)
    door = next(p for p in json.loads(planner.scene())["parts"] if p["name"].startswith("door"))
    assert len(door["matrix"]) == 4
    planner.set_display(open_fronts=False)


def test_pick_and_highlight(planner):
    planner.request("POST", "api/highlight", json.dumps({"parts": ["side_left"]}))
    assert json.loads(planner.view_state())["highlight"] == ["side_left"]
    planner.pick("shelf_0_1")
    assert planner.request("GET", "api/version")["json"]["pick"]["name"] == "shelf_0_1"


@pytest.mark.requires_occ
def test_shape_without_brep_is_the_breps_silhouette(planner):
    pytest.importorskip("OCP")
    for name, plate in planner.cabinet.plates.items():
        brep, _ = plate_outline(plate)
        shape = plate_shape(plate)
        largest = max(shape.geoms, key=lambda g: g.area) if shape.geom_type == "MultiPolygon" else shape
        assert largest.area == pytest.approx(Polygon(brep).area, abs=1.0), name


def test_shapes_have_their_through_cuts():
    # stopped dados notch the shelves at the front: the silhouette is the outline minus the notch
    cabinet = Cabinet(CabinetConfig.grid(1, 2, interior_joint="stopped_dado"))
    shelf = next(p for n, p in cabinet.plates.items() if n.startswith("shelf"))
    shape = plate_shape(shelf)
    outline = Polygon([(p[0], p[1]) for p in shelf.outline_a.points[:-1]])
    assert shape.area < outline.area
