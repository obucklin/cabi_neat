import pytest
from compas.data import json_dumps
from compas.data import json_loads

from cabi_neat import Cabinet
from cabi_neat import CabinetConfig
from cabi_neat import Zone
from cabi_neat import ZoneKind
from cabi_neat import Cell
from cabi_neat import columns
from cabi_neat import rows
from cabi_neat.builder import open_transformation
from cabi_neat.joints import CupHingeJoint
from cabi_neat.joints import PlateGrooveJoint
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
    CabinetConfig.shelves_and_drawers(1, 3, door_mount="inset", shelves_between_drawers=True, drawer_front_one_piece=True, drawer_height=180),
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
    lo, hi = list(part.origin), list(part.max)
    for (main, cross), (kind, parameters) in layout.joint_kinds.items():
        if main != part.name:
            continue
        other = layout.part(cross)
        if kind in ("clamex_p14", "cup_hinge"):
            continue
        if kind == "rabbet":
            depth = parameters["rabbet_depth"] if parameters["rabbet_depth"] is not None else other.thickness / 2
        else:
            fraction = {"stopped_dado": 1 / 3, "groove": 1 / 2}.get(kind, 1 / 4)
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
    names = {frozenset(e.name for e in j.elements): type(j).__name__ for j in cabinet.model.joints}
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
    config = CabinetConfig(width=800, height=1200, depth=500, thickness=18, back_thickness=8, rabbet_depth=6, carcass_joint="butt", door_style="none")
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
    cabinet = Cabinet(CabinetConfig(back_joint="butt", carcass_joint="butt", door_style="none"))
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


def test_panel_joint_parameters_reach_the_joints():
    interior = rows(
        "open",
        "open",
        "open",
        "open",
        panel_joints=[
            {"type": "clamex_p14", "distance": 90, "center_connector": False},
            {"type": "stopped_dado", "stop_distance": 40, "depth": 5},
            {"type": "mortise_tenon", "tenon_count": 1},
        ],
    )
    cabinet = Cabinet(CabinetConfig(interior=interior, door_style="none"))
    assert cabinet.errors == []
    clamex = cabinet.model.get_joint(cabinet.plates["shelf_0"], cabinet.plates["side_left"])
    assert (clamex.distance, clamex.center_connector, len(clamex.connector_frames())) == (90, False, 2)
    dado = cabinet.model.get_joint(cabinet.plates["shelf_1"], cabinet.plates["side_left"])
    assert (dado.stop_distance, dado.pocket_depth) == (40, 5)
    tenon = cabinet.model.get_joint(cabinet.plates["shelf_2"], cabinet.plates["side_left"])
    assert tenon.tenon_count == 1


@pytest.mark.parametrize("mount, plate_line", [("overlay", 37), ("inset", 18 + 37)])
def test_doors_hang_on_cup_hinges(mount, plate_line):
    cabinet = Cabinet(CabinetConfig(width=800, height=1200, door_mount=mount, door_style="double", hinges={"hinge_count": 3}))
    assert cabinet.errors == []
    hinges = {j.door.attributes["hinge_side"]: j for j in cabinet.model.joints if isinstance(j, CupHingeJoint)}
    assert {side: j.panel.name for side, j in hinges.items()} == {"left": "side_left", "right": "side_right"}
    for joint in hinges.values():
        assert joint.mount == mount
        frames = joint.hinge_frames()
        assert len(frames) == 3
        for frame in frames:
            holes = joint.plate_points(frame)
            assert [p.y for p in holes] == pytest.approx([plate_line, plate_line])  # from the side's front edge at y=0
            assert abs(holes[1].z - holes[0].z) == pytest.approx(32)
    door = hinges["left"].door
    assert sum(type(f).__name__ == "Drilling" for f in door.features) == 6
    assert sum(type(f).__name__ == "FreeContour" for f in door.features) == 1 + 3  # outline + cups


def test_door_hangs_on_divider():
    interior = columns(Cell(door_style="none"), Cell(door_style="single", hinge_side="left"), door_style="none")
    cabinet = Cabinet(CabinetConfig(interior=interior))
    joint = next(j for j in cabinet.model.joints if isinstance(j, CupHingeJoint))
    assert (joint.door.name, joint.panel.name) == ("door_1", "divider_0")
    divider = cabinet.layout.part("divider_0")
    for frame in joint.hinge_frames():
        # the plate holes are on the divider's face towards the door
        assert all(p.x == pytest.approx(divider.max[0]) for p in joint.plate_points(frame))


def test_hinge_dimensions_from_config():
    cabinet = Cabinet(CabinetConfig(hinges={"door_hole_spacing": 52, "end_distance": 80}))
    joint = next(j for j in cabinet.model.joints if isinstance(j, CupHingeJoint))
    assert (joint.door_hole_spacing, joint.end_distance, joint.hinge_count, joint.door_hole_diameter) == (52, 80, 4, 3.0)
    with pytest.raises(ValueError, match="Unknown hinge dimensions"):
        CabinetConfig(hinges={"screw": 3})


def drawer_joints(cabinet):
    """{(main, cross): joint type} for the joints of drawer parts."""
    joints = {}
    for joint in cabinet.model.joints:
        names = [e.name for e in joint.elements]
        if any(n.startswith("drawer_") for n in names):
            main = joint.main_plate.name if hasattr(joint, "main_plate") else names[0]
            cross = joint.cross_plate.name if hasattr(joint, "cross_plate") else names[1]
            joints[(main, cross)] = type(joint).__name__
    return joints


def test_two_piece_drawer_joints():
    cabinet = Cabinet(CabinetConfig.stack([Zone(ZoneKind.DRAWER, 200), "open"], door_style="none"))
    assert cabinet.errors == []
    d = "drawer_0_0"
    assert drawer_joints(cabinet) == {
        (f"{d}_box_front", f"{d}_side_l"): "PlateRabbetJoint",
        (f"{d}_box_front", f"{d}_side_r"): "PlateRabbetJoint",
        (f"{d}_back", f"{d}_side_l"): "PlateRabbetJoint",
        (f"{d}_back", f"{d}_side_r"): "PlateRabbetJoint",
        (f"{d}_bottom", f"{d}_side_l"): "PlateGrooveJoint",
        (f"{d}_bottom", f"{d}_side_r"): "PlateGrooveJoint",
        (f"{d}_bottom", f"{d}_box_front"): "PlateGrooveJoint",
        (f"{d}_bottom", f"{d}_back"): "PlateGrooveJoint",
    }
    bottom = cabinet.layout.part(f"{d}_bottom")
    side = cabinet.layout.part(f"{d}_side_l")
    assert bottom.origin[2] == pytest.approx(side.origin[2] + 10)  # raised off the lower edge
    grooves = {j.cross_plate.name: j for j in cabinet.model.joints if isinstance(j, PlateGrooveJoint)}
    assert grooves[f"{d}_side_l"].through and not grooves[f"{d}_box_front"].through
    assert grooves[f"{d}_side_l"].pocket_depth == pytest.approx(9)  # half the 18 mm side
    # drawer sides, front and back are as thick as the carcass
    for name in ("side_l", "side_r", "box_front", "back"):
        assert cabinet.layout.part(f"{d}_{name}").thickness == pytest.approx(18)


def test_one_piece_drawer_front_uses_clamex():
    config = CabinetConfig.stack([Zone(ZoneKind.DRAWER, 200), "open"], door_style="none", drawer_front_one_piece=True)
    cabinet = Cabinet(config)
    assert cabinet.errors == []
    d = "drawer_0_0"
    joints = drawer_joints(cabinet)
    assert joints[(f"{d}_side_l", f"{d}_front")] == "ClamexP14Joint"
    assert joints[(f"{d}_side_r", f"{d}_front")] == "ClamexP14Joint"
    assert joints[(f"{d}_back", f"{d}_side_l")] == "PlateRabbetJoint"
    assert joints[(f"{d}_bottom", f"{d}_front")] == "PlateGrooveJoint"
    front = cabinet.plates[f"{d}_front"]
    # one connector per side, in the middle of its edge, and two alignment dowels
    assert sum(type(f).__name__ == "P14Face" for f in front.features) == 2
    assert sum(type(f).__name__ == "Drilling" for f in front.features) == 4
    side = cabinet.plates[f"{d}_side_l"]
    assert sum(type(f).__name__ == "P14Edge" for f in side.features) == 1
    clamex = cabinet.model.get_joint(side, front)
    edge_height = cabinet.layout.part(f"{d}_side_l").size[2]
    assert [f.point.z - side.modelgeometry.aabb.zmin for f in clamex.connector_frames()] == pytest.approx([edge_height / 2])
    assert sorted(f.point.z - side.modelgeometry.aabb.zmin for f in clamex.dowel_frames()) == pytest.approx([30, edge_height - 30])
    # the groove in the visible front is exactly as long as the bottom's edge including its corners, which reach
    # 9 mm into the side grooves at both ends: as short as possible without notching the bottom
    groove = next(j for j in cabinet.model.joints if isinstance(j, PlateGrooveJoint) and j.cross_plate is front)
    assert not groove.through
    bottom_part = cabinet.layout.part(f"{d}_bottom")
    start, end = groove.groove_interval()
    assert end - start == pytest.approx(bottom_part.size[0] + 2 * 9)
    # the bottom reaches 9 mm into all four grooves, corners included
    w, b, t = bottom_part.size[0], bottom_part.size[1], bottom_part.size[2]
    expected = w * b * t + 9 * t * (2 * w + 2 * b) + 4 * 9 * 9 * t
    assert cabinet.plates[f"{d}_bottom"].modelgeometry.volume == pytest.approx(expected, rel=1e-5)
    box = front.modelgeometry.aabb
    part = cabinet.layout.part(f"{d}_front")
    assert (box.xmin, box.xmax) == pytest.approx((part.origin[0], part.max[0]), abs=1e-3)
