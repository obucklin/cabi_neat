import pytest

from cabi_neat import CabinetConfig
from cabi_neat import Column
from cabi_neat import DoorMount
from cabi_neat import DoorStyle
from cabi_neat import HingeSide
from cabi_neat import Zone
from cabi_neat import ZoneKind


def test_stack_accepts_kind_strings():
    config = CabinetConfig.stack(["drawer", "open", Zone(ZoneKind.OPEN, height=300)])
    assert len(config.columns) == 1
    assert [z.kind for z in config.columns[0].zones] == [ZoneKind.DRAWER, ZoneKind.OPEN, ZoneKind.OPEN]
    assert config.columns[0].zones[2].height == 300


def test_grid():
    config = CabinetConfig.grid(3, 4)
    assert len(config.columns) == 3
    assert all(len(c.zones) == 4 for c in config.columns)
    assert all(z.kind == ZoneKind.OPEN for c in config.columns for z in c.zones)


def test_shelves_and_drawers():
    config = CabinetConfig.shelves_and_drawers(shelves=2, drawers=3, columns=2)
    kinds = [z.kind for z in config.columns[0].zones]
    assert kinds == [ZoneKind.DRAWER] * 3 + [ZoneKind.OPEN] * 3
    assert config.columns[0].zones is not config.columns[1].zones


def test_enums_from_strings():
    config = CabinetConfig(door_mount="inset", door_style="double", hinge_side="right")
    assert config.door_mount == DoorMount.INSET
    assert config.door_style == DoorStyle.DOUBLE
    assert config.hinge_side == HingeSide.RIGHT


def test_json_roundtrip():
    config = CabinetConfig(
        width=1000,
        door_mount=DoorMount.INSET,
        columns=[Column(zones=[Zone(ZoneKind.DRAWER, 200), Zone()], width=400, door_style=DoorStyle.SINGLE, hinge_side=HingeSide.RIGHT), Column()],
    )
    restored = CabinetConfig.from_json(config.to_json())
    assert restored == config


def test_json_file_roundtrip(tmp_path):
    path = tmp_path / "cabinet.json"
    config = CabinetConfig.grid(2, 3)
    config.to_json(str(path))
    assert CabinetConfig.from_json(str(path)) == config


def test_unknown_parameter_rejected():
    with pytest.raises(ValueError, match="colour"):
        CabinetConfig.from_data({"colour": "red"})


def test_copy_is_deep():
    config = CabinetConfig.grid(2, 2)
    copy = config.copy(width=900)
    copy.columns[0].zones[0].height = 100
    assert copy.width == 900
    assert config.columns[0].zones[0].height is None


def test_back_joint_validated():
    assert CabinetConfig(back_joint="butt").back_joint == "butt"
    with pytest.raises(ValueError, match="back_joint"):
        CabinetConfig(back_joint="glue")


def test_interior_joint_validated():
    with pytest.raises(ValueError, match="interior_joint"):
        CabinetConfig(interior_joint="dowel")


def test_drawer_panels_follow_carcass_thickness():
    assert CabinetConfig(thickness=19).drawer_panel_thickness == 19
    assert CabinetConfig(thickness=19, drawer_side_thickness=15).drawer_panel_thickness == 15


def test_machining():
    config = CabinetConfig(machining={"drawers": {"groove": {"width": "0.5", "tool": 410.0}}, "back": {"rabbet": {}}})
    # validated and tidied: numbers, without empty values or entries
    assert config.machining == {"drawers": {"groove": {"width": 0.5, "tool": 410}}}
    assert CabinetConfig.from_json(config.to_json()).machining == config.machining
    assert CabinetConfig(machining={}).machining is None
    with pytest.raises(ValueError):
        CabinetConfig(machining={"roof": {}})
    with pytest.raises(ValueError):
        CabinetConfig(machining={"drawers": {"groove": {"colour": 1}}})


def test_joints_own_machining_over_the_cabinets():
    from cabi_neat.config import Cell

    config = CabinetConfig(machining={"drawers": {"groove": {"width": 0.5}}, "hinges": {"pot": {"depth": 1}}})
    drawer = Cell("drawer", drawer={"machining": {"groove": {"depth": 1}, "rabbet": {"tool": 405}}})
    assert config.drawer_options(drawer)["machining"] == {"groove": {"width": 0.5, "depth": 1.0}, "rabbet": {"tool": 405}}
    door = Cell(hinges={"cup_depth": 12, "machining": {"pot": {"width": 1}}})
    options = config.hinge_options(door)
    assert options["cup_depth"] == 12 and options["machining"] == {"pot": {"depth": 1.0, "width": 1.0}}
    with pytest.raises(ValueError):
        Cell(hinges={"machining": {"pot": {"colour": 1}}})
