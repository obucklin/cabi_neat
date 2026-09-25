# cabi_neat

Parametric cabinets built from [compas_timber](https://github.com/gramaziokohler/compas_timber) plates and plate joints, with a browser configurator based on compas_threejs.

## Model

A `CabinetConfig` describes the cabinet (all dimensions in mm):

- **Outer dimensions**: `width`, `height`, `depth`, plus panel, back and front thicknesses.
- **Layout**: a list of `Column`s (left to right, separated by dividers), each a stack of `Zone`s from bottom to top (separated by shelves). A zone is `open` or a `drawer`. Consecutive drawers stack without a shelf between them. `None` widths and heights share the remaining space equally.
- **Drawers**: `drawer_height` sets a default height for drawer zones that don't set their own. `shelves_between_drawers` puts a shelf between stacked drawers. `drawer_front_one_piece` makes the visible front double as the drawer box front, instead of a separate front screwed onto a complete box.
- **Fronts**: `door_mount` is `overlay` (fronts in front of the carcass) or `inset` (fronts flush inside the openings). `door_style` is `none`, `single` (hinged on `hinge_side`, left or right) or `double`. Each run of consecutive open zones in a column gets one door or a pair of doors. Columns can override `door_style` and `hinge_side`. With inset fronts, shelves behind a door are set back behind it, while shelves between fronts come forward flush with the carcass front.

Presets cover the common layouts:

```python
from cabi_neat import Cabinet, CabinetConfig

CabinetConfig.stack(["drawer", "drawer", "open", "open"])  # one column, arbitrary zones
CabinetConfig.grid(columns=3, rows=4)  # equal compartments
CabinetConfig.shelves_and_drawers(shelves=2, drawers=3, columns=2)  # drawers at the bottom, shelves above

cabinet = Cabinet(CabinetConfig.grid(2, 3, door_mount="inset"))
cabinet.model  # compas_timber TimberModel, one Plate per part, joinery processed
print(cabinet.format_cut_list())
```

`Cabinet` goes through two steps:

1. `cabi_neat.layout.resolve_layout(config)` turns the config into axis-aligned `Part` boxes and a list of (main, cross) joint pairs. This is pure Python and needs no geometry kernel.
2. `cabi_neat.builder` creates a `Plate` per part (with `category`, `hinge_side`, etc. in `plate.attributes`) and joins each pair with `PlateLButtJoint` or `PlateTButtJoint`, depending on the topology that compas_timber's `PlateConnectionSolver` detects.

### Custom interiors

For any arrangement of shelves and dividers, set `interior` to a tree of splits instead of using `columns`:

- `rows(...)` stacks its children from bottom to top, with a shelf between each pair.
- `columns(...)` places its children from left to right, with a vertical divider between each pair.
- `Cell(kind, size)`, or the strings `"open"` and `"drawer"`, is a compartment.

Each child's `size` is its clear height (in `rows`) or clear width (in `columns`). That fixes exactly where the panels between children go. Children without a size share the remaining space.

The example below has a full-width shelf with 300 mm clear above it. Under that shelf a divider stands on the cabinet bottom, with 400 mm clear on its left. The left side has shelves 500 mm and 400 mm apart, and the right side is one open cavity:

```python
from cabi_neat import CabinetConfig, Cell, columns, rows

interior = rows(
    columns(
        rows(Cell(size=500), Cell(size=400), "open", size=400),
        "open",
    ),
    Cell(size=300),
)
config = CabinetConfig(width=1000, height=1800, interior=interior)
```

Fronts follow these rules:

- **Door style and hinge side:** `door_style` and `hinge_side` on a split or cell apply to everything below it.
- **Default doors:** consecutive open cells in a `rows` split share one door, and each open cell in a `columns` split gets its own door.
- **One door over a region:** `one_door=True` on a split covers the whole split with a single door, or a pair of doors for double style.
- **Inset setback:** with inset fronts, a panel is set back only when a single door covers it. Otherwise it's flush with the carcass front.

`columns=[Column(...)]` is shorthand for `columns(rows(...), ...)`. See `examples/04_high_shelf.json`.

### Joints

Panels are joined with compas_timber's `PlateLButtJoint` / `PlateTButtJoint`. The back runs the full height between the sides. The top and bottom butt against it as `main_plate`, with the back as `cross_plate`. The back sits in rabbets cut into the sides using `cabi_neat.joints.PlateRabbetJoint`, set by `back_joint="rabbet"` (the default; `"butt"` turns it off) and `rabbet_depth` (default: half the side thickness). The rabbet joint is an L joint with the back as main plate. The back is extended `rabbet_depth` into each side, and the side gets a `FreeContour` pocket as wide as the back is thick. Each shelf and divider picks its own joint to the sides, top, bottom and other panels: mortise and tenon, Clamex P-14, or stopped dado. In the planner it's a dropdown next to each panel's position. In the config it's `Split.panel_joints`, one entry per panel, where `None` means the cabinet's `interior_joint`. Shelves at the same height across columns share a setting.

`cabi_neat.joints.PlateMortiseTenon` is a T joint. Its settings:

- `interior_joint="mortise_tenon"` is the default; `"butt"` gives plain T-butts.
- `tenon_depth`: how far the tenons go in. The default is a quarter of the receiving panel's thickness.
- `tenon_count`: the number of tenons per edge, default 2. They're evenly spaced, and both ends of the edge are left free.

The shelf's edge is extended into the other panel. `FreeContour` pockets then cut the extension away between the tenons and cut matching blind mortises. Joints with the back stay plain T-butts.

By default, the top and bottom are joined to the sides with Lamello Clamex P-14 connectors, including a centre connector (`carcass_joint="clamex_p14"`, `clamex_center=True`), using `cabi_neat.joints.ClamexP14Joint`:

- The plates are trimmed as in an L or T butt joint.
- Connectors sit at the middle of the main plate's thickness, `clamex_distance` (default 60) from both ends of the joining edge. With `clamex_center`, there's one more in the middle.
- Each connector adds two features, both compas_model `Feature`s that cut a P-System groove (a Ø100.4 blade segment, 7 wide, 14.7 deep):
  - `P14Edge(ref_side, location)` in the main plate's edge;
  - `P14Face(ref_side, x, y, orientation)` in the cross plate's face.
- `joint.connector_frames()` gives each connector's position and orientation.

`cabi_neat.joints.PlateStoppedDadoJoint` is a stopped dado (housing), used as follows:

- The groove in the cross plate runs out through its edge in `open_direction`; the cabinet uses the back. It stops `stop_distance` (`dado_stop`, default 20) behind the main plate's front edge.
- The main plate's edge is extended into the groove, with a notch where the groove stops, so the panel slides in from behind and nothing shows at the front.
- The groove's `depth` (`dado_depth`) defaults to a third of the cross plate's thickness.

The cut list is measured on the processed plates, so it includes the back's extra width and the tenons.

Configs serialize to JSON with `config.to_json(path)` and `CabinetConfig.from_json(path)`; see `examples/03_custom_stack.json`.

## Planner

```bash
python -m cabi_neat                               # or: cabi-neat
python -m cabi_neat examples/04_high_shelf.json   # start from a saved config
```

This opens the planner at `http://127.0.0.1:9001/planner/`. It shows a 2D front elevation next to a live 3D view, and all the controls are in the planner:

- **Cabinet:** dimensions, panel / back / front thickness, front gap, overlay or inset fronts, and the drawer options.
- **Sections:** the whole interior is a section, and so is every compartment made by a shelf or divider.
  1. **Select** a section by clicking it. Each click goes one level deeper, `Esc` goes back up, and the breadcrumbs jump to any level.
  2. **Add or remove** shelves and dividers with the counters. They're evenly spaced. Dividers run full height, and the shelf count applies to every column. Select a single column to give it a different shelf count.
  3. **Position** the selected section's panels by dragging them, or by typing their distance from the bottom or left of the section. Shelves at the same height in different columns move together.
  4. **Fronts:** give a section a door (hinged left or right, or double), or tick **Drawer** for a section without shelves or dividers. Everything inside a section with a door is behind that door: those sections can't have fronts of their own, and with inset fronts their panels are set back. Panels between fronts come forward flush with the carcass. Dividing a drawer gives stacked or side-by-side drawers.

  Child sections that have their own shelves or dividers are greyed out, and only the selected section's own panels can be edited.
- **View:** show fronts in the plan, and show or open fronts in the 3D view (doors swing on their hinges, drawers slide out).
- **Toolbar:** undo and redo, a snap grid, a cut list, and JSON save/load.

The page keeps no geometry of its own. It edits the interior tree (`planner_frontend/tree.js`) and posts the config to `/planner/api/config`. Python then resolves and builds the cabinet, updates the 3D view, and sends the resolved layout back for drawing. `cabi_neat.sections.sectioned(config)` converts any config into the planner's *section form*, where every node has an explicit front, without changing its geometry.

## Setup

Plate geometry needs the OCC backend of compas_brep.

```bash
conda create -n cabi-dev python=3.12
conda activate cabi-dev
pip install -e ../compas_timber -e ../compas_threejs -e ".[dev,viewer]"
pytest
node --test tests/js/*.test.mjs   # planner tree operations
```

An editable checkout of compas_threejs has no built frontend. Run `invoke pre-build` in the compas_threejs repo once (this needs npm). Pip releases of compas_threejs already include the frontend.
