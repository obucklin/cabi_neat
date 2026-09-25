import pytest

from cabi_neat import CabinetConfig
from cabi_neat import Cell
from cabi_neat import Column
from cabi_neat import Zone
from cabi_neat import ZoneKind
from cabi_neat import columns
from cabi_neat import resolve_layout
from cabi_neat import rows

W, H, D, T = 1000, 1800, 500, 20


def high_shelf_config(**kwargs):
    """A full-width shelf with 300 mm above it; below it a divider with 400 mm clear on the left, split by two shelves."""
    interior = rows(
        columns(
            rows(Cell(size=500), Cell(size=400), Cell(), size=400),
            "open",
        ),
        Cell(size=300),
    )
    params = dict(width=W, height=H, depth=D, thickness=T, interior=interior)
    params.update(kwargs)
    return CabinetConfig(**params)


def test_high_shelf_with_divider_below():
    layout = resolve_layout(high_shelf_config(door_style="none"))
    shelves = {p.name: p for p in layout.by_category("shelf")}
    dividers = layout.by_category("divider")

    high = shelves["shelf_0"]
    assert high.origin[0] == T and high.size[0] == pytest.approx(W - 2 * T)  # full interior width
    assert high.max[2] == pytest.approx(H - T - 300)  # 300 mm clear above it

    assert len(dividers) == 1
    divider = dividers[0]
    assert divider.origin[0] == pytest.approx(T + 400)  # 400 mm clear to its left
    assert divider.origin[2] == pytest.approx(T)  # stands on the bottom
    assert divider.max[2] == pytest.approx(high.origin[2])  # up to the high shelf

    left = sorted((p for p in shelves.values() if p.name != "shelf_0"), key=lambda p: p.origin[2])
    assert [p.origin[2] for p in left] == pytest.approx([T + 500, T + 500 + T + 400])
    assert all(p.origin[0] == T and p.size[0] == pytest.approx(400) for p in left)

    joints = set(layout.joints)
    assert (divider.name, "bottom") in joints and (divider.name, "shelf_0") in joints
    assert (left[0].name, "side_left") in joints and (left[0].name, divider.name) in joints
    assert ("shelf_0", "side_left") in joints and ("shelf_0", "side_right") in joints


def test_high_shelf_doors():
    layout = resolve_layout(high_shelf_config())
    doors = {p.name for p in layout.by_category("door")}
    # the left stack shares one door, the right cavity and the top compartment get one each
    assert doors == {"door_0_0_0", "door_0_1", "door_1"}


def test_columns_shorthand_equals_tree():
    shorthand = CabinetConfig(columns=[Column(zones=[Zone(ZoneKind.DRAWER, 200), Zone()], width=300), Column(door_style="double")])
    tree = CabinetConfig(
        interior=columns(rows(Cell("drawer", 200), "open", size=300), rows("open", door_style="double")),
    )
    a, b = resolve_layout(shorthand), resolve_layout(tree)
    assert [(p.name, p.origin, p.size) for p in a.parts] == [(p.name, p.origin, p.size) for p in b.parts]
    assert a.joints == b.joints


def test_one_door_covers_split_and_sets_back_panels():
    interior = columns(rows("open", "open"), "open", one_door=True)
    layout = resolve_layout(CabinetConfig(interior=interior, door_mount="inset", door_style="double"))
    assert sorted(p.name for p in layout.by_category("door")) == ["door_l", "door_r"]
    for panel in layout.by_category("shelf", "divider"):
        assert panel.origin[1] == pytest.approx(layout.config.front_thickness), panel.name


def test_panels_between_fronts_are_flush():
    interior = columns("open", rows("open", "open"))
    layout = resolve_layout(CabinetConfig(interior=interior, door_mount="inset"))
    # the divider separates two doors, the right stack's shelf sits behind one door
    assert layout.part("divider_0").origin[1] == 0
    assert layout.part("shelf_1_0").origin[1] == pytest.approx(layout.config.front_thickness)


def test_drawer_behind_door_rejected():
    with pytest.raises(ValueError, match="behind a door"):
        resolve_layout(CabinetConfig(interior=rows("drawer", "open", one_door=True)))


def test_side_by_side_drawers():
    layout = resolve_layout(CabinetConfig(interior=rows(columns("drawer", "drawer", "drawer", size=200), "open")))
    assert len(layout.by_category("drawer_front")) == 3
    assert len(layout.by_category("divider")) == 2


def test_cell_as_interior():
    layout = resolve_layout(CabinetConfig(interior=Cell()))
    assert layout.by_category("shelf", "divider") == []
    assert len(layout.by_category("door")) == 1


def test_fixed_sizes_must_fit():
    with pytest.raises(ValueError):
        resolve_layout(high_shelf_config(height=800))


def test_interior_json_roundtrip():
    config = high_shelf_config()
    restored = CabinetConfig.from_json(config.to_json())
    assert restored == config
    assert [p.name for p in resolve_layout(restored).parts] == [p.name for p in resolve_layout(config).parts]


def test_interior_json_shorthand():
    data = {"interior": {"direction": "rows", "children": ["drawer", {"direction": "columns", "children": ["open", "open"]}]}}
    layout = resolve_layout(CabinetConfig.from_data(data))
    assert len(layout.by_category("divider")) == 1


@pytest.mark.parametrize("shelved", [0, 1])
def test_door_on_divided_section_next_to_door_on_compartment(shelved):
    """Regression: a door over a shelved section and a door over a single compartment used to share a part name."""
    shelved_side = rows(Cell(door_style="none"), Cell(door_style="none"), door_style="single", one_door=True)
    plain_side = Cell(door_style="single", hinge_side="right")
    children = [shelved_side, plain_side] if shelved == 0 else [plain_side, shelved_side]
    layout = resolve_layout(CabinetConfig(interior=columns(*children, door_style="none")))
    doors = layout.by_category("door")
    assert len(doors) == 2
    assert len({p.name for p in layout.parts}) == len(layout.parts)


def test_panel_joints_validated_and_serialized():
    with pytest.raises(ValueError, match="one entry per panel"):
        rows("open", "open", panel_joints=[])
    with pytest.raises(ValueError, match="Panel joints"):
        rows("open", "open", panel_joints=["glue"])
    config = CabinetConfig(interior=rows("open", "open", panel_joints=["clamex_p14"]))
    assert CabinetConfig.from_json(config.to_json()) == config


def test_panel_joints_survive_sectioning():
    from cabi_neat.sections import sectioned

    interior = rows("drawer", "open", "open", "open", panel_joints=["clamex_p14", "butt", "mortise_tenon"])
    layout = resolve_layout(CabinetConfig(interior=interior))
    before = {p.name: p.attributes["joint"] for p in layout.by_category("shelf")}
    after_config = sectioned(CabinetConfig(interior=interior))
    after = {tuple(p.origin): p.attributes["joint"] for p in resolve_layout(after_config).by_category("shelf")}
    assert sorted(after.values()) == sorted(before.values())
    by_position = {tuple(p.origin): p.attributes["joint"] for p in layout.by_category("shelf")}
    assert after == by_position


def test_panel_joint_parameters():
    interior = rows("open", "open", "open", panel_joints=[{"type": "clamex_p14", "distance": 80, "center_connector": False}, {"type": "mortise_tenon"}])
    layout = resolve_layout(CabinetConfig(interior=interior, tenon_count=3))
    first, second = layout.part("shelf_0"), layout.part("shelf_1")
    assert first.attributes["joint_parameters"] == {"distance": 80, "center_connector": False}
    assert second.attributes["joint_parameters"] == {"depth": None, "tenon_count": 3}  # cabinet defaults
    kind, parameters = layout.joint_kinds[("shelf_0", "side_left")]
    assert kind == "clamex_p14" and parameters["distance"] == 80
    config = CabinetConfig(interior=interior)
    assert CabinetConfig.from_json(config.to_json()) == config
    with pytest.raises(ValueError, match="Unknown parameters"):
        rows("open", "open", panel_joints=[{"type": "clamex_p14", "depth": 3}])


def test_per_section_hinges_drawers_and_drawer_shelves():
    from cabi_neat.sections import sectioned

    interior = rows(
        Cell("drawer", drawer={"front_one_piece": True, "bottom_offset": 15}),
        Cell("drawer"),
        Cell(door_style="single", hinges={"hinge_count": 2, "cup_edge_distance": 3}),
        drawer_shelves=True,
    )
    config = CabinetConfig(interior=interior, hinges={"end_distance": 80})
    layout = resolve_layout(config)
    # the two stacked drawers get a shelf between them (a shelf between drawers and door anyway)
    assert len(layout.by_category("shelf")) == 2
    # the first drawer has a one-piece front (no box front), the second uses the cabinet's two-piece default
    assert "drawer_0_box_front" not in [p.name for p in layout.parts]
    assert "drawer_1_box_front" in [p.name for p in layout.parts]
    assert layout.part("drawer_0_bottom").origin[2] - layout.part("drawer_0_side_l").origin[2] == pytest.approx(15)
    # the door's hinges: its own dimensions over the cabinet's
    kind, parameters = next(v for (main, cross), v in layout.joint_kinds.items() if v[0] == "cup_hinge")
    assert (parameters["hinge_count"], parameters["cup_edge_distance"], parameters["end_distance"]) == (2, 3, 80)
    # the options survive JSON and conversion to section form
    assert CabinetConfig.from_json(config.to_json()) == config
    converted = sectioned(config).interior
    assert converted.drawer_shelves is True
    assert converted.children[0].drawer == {"front_one_piece": True, "bottom_offset": 15}
    assert converted.children[2].hinges == {"hinge_count": 2, "cup_edge_distance": 3}
    with pytest.raises(ValueError, match="Unknown drawer settings"):
        Cell("drawer", drawer={"color": "red"})
