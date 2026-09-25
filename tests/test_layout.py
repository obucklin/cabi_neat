import pytest

from cabi_neat import CabinetConfig
from cabi_neat import Column
from cabi_neat import DoorMount
from cabi_neat import DoorStyle
from cabi_neat import HingeSide
from cabi_neat import Zone
from cabi_neat import ZoneKind
from cabi_neat import resolve_layout
from cabi_neat.layout import distribute

TOL = 1e-6


def overlaps(a, b):
    """True if the part boxes intersect with positive volume."""
    return all(a.origin[i] < b.max[i] - TOL and b.origin[i] < a.max[i] - TOL for i in range(3))


def configs():
    yield CabinetConfig()
    yield CabinetConfig.grid(3, 4, door_style="double")
    yield CabinetConfig.shelves_and_drawers(2, 3, columns=2, door_mount="inset")
    yield CabinetConfig.stack(["drawer", "open", "drawer", "drawer", "open", "open"], door_mount="inset", hinge_side="right")
    yield CabinetConfig.stack(["drawer", "open", "drawer", "drawer", "open", "open"], door_mount="overlay")
    yield CabinetConfig.shelves_and_drawers(1, 3, door_mount="inset", shelves_between_drawers=True, drawer_front_one_piece=True, drawer_height=150)
    yield CabinetConfig.shelves_and_drawers(1, 3, door_mount="overlay", drawer_front_one_piece=True)


@pytest.mark.parametrize("config", list(configs()))
def test_parts_inside_envelope(config):
    layout = resolve_layout(config)
    for part in layout.parts:
        ymin = -config.front_thickness if part.category in ("door", "drawer_front") and config.door_mount == DoorMount.OVERLAY else 0
        assert part.origin[0] >= -TOL and part.max[0] <= config.width + TOL, part.name
        assert part.origin[1] >= ymin - TOL and part.max[1] <= config.depth + TOL, part.name
        assert part.origin[2] >= -TOL and part.max[2] <= config.height + TOL, part.name
        assert min(part.size) > 0, part.name


@pytest.mark.parametrize("config", list(configs()))
def test_no_collisions(config):
    parts = resolve_layout(config).parts
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            assert not overlaps(a, b), (a.name, b.name)


@pytest.mark.parametrize("config", list(configs()))
def test_joint_pairs_refer_to_parts(config):
    layout = resolve_layout(config)
    names = {p.name for p in layout.parts}
    assert len(names) == len(layout.parts)
    for main, cross in layout.joints:
        assert main in names and cross in names


def test_carcass_part_counts():
    layout = resolve_layout(CabinetConfig.grid(3, 4, door_style="none"))
    count = {c: len(layout.by_category(c)) for c in ("side", "top", "bottom", "back", "divider", "shelf", "door")}
    assert count == {"side": 2, "top": 1, "bottom": 1, "back": 1, "divider": 2, "shelf": 9, "door": 0}


def test_column_widths_fill_interior():
    config = CabinetConfig(width=1000, thickness=20, columns=[Column(width=300), Column(), Column()])
    layout = resolve_layout(config)
    shelves_or_tops = sorted(p.origin[0] for p in layout.by_category("divider"))
    # column 0: x 20..320, divider 320..340, column 1: 340..650, divider 650..670, column 2: 670..980
    assert shelves_or_tops == pytest.approx([320, 650])


def test_zone_heights_and_stacked_drawers():
    config = CabinetConfig.stack([Zone(ZoneKind.DRAWER, 150), Zone(ZoneKind.DRAWER, 150), "open"], height=1000, thickness=20, door_style="none")
    layout = resolve_layout(config)
    shelves = layout.by_category("shelf")
    # no shelf between the two stacked drawers, one between drawers and the open zone
    assert len(shelves) == 1
    assert shelves[0].origin[2] == pytest.approx(20 + 150 + 150)
    assert len(layout.by_category("drawer_front")) == 2


def test_overlay_fronts_cover_carcass():
    config = CabinetConfig(width=600, height=800, thickness=18, front_gap=2, door_mount="overlay", door_style="single")
    door = resolve_layout(config).by_category("door")[0]
    assert door.origin == pytest.approx((1, -18, 1))
    assert door.size == pytest.approx((598, 18, 798))


def test_overlay_fronts_split_shared_panels():
    config = CabinetConfig.grid(2, 1, width=600, thickness=18, front_gap=2)
    doors = sorted(resolve_layout(config).by_category("door"), key=lambda p: p.origin[0])
    # the gap between the two doors is centered on the divider
    divider_center = 18 + (600 - 3 * 18) / 2 + 9
    assert doors[1].origin[0] - doors[0].max[0] == pytest.approx(2)
    assert (doors[1].origin[0] + doors[0].max[0]) / 2 == pytest.approx(divider_center)


def test_inset_fronts_inside_opening():
    config = CabinetConfig(width=600, height=800, thickness=18, front_gap=2, door_mount="inset", inset_setback=1)
    layout = resolve_layout(config)
    door = layout.by_category("door")[0]
    assert door.origin == pytest.approx((20, 1, 20))
    assert door.size == pytest.approx((600 - 40, 18, 800 - 40))


def test_inset_pushes_shelves_back():
    config = CabinetConfig.grid(1, 2, door_mount="inset", front_thickness=19)
    shelf = resolve_layout(config).by_category("shelf")[0]
    assert shelf.origin[1] == pytest.approx(19)


def test_single_door_hinge_side():
    for side in (HingeSide.LEFT, HingeSide.RIGHT):
        door = resolve_layout(CabinetConfig(hinge_side=side)).by_category("door")[0]
        assert door.attributes["hinge_side"] == side.value
        expected_x = door.origin[0] if side == HingeSide.LEFT else door.max[0]
        assert door.attributes["hinge_point"][0] == pytest.approx(expected_x)


def test_column_overrides_door_style():
    config = CabinetConfig(columns=[Column(door_style="none"), Column(hinge_side="right")], hinge_side="left")
    doors = resolve_layout(config).by_category("door")
    assert len(doors) == 1
    assert doors[0].attributes["node"] == "1"
    assert doors[0].attributes["hinge_side"] == "right"


def test_double_doors_hinge_outwards():
    doors = sorted(resolve_layout(CabinetConfig(door_style=DoorStyle.DOUBLE)).by_category("door"), key=lambda p: p.origin[0])
    assert [d.attributes["hinge_side"] for d in doors] == ["left", "right"]
    assert doors[0].attributes["hinge_point"][0] == pytest.approx(doors[0].origin[0])
    assert doors[1].attributes["hinge_point"][0] == pytest.approx(doors[1].max[0])


def test_door_per_run_of_open_zones():
    config = CabinetConfig.stack(["open", "open", "drawer", "open"])
    doors = resolve_layout(config).by_category("door")
    assert len(doors) == 2


def test_drawer_box_fits_zone():
    config = CabinetConfig.stack([Zone(ZoneKind.DRAWER, 200), "open"], width=600, thickness=18, drawer_clearance=13)
    layout = resolve_layout(config)
    sides = sorted(layout.by_category("drawer_side"), key=lambda p: p.origin[0])
    assert sides[0].origin[0] == pytest.approx(18 + 13)
    assert sides[1].max[0] == pytest.approx(600 - 18 - 13)
    assert sides[0].size[2] == pytest.approx(200 - config.drawer_top_clearance)


def test_distribute():
    assert distribute(100, [None, 40, None]) == pytest.approx([30, 40, 30])
    assert distribute(100, [60, 40]) == [60, 40]
    with pytest.raises(ValueError):
        distribute(100, [60, 50])
    with pytest.raises(ValueError):
        distribute(100, [100, None])


@pytest.mark.parametrize(
    "config",
    [
        CabinetConfig(width=30),
        CabinetConfig.grid(30, 1, width=400),
        CabinetConfig.shelves_and_drawers(0, 20, height=500),
        CabinetConfig(columns=[Column(width=2000)]),
        CabinetConfig(columns=[Column(zones=[])]),
    ],
)
def test_invalid_configs_raise(config):
    with pytest.raises(ValueError):
        resolve_layout(config)


def test_drawer_height_default():
    config = CabinetConfig.stack(["drawer", Zone(ZoneKind.DRAWER, 250), "open"], height=1000, thickness=20, drawer_height=150, door_style="none")
    fronts = sorted((p for p in resolve_layout(config).by_category("drawer_side") if p.name.endswith("_l")), key=lambda p: p.origin[2])
    # the second drawer's box starts at its front, half the front gap above the boundary between the stacked drawers
    assert [f.origin[2] for f in fronts] == pytest.approx([20, 170 + 1.5])
    assert fronts[1].size[2] == pytest.approx(250 - config.drawer_top_clearance - 1.5)


def test_shelves_between_drawers():
    config = CabinetConfig.shelves_and_drawers(0, 3)
    assert len(resolve_layout(config).by_category("shelf")) == 1
    assert len(resolve_layout(config.copy(shelves_between_drawers=True)).by_category("shelf")) == 3


def test_inset_shelves_flush_between_fronts():
    config = CabinetConfig.stack(["drawer", "drawer", "open", "open"], door_mount="inset", shelves_between_drawers=True)
    shelves = {p.name: p for p in resolve_layout(config).by_category("shelf")}
    assert shelves["shelf_0_0"].origin[1] == 0  # between two drawer fronts
    assert shelves["shelf_0_1"].origin[1] == 0  # between a drawer front and a door
    assert shelves["shelf_0_2"].origin[1] == pytest.approx(config.front_thickness)  # behind the door
    assert shelves["shelf_0_2"].max[1] == pytest.approx(config.depth - config.back_thickness)


def test_inset_shelves_flush_without_doors():
    config = CabinetConfig.grid(1, 3, door_mount="inset", door_style="none")
    assert all(p.origin[1] == 0 for p in resolve_layout(config).by_category("shelf"))


def test_inset_drawer_fronts_keep_gap_to_flush_shelf():
    config = CabinetConfig.stack(["drawer", "drawer"], door_mount="inset", shelves_between_drawers=True, front_gap=2)
    layout = resolve_layout(config)
    shelf = layout.by_category("shelf")[0]
    lower, upper = sorted(layout.by_category("drawer_front"), key=lambda p: p.origin[2])
    assert shelf.origin[2] - lower.max[2] == pytest.approx(2)
    assert upper.origin[2] - shelf.max[2] == pytest.approx(2)


@pytest.mark.parametrize("mount", ["overlay", "inset"])
def test_one_piece_drawer_front(mount):
    config = CabinetConfig.stack(["drawer"], door_mount=mount, drawer_front_one_piece=True)
    layout = resolve_layout(config)
    assert layout.by_category("drawer_box_front") == []
    front = layout.by_category("drawer_front")[0]
    bottom = layout.by_category("drawer_bottom")[0]
    # box parts start right behind the front
    assert bottom.origin[1] == pytest.approx(front.max[1])
    for side in layout.by_category("drawer_side"):
        assert side.origin[1] == pytest.approx(front.max[1])
        assert (side.name, front.name) in layout.joints
    assert (bottom.name, front.name) in layout.joints


@pytest.mark.parametrize("mount", ["overlay", "inset"])
@pytest.mark.parametrize("between", [False, True])
def test_drawer_boxes_do_not_reach_below_their_fronts(mount, between):
    config = CabinetConfig.stack(["drawer", "drawer", "drawer", "open"], door_mount=mount, shelves_between_drawers=between)
    layout = resolve_layout(config)
    for front in layout.by_category("drawer_front"):
        key = front.attributes["drawer"]
        box = [p for p in layout.parts if p.attributes.get("drawer") == key and p.category != "drawer_front"]
        assert min(p.origin[2] for p in box) >= front.origin[2] - 1e-9, key
        assert max(p.max[2] for p in box) <= front.max[2] + 1e-9, key
