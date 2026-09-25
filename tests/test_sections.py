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
            # every split keeps at least one flexible child, so the cabinet can be resized
            assert any(c.size is None for c in node.children)
        if covered:
            assert not has_door
            assert not (isinstance(node, Cell) and node.kind == ZoneKind.DRAWER)


def test_explicit_doors_are_separate():
    config = sectioned(CabinetConfig.stack(["open", "open"]))
    # the two stacked compartments shared one door, so they become one section with a door and a shelf behind it; the
    # single column around it is the whole interior, so that section is the interior itself
    interior = config.interior
    assert interior.door_style == DoorStyle.SINGLE and interior.one_door
    assert len(interior.children) == 2
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


def test_single_child_splits_are_collapsed():
    # the columns shorthand with one column: that column is the whole interior, not a section of its own
    interior = sectioned(CabinetConfig.shelves_and_drawers(1, 2)).interior
    assert interior.direction.value == "rows"
    assert [getattr(c, "kind", None) for c in interior.children[:2]] == [ZoneKind.DRAWER, ZoneKind.DRAWER]
    stack = [interior]
    while stack:
        node = stack.pop()
        if isinstance(node, Split):
            assert len(node.children) > 1 or node.one_door
            stack.extend(node.children)


def test_automatic_spacing_survives_and_redistributes():
    # shelves added by count have no sizes of their own: they stay evenly spaced when the cabinet is resized
    config = sectioned(CabinetConfig(height=1200, thickness=18, interior=rows("open", "open", "open", door_style="none")))
    assert all(c.size is None for c in config.interior.children)

    def shelves(c):
        return sorted(p.origin[2] for p in resolve_layout(c).by_category("shelf"))

    taller = config.copy(height=1800)
    inner = 1800 - 2 * 18 - 2 * 18
    assert shelves(taller) == pytest.approx([18 + inner / 3, 18 + 2 * inner / 3 + 18])
    # an explicit size stays; the others share what's left
    config.interior.children[0].size = 300
    taller = config.copy(height=1800)
    rest = (1800 - 4 * 18 - 300) / 2
    assert shelves(taller) == pytest.approx([18 + 300, 18 + 300 + 18 + rest])
