"""Fabrication output: BTLx (through compas_timber) and HOPS (through easyhops).

Both work from a built :class:`cabi_neat.Cabinet`. Features that aren't BTLx processings (the Clamex P-14 grooves) can't
go into BTLx and are left out of both; every export returns a report of what it left out.
"""

import io
import math
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List

from compas_timber.fabrication import BTLxProcessing
from compas_timber.fabrication import BTLxWriter

# ==========================================================================
# BTLx
# ==========================================================================


@dataclass
class ExportReport:
    """What an export produced and what it had to leave out."""

    skipped: List[str] = field(default_factory=list)  # human-readable, one entry per left-out feature
    errors: List[str] = field(default_factory=list)

    def summary(self):
        lines = []
        if self.skipped:
            lines.append("{} feature(s) not exported:".format(len(self.skipped)))
            lines.extend("  " + s for s in self.skipped)
        if self.errors:
            lines.append("{} error(s):".format(len(self.errors)))
            lines.extend("  " + e for e in self.errors)
        return "\n".join(lines) or "Everything was exported."


def _describe(feature):
    names = {"P14Edge": "Clamex P-14 groove (edge)", "P14Face": "Clamex P-14 groove (face)"}
    return names.get(type(feature).__name__, type(feature).__name__)


@contextmanager
def only_btlx_features(model):
    """Temporarily hide the plates' features that aren't BTLx processings, e.g. Clamex P-14 grooves.

    Yields ``{element: [hidden features]}``. The features are restored afterwards, in their original order.
    """
    originals = {}
    hidden = {}
    for element in model.elements():
        features = getattr(element, "_features", None)
        if not features:
            continue
        other = [f for f in features if not isinstance(f, BTLxProcessing)]
        if other:
            originals[element] = features
            hidden[element] = other
            # set the list directly: the public API would reset the element's cached geometry, which doesn't change
            element._features = [f for f in features if isinstance(f, BTLxProcessing)]
    try:
        yield hidden
    finally:
        for element, features in originals.items():
            element._features = features


def btlx(cabinet, filepath=None, project_name="cabi_neat"):
    """The cabinet's BTLx, as an XML string, written to ``filepath`` if given.

    Returns
    -------
    tuple(str, :class:`ExportReport`)
    """
    writer = BTLxWriter(project_name=project_name)
    report = ExportReport()
    with only_btlx_features(cabinet.model) as hidden:
        xml = writer.model_to_xml(cabinet.model)
    for element, features in hidden.items():
        report.skipped.extend("{}: {} (not a BTLx processing)".format(element.name, _describe(f)) for f in features)
    report.errors.extend(str(e) for e in getattr(writer, "errors", []) or [])
    if filepath:
        with open(filepath, "w") as f:
            f.write(xml)
    return xml, report


# ==========================================================================
# HOPS
# ==========================================================================


@dataclass
class HopsTools:
    """Machine tools for the HOPS export. Positions are machine-specific: set them to match your tool changer.

    Parameters
    ----------
    router
        For pockets (rabbets, grooves, mortises, hinge pots). Default: easyhops' SRSLD12 (Ø11.8, position 404).
    small_router
        For pockets narrower than ``router``'s diameter (e.g. 8 mm grooves). Default: a Ø6 router at position 405 -
        a placeholder, adjust it to your machine.
    small_router_diameter
        Diameter of ``small_router``, for the pockets it can mill.
    drill
        For drillings. ``None`` uses a drill at position 607 (easyhops' default).
    """

    router: object = None
    router_diameter: float = 11.8
    small_router: object = None
    small_router_diameter: float = 6.0
    drill: object = None

    def __post_init__(self):
        from easyhops.tool_library import SRSLD12
        from easyhops.tool_library import MachiningTool
        from easyhops.tool_library import ToolCallType

        if self.router is None:
            self.router = SRSLD12()
        if self.small_router is None:
            self.small_router = MachiningTool(position=405, tool_type=ToolCallType.ROUTER, name="Router D6")


def _is_rectangle(points, box_dims, tol=1e-3):
    """True if a closed polyline's points are the corners of an axis-aligned rectangle of ``box_dims``."""
    xs = sorted(set(round(p[0], 3) for p in points))
    ys = sorted(set(round(p[1], 3) for p in points))
    return len(xs) == 2 and len(ys) == 2 and abs(xs[1] - xs[0] - box_dims[0]) < tol and abs(ys[1] - ys[0] - box_dims[1]) < tol


def _pocket_width(points):
    """The narrowest dimension of a pocket: the shorter side of a rectangle, else of its bounding box."""
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if len(pts) == 4:
        sides = [math.dist(pts[i][:2], pts[(i + 1) % 4][:2]) for i in range(4)]
        return min(sides)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(max(xs) - min(xs), max(ys) - min(ys))


class PlateHopsJob:
    """Builds the HOPS job of one plate: its face with the most features on top, the other face machined first."""

    def __init__(self, plate, tools=None):
        self.plate = plate
        self.tools = tools or HopsTools()
        self.skipped = []
        self._pockets = 0

    def _face_sides(self):
        """The plate's two faces as reference side indices: the one with more features first (it ends up on top)."""
        counts = {0: 0, 2: 0}
        for feature in self.plate.features:
            if isinstance(feature, BTLxProcessing) and feature.ref_side_index in counts:
                counts[feature.ref_side_index] += 1
        top = 2 if counts[2] > counts[0] else 0
        return top, (top + 2) % 4

    def build(self):
        from easyhops.hop_core import FinishedPart
        from easyhops.hop_core import ParkMode
        from easyhops.hop_core import ParkPosition
        from easyhops.hop_core import VarsDefinition
        from easyhops.hop_job import HOPSJob
        from easyhops.utility_commands import MachineStop

        plate = self.plate
        top, bottom = self._face_sides()
        length, width, height = self._size()
        vars = VarsDefinition(dx=length, dy=width, dz=height)
        vars.add_variable("RSI", str(top), "ReferenceSideIndex (0-5)")
        job = HOPSJob(vars=vars, finished_part=FinishedPart(), park_mode=ParkPosition(mode=ParkMode.RIGHT_FRONT), machinings=[], ref_side_index=top)

        first, second = [], []
        for index, feature in enumerate(plate.features):
            if not isinstance(feature, BTLxProcessing):
                self.skipped.append("{}: {} (not a BTLx processing)".format(plate.name, _describe(feature)))
                continue
            side = feature.ref_side_index
            if index == 0 and not feature.counter_sink:
                self._outline(feature)  # the plate's outline: cut to size, nothing to machine if it's a rectangle
            elif side == bottom:
                first.extend(self._convert(feature, machine_side=bottom))
            elif side in (0, 1, 2, 3):
                second.extend(self._convert(feature, machine_side=top))
            else:
                self.skipped.append("{}: {} into an end (reference side {}) - not supported by easyhops".format(plate.name, type(feature).__name__, side + 1))

        if first:
            job.add(first)
            job.add(MachineStop("Turn the panel over (reference side {} up)".format(top + 1)))
        if second:
            job.add(second)
        return job

    def _size(self):
        """(length, width, thickness) of the plate's blank, including what joints extended it by.

        (``get_dimensions_relative_to_side`` reports the plate's original width, before any extension.)
        """
        blank = self.plate.blank
        return blank.xsize, blank.ysize, blank.zsize

    def _outline(self, feature):
        contour = feature.contour_param_object
        points = list(getattr(contour, "polyline", None) or [])
        if points and not _is_rectangle(points, self._size()[:2]):
            self.skipped.append("{}: non-rectangular outline - not supported yet, cut it to shape separately".format(self.plate.name))

    def _convert(self, feature, machine_side):
        name = type(feature).__name__
        if name == "Drilling":
            return self._drilling(feature, machine_side)
        if name == "FreeContour" and feature.counter_sink:
            return self._pocket(feature, machine_side)
        self.skipped.append("{}: {} - not supported yet".format(self.plate.name, name))
        return []

    def _drilling(self, drilling, machine_side):
        """A drilling, with easyhops' conventions for the top face and the long edges, to the drilling's own depth.

        Mirrors ``easyhops.strategies.drilling.DrillingStrategies.drilling``, which can't be imported at the moment
        (its package imports ``AngledLine`` from the wrong module) and always drills to the tool's maximum depth.
        """
        from compas.tolerance import TOL
        from easyhops.hop_core import EasySnapXY
        from easyhops.hop_core import EasySnapZ
        from easyhops.hop_job import HOPSMachining
        from easyhops.machining_commands import DrillingOperation
        from easyhops.tool_library import MachiningTool
        from easyhops.tool_library import ToolCallType
        from easyhops.work_planes import WorkPlane

        diff = (drilling.ref_side_index - machine_side) % 4
        if diff == 0:  # the face on top
            x, y, z = drilling.start_x, drilling.start_y, 0.0
            rotation, tilt = 180 - drilling.angle, -drilling.inclination
            snap = EasySnapXY.FRONT_LEFT
        elif diff == 1:  # the front edge
            x, y, z = drilling.start_x, 0.0, drilling.start_y
            rotation, tilt = drilling.inclination, 180 - drilling.angle
            snap = EasySnapXY.FRONT_LEFT
        elif diff == 3:  # the back edge
            x, y, z = drilling.start_x, 0.0, drilling.start_y
            rotation = 180 - drilling.inclination if TOL.is_close(drilling.angle, 180) else -(180 - drilling.inclination)
            tilt = drilling.angle
            snap = EasySnapXY.REAR_LEFT
        else:
            self.skipped.append("{}: drilling from the underside - not supported".format(self.plate.name))
            return []
        depth = drilling.depth if drilling.depth_limited else self._size()[2] + 1.0
        operation = DrillingOperation(x=x, y=y, z=z, depth=-depth, diameter=drilling.diameter, rotation=rotation, tilt=tilt, easy_snap_xy=snap, easy_snap_z=EasySnapZ.TOP_SIDE)
        tool = self.tools.drill or MachiningTool(position=607, tool_type=ToolCallType.DRILLER)
        comment = "\n".join(["; ---------------------------------", "; Drilling D{:g}, {:.1f} deep".format(drilling.diameter, depth), "; ---------------------------------"])
        return [HOPSMachining(tool=tool, work_plane=WorkPlane.TOP, operations=operation, comments=[comment])]

    def _pocket(self, free_contour, machine_side):
        from easyhops.contour_commands import CloseContour
        from easyhops.contour_commands import ContourLine
        from easyhops.contour_commands import ContourStart
        from easyhops.hop_core import EasySnapXY
        from easyhops.hop_job import HOPSMachining
        from easyhops.hop_macros import FreeFormPocket
        from easyhops.work_planes import WorkPlane

        contour = free_contour.contour_param_object
        points = list(getattr(contour, "polyline", None) or [])
        if not points or any(abs(i) > 1e-6 for i in getattr(contour, "inclination", [0.0])):
            self.skipped.append("{}: FreeContour with inclined sides - not supported yet".format(self.plate.name))
            return []
        if free_contour.ref_side_index != machine_side:
            self.skipped.append("{}: pocket on an edge (reference side {}) - not supported yet".format(self.plate.name, free_contour.ref_side_index + 1))
            return []
        width = _pocket_width(points)
        if width >= self.tools.router_diameter:
            tool = self.tools.router
        elif width >= self.tools.small_router_diameter:
            tool = self.tools.small_router
        else:
            self.skipped.append("{}: pocket {:.1f} wide - narrower than the smallest router".format(self.plate.name, width))
            return []

        self._pockets += 1
        name = "P{}".format(self._pockets)
        if points[0] != points[-1]:
            points.append(points[0])
        commands = [ContourStart(name, x=round(points[0][0], 3), y=round(points[0][1], 3), easy_snap_xy=EasySnapXY.FRONT_LEFT)]
        commands += [ContourLine(x=round(p[0], 3), y=round(p[1], 3), easy_snap_xy=EasySnapXY.FRONT_LEFT) for p in points[1:-1]]
        commands.append(CloseContour())
        commands.append(FreeFormPocket(contour_name=name, depth=-round(contour.depth, 3)))
        comment = "\n".join(["; ---------------------------------", "; Pocket {:.1f} deep".format(contour.depth), "; ---------------------------------"])
        return [HOPSMachining(tool=tool, work_plane=WorkPlane.TOP, operations=commands, comments=[comment])]


def hops(cabinet, directory=None, tools=None):
    """One HOPS job per plate of the cabinet, written to ``directory`` as ``<part name>.hop`` if given.

    Returns
    -------
    tuple(dict(str, str), :class:`ExportReport`)
        ``{file name: HOPS text}`` and what was left out.
    """
    import os

    report = ExportReport()
    files: Dict[str, str] = {}
    for part in cabinet.layout.parts:
        builder = PlateHopsJob(cabinet.plates[part.name], tools)
        try:
            job = builder.build()
        except Exception as e:  # keep going: one part's problem shouldn't stop the others
            report.errors.append("{}: {}".format(part.name, e))
            continue
        report.skipped.extend(builder.skipped)
        files["{}.hop".format(part.name)] = str(job)
    if directory:
        os.makedirs(directory, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(directory, name), "w") as f:
                f.write(text)
    return files, report


def hops_zip(cabinet, tools=None):
    """All HOPS jobs of the cabinet in a zip, plus a report.txt of what was left out. Returns ``(bytes, report)``."""
    files, report = hops(cabinet, tools=tools)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
        archive.writestr("report.txt", report.summary())
    return buffer.getvalue(), report


def _short_summary(report):
    """A one-line summary of a report, e.g. for a response header."""
    from collections import Counter

    kinds = Counter(s.split(": ", 1)[-1].split(" (")[0].split(" -")[0] for s in report.skipped)
    parts = ["{} x {}".format(n, kind) for kind, n in kinds.most_common()]
    return ("Not exported: " + "; ".join(parts)) if parts else "Everything was exported."


def main():
    """Export a cabinet config to BTLx and/or HOPS: ``python -m cabi_neat.export cabinet.json --btlx out.btlx --hops out/``."""
    import argparse

    from .builder import Cabinet
    from .config import CabinetConfig

    parser = argparse.ArgumentParser(description="Export a cabinet to BTLx and/or HOPS.")
    parser.add_argument("config", help="Cabinet config JSON file.")
    parser.add_argument("--btlx", help="BTLx file to write.")
    parser.add_argument("--hops", help="Directory to write one .hop file per part into.")
    args = parser.parse_args()
    if not (args.btlx or args.hops):
        parser.error("give --btlx and/or --hops")
    cabinet = Cabinet(CabinetConfig.from_json(args.config))
    for error in cabinet.errors:
        print("Joinery: {}".format(error))
    if args.btlx:
        _, report = btlx(cabinet, args.btlx)
        print("BTLx written to {}. {}".format(args.btlx, report.summary()))
    if args.hops:
        files, report = hops(cabinet, args.hops)
        print("{} HOPS files written to {}. {}".format(len(files), args.hops, report.summary()))


if __name__ == "__main__":
    main()
