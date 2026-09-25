import pytest

from cabi_neat import CabinetConfig
from cabi_neat import DoorStyle
from cabi_neat import Cell
from cabi_neat import Split
from cabi_neat import ZoneKind
from cabi_neat import columns
from cabi_neat import resolve_layout
from cabi_neat import rows
from cabi_neat.sections import sectioned

CONFIGS = [
    CabinetConfig(),
    CabinetConfig.grid(3, 4, door_style="double"),
    CabinetConfig.shelves_and_drawers(2, 3, columns=2, door_mount="inset"),
    CabinetConfig.stack(["open", "open", "drawer", "drawer", "open"], hinge_side="right", door_style="none"),
    CabinetConfig.stack(["open", "drawer", "open", "open"], shelves_between_drawers=True),
    CabinetConfig(height=1800, interior=rows(columns(rows(Cell(size=500), Cell(size=400), "open", size=400), "open"), Cell(size=300))),
    CabinetConfig(door_mount="inset", interior=rows(columns("drawer", "drawer", size=200), columns(rows("open", "open"), "open", one_door=True))),
    CabinetConfig(interior=Cell()),
]


def geometry(config):
    return sorted((p.category, tuple(round(v, 6) for v in p.origin), tuple(round(v, 6) for v in p.size)) for p in resolve_layout(config).parts)


@pytest.mark.parametrize("config", CONFIGS)
def test_sectioned_keeps_geometry(config):
    assert geometry(sectioned(config)) == geometry(config)


def walk(node, covered=False):
    yield node, covered
    for child in getattr(node, "children", []):
        yield from walk(child, covered or (node.door_style not in (None, DoorStyle.NONE)))


@pytest.mark.parametrize("config", CONFIGS)
def test_sectioned_form(config):
    root = sectioned(config).interior
    for node, covered in walk(root):
        assert node.door_style is not None
        has_door = node.door_style != DoorStyle.NONE
        if isinstance(node, Split):
            assert node.one_door == has_door
            # every split keeps exactly one flexible child, the last one
            assert [c.size is None for c in node.children] == [False] * (len(node.children) - 1) + [True]
        if covered:
            assert not has_door
            assert not (isinstance(node, Cell) and node.kind == ZoneKind.DRAWER)


def test_explicit_doors_are_separate():
    config = sectioned(CabinetConfig.stack(["open", "open"]))
    column = config.interior.children[0]
    # the two stacked compartments shared one door, so they become one section with a door and a shelf behind it
    assert len(column.children) == 1
    assert column.children[0].door_style == DoorStyle.SINGLE and column.children[0].one_door
    config.interior = rows(Cell(door_style="single"), Cell(door_style="single"))
    assert len(resolve_layout(config).by_category("door")) == 2


def test_sectioned_can_still_resize():
    config = sectioned(CabinetConfig.shelves_and_drawers(2, 2, columns=2))
    resolve_layout(config.copy(width=1200, height=1600))


def test_regions_cover_nodes():
    config = CabinetConfig(height=1800, interior=rows(columns("open", "open"), Cell(size=300)))
    regions = resolve_layout(config).regions
    assert set(regions) == {"", "0", "0.0", "0.1", "1"}
    assert regions["1"][3] == pytest.approx(300)
