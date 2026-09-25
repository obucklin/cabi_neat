import pytest
from compas.data import json_dumps
from compas.data import json_loads

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig
from cabi_neat import Cell
from cabi_neat import columns
from cabi_neat import rows
from cabi_neat.builder import open_transformation
from cabi_neat.joints import PlateMortiseTenon
from cabi_neat.joints import PlateRabbetJoint
from cabi_neat.joints import PlateStoppedDadoJoint

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
    CabinetConfig(door_mount="inset", interior_joint="stopped_dado", interior=columns(rows("open", "open", "open"), rows("open", "open"))),
]


@pytest.mark.parametrize("config", CONFIGS)
def test_build_without_errors(config):
    cabinet = Cabinet(config)
    assert cabinet.errors == []
    assert len(cabinet.plates) == len(cabinet.layout.parts)
    assert len(list(cabinet.model.elements())) == len(cabinet.layout.parts)
    assert len(list(cabinet.model.joints)) == len(cabinet.layout.joints)


def expected_box(layout, part):
    """The part's layout box, plus what joints add: the back reaches into the side rabbets, tenons into their mortises."""
    config = layout.config
    lo, hi = list(part.origin), list(part.max)
    for (main, cross), (kind, parameters) in layout.joint_kinds.items():
        if main != part.name:
            continue
        other = layout.part(cross)
        if kind == "clamex_p14":
            continue
        if kind == "rabbet":
            depth = parameters["rabbet_depth"] if parameters["rabbet_depth"] is not None else config.thickness / 2
        else:
            fraction = 1 / 3 if kind == "stopped_dado" else 1 / 4
            depth = parameters["depth"] if parameters["depth"] is not None else other.thickness * fraction
        for i in range(3):
            if abs(part.max[i] - other.origin[i]) < 1e-6:
                hi[i] += depth
            elif abs(part.origin[i] - other.max[i]) < 1e-6:
                lo[i] -= depth
    return lo, hi


@pytest.mark.parametrize("config", CONFIGS)
def test_processed_plates_match_layout(config):
    """Parts are created at their final size, so joinery must only move faces where a joint says so."""
    cabinet = Cabinet(config)
    for part in cabinet.layout.parts:
        box = cabinet.plates[part.name].modelgeometry.aabb
        lo, hi = expected_box(cabinet.layout, part)
        assert (box.xmin, box.ymin, box.zmin) == pytest.approx(lo, abs=1e-3), part.name
        assert (box.xmax, box.ymax, box.zmax) == pytest.approx(hi, abs=1e-3), part.name


def test_joint_types():
    cabinet = Cabinet(CabinetConfig.grid(2, 2))
    names = {frozenset((j.plate_a.name, j.plate_b.name)): type(j).__name__ for j in cabinet.model.joints}
    assert names[frozenset(("top", "side_left"))] == "ClamexP14Joint"
    assert names[frozenset(("shelf_0_0", "side_left"))] == "PlateMortiseTenon"
    assert names[frozenset(("divider_0", "top"))] == "PlateMortiseTenon"
    assert names[frozenset(("shelf_0_0", "back"))] == "PlateTButtJoint"


def test_plate_attributes():
    cabinet = Cabinet(CabinetConfig(hinge_side="right"))
    door = cabinet.plates["door_0_0"]
    assert door.attributes["category"] == "door"
    assert door.attributes["hinge_side"] == "right"
    assert set(cabinet.parts_by_category()) >= {"side", "top", "bottom", "back", "door"}


def test_cut_list():
    cabinet = Cabinet(CabinetConfig(width=800, height=1200, depth=500, thickness=18))
    rows = {row[1]: row[2:] for row in cabinet.cut_list()}
    assert rows["side_left"] == pytest.approx((1200, 500, 18), abs=1e-3)
    assert rows["top"] == pytest.approx((800 - 36, 500 - 8, 18), abs=1e-3)  # in front of the back
    assert rows["back"] == pytest.approx((1200, 800 - 36 + 18, 8), abs=1e-3)  # full height, into both 9 mm rabbets
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


def test_back_sits_in_side_rabbets():
    config = CabinetConfig(width=800, height=1200, depth=500, thickness=18, back_thickness=8, rabbet_depth=6, carcass_joint="butt")
    cabinet = Cabinet(config)
    rabbets = [j for j in cabinet.model.joints if isinstance(j, PlateRabbetJoint)]
    assert sorted((j.main_plate.name, j.cross_plate.name) for j in rabbets) == [("back", "side_left"), ("back", "side_right")]
    assert all(j.depth == 6 for j in rabbets)
    side = cabinet.plates["side_left"]
    assert len(side.features) == 2  # outline + rabbet
    # the rabbet removes 6 x 8 mm along the full height
    assert side.modelgeometry.volume == pytest.approx(18 * 500 * 1200 - 6 * 8 * 1200, rel=1e-6)
    back = cabinet.plates["back"].modelgeometry.aabb
    assert (back.xmin, back.xmax) == pytest.approx((18 - 6, 800 - 18 + 6), abs=1e-3)


def test_butt_back_joint():
    cabinet = Cabinet(CabinetConfig(back_joint="butt", carcass_joint="butt"))
    assert not any(isinstance(j, PlateRabbetJoint) for j in cabinet.model.joints)
    assert cabinet.plates["side_left"].modelgeometry.volume == pytest.approx(18 * 500 * 1200, rel=1e-6)


def test_rabbet_too_deep_is_reported():
    cabinet = Cabinet(CabinetConfig(rabbet_depth=30))
    assert len(cabinet.errors) == 2
    assert "Rabbet depth" in str(cabinet.errors[0].debug_info)


def test_top_and_bottom_butt_against_back():
    cabinet = Cabinet(CabinetConfig(depth=500, height=1200, back_thickness=8))
    for name in ("top", "bottom"):
        joint = cabinet.model.get_joint(cabinet.plates[name], cabinet.plates["back"])
        assert type(joint).__name__ == "PlateLButtJoint"
        assert (joint.main_plate.name, joint.cross_plate.name) == (name, "back")
    top = cabinet.plates["top"].modelgeometry.aabb
    back = cabinet.plates["back"].modelgeometry.aabb
    assert top.ymax == pytest.approx(500 - 8, abs=1e-3)
    assert (back.zmin, back.zmax) == pytest.approx((0, 1200), abs=1e-3)


def test_shelf_tenons_in_side_mortises():
    config = CabinetConfig.stack(["open", "open"], width=800, height=1200, depth=500, thickness=18, back_thickness=8, door_style="none", tenon_count=3, carcass_joint="butt")
    cabinet = Cabinet(config)
    joint = cabinet.model.get_joint(cabinet.plates["shelf_0_0"], cabinet.plates["side_left"])
    assert isinstance(joint, PlateMortiseTenon)
    assert (joint.main_plate.name, joint.cross_plate.name) == ("shelf_0_0", "side_left")
    assert joint.tenon_depth == pytest.approx(4.5)
    shelf_depth = 500 - 8
    tenon = 3 * (shelf_depth / 7) * 18 * 4.5  # 3 tenons, each a seventh of the edge, into 4.5 mm mortises
    shelf = cabinet.plates["shelf_0_0"].modelgeometry
    assert shelf.volume == pytest.approx((800 - 36) * shelf_depth * 18 + 2 * tenon, rel=1e-6)
    side = cabinet.plates["side_left"].modelgeometry
    rabbet = 9 * 8 * 1200
    assert side.volume == pytest.approx(18 * 500 * 1200 - rabbet - tenon, rel=1e-5)  # FreeContour pockets overshoot by 0.001
    rows = {row[1]: row[2:] for row in cabinet.cut_list()}
    assert rows["shelf_0_0"][0] == pytest.approx(800 - 36 + 9, abs=1e-3)  # tenons at both ends


def test_mortise_tenon_options():
    cabinet = Cabinet(CabinetConfig.grid(1, 2, tenon_depth=6, tenon_count=1, door_style="none"))
    joint = cabinet.model.get_joint(cabinet.plates["shelf_0_0"], cabinet.plates["side_right"])
    assert joint.tenon_depth == 6 and joint.tenon_count == 1
    assert cabinet.errors == []
    butt = Cabinet(CabinetConfig.grid(1, 2, interior_joint="butt", door_style="none"))
    assert not any(isinstance(j, PlateMortiseTenon) for j in butt.model.joints)


def test_mortise_tenon_serialization():
    cabinet = Cabinet(CabinetConfig.grid(1, 2, tenon_depth=5, door_style="none"))
    model = json_loads(json_dumps(cabinet.model))
    joints = [j for j in model.joints if isinstance(j, PlateMortiseTenon)]
    assert len(joints) == 2 and all(j.depth == 5 for j in joints)


def test_clamex_carcass_joint():
    cabinet = Cabinet(CabinetConfig(door_style="none"))  # Clamex with a center connector is the default
    assert cabinet.errors == []
    joints = [j for j in cabinet.model.joints if type(j).__name__ == "ClamexP14Joint"]
    assert sorted((j.main_plate.name, j.cross_plate.name) for j in joints) == [
        ("bottom", "side_left"),
        ("bottom", "side_right"),
        ("top", "side_left"),
        ("top", "side_right"),
    ]
    assert all(len(j.connector_frames()) == 3 for j in joints)
    assert all(j.center_connector for j in joints)


def test_panel_joints_per_panel():
    interior = rows(Cell(), Cell(), Cell(), panel_joints=["clamex_p14", None])
    cabinet = Cabinet(CabinetConfig(interior=interior, door_style="none"))
    assert cabinet.errors == []
    types = {frozenset((j.plate_a.name, j.plate_b.name)): type(j).__name__ for j in cabinet.model.joints}
    assert types[frozenset(("shelf_0", "side_left"))] == "ClamexP14Joint"
    assert types[frozenset(("shelf_1", "side_left"))] == "PlateMortiseTenon"  # the cabinet default
    assert cabinet.layout.part("shelf_0").attributes["joint"] == "clamex_p14"


def test_stopped_dado_shelf():
    config = CabinetConfig.stack(["open", "open"], width=800, height=1200, depth=500, thickness=18, back_thickness=8, door_style="none")
    config.interior = rows("open", "open", panel_joints=["stopped_dado"])
    config.carcass_joint = "butt"
    cabinet = Cabinet(config)
    assert cabinet.errors == []
    joint = cabinet.model.get_joint(cabinet.plates["shelf_0"], cabinet.plates["side_left"])
    assert isinstance(joint, PlateStoppedDadoJoint)
    assert (joint.main_plate.name, joint.cross_plate.name) == ("shelf_0", "side_left")
    shelf_depth = 500 - 8
    # the shelf gains its 6 mm extension on both ends, minus the 20 mm notch at the front
    shelf = cabinet.plates["shelf_0"].modelgeometry
    assert shelf.volume == pytest.approx((800 - 36) * shelf_depth * 18 + 2 * 6 * 18 * (shelf_depth - 20), rel=1e-5)
    box = shelf.aabb
    assert (box.xmin, box.xmax, box.ymin) == pytest.approx((18 - 6, 800 - 18 + 6, 0), abs=1e-3)
    # the groove runs from 20 mm behind the front out through the side's back edge (through the rabbet)
    side = cabinet.plates["side_left"].modelgeometry
    rabbet = 9 * 8 * 1200
    groove = 6 * 18 * (shelf_depth - 20)  # up to the back's front face; behind that it's inside the rabbet
    assert side.volume == pytest.approx(18 * 500 * 1200 - rabbet - groove, rel=1e-5)
