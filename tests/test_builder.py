import pytest
from compas.data import json_dumps
from compas.data import json_loads

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig
from cabi_neat import Cell
from cabi_neat import columns
from cabi_neat import rows
from cabi_neat.builder import open_transformation

pytest.importorskip("OCP", reason="requires the OCC backend (compas_brep[occ])")

pytestmark = pytest.mark.requires_occ

CONFIGS = [
    CabinetConfig(),
    CabinetConfig.grid(3, 3, door_style="double"),
    CabinetConfig.shelves_and_drawers(2, 2, columns=2, door_mount="inset"),
    CabinetConfig.stack(["drawer", "drawer", "open", "open"], hinge_side="right"),
    CabinetConfig.shelves_and_drawers(1, 3, door_mount="inset", shelves_between_drawers=True, drawer_front_one_piece=True, drawer_height=150),
    CabinetConfig.shelves_and_drawers(1, 3, drawer_front_one_piece=True),
    CabinetConfig(height=1800, interior=rows(columns(rows(Cell(size=500), Cell(size=400), Cell(), size=400), "open"), Cell(size=300))),
    CabinetConfig(door_mount="inset", interior=rows(columns("drawer", "drawer", size=200), columns(rows("open", "open"), "open", one_door=True))),
    CabinetConfig(interior=columns(rows(Cell(door_style="none"), Cell(door_style="none"), door_style="single", one_door=True), Cell(door_style="single"), door_style="none")),
]


@pytest.mark.parametrize("config", CONFIGS)
def test_build_without_errors(config):
    cabinet = Cabinet(config)
    assert cabinet.errors == []
    assert len(cabinet.plates) == len(cabinet.layout.parts)
    assert len(list(cabinet.model.elements())) == len(cabinet.layout.parts)
    assert len(list(cabinet.model.joints)) == len(cabinet.layout.joints)


@pytest.mark.parametrize("config", CONFIGS)
def test_processed_plates_match_layout(config):
    """Parts are created at their final size, so joinery must not move any faces."""
    cabinet = Cabinet(config)
    for part in cabinet.layout.parts:
        box = cabinet.plates[part.name].modelgeometry.aabb
        assert (box.xmin, box.ymin, box.zmin) == pytest.approx(part.origin, abs=1e-3), part.name
        assert (box.xmax, box.ymax, box.zmax) == pytest.approx(part.max, abs=1e-3), part.name


def test_joint_types():
    cabinet = Cabinet(CabinetConfig.grid(2, 2))
    names = {frozenset((j.plate_a.name, j.plate_b.name)): type(j).__name__ for j in cabinet.model.joints}
    assert names[frozenset(("top", "side_left"))] == "PlateLButtJoint"
    assert names[frozenset(("shelf_0_0", "side_left"))] == "PlateTButtJoint"
    assert names[frozenset(("divider_0", "top"))] == "PlateTButtJoint"


def test_plate_attributes():
    cabinet = Cabinet(CabinetConfig(hinge_side="right"))
    door = cabinet.plates["door_0_0"]
    assert door.attributes["category"] == "door"
    assert door.attributes["hinge_side"] == "right"
    assert set(cabinet.parts_by_category()) >= {"side", "top", "bottom", "back", "door"}


def test_cut_list():
    cabinet = Cabinet(CabinetConfig(width=800, height=1200, depth=500, thickness=18))
    rows = {row[1]: row[2:] for row in cabinet.cut_list()}
    assert rows["side_left"] == pytest.approx((1200, 500, 18))
    assert rows["top"] == pytest.approx((800 - 36, 500, 18))
    assert "category" in cabinet.format_cut_list()


def test_open_doors_swing_out_on_hinge_side():
    for side in ("left", "right"):
        cabinet = Cabinet(CabinetConfig(hinge_side=side))
        part = cabinet.layout.part("door_0_0")
        box = cabinet.plates[part.name].modelgeometry.transformed(open_transformation(part, door_angle=90)).aabb
        assert box.ymax <= 1e-3  # swung in front of the carcass
        hinge_x = part.attributes["hinge_point"][0]
        assert (box.xmin if side == "left" else box.xmax) == pytest.approx(hinge_x, abs=part.thickness + 1e-3)


def test_model_serialization_roundtrip():
    cabinet = Cabinet(CabinetConfig.shelves_and_drawers(1, 1))
    model = json_loads(json_dumps(cabinet.model))
    assert len(list(model.elements())) == len(list(cabinet.model.elements()))
    assert len(list(model.joints)) == len(list(cabinet.model.joints))
