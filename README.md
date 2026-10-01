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

Panels are joined with compas_timber's `PlateLButtJoint` / `PlateTButtJoint`. The back runs the full height between the sides. The top and bottom butt against it as `main_plate`, with the back as `cross_plate`. The back sits in rabbets cut into the sides using `cabi_neat.joints.PlateRabbetJoint`, set by `back_joint="rabbet"` (the default; `"butt"` turns it off) and `rabbet_depth` (default: half the side thickness). The rabbet joint is an L joint with the back as main plate. The back is extended `rabbet_depth` into each side, and the side gets a `FreeContour` pocket as wide as the back is thick. Each shelf and divider picks its own joint to the sides, top, bottom and other panels: mortise and tenon, Clamex P-14, or stopped dado. In the planner it's a dropdown next to each panel's position, followed by only that joint's parameters. Empty fields use the defaults. In the config it's `Split.panel_joints`, one entry per panel:

- a joint type;
- a dict with the type and its parameters, e.g. `{"type": "clamex_p14", "distance": 80, "center_connector": false}` (see `JOINT_PARAMETERS`);
- or `None`, which means the cabinet's `interior_joint`. Shelves at the same height across columns share a setting.

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

Drawer boxes are joined like this:

- **Box back:** rabbeted into the sides (`PlateRabbetJoint`).
- **Box front, two-piece fronts:** also rabbeted into the sides. The visible front is fixed to the box front.
- **One-piece fronts** (`drawer_front_one_piece`): the visible front is the box front. Each side is fixed to its back face with one Clamex P-14 connector in the middle, plus two Ø8 alignment dowels 30 from the ends.
- **Bottom:** sits in grooves in the sides, front and back (`cabi_neat.joints.PlateGrooveJoint`), `drawer_bottom_offset` (default 10) above the box's lower edge. It's as thick as the carcass back unless `drawer_bottom_thickness` says otherwise. The grooves run through the sides. In the front and back they stop, exactly as long as the bottom's edge including its corners reaching into the side grooves: as short as possible without notching the bottom, and invisible on a one-piece front. Their depth (`drawer_groove_depth`) defaults to half the grooved panel's thickness.

Doors hang on European concealed cup hinges ("Topfband") using `cabi_neat.joints.CupHingeJoint`, a joint between the door and the side or divider on its hinge side. Each hinge makes these cuts:

- **Door:** a Ø35 × 13 pot (`FreeContour`) whose edge is 5 mm from the door edge (the pot offset). Also two Ø3 × 11 screw holes (`Drilling`), 48 apart and 6 further from the edge than the pot centre.
- **Side:** the mounting plate's two Ø5 × 12 screw holes (`Drilling`), 32 apart, on the face towards the door. They're 37 from the door's back face, so for inset doors they move back by the door's thickness.

Each door gets 4 hinges, 100 from its ends; set `hinge_count` to `None` to pick the count from the door height. The hinge type follows `door_mount`.

Every dimension can be changed per door (the `hinges` of a section with a door, or in the planner under that door). `CabinetConfig.hinges` sets cabinet-wide defaults, e.g. `{"cup_edge_distance": 3, "door_hole_spacing": 52}`.

The cut list is measured on the processed plates, so it includes the back's extra width and the tenons.

Configs serialize to JSON with `config.to_json(path)` and `CabinetConfig.from_json(path)`; see `examples/03_custom_stack.json`.

## Planner

```bash
python -m cabi_neat                               # or: cabi-neat
python -m cabi_neat examples/04_high_shelf.json   # start from a saved config
```

This opens the planner at `http://127.0.0.1:9001/planner/`. It shows a 2D front elevation next to a 3D view.

**Hierarchy bar.** Below the toolbar, `↑ Up | Carcass › Section 1 › Section 1.2` shows where you are.
- Click a button to go to that level, or use `↑ Up` / `Esc` for the parent.
- To go deeper, click a compartment in the plan.

**Sidebar.** It shows only the current compartment's parameters:
- **Carcass** (the top level): cabinet dimensions, thicknesses, front gap, overlay or inset, carcass joints (back rabbet, Clamex top and bottom), then the interior's front, shelves and dividers.
- **A section:**
  - **Dimensions:** clear size, its own size, flexible.
  - **Type:** Open, Door or Drawers. Switching type converts the section, and only that type's controls are shown:
    - **Open:** shelves and dividers, with each panel's position and joint (and that joint's parameters).
    - **Door:** hinged left, right or double, with the door's **Hinges** as a collapsible menu (collapsible General, Door and Side groups). Below that, the shelves and dividers behind the door.
    - **Drawers:** how many stacked drawers, dividers for side-by-side stacks, and "Shelves between drawers" (with 2 or more). A collapsible **Drawer settings** menu (one-piece front, bottom height, bottom thickness, bottom groove depth) applies to all of the section's drawers. The drawer positions can be typed too.
  - A section behind a door has no type: it's open, with its shelves and dividers.

Empty fields use the cabinet's defaults, shown as placeholders.

**How sections work:**
- Shelves, dividers and drawers are evenly spaced when added. Their spacing stays automatic: when the parent compartment or the cabinet changes size, they redistribute evenly.
- A position you type or drag, or a size you type, is fixed and stays. Only the compartment before a moved panel becomes fixed, and the automatic ones around it keep sharing the rest. Tick "Automatic" on a compartment to go back to shared spacing.
- Dividers run full height, and the shelf count applies to every column.
- Drag the current section's panels in the plan, or type their positions.
- A section with a door covers everything inside it.
- Drawers can't be behind a door.
- **Highlighting:** clicking a shelf or divider (or its row in the sidebar) highlights it in the plan, the sidebar and the 3D view. Clicking a part in the 3D view goes to its section and highlights it there.
- **Hinges:** doors show their hinges' pots in the plan, where the hinge joint drills them. For drawers below shelves, divide the section first, then set each part's type.

**Generate 3D.** Edits only update the plan. **Generate 3D** (top right) builds the timber model and updates the 3D view. It's highlighted while the 3D view is out of date. The cut list generates first if needed.

**Toolbar.** Undo/redo, a snap grid, the cut list, JSON save (with a save dialog in Chrome/Edge) and load, and toggles for fronts in the plan and fronts shown or open in 3D.

The page keeps no geometry of its own. It edits the interior tree (`planner_frontend/tree.js`) and posts the config to `/planner/api/config`. Python then resolves and builds the cabinet, updates the 3D view, and sends the resolved layout back for drawing. `cabi_neat.sections.sectioned(config)` converts any config into the planner's *section form*, where every node has an explicit front, without changing its geometry.

## Fabrication output

```bash
python -m cabi_neat.export cabinet.json --btlx cabinet.btlx --hops hops/
```

The planner's toolbar has **Export BTLx** and **Export HOPS** (a zip with one program per part and a `report.txt`). From Python, `cabi_neat.export.btlx(cabinet, path)` and `cabi_neat.export.hops(cabinet, directory)` do the same. Each export returns a report of what it couldn't export.

- **BTLx** is written by compas_timber's `BTLxWriter` from the cabinet's `TimberModel`. The model is in millimetres (`Tolerance(unit="MM")`). Features that aren't BTLx processings, the Clamex P-14 grooves, are left out while writing and stay on the plates.
- **HOPS** programs are written like the workshop's own (`Hops/Hop/OB/flur`) and use its macros (`cabi_neat.hops`), one per plate:
  - The part is cut to size first: saw 201 and `_Sformat_V5`.
  - Rabbets are a single line along their inner edge (`SP`/`G01`/`EP`, radius correction 1), as in `sides_schrank`.
  - Grooves for drawer bottoms in the drawer sides and back are lines along their walls with a linear approach (`SP` 1) and no lead-out (`EP` 0), with the largest router that fits the width. Closed ends stop a tool radius short, open ones run a tool diameter past the edge. In a one-piece drawer front the groove is a rectangular pocket, like the other pockets. 
  - Other pockets (mortises, tenon gaps, housings) use `_RechteckTasche_V5` with the largest router that fits, its radius as the corner radius, and no lead-out. Pockets open to an edge are widened past it for a bigger router. Other shapes use a contour and `_ExecutePocket_V5`.
  - The notches that leave a stopped dado's tenon are milled from the plate's end, in its plane (`Ebene1`-`Ebene4`). A contour runs up the tenon's end with arcs of the dado router's radius, so the tenon fits the dado's rounded end. It's run with decreasing allowances, the last 0, to clear the notch.
  - Hinge pots use `_Kreisbahn_V5` with the Ø20 router (403).
  - Drillings into faces use `Bohrung`, drillings into edges `HorzB`, always with a spindle drill (`WZB`) of the hole's diameter: the shortest one that's deep enough. The drill head isn't used. The defaults include a Ø8 (603) and a Ø3 (602, placeholder number) to be loaded.
  - Clamex P-14 in faces uses `Clamex_MS_FI` with the finger mill (136). In edges it uses `Clamex1_Stumpf` (ends) and `Clamex1_Stumpf_X` (long edges) with the disc (108), plus the access holes (drill 135).
  - With machining on both faces, the other face comes first, then `MachineStop_V7` to turn the panel over about its long edge. A plate with Clamex access holes has their face on top: the underside of shelves and of the top, and the inside of everything else.
  - The tools default to the ETH machine's tool table (092026tools), plus a Ø8 (603) and a Ø3 (602, placeholder number) drill to be loaded. `HopsTools.from_too(path)` reads another table, including the Clamex tools.
  - Identical parts (shelves, drawer bottoms, …) share one program, named after the first with `_CUT_<n>` appended, e.g. `shelf_0_2_CUT_4.hop` is cut four times. `report.txt` lists the parts each one is for.
  - Every part lies on the table with its long edge along X. Of the two ways, drawer parts (sides, back, front) take the one with their bottom edge, and the groove, at Y = 0.
  - Files are written with Windows line endings.

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
